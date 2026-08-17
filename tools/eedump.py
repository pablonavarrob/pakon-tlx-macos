#!/usr/bin/env python3
"""Dump the scanner's EEPROM, READ ONLY, and look for the light calibration.

Why this exists: a normal scan does not derive the LED currents and duty
cycles.  The error chain the OEM prints is

    FuncScanPictures -> bBeforeScan -> bCalibrateFindCorrections

with no FindLedCurrent / FindLedDutyCycle in it -- those belong to the full
Calibration Wizard.  So a scan LOADS the light settings, and TLB.dll loads them
from the EEPROM (its CalibrateEEProm interface).  On this machine they come out
as current=1 and duty=0, which is why the lamp is lit but produces nothing and
calibration parks in "Corrections" for ever.

This reads exactly the region TLB.dll reads at init, decoded from a live
capture of its own control transfers:

    0x0000   8 B    header
    0x0008 - 0x018E 390 B   main calibration block  <- currents/duty live here
    0x0800   8 B  + 0x0808  28 B

THE EEPROM IS NEVER WRITTEN.  Only the two known read opcodes are issued:
0xA4 with wValue 0xA5 (read-select) and 0xA9 IN.  It holds irreplaceable
per-unit calibration.

    ./.venv/bin/python eedump.py
"""
import os
import struct
import sys

import usb1

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "server"))
import pakonload                                          # noqa: E402

VID, PID_LOADED, PID_UNLOADED = 0x0F05, 0xF135, 0xF235
EE_INDEX, EE_SETUP, EE_READ = 0x1234, 0xA4, 0xA9
READ_SELECT = 0x00A5
# (offset, length) exactly as TLB.dll asks for them
REGIONS = ([(0x0000, 8)] + [(0x0008 + 32 * i, 32) for i in range(13)]
           + [(0x0188, 6), (0x0800, 8), (0x0808, 28)])
OUT = "/tmp/eeprom.bin"


def read_eeprom(h):
    blocks = {}
    for off, n in REGIONS:
        h.controlWrite(0x40, EE_SETUP, READ_SELECT, EE_INDEX, b"", timeout=2000)
        blocks[off] = bytes(h.controlRead(0xC0, EE_READ, off, EE_INDEX, n, timeout=2000))
    return blocks


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
    analyse(main_block, 0x0000)


if __name__ == "__main__":
    main()
