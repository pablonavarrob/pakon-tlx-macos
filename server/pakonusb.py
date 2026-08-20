#!/usr/bin/env python3
r"""pakonusb -- serve \\.\Pakon135 to the OEM TLX stack running under Wine.

Self-contained on purpose: pkusb.dll (inside the Wine process) intercepts
tlx.dll's and TLB.dll's five device calls and forwards them here over a local
socket; this side owns libusb.  Nothing outside this directory is needed.

Endpoints and IOCTLs, all read out of the OEM driver and cross-checked against
TLB.dll's call sites:

    0x222059  EP0 control transfer.  10-byte input struct:
              direction, requestType(1=class,2=vendor), recipient, reserved,
              bRequest, pad, wValue:u16, wIndex:u16
    0x222090  bulk OUT EP 0x01 then bulk IN EP 0x81 -- command/response channel
    0xFFFFFFFF  pseudo-code for ReadFile: bulk IN EP 0x86 (the image stream)

DEVICE SAFETY -- enforced here because this is the only place it can be:
  * The EEPROM is NEVER written.  It holds irreplaceable per-unit calibration.
    On wIndex 0x1234 only the known reads pass: request 0xA4 with wValue bit 0
    set (read setup), and request 0xA9 IN.  Anything else is dropped.
  * LED currents are clamped to the firmware's OWN ceilings, which depend on
    the board AND on whether IR is lit -- on a 0x24 F-135 that is R8/G8/B8/Ir8
    with IR on, a QUARTER of the 0x44 board's G/B limit.  Unknown board or
    unknown lamp state takes the strictest row, never the loosest.  LED wear is
    this unit's known failure mode.  Currents are set by a type-2 WRITE of
    register 0x81 whose 5-byte payload is [B, IR, R, _, G].
  * On exit the motor stop order rate=0 -> go -> idle is issued.  A bare stop
    does NOT halt the drive.

    ./.venv/bin/python pakonusb.py
"""
import collections
import os
import socket
import struct
import sys
import threading
import time

try:
    import usb1
except ImportError:                      # --selftest exercises pure logic only
    if "--selftest" not in sys.argv:
        raise
    usb1 = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pakonload                                      # noqa: E402
except ImportError:
    if "--selftest" not in sys.argv:
        raise
    pakonload = None
import ppb                                                # noqa: E402

VID, PID_LOADED, PID_UNLOADED = 0x0F05, 0xF135, 0xF235
EP_CMD_OUT, EP_CMD_IN, EP_IMG_IN = 0x01, 0x81, 0x86
RESP_MAX = 64
IMG_CHUNK = 0x5000          # 20480-byte image packets, as the OEM driver used
IMG_TRANSFERS = 16          # in-flight async reads; one at a time cannot keep up
IDLE_STOP = 45.0            # seconds of TOTAL device silence before we stop the
                            # scan.  It must outlast the OEM's own quiet spells:
                            # its white grab is 256 lines over ~6 s with no image
                            # read at all, and a 6 s image-only watchdog cut the
                            # lamp in the middle of calibration.  The OEM
                            # heartbeats at 20 Hz while scanning, so no commands
                            # AND no reads really does mean it has given up.
                            # 10s was too short: after an 8 MB read TLB goes
                            # completely quiet for >10s while it processes it,
                            # and the lamp was cut in the middle of calibration.
IMG_IDLE_STOP = 120.0       # the OEM can stay alive yet stop consuming images;
                            # the lamp must not burn indefinitely in that case
MAX_BUF = 64 << 20          # cap on unconsumed image data
ALIGN_SCAN = 131072         # bytes inspected to find the line phase: ~10 lines
                            # of 3-channel (12216 B) or 8 of 4-channel (16000 B),
                            # enough for the dominant phase to beat LSB noise
EP6_DEADLINE = 20.0         # max seconds to fill one image read
EP6_LINES = int(os.environ.get("PAKON_EP6_LINES", "128"))
                            # lines returned per image read (0 = fill the whole
                            # ring).  The OEM's own Error-149 tuning uses a
                            # trigger of ~15% of the ring; 128 lines is that
                            # order and matches its 128-dark-line calibration.
BURST_GAP = 1.0             # a read this long after the last one starts a new
                            # scan: anything still buffered predates it and is
                            # dropped.  Reads within a scan are milliseconds
                            # apart, so this never breaks a line's continuity.

PORT = 5140
IOCTL_VENDOR, IOCTL_CMD = 0x222059, 0x222090
READ_EP6 = 0xFFFFFFFF

EE_INDEX, EE_SETUP, EE_READ, EE_WRITE = 0x1234, 0xA4, 0xA9, 0xA2

# The firmware's own ceilings (0x100203c0) depend on BOTH the board and whether
# IR is lit -- see the table in docs/PROTOCOL.md.  Keyed [board][ir_on] -> per
# channel maximum.  Register 0x81's payload order is [B, IR, R, _, G], which is
# why the slot indices below are 0/1/2/4 and not 0/1/2/3.
#
# The 0x24 row is a QUARTER of the 0x44 row on G and B.  Using the wrong row is
# not a rounding error, so when the board or the lamp state is not yet known we
# take the strictest row of all.  Permissive-by-default is the wrong failure
# mode when LED wear is the known way these units die.
LED_CEILINGS = {
    0x44: {True:  {"R": 8, "G": 24, "B": 24, "IR": 8},
           False: {"R": 4, "G": 20, "B": 20, "IR": 0}},
    0x24: {True:  {"R": 8, "G": 8,  "B": 8,  "IR": 8},
           False: {"R": 6, "G": 8,  "B": 8,  "IR": 0}},
}
LED_SLOTS = {0: "B", 1: "IR", 2: "R", 4: "G"}
LED_STRICTEST = {"R": 4, "G": 8, "B": 8, "IR": 0}
LAMP_REG = 0x80             # PICL: bit0 visible, bit1 IR (docs/PROTOCOL.md)

