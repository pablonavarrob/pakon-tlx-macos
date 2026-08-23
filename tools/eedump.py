#!/usr/bin/env python3
"""Dump the scanner's per-unit EEPROM, READ ONLY, and verify it.

The chip (I2C 0x52 on the motherboard) holds the data that makes this unit
this unit, written at the factory and by the Calibration Wizard: the serial
number, the per-DpiBase optical offsets, motor speeds (normal and IR) and
MotorAdjust words, and the 60 colour-matrix floats. It is irreplaceable. It
does NOT hold the light calibration (LED currents and duty cycles): those live in the
Windows registry, written by TLB itself when you run Light Correction, which
is why a fresh install shows current=1 / duty=0 (see docs/PROTOCOL.md). This
tool started life looking for them here; what it is good for is checking the
chip.

The data is stored twice (layout from the pakon-mac project's reading of
TLB's FN_bReadEEPromToRegistry, https://github.com/gazzdingo/pakon-mac
docs/69-calibration-auto-load.md, confirmed on hardware here). Two sections, each {u32 length; u32 crc32;
payload}, CRC-32 (zlib) over the payload only:

    section A  0x0000  398 B   backup at 0x0400
    section B  0x0800   36 B   backup at 0x0A00

TLB reads the primaries at init; the backups are Kodak's insurance. This
reads all four, checks every CRC and compares each primary with its backup,
so a flipped byte shows up as which byte, in which copy, and whether the
other copy is good. The read sequence is exactly the one TLB uses, decoded
from a live capture of its own control transfers.

THE EEPROM IS NEVER WRITTEN. Only the two known read opcodes are issued:
0xA4 with wValue 0xA5 (read-select) and 0xA9 IN at a byte offset, <=32 B.

    ./.venv/bin/python tools/eedump.py
"""
import os
import struct
import sys

# python-libusb1's dylib search list misses Intel Homebrew's
# /usr/local/opt/libusb/lib (it only checks the Apple Silicon path), and
# DYLD_LIBRARY_PATH set after the process has started is too late for dyld to
# see -- so if it's not already pointed at libusb, relaunch once with it set.
# run.sh does this for the server; this tool is invoked directly, so it has
# to do it itself.
if sys.platform == "darwin" and "_PAKON_LIBUSB_REEXEC" not in os.environ:
    import subprocess
    try:
        prefix = subprocess.run(["brew", "--prefix", "libusb"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        prefix = ""
    if prefix:
        os.environ["_PAKON_LIBUSB_REEXEC"] = "1"
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = f"{prefix}/lib" + (f":{existing}" if existing else "")
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)

import usb1

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "server"))
import pakonload                                          # noqa: E402

VID, PID_LOADED, PID_UNLOADED = 0x0F05, 0xF135, 0xF235
EE_INDEX, EE_SETUP, EE_READ = 0x1234, 0xA4, 0xA9
READ_SELECT = 0x00A5
# (offset, length) exactly as TLB.dll asks for them at init (the primaries)
REGIONS = ([(0x0000, 8)] + [(0x0008 + 32 * i, 32) for i in range(13)]
           + [(0x0188, 6), (0x0800, 8), (0x0808, 28)])
# Each section is {u32 length; u32 crc32; payload}; the backups sit 0x400 and
# 0x200 above their primaries. Lengths are read from the headers, capped here.
SECTIONS = (("A", 0x0000, 0x0400, 0x400), ("B", 0x0800, 0x0A00, 0x200))
OUT = "/tmp/eeprom.bin"


def read_eeprom(h):
    blocks = {}
    for off, n in REGIONS:
        blocks[off] = read_region(h, off, n)
    return blocks


def read_section(h, base, maxlen):
    """One whole section by its own header: 8 B header, then the payload the
    header says is there (capped), in <=32 B reads."""
    import zlib
    hdr = read_region(h, base, 8)
    length, crc = struct.unpack("<II", hdr)
    if not 8 < length <= maxlen:
        return hdr, length, crc, None
    data = bytearray(hdr)
    off = base + 8
    while off < base + length:
        n = min(32, base + length - off)
        data += read_region(h, off, n)
        off += n
    calc = zlib.crc32(bytes(data[8:length])) & 0xFFFFFFFF
    return bytes(data), length, crc, calc


def read_region(h, off, n):
    h.controlWrite(0x40, EE_SETUP, READ_SELECT, EE_INDEX, b"", timeout=2000)
    return bytes(h.controlRead(0xC0, EE_READ, off, EE_INDEX, n, timeout=2000))


