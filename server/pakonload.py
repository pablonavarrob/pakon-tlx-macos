#!/usr/bin/env python3
"""pakon-load: bring a Pakon F135 scanner to life on Linux (userspace, libusb).

The scanner is a Cypress EZ-USB FX2 that boots with no application firmware.
With its EEPROM programmed it enumerates as 0f05:f235 (REV aaXX) exposing only
EP0; after firmware upload it re-enumerates as 0f05:f135 with the bulk
command/image endpoints.

This tool bundles ONLY the generic Cypress EZ-USB second-stage loader
(firmware/ezusb_stage2.ihex). The scanner application firmware (pakon5/7/8.hex)
is Kodak/Pakon property and is NOT bundled — you supply it from your original
Pakon CD via --firmware-dir (it is auto-detected under /run/media and /media).

Load procedure (ported from the open-source FX35Loader.c, verified against a
real USB capture):
  1. hold 8051 in reset            (0xA0 -> CPUCS 0x7F92=1 and 0xE600=1)
  2. download 2nd-stage loader     (0xA0 internal-RAM writes; enables 0xA3)
  3. release 8051                  (CPUCS = 0)
  4. init                          (0xA4, wValue 0xA1)
  5. read personality              (0xA9 -> id, VID, PID, REV)
  6. SELECT app firmware from the personality wRevision (matches the OEM
     DownloadFirmware: it picks the image AFTER the A9 read, not from the
     pre-load USB bcdDevice). Falls back to bcdDevice if unavailable/unmapped.
  7. download app firmware         (0xA3 external pass, then 0xA0 internal pass)
  8. reset 8051 (1 then 0) to start the application firmware

Run as root, or install udev/70-pakon.rules for non-root access.
"""
import glob
import os
import struct
import sys
import time

import usb1

VID = 0x0F05
PID_UNLOADED = 0xF235
PID_LOADED = 0xF135

CPUCS_EZUSB = 0x7F92          # 8051 reset register (EZ-USB)
CPUCS_FX2 = 0xE600            # 8051 reset register (FX2)
A_LOAD_INTERNAL = 0xA0        # write on-chip RAM (implemented in EZ-USB core)
A_LOAD_EXTERNAL = 0xA3        # write external RAM (needs 2nd-stage loader)
A_INIT = 0xA4
A_PERSONALITY = 0xA9
MAX_INTERNAL = 0x1B3F         # <= this -> internal (A0); above -> external (A3)
CHUNK = 0x40                  # 64-byte download chunks (Rev B safe)

def _fw_asset(name):
    # Local copy: this module is used standalone, outside the psix package.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


STAGE2 = _fw_asset('ezusb_stage2.ihex')                          # generic EZ-USB loader (shipped, ours)
# Where the user's Kodak scanner firmware (pakon*.hex / pkninit.hex — NOT shipped) lives. Defaults to a
# user dir; overridable via $PSIX_FIRMWARE_DIR or --firmware-dir. (load_firmware also auto-detects the CD.)
FWDIR = os.environ.get("PSIX_FIRMWARE_DIR") or os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")),
    "psix", "firmware")
REV_TO_FILE = {0xAA05: 'pakon5.hex', 0xAA07: 'pakon7.hex', 0xAA08: 'pakon8.hex'}


def firmware_status(firmware_dir=FWDIR):
    """Inspect the firmware dir for the user-supplied scanner image. The only file
    actually required to load an F135+ is a REV-matched application image
    (pakon5/7/8.hex); the second-stage loader is bundled. Returns a UI-friendly dict."""
    app_images = sorted(os.path.basename(p)
                        for p in glob.glob(os.path.join(firmware_dir, '[Pp]akon[0-9].hex')))
    other = sorted(os.path.basename(p)
                   for p in glob.glob(os.path.join(firmware_dir, '*.hex'))
                   if os.path.basename(p) not in app_images)
    return {
        "dir": firmware_dir,
        "present": bool(app_images),          # enough to attempt a load
        "app_images": app_images,
        "other_files": other,
        "expected": sorted(set(REV_TO_FILE.values())),   # pakon5/7/8.hex
    }