# The PICs' bootloader I2C addresses.  The bootloader is the only code on either
# chip that can erase or write flash, and it is reachable over THIS command
# channel -- a type-4 packet to 0x46 with the right command bits is a 64-byte
# flash row erase.  That is how a real unit lost a row of its motor controller's
# firmware.  TLB pings these while probing for the controller pair, so reads and
# polls pass; writes never do.  This is what makes "do not flash the PICs" a
# guarantee rather than a note in a README.
BOOTLOADER_ADDRS = (0x22, 0x26, 0x42, 0x46)
WRITE_TYPES = (2, 4)        # 1 = READ, 2 = WRITE, 3 = POLL, 4 = WRITE2

# The controller pair is PROBED by TLB at start-up and differs per board
# (0x24/0x20 on an F-135, 0x44/0x40 on another variant), so never assume it:
# ppb.board() tracks whatever this scanner actually answers on.  Getting this
# wrong silently breaks the trigger detection and the motor stop.
REG_RATE, GO_FWD, IDLE = 0xA5, 0xA0, 0xA2
REG_TRIGGER = 0x91          # WRITE PICL reg0x91 resets the line counter and the
                            # EP6 stream starts there -- PakonKit/docs/scan-start.md
                            # triggers #1 (calibration) and #2 (transport scan)
DUMP_PATH = os.environ.get("PAKON_DUMP", "/tmp/ep6_dump.bin")   # first image read, kept for framing analysis


_T0 = time.time()


_CAPTURE = [None]        # the open capture file, or None


def say(m):
    print("%8.3f %s" % (time.time() - _T0, m), flush=True)
    cap = _CAPTURE[0]
    if cap is not None:
        try:
            cap.write('{"t":%.6f,"d":"log","msg":%s}\n' % (time.time(), json.dumps(m)))
        except ValueError:
            pass            # capture closed during shutdown


def line_sync(head):
    """(phase, period) of the line-sync markers, in samples, or (None, None).

    Each line begins with a marker: bit0 of its first uint16 sample.  Dark-region
    LSB noise sets that bit at random positions too, so the FIRST set bit is not
    necessarily a line start -- taking it naively mis-set a measured capture by
    2094 samples.  psix's decode.py settles the method: the true markers all
    share one position mod P, so take the dominant spacing as the period and
    then the dominant phase.
    """
    pos = [i >> 1 for i in range(0, len(head), 2) if head[i] & 1]
    if len(pos) < 8:
        return None, None
    gaps = collections.Counter(b - a for a, b in zip(pos, pos[1:]))
    period = gaps.most_common(1)[0][0]
    if period < 1000:                      # noise cluster, not a line period
        return None, None
    phase = collections.Counter(p % period for p in pos).most_common(1)[0][0]
    return phase, period