def verify(h):
    """Read both copies of both sections, check the CRCs, compare the copies."""
    print("\n=== sections and backups ===")
    copies = {}
    for name, prim, back, maxlen in SECTIONS:
        for which, base in (("primary", prim), ("backup", back)):
            data, length, crc, calc = read_section(h, base, maxlen)
            copies[(name, which)] = data
            if calc is None:
                print("   %s %-7s @0x%03x  header len=%d -- implausible, skipped"
                      % (name, which, base, length))
                continue
            print("   %s %-7s @0x%03x  len=%3d  crc stored 0x%08x  computed 0x%08x  %s"
                  % (name, which, base, length, crc, calc,
                     "ok" if crc == calc else "MISMATCH"))
    for name, _p, _b, _m in SECTIONS:
        a, b = copies.get((name, "primary")), copies.get((name, "backup"))
        if a is None or b is None or len(a) < 8 or len(b) < 8:
            continue
        if a == b:
            print("   %s primary == backup" % name)
        else:
            diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
            print("   %s primary != backup: %d byte(s) differ" % (name, len(diffs)))
            for i in diffs[:8]:
                print("      0x%03x  primary 0x%02x  backup 0x%02x" % (i, a[i], b[i]))
    return copies


def hexdump(off, data):
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        print("  %04x  %-47s |%s|" % (off + i, row.hex(" "),
              "".join(chr(c) if 32 <= c < 127 else "." for c in row)))


def analyse(flat, base):
    """The light block holds iCurrent_R/G/B/Ir and dfDutyCycle_R/G/B/Ir.
    Currents are small ints; duty cycles are IEEE doubles.  Rather than guess
    the layout, report every plausible candidate and let the values speak."""
    print("\n=== plausible LED currents (bytes in 1..31, runs of >=3) ===")
    run = []
    for i, c in enumerate(flat):
        if 1 <= c <= 31:
            run.append((base + i, c))
        else:
            if len(run) >= 3:
                print("   @0x%04x: %s" % (run[0][0], [v for _, v in run]))
            run = []
    if len(run) >= 3:
        print("   @0x%04x: %s" % (run[0][0], [v for _, v in run]))

    print("\n=== plausible duty cycles / gains (finite doubles 0.001..100000) ===")
    for i in range(0, len(flat) - 8, 4):
        (d,) = struct.unpack_from("<d", flat, i)
        if 0.001 < abs(d) < 100000 and d == d:
            print("   @0x%04x: %.6f" % (base + i, d))

    print("\n=== 16-bit words that are neither 0 nor 0xFFFF ===")
    nz = sum(1 for i in range(0, len(flat) - 1, 2)
             if struct.unpack_from("<H", flat, i)[0] not in (0, 0xFFFF))
    print("   %d of %d words carry data" % (nz, len(flat) // 2))
    if nz == 0:
        print("   -> THE BLOCK IS BLANK.  No light calibration is stored on this "
              "unit,\n      which is exactly why TLB falls back to current=1 / duty=0.")


def main():
    ctx = usb1.USBContext()
    ctx.open()
    h = ctx.openByVendorIDAndProductID(VID, PID_LOADED)
    if h is None:
        if ctx.openByVendorIDAndProductID(VID, PID_UNLOADED) is None:
            raise SystemExit("No scanner on the bus -- power it on and plug it in.")
        print("uploading firmware first...")
        ctx.close()
        pakonload.load_firmware(log=lambda m: print("  fw:", m))
        ctx = usb1.USBContext()
        ctx.open()
        h = ctx.openByVendorIDAndProductID(VID, PID_LOADED)
        if h is None:
            raise SystemExit("firmware uploaded but no re-enumeration")
    try:
        h.setAutoDetachKernelDriver(True)
    except usb1.USBError:
        pass
    h.claimInterface(0)
    try:
        blocks = read_eeprom(h)
        copies = verify(h)
    finally:
        h.releaseInterface(0)
        h.close()
        ctx.close()

    for off in sorted(blocks):
        print("--- 0x%04x (%d bytes)" % (off, len(blocks[off])))
        hexdump(off, blocks[off])

    main_block = b"".join(blocks[o] for o in sorted(blocks) if o < 0x800)
    with open(OUT, "wb") as f:
        f.write(main_block)
    print("\nwrote %d bytes to %s" % (len(main_block), OUT))
    for (name, which), data in sorted(copies.items()):
        path = OUT.replace(".bin", "_%s_%s.bin" % (name, which))
        with open(path, "wb") as f:
            f.write(data)
    print("wrote the four section copies beside it as %s"
          % OUT.replace(".bin", "_{A,B}_{primary,backup}.bin"))
    analyse(main_block, 0x0000)


if __name__ == "__main__":
    main()