def parse_ihex(path):
    """Parse Intel HEX -> ordered list of (address, data_bytes) type-0 records.
    Tolerates the ': '-spaced variant some Pakon files use."""
    out = []
    for line in open(path, 'r', errors='replace'):
        line = line.strip()
        if not line.startswith(':'):
            continue
        body = bytes.fromhex(line[1:].replace(' ', ''))
        if len(body) < 5:
            continue
        ln, hi, lo, typ = body[0], body[1], body[2], body[3]
        if typ == 1:            # EOF
            break
        if typ != 0:            # ignore extended-address records (firmware is <64K)
            continue
        out.append(((hi << 8) | lo, body[4:4 + ln]))
    return out


def control_write(h, request, value, data=b''):
    h.controlWrite(0x40, request, value, 0, data, timeout=2000)


def reset_8051(h, hold):
    """hold=1 stops the CPU, hold=0 starts it. Writes both CPUCS registers."""
    control_write(h, A_LOAD_INTERNAL, CPUCS_EZUSB, bytes([hold]))
    control_write(h, A_LOAD_INTERNAL, CPUCS_FX2, bytes([hold]))


def download_records(h, records):
    """Download in two passes, exactly like FX35Loader's Ezusb_DownloadIntelHex:
      1. external RAM (A3) FIRST, while the 8051 is RUNNING the 2nd-stage loader
         (the core can't do A3; the loader firmware must be executing to service it).
      2. then HOLD the 8051 in reset and write internal RAM (A0), which the FX2
         core handles even with the CPU halted (and which would otherwise clobber
         the running loader's own code).
    Each transfer is chunked to <=64 bytes (Rev B safe)."""
    for addr, data in records:                      # external pass (A3), CPU running
        if addr > MAX_INTERNAL:
            for off in range(0, len(data), CHUNK):
                control_write(h, A_LOAD_EXTERNAL, addr + off, data[off:off + CHUNK])
    reset_8051(h, 1)                                # halt CPU before touching internal RAM
    for addr, data in records:                      # internal pass (A0), CPU halted
        if addr <= MAX_INTERNAL:
            for off in range(0, len(data), CHUNK):
                control_write(h, A_LOAD_INTERNAL, addr + off, data[off:off + CHUNK])


def parse_personality(pers):
    """Unpack the 8-byte DEVICE_PERSONALITY (FX35 F135Loader.h, pack(1)):
    BYTE id, WORD wVendorId, WORD wProductId, WORD wRevision, BYTE unk."""
    if pers is None or len(pers) < 8:
        return None
    pid_id, pvid, ppid, prev, _ = struct.unpack('<BHHHB', bytes(pers[:8]))
    return {'id': pid_id, 'vid': pvid, 'pid': ppid, 'rev': prev}


def find_firmware_file(fwdir, rev, strict=False):
    """Map a revision -> firmware .hex. `strict` (used for the personality wRevision
    path) refuses the last-resort glob guess, so an unrecognised REV falls through to
    the proven bcdDevice path instead of grabbing an arbitrary pakon*.hex."""
    if rev is None:
        return None
    name = REV_TO_FILE.get(rev)
    if name is None:
        # fall back to whatever pakon<N>.hex matches the low REV nibble
        name = 'pakon%d.hex' % (rev & 0x0F)
    for cand in (name, name.capitalize(), name.upper()):
        p = os.path.join(fwdir, cand)
        if os.path.exists(p):
            return p
    if strict:
        return None
    hits = sorted(glob.glob(os.path.join(fwdir, '[Pp]akon[0-9].hex')))
    return hits[0] if hits else None