class Scanner:
    ir_on = None            # class default: unknown -> strictest ceilings
    lastquiet = None        # (n, request, response) of the last suppressed poll

    def __init__(self):
        self.ctx = usb1.USBContext()
        self.ctx.open()
        self.h = None
        self.lock = threading.RLock()
        self.blocked = []
        self.clamped = []
        self.n = 0
        self.imgbuf = bytearray()
        self.imglock = threading.Lock()
        self.imgthread = None
        self.imgtotal = 0
        self.reading = False
        self._lastmb = 0
        self.lastread = 0.0
        self.dump = bytearray()      # first image read, written to DUMP_PATH
        self.lastcmd = time.time()   # any device command: the OEM's liveness
        self.align = False           # align the next read to a line boundary
        self.line_bytes = 0          # measured from the markers at align time
        self.served = False          # this transfer has delivered bytes to the OEM
        self.lastpoll = {}           # last response per poll target
        # Research capture: every command and reply as timestamped JSON lines,
        # enabled by PAKON_CAPTURE=<path>.  Image pixels, EEPROM contents and
        # firmware are never written -- only markers with their sizes -- so a
        # capture is safe to publish.
        cap_path = os.environ.get("PAKON_CAPTURE")
        if cap_path:
            os.makedirs(os.path.dirname(cap_path) or ".", exist_ok=True)
        self.capture = open(cap_path, "a", buffering=1) if cap_path else None
        _CAPTURE[0] = self.capture
        if self.capture is not None:
            self.capture.write(
                '{"d":"meta"'
                ',"t":%.6f' % time.time() +
                ',"label":%s' % json.dumps(os.environ.get("PAKON_CAPTURE_LABEL", "")) +
                ',"bridge":"pakon-tlx-macos with the capture-logging patch"'
                ',"clock":"host wall clock, time.time()"'
                ',"scope":"USB application traffic; driver-shim internals and'
                ' image-ring mechanics are not part of the record"'
                ',"streams":{"cmd":"PPB command the OEM stack sent, hex"'
                ',"cmd_mod":"what reached the scanner when the LED clamp changed'
                ' the command; absent otherwise"'
                ',"rsp":"the scanner reply, hex"'
                ',"ep0":"control transfer, setup fields and length; payload'
                ' omitted (EEPROM contents are per-unit); blocked:true if refused"'
                ',"ep6":"image-data transfer, byte count only, pixels omitted"'
                ',"fw":"marker: firmware upload happened here, bytes omitted"'
                ',"log":"the bridge\'s own narration, for context"}}\n')

    def open(self):
        self.h = self.ctx.openByVendorIDAndProductID(VID, PID_LOADED)
        if self.h is not None:
            say("scanner already has application firmware (warm start)")
        if self.h is None:
            # Firmware lives in RAM, so it is gone after every power cycle.
            # Upload it here rather than making the user run a second tool.
            probe = self.ctx.openByVendorIDAndProductID(VID, PID_UNLOADED)
            if probe is None:
                raise SystemExit(f"No scanner {VID:04x}:{PID_LOADED:04x} on the bus. "
                                 "Powered on and plugged in?")
            probe.close()
            say("scanner has no application firmware -- uploading it (cold start)")
            if self.capture is not None:
                self.capture.write(
                    '{"t":%.6f,"d":"fw","note":"application firmware uploaded'
                    ' over EP0 here; bytes omitted (Kodak firmware, never'
                    ' published)"}\n' % time.time())
            self.ctx.close()
            pakonload.load_firmware(log=lambda m: say(f"  fw: {m}"))
            self.ctx = usb1.USBContext()
            self.ctx.open()
            self.h = self.ctx.openByVendorIDAndProductID(VID, PID_LOADED)
            if self.h is None:
                raise SystemExit("firmware uploaded but the scanner did not "
                                 "re-enumerate; power-cycle and try again")
        try:
            self.h.setAutoDetachKernelDriver(True)
        except usb1.USBError:
            pass
        self.h.claimInterface(0)
        # Deliberately NOT start_stream().  EP6 free-runs the moment the
        # firmware is up -- 37 MB arrived here before the client had even
        # connected -- so draining it early only builds a backlog of pre-scan
        # data.  Serving that backlog is what produced EC_DRV_RingTailOverflow:
        # the OEM's first read was satisfied in 0.0s from bytes captured
        # minutes earlier, before the lamp, the LED currents or the gains were
        # set, so uiGetCorrections found nothing to correct with.  The stream
        # now starts on the first ReadFile, which makes reads current by
        # construction.
        return self

    def reopen(self):
        """Re-acquire the device after a re-enumeration.  Returns True if the
        handle is usable again."""
        say("  device vanished -- trying to reconnect")
        self.reading = False
        try:
            if self.h:
                self.h.close()
        except Exception:
            pass
        self.h = None
        with self.imglock:
            self.imgbuf = bytearray()        # anything buffered predates the reset
        try:
            self.ctx.close()
        except Exception:
            pass
        try:
            self.ctx = usb1.USBContext()
            self.ctx.open()
            self.open()
            say("  reconnected")
            return True
        except (usb1.USBError, SystemExit) as e:
            say(f"  reconnect failed: {e}")
            return False

    def close(self):
        try:
            if self.h:
                self.h.releaseInterface(0)
                self.h.close()
        finally:
            self.ctx.close()

    # ---------------- command channel ----------------
    def led_ceilings(self):
        """The row of the firmware's ceiling table that applies right now.

        Unknown board or unknown lamp state -> the strictest row, never the
        loosest: a 0x44 ceiling applied to a 0x24 board would permit 3x the
        vendor's own limit on G and B."""
        board = ppb.board()[0]
        if board not in LED_CEILINGS or self.ir_on is None:
            return LED_STRICTEST
        return LED_CEILINGS[board][self.ir_on]

    def note_lamp(self, p):
        """Track the IR bit of the last lamp write, because it moves the
        ceilings.  PICL reg 0x80: bit0 visible, bit1 IR."""
        if len(p) >= 6 and p[0] == 2 and p[4] == LAMP_REG and p[2] == ppb.board()[1]:
            self.ir_on = bool(p[5] & 0x02)

    def watch_bootloader(self, p):
        """Report -- do NOT block -- a write aimed at a PIC bootloader address.

        That path can erase PIC flash, and it is reachable over this channel.
        But TLB probes for its controller pair at start-up across exactly these
        addresses, and we have no capture of our own showing whether it probes
        with reads or writes.  Blocking would risk breaking discovery, which is
        the first thing that happens, to defend against Kodak's own software
        spontaneously reflashing Kodak's own hardware -- a threat that does not
        exist here.  So this is instrumentation: if it ever fires, the trace
        says so and we can decide with evidence instead of inference."""
        if len(p) >= 5 and p[0] in WRITE_TYPES and p[2] in BOOTLOADER_ADDRS:
            say(f"  ?? write to bootloader address 0x{p[2]:02x} reg 0x{p[4]:02x}"
                f" -- passed through, but that path can erase PIC flash")
        return False

    def clamp_leds(self, p):
        """Type-2 WRITE of reg 0x81 sets LED current.  Packet layout is
        [type, len, addr, count, reg, *value]."""
        self.note_lamp(p)
        if len(p) >= 6 and p[0] == 2 and p[4] == 0x81:
            ceilings = self.led_ceilings()
            for i, name in LED_SLOTS.items():
                ceil, j = ceilings[name], 5 + i
                if i < p[3] and j < len(p) and p[j] > ceil:
                    self.clamped.append((name, p[j], ceil))
                    say(f"  !! LED {name} current {p[j]} -> clamped to {ceil}"
                        f"  (board 0x{ppb.board()[0]:02x}, IR {self.ir_on})")
                    p[j] = ceil
        return p

    def cmd(self, pkt, outsz):
        pkt = bytearray(pkt)
        cap = self.capture
        if cap is not None:
            cap.write('{"t":%.6f,"d":"cmd","hex":"%s"}\n' % (time.time(), bytes(pkt).hex()))
        original = bytes(pkt)
        self.watch_bootloader(pkt)
        pkt = self.clamp_leds(pkt)
        if cap is not None and bytes(pkt) != original:
            cap.write('{"t":%.6f,"d":"cmd_mod","hex":"%s"}\n' % (time.time(), bytes(pkt).hex()))
        self.lastcmd = time.time()
        _picm, picl = ppb.board()
        trigger = (len(pkt) >= 5 and pkt[0] == 2 and pkt[2] == picl
                   and pkt[4] == REG_TRIGGER)
        with self.lock:
            self.h.bulkWrite(EP_CMD_OUT, bytes(pkt), timeout=3000)
            r = bytes(self.h.bulkRead(EP_CMD_IN, RESP_MAX, timeout=3000))
        if cap is not None:
            cap.write('{"t":%.6f,"d":"rsp","hex":"%s"}\n' % (time.time(), r.hex()))
        if trigger:
            self.arm_stream()
        return r[:outsz] if outsz else r

    def arm_stream(self):
        """The scan trigger (WRITE PICL reg0x91) resets the scanner's LINE
        COUNTER; it does not restart the byte stream.  The OEM issues Trigger #2
        in the middle of the same transfer, so clearing and re-aligning here
        punched a ~15 KB hole into a stream TLB was already tracking line by
        line -- which is exactly EC_DRV_LostSync (1003).  Only reset when no
        transfer is running yet."""
        if self.served:
            say("  EP6 trigger during an active transfer -- stream kept continuous")
            self.lastread = time.time()
            return
        self.stop_stream()          # retires in-flight transfers first
        self.imgtotal = 0
        self.lastread = time.time()
        self.align = True
        self.start_stream()
        say("  EP6 armed (PICL reg0x91: line counter reset, stream starts here)")

    def align_to_line(self, timeout=5.0):
        """Drop bytes until the buffer begins exactly on a line boundary.

        Every CCD line starts with a hardware line-sync marker -- bit0 of its
        first uint16 sample -- and TLB.dll frames lines by scanning for it
        (0x1001d2b0, `test byte ptr [ebp], 1`).  Clearing the buffer at the
        trigger lands a sample or two early, so measured captures came out at
        phase 1: line 0 began at byte 2, not byte 0.  Returns the bytes dropped,
        or -1 if no marker turned up.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.imglock:
                if len(self.imgbuf) >= ALIGN_SCAN:
                    phase, period = line_sync(bytes(self.imgbuf[:ALIGN_SCAN]))
                    if phase is None:
                        del self.imgbuf[:ALIGN_SCAN]       # none here, keep going
                    else:
                        self.line_bytes = period * 2
                        del self.imgbuf[:phase * 2]
                        return phase * 2
            time.sleep(0.001)
        return -1

    # ---------------- EP0 control ----------------
    @staticmethod
    def allowed(req, val, idx, direction):
        """Read-only EEPROM policy.  Outside wIndex 0x1234 this is ordinary
        vendor traffic (0xA0 8051 RAM download and friends) and passes.

        Scope, stated honestly: this guards the vendor EP0 path.  0xA0 loads
        8051 RAM and we must allow it (our own firmware upload needs it), so an
        image loaded that way could drive the FX2's own I2C controller and reach
        the EEPROM from inside the chip.  Nothing in this project does that, but
        the rule is "no EEPROM writes via the vendor path", not "impossible"."""
        # 0xA2 is the EEPROM WRITE opcode.  Refuse it at ANY wIndex: the vendor
        # path uses 0x1234, but the write can be issued with wValue=wIndex=0 and
        # would sail past a guard that only looks at 0x1234.  No call site in
        # TLB.dll passes it, so nothing legitimate is lost.
        if req == EE_WRITE:
            return False
        if idx != EE_INDEX:
            return True
        # wValue is an I2C device selector, ((n | 0x50) << 1) | readBit, not a
        # byte address -- which is why bit 0 is the read/write bit.
        if req == EE_SETUP:
            return bool(val & 1)          # bit 0 selects a READ setup
        if req == EE_READ:
            return bool(direction)        # IN only
        return False                      # default deny

    def vendor(self, data, outsz):
        if len(data) < 10:
            return None
        direction, rtype, recip = data[0], data[1], data[2]
        req = data[4]
        val, idx = struct.unpack_from("<HH", data, 6)
        if not self.allowed(req, val, idx, direction):
            self.blocked.append((req, val, idx))
            if self.capture is not None:
                self.capture.write(
                    '{"t":%.6f,"d":"ep0","req":%d,"wValue":%d,"wIndex":%d,"dir":"%s","blocked":true}\n'
                    % (time.time(), req, val, idx, "in" if direction else "out"))
            say(f"  !! BLOCKED control req=0x{req:02x} wValue=0x{val:04x} "
                f"wIndex=0x{idx:04x} -- would touch the EEPROM")
            return None
        bm = ((0x80 if direction else 0)
              | (0x20 if rtype == 1 else 0x40)
              | (recip & 0x1F))
        with self.lock:
            if direction:
                out = bytes(self.h.controlRead(bm, req, val, idx, outsz, timeout=3000))
            else:
                self.h.controlWrite(bm, req, val, idx, b"", timeout=3000)
                out = b""
        if self.capture is not None:
            self.capture.write(
                '{"t":%.6f,"d":"ep0","req":%d,"wValue":%d,"wIndex":%d,"dir":"%s","n":%d}\n'
                % (time.time(), req, val, idx, "in" if direction else "out", len(out)))
        return out

    # ---------------- image stream (EP 0x86) ----------------
    # The scanner streams continuously once a scan starts; if we only read when
    # the OEM asks, its ring buffer overflows (EC_DRV_RingTailOverflow 1002) and
    # calibration aborts.  So a reader thread drains the endpoint the whole time
    # and ReadFile is served from that buffer.  It must NOT take self.lock, or
    # image reads would block the command channel.
    def _reader(self):
        """Keep N transfers in flight with no timeout, exactly as psix does.

        A single synchronous bulkRead with a short timeout cannot keep this pipe
        full, and on macOS the repeated timeouts wedge the endpoint into
        LIBUSB_ERROR_IO -- which is why no image data reached the OEM and its
        ring overflowed.
        """
        inflight = [0]

        def cb(transfer):
            # Only while reading: a transfer submitted before a trigger must not
            # land in the buffer after it, or pre-trigger data is spliced on top
            # of post-trigger data.  That is what put one 8202-sample gap into an
            # otherwise perfectly periodic capture.
            if self.reading and transfer.getStatus() == usb1.TRANSFER_COMPLETED:
                n = transfer.getActualLength()
                if n:
                    with self.imglock:
                        # Safety valve: if the consumer stalls, do not grow
                        # without bound (a stall once reached 240 MB).
                        if len(self.imgbuf) < MAX_BUF:
                            self.imgbuf.extend(transfer.getBuffer()[:n])
                        self.imgtotal += n
                    if self.capture is not None:
                        self.capture.write('{"t":%.6f,"d":"ep6","n":%d}\n' % (time.time(), n))
            if self.reading:
                try:
                    transfer.submit()
                    if self.imgtotal >> 24 != self._lastmb:      # every 16 MB
                        self._lastmb = self.imgtotal >> 24
                        say(f"  EP6 streaming: {self.imgtotal >> 20} MB")
                    return
                except usb1.USBError:
                    pass
            inflight[0] -= 1

        transfers = []
        try:
            for _ in range(IMG_TRANSFERS):
                t = self.h.getTransfer()
                t.setBulk(EP_IMG_IN, IMG_CHUNK, callback=cb, timeout=0)
                t.submit()
                transfers.append(t)
                inflight[0] += 1
        except usb1.USBError as e:
            say(f"  EP6 submit failed: {e}")
            self.reading = False
            return
        while self.reading and inflight[0] > 0:
            try:
                self.ctx.handleEventsTimeout(0.1)
            except usb1.USBErrorInterrupted:
                pass
            except usb1.USBError as e:
                say(f"  EP6 event loop: {e}")
                break
        # Retire the outstanding transfers before returning, so none of them can
        # complete into the next scan's buffer.
        for t in transfers:
            try:
                t.cancel()
            except usb1.USBError:
                pass
        end = time.time() + 2.0
        while inflight[0] > 0 and time.time() < end:
            try:
                self.ctx.handleEventsTimeout(0.05)
            except usb1.USBError:
                break
        say(f"  EP6 reader finished ({self.imgtotal} bytes)")

    def idle_watchdog(self):
        """If the OEM stops asking for image data, the scan is over or it gave
        up -- stop draining and shut the scanner down.  Without this an aborted
        scan leaves the lamp burning and the transport running, and LED wear is
        this unit's known failure mode."""
        while self.reading:
            time.sleep(1.0)
            now = time.time()
            quiet = now - self.lastread
            if (self.imgtotal > 1 << 20 and self.lastread
                    # either the OEM has gone away entirely, or it is alive but
                    # has stopped consuming images -- the second case still
                    # leaves the lamp burning, so it needs its own, longer cap.
                    and (quiet > IMG_IDLE_STOP
                         or (quiet > IDLE_STOP and now - self.lastcmd > IDLE_STOP))):
                say(f"  no image read for {IDLE_STOP}s -- stopping the scan "
                    f"(lamp off, motor idle)")
                # This has to stop the reader too.  It used to only clear the
                # buffer and carry on draining, so the backlog rebuilt itself
                # immediately and the next scan was served stale data again.
                self.reading = False
                self.lamp_off()
                self.safe_stop()
                self.imgtotal = 0
                self.lastread = 0.0
                with self.imglock:
                    self.imgbuf.clear()
                return

    def stop_stream(self):
        """Stop draining and wait for the reader to retire its transfers, so a
        later scan cannot be handed bytes captured before it."""
        t = self.imgthread
        self.reading = False
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        self.imgthread = None
        self.served = False
        with self.imglock:
            self.imgbuf.clear()

    def start_stream(self):
        if self.reading:
            return
        self.reading = True
        self.imgthread = threading.Thread(target=self._reader, daemon=True)
        self.imgthread.start()
        threading.Thread(target=self.idle_watchdog, daemon=True).start()
        say("  EP6 stream reader started")

    def begin_read(self, now):
        """Start-of-read bookkeeping.  It used to drop the buffer when a read
        arrived more than BURST_GAP after the previous one -- but with the ring
        a gap simply means the consumer was behind and we stopped asking, so
        flushing there tore a hole in the stream.  The only legitimate reset
        point is arm_stream(), before a transfer starts."""
        self.lastread = now
        self.start_stream()
        return 0

    def image(self, n):
        self.begin_read(time.time())
        deadline = time.time() + 4.0
        while time.time() < deadline:
            with self.imglock:
                if len(self.imgbuf) >= n:
                    out = bytes(self.imgbuf[:n])
                    del self.imgbuf[:n]
                    return out
            time.sleep(0.0005)
        with self.imglock:                 # short read beats no read
            out = bytes(self.imgbuf[:n])
            del self.imgbuf[:n]
        return out

    # ---------------- dispatch ----------------
    def __call__(self, code, data, outsz):
        # After a failed reopen() the handle is None until the scanner is back
        # on the bus.  Requests must not reach bulkWrite/controlRead with it:
        # that raised AttributeError, which the USBError handler below does not
        # catch, so every request after a lost device became a server traceback
        # and a dropped client.  Try once to reopen (it may have returned), and
        # otherwise answer with an error the client can act on.
        if self.h is None:
            if not self.reopen():
                say(f"  no scanner: request 0x{code:08x} refused "
                    "(power it on and plug it in; retrying on the next request)")
                return None
        try:
            if code == READ_EP6:
                return self.image(outsz)
            if code == IOCTL_CMD:
                return self.cmd(data, outsz)
            if code == IOCTL_VENDOR:
                return self.vendor(data, outsz)
        except usb1.USBError as e:
            say(f"  USB error on 0x{code:08x}: {e}")
            # LIBUSB_ERROR_NO_DEVICE means the scanner left the bus and came
            # back -- a re-enumeration, which happens if it resets or drops its
            # firmware.  Our handle is dead from that moment, and without this
            # every later request failed identically for the rest of the
            # session.  Re-open once and retry: open() reloads firmware if the
            # device came back as the unloaded PID.
            if getattr(e, "value", None) == usb1.ERROR_NO_DEVICE and self.reopen():
                try:
                    if code == IOCTL_CMD:
                        return self.cmd(data, outsz)
                    if code == IOCTL_VENDOR:
                        return self.vendor(data, outsz)
                except usb1.USBError as e2:
                    say(f"  still failing after reconnect: {e2}")
            return None
        say(f"  unsupported IOCTL 0x{code:08x} (in={len(data)} out={outsz})")
        return None

    def lamp_off(self):
        try:
            # The lamp is PICL reg0x80 (bit0 visible, bit1 IR), NOT a HOST
            # register -- an earlier version wrote 0x10 here, which did nothing.
            _picm, picl = ppb.board()
            self.cmd(bytes([2, 4, picl, 1, 0x80, 0x00]), RESP_MAX)
            say("  lamp off")
        except Exception as e:
            say(f"  lamp off failed: {type(e).__name__}")

    def safe_stop(self):
        """rate=0 -> go -> idle on both controllers.  Order matters: a bare
        stop leaves the drive turning."""
        if not self.h:
            return
        for addr in ppb.board():          # (PICM, PICL), whatever they are here
            for pkt in (bytes([2, 5, addr, 2, REG_RATE, 0, 0]),
                        bytes([2, 3, addr, 1, GO_FWD]),
                        bytes([2, 3, addr, 1, IDLE])):
                try:
                    self.cmd(pkt, RESP_MAX)
                except Exception:
                    pass
        say("motor stop sequence issued (rate=0 -> go -> idle)")


def _recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        c = conn.recv(n - len(buf))
        if not c:
            raise ConnectionError("short read")
        buf += c
    return buf


def handle(conn, dev):
    while True:
        code, outsz, inlen, odlen = struct.unpack("<4I", _recv_exact(conn, 16))
        data = _recv_exact(conn, inlen) if inlen else b""
        if odlen:
            _recv_exact(conn, odlen)
        dev.n += 1

        if code == READ_EP6:
            # Fill the WHOLE request.  Disassembling TLB.dll settles this:
            # EC_DRV_RingTailOverflow (1002) is raised at 0x100301d7 only when
            # the count it derives after copying is ZERO ("test edi,edi / jne
            # normal-path"), so the name misleads -- it means "no new lines",
            # not "too much data".  A short read is a partial scan line, which
            # rounds down to zero complete lines.
            t0 = time.time()
            stale = dev.begin_read(t0)
            if stale:
                say(f"  EP6 new scan: dropped {stale} stale bytes")
            if dev.align:
                dev.align = False
                skipped = dev.align_to_line()
                say(f"  EP6 aligned to line 0 (dropped {skipped} bytes)"
                    if skipped >= 0 else "  EP6 NO line marker found -- framing "
                    "will be wrong, capture kept for analysis")
            # Hand back whole lines once enough have arrived, rather than
            # holding the pipe until the entire ring is full.  outsz is the
            # WHOLE ring; completing it in one go leaves the driver a full ring
            # ahead of the consumer, which is what EC_DRV_RingTailOverflow
            # reports ("HighWaterDriverRingTail vs HighWaterProcessedRingTail").
            # Serve exactly what was asked for.  Do NOT round to whole lines:
            # the shim is filling a circular ring and asks for the bytes left
            # before the wrap, which is not a line multiple.  Trimming made the
            # tail drift out of step until `room` fell below one line, and then
            # whole-line rounding returned 0 for ever -- 47k spinning requests
            # and a 240 MB backlog.  Line 0 is already at the ring start thanks
            # to align_to_line; after that the ring is just contiguous bytes.
            # Complete as soon as a batch of whole lines is ready, up to the
            # size asked for.  TLB.dll posts a read, waits on the OVERLAPPED
            # event, takes the count and immediately posts the next one
            # (its loop at 0x10029db9), so sitting on the pipe until the whole
            # 8 MiB ring is full just stalls that loop.
            # Return EXACTLY what was asked for.  The shim is filling a ring of
            # fixed-size packets (hdr[0x24] = 20480) and asks for a whole
            # number of them; giving it fewer -- which whole-LINE rounding did,
            # 163840 asked vs 160000 sent -- leaves a fraction of a packet
            # unwritten every cycle and walks the ring out of step.  Line
            # framing is not our job here: align_to_line() puts line 0 at the
            # start and TLB frames the rest from the sync markers.
            want = outsz
            deadline = t0 + EP6_DEADLINE
            while time.time() < deadline:
                with dev.imglock:
                    if len(dev.imgbuf) >= want:
                        break
                time.sleep(0.001)
            with dev.imglock:
                take = want if len(dev.imgbuf) >= want else 0
                out = bytes(dev.imgbuf[:take])
                del dev.imgbuf[:take]
            conn.sendall(struct.pack("<2I", 1, len(out)))
            if out:
                conn.sendall(out)
            sent = len(out)
            if sent:
                dev.served = True
            dev.lastread = time.time()
            if dev.dump is not None:
                dev.dump.extend(out)
            say(f"  {dev.n:6d} EP6 asked={outsz} sent={sent} "
                f"streamed={dev.imgtotal} buffered={len(dev.imgbuf)} "
                f"{time.time()-t0:.1f}s")
            if dev.dump is not None and sent:
                # Exactly the bytes the OEM was handed.  Its marker scan is what
                # crashed, so this is the evidence that says whether the line
                # markers are there at all -- guessing the layout is not allowed.
                with open(DUMP_PATH, "wb") as f:
                    f.write(dev.dump)
                say(f"  wrote {len(dev.dump)} bytes to {DUMP_PATH}")
                dev.dump = None
            continue

        out = dev(code, data, outsz)
        if out is None:
            conn.sendall(struct.pack("<2I", 0, 0))
        else:
            out = out[:outsz]
            conn.sendall(struct.pack("<2I", 1, len(out)) + out)
        # Log every command.  The setup the OEM performs just before its first
        # image read is the one thing still not understood, and at ~500
        # commands a scan the volume is irrelevant.
        # Log the RESPONSE too -- the preflight loop is gated on status bits
        # that were invisible while this only printed the length.  Polls repeat
        # at 20 Hz, so those are logged only when the status actually changes.
        quiet = False
        if data[:1] == b"\x03":            # polls repeat at 20 Hz: only on change
            k = data[:3].hex()
            quiet = dev.lastpoll.get(k) == out
            dev.lastpoll[k] = out
            if quiet:
                # Keep it, do not just drop it.  When the client gives up, the
                # last thing it saw was almost always a poll response -- and
                # suppressing repeats meant exactly that datum was missing from
                # the log at exactly the moment it mattered.
                dev.lastquiet = (dev.n, data, out)
        if not quiet:
            if code == IOCTL_CMD:
                txt = ppb.decode(data, out)
            elif code == IOCTL_VENDOR:
                txt = ppb.decode_vendor(data, out)
            else:
                txt = f"0x{code:08x} {data[:14].hex()}"
            say(f"  {dev.n:6d} {txt}" + ("  FAIL" if out is None else ""))