def load_firmware(firmware_dir=FWDIR, stage2=STAGE2, wait=8.0, log=print):
    """Upload application firmware to an unloaded (f235) Pakon and wait for it to
    re-enumerate as loaded (f135).

    Returns True on success (including when a loaded device is already present),
    False otherwise. `log` is a callable(str) for progress (default print); pass
    a no-op to silence. This is the callable core; main() is a thin CLI wrapper.
    """
    if log is None:
        log = lambda *_a, **_k: None

    with usb1.USBContext() as ctx:
        if ctx.openByVendorIDAndProductID(VID, PID_LOADED, skip_on_error=True):
            log(f"Device already loaded ({VID:04x}:{PID_LOADED:04x}). Power-cycle to reload.")
            return True
        handle = ctx.openByVendorIDAndProductID(VID, PID_UNLOADED, skip_on_error=True)
        if handle is None:
            log(f"ERROR: no unloaded Pakon device ({VID:04x}:{PID_UNLOADED:04x}). "
                f"Plugged in & powered? Permission? (see udev/70-pakon.rules)")
            return False

        dev = handle.getDevice()
        rev = dev.getbcdDevice()
        log(f"Found unloaded scanner {VID:04x}:{PID_UNLOADED:04x} REV 0x{rev:04x}")

        # early sanity check (before touching the device): the firmware dir must hold candidates.
        # Precise selection happens AFTER the A9 personality read (matches the OEM).
        if not glob.glob(os.path.join(firmware_dir, '[Pp]akon[0-9].hex')):
            log(f"ERROR: no pakon*.hex in {firmware_dir}\n"
                f"  Add your scanner firmware (pakon5/7/8.hex) to that folder — or drop it on the "
                f"Settings page in the app (see README.md).")
            return False
        log(f"  stage-2 loader : {stage2}")
        stage2_recs = parse_ihex(stage2)

        try:
            handle.setAutoDetachKernelDriver(True)
        except usb1.USBError:
            pass

        t0 = time.monotonic()
        reset_8051(handle, 1)                 # hold CPU to load 2nd-stage loader
        download_records(handle, stage2_recs) # 2nd-stage loader (internal only)
        reset_8051(handle, 0)                 # RUN 2nd-stage loader (services A3)
        control_write(handle, A_INIT, 0xA1)   # init

        # A9 personality read -> drives firmware selection, exactly like the OEM
        # DownloadFirmware (which picks the image from wRevision AFTER loading stage-1).
        sel_rev = None
        try:
            pers = parse_personality(handle.controlRead(0xC0, A_PERSONALITY, 0, 0, 8, timeout=2000))
        except usb1.USBError as e:
            pers = None
            log(f"  (personality read failed: {e})")
        if pers:
            log("  personality   : id=0x%02x VID=0x%04x PID=0x%04x REV=0x%04x"
                % (pers['id'], pers['vid'], pers['pid'], pers['rev']))
            if pers['rev'] not in (0x0000, 0xffff):   # plausible revision
                sel_rev = pers['rev']

        # primary: personality wRevision (OEM behaviour, strict — no glob guess)
        appfw = find_firmware_file(firmware_dir, sel_rev, strict=True)
        if appfw:
            log(f"  app firmware  : {appfw}  (selected from personality wRevision 0x{sel_rev:04x})")
            if sel_rev != rev:
                log(f"    note: personality REV 0x{sel_rev:04x} != USB bcdDevice 0x{rev:04x}")
        else:                                          # fallback: proven bcdDevice path
            appfw = find_firmware_file(firmware_dir, rev)
            if appfw:
                why = "no personality" if sel_rev is None else f"wRevision 0x{sel_rev:04x} unmapped"
                log(f"  app firmware  : {appfw}  (fallback to USB bcdDevice 0x{rev:04x}; {why})")
        if not appfw:
            log(f"ERROR: no pakon*.hex matches personality/bcdDevice REV in {firmware_dir}.\n"
                f"  Stage-1 loader is up; power-cycle the scanner and retry.")
            return False
        apprecs = parse_ihex(appfw)

        try:
            # app firmware: external pass needs the loader RUNNING; download_records
            # halts the CPU itself before the internal pass.
            download_records(handle, apprecs)     # application firmware (ext A3 + int A0)
            reset_8051(handle, 1)
            reset_8051(handle, 0)                 # start application firmware
        except usb1.USBError as e:
            # final reset drops the device off the bus mid-transfer; expected
            log(f"  (device reset/disconnected during final stage: {e})")
        log(f"  upload finished in {time.monotonic()-t0:.2f}s")
        del handle

    log(f"Waiting up to {wait:.0f}s for re-enumeration as {VID:04x}:{PID_LOADED:04x}...")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        with usb1.USBContext() as ctx:
            h = ctx.openByVendorIDAndProductID(VID, PID_LOADED, skip_on_error=True)
            if h:
                d = h.getDevice()
                log(f"SUCCESS: scanner is now {VID:04x}:{PID_LOADED:04x} "
                    f"(bus {d.getBusNumber()} addr {d.getDeviceAddress()}).")
                return True
        time.sleep(0.3)
    log("ERROR: device did not re-enumerate as f135 within timeout.")
    return False