def main():
    dev = Scanner().open()
    say(f"scanner open ({VID:04x}:{PID_LOADED:04x})")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(8)
    say(f"serving \\\\.\\Pakon135 on 127.0.0.1:{PORT} -- start the TLX client now")
    try:
        while True:
            conn, _ = srv.accept()
            # One thread per connection; the device lock serialises USB access,
            # because the OEM opens the handle from several threads.
            threading.Thread(target=_serve_one, args=(conn, dev), daemon=True).start()
    except KeyboardInterrupt:
        say("\nstopping")
    finally:
        dev.reading = False
        dev.lamp_off()
        dev.safe_stop()
        if dev.clamped:
            say(f"LED clamps applied: {dev.clamped}")
        if dev.blocked:
            say(f"EEPROM writes BLOCKED: {dev.blocked}")
        dev.close()
        say("device closed")
    return 0


def _say_lastpoll(dev):
    """The last poll we suppressed from the log.  When the OEM abandons a
    session it has usually just read a status it did not like, and that read is
    a repeat -- so it was the one line the log was throwing away."""
    lq = getattr(dev, "lastquiet", None)
    if not lq:
        return
    n, data, out = lq
    say(f"  last suppressed poll was #{n}: {ppb.decode(data, out)}")
    say(f"    request  {data.hex()}")
    say(f"    response {out.hex() if out else '(none)'}")


def _serve_one(conn, dev):
    say("client connected")
    try:
        handle(conn, dev)
    except (ConnectionError, OSError, struct.error) as e:
        _say_lastpoll(dev)
        # Never swallow this.  A silent "client disconnected" hides the one fact
        # that matters: whether WE dropped the link or the client did.  If the
        # shim closed first, the failure is upstream of us and the packet TLB
        # names in its error dialog was never even sent.
        say(f"  link lost: {type(e).__name__}: {e}")
    except Exception:
        import traceback
        _say_lastpoll(dev)
        say("  SERVER BUG -- unhandled exception while serving a request:")
        for line in traceback.format_exc().splitlines():
            say("    " + line)
    finally:
        conn.close()
        say(f"client disconnected ({dev.n} requests total)")


def selftest():
    """The two things that must never regress: the read-only EEPROM policy and
    the LED ceilings (both protect irreplaceable hardware), plus serving
    current image data instead of a backlog.  Runs without a scanner."""
    A = Scanner.allowed
    assert not A(EE_WRITE, 0xA0, EE_INDEX, 0)      # 0xA2 EEPROM WRITE: blocked
    assert not A(EE_WRITE, 0xA1, EE_INDEX, 1)      # ...even claiming to be a read
    assert A(EE_SETUP, 0xA5, EE_INDEX, 0)          # read setup: allowed
    assert not A(EE_SETUP, 0xA4, EE_INDEX, 0)      # write setup: blocked
    assert A(EE_READ, 0, EE_INDEX, 1)              # read, IN: allowed
    assert not A(EE_READ, 0, EE_INDEX, 0)          # read opcode going OUT
    assert not A(0xA2, 0, EE_INDEX, 0)             # unknown EEPROM opcode
    assert A(0xA0, 0, 0, 0)                        # ordinary vendor traffic

    d = Scanner.__new__(Scanner)                   # no USB context needed
    d.clamped = []
    picl, picm = ppb.board()[1], ppb.board()[0]

    # Board/IR state unknown -> the STRICTEST row, not the loosest.  Getting
    # this backwards let G and B reach 20 on a board the firmware caps at 8.
    d.ir_on = None

    # 0xA2 is the EEPROM WRITE opcode and must die at ANY wIndex.  A guard that
    # only inspects wIndex 0x1234 is bypassed by wValue=wIndex=0.
    assert not A(EE_WRITE, 0, 0, 0)
    assert not A(EE_WRITE, 0xA4, 0x0000, 0)

    # Writes to a PIC bootloader address are REPORTED, never blocked -- see
    # watch_bootloader.  It must never claim to have stopped anything.
    for ba in BOOTLOADER_ADDRS:
        assert not d.watch_bootloader(bytearray([4, 3, ba, 0, 0x0C]))
    assert not d.watch_bootloader(bytearray([1, 3, ba, 0, 0x02]))

    # ppb must never latch the operational pair onto a bootloader address, or
    # lamp-off and motor-stop end up addressing nothing.
    before = ppb.board()
    ppb.note_address(0x46)
    assert ppb.board() == before, (ppb.board(), before)
    ppb.note_address(0x26)
    assert ppb.board() == before


    if picm == 0x24:                               # this scanner's board
        # A lamp write with IR lit selects the IR-on row: R8 G8 B8 Ir8.
        d.clamp_leds(bytearray([2, 3, picl, 1, LAMP_REG, 0x03]))
        assert d.ir_on is True
        p = d.clamp_leds(bytearray([2, 6, picl, 5, 0x81, 99, 99, 99, 0, 99]))
        assert list(p[5:10]) == [8, 8, 8, 0, 8], list(p[5:10])
        # Visible only -> IR must be driven to 0, and R drops to 6.
        d.clamp_leds(bytearray([2, 3, picl, 1, LAMP_REG, 0x01]))
        assert d.ir_on is False
        p = d.clamp_leds(bytearray([2, 6, picl, 5, 0x81, 99, 99, 99, 0, 99]))
        assert list(p[5:10]) == [8, 0, 6, 0, 8], list(p[5:10])
        # Never *raise* a value that is already under the ceiling.
        p = d.clamp_leds(bytearray([2, 6, picl, 5, 0x81, 3, 0, 2, 0, 2]))
        assert list(p[5:10]) == [3, 0, 2, 0, 2], list(p[5:10])
    d.ir_on = None

    # 0xA2 is the EEPROM WRITE opcode and must die at ANY wIndex.  A guard that
    # only inspects wIndex 0x1234 is bypassed by wValue=wIndex=0.
    assert not A(EE_WRITE, 0, 0, 0)
    assert not A(EE_WRITE, 0xA4, 0x0000, 0)

    # Writes to a PIC bootloader address are REPORTED, never blocked -- see
    # watch_bootloader.  It must never claim to have stopped anything.
    for ba in BOOTLOADER_ADDRS:
        assert not d.watch_bootloader(bytearray([4, 3, ba, 0, 0x0C]))
    assert not d.watch_bootloader(bytearray([1, 3, ba, 0, 0x02]))

    # ppb must never latch the operational pair onto a bootloader address, or
    # lamp-off and motor-stop end up addressing nothing.
    before = ppb.board()
    ppb.note_address(0x46)
    assert ppb.board() == before, (ppb.board(), before)
    ppb.note_address(0x26)
    assert ppb.board() == before

    # begin_read must NEVER discard buffered data: a gap between reads only
    # means the consumer was behind, and dropping there caused EC_DRV_LostSync.
    d.imgbuf = bytearray(b"live" * 1000)
    d.imglock = threading.Lock()
    d.reading = True                               # so start_stream is a no-op
    d.lastread = 100.0
    assert d.begin_read(100.0 + BURST_GAP + 5.0) == 0          # big gap...
    assert len(d.imgbuf) == 4000, "begin_read must not drop the stream"

    # and a trigger during a live transfer must leave the stream alone
    d.served = True
    d.imgtotal = 12345
    d.align = False
    d.arm_stream()
    assert len(d.imgbuf) == 4000, "arm during a transfer must not clear"
    assert d.imgtotal == 12345, "arm during a transfer must not reset counters"
    assert not d.align, "arm during a transfer must not force re-alignment"

    # Line alignment must survive LSB noise.  A real capture had a stray set bit
    # two bytes in, and aligning to that first set bit put the stream 2094
    # samples out; the dominant phase is what actually identifies line 0.
    period, phase = 6108, 137
    head = bytearray(2 * period * 12)
    for i in range(0, len(head), 2):
        head[i] = 0x02                             # even sample, no marker
    for k in range(12):
        head[2 * (phase + k * period)] |= 1        # the real line markers
    head[2 * 5] |= 1                               # noise before line 0 ...
    head[2 * 900] |= 1                             # ... and more noise
    assert line_sync(bytes(head)) == (phase, period), line_sync(bytes(head))
    assert line_sync(b"\x02" * 4096) == (None, None)   # no markers at all

    d.imgbuf = bytearray(head) * 2
    assert d.align_to_line(timeout=0.2) == phase * 2
    assert d.imgbuf[0] & 1                         # now starts on a real marker
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main() or 0)
