#!/usr/bin/env python3
"""Decode Pakon PPB packets into readable text, for live tracing.

Packet layout on the wire (confirmed against captures and TLB.dll call sites):

    [type][len][addr][count][reg][payload...]

    type 1 = READ, 2 = WRITE, 3 = POLL, 4 = WRITE2 ([4][3][addr][0][reg])
    addr 0x10 = HOST, 0x20 = PICL, 0x24 = PICM (a.k.a. SUB44)

Register meanings are the ones that have actually been observed on the wire or
are documented in PakonKit/docs/scan-start.md.  Anything not in the table is
printed as a bare register number rather than invented.
"""

# TLB PROBES for its controller pair at start-up (0x1000afd0): it pings 0x44,
# then 0x46, then 0x24, then 0x26, and uses PICL = PICM - 4.  So the addresses
# are board-dependent, not fixed -- an F-135 answers on 0x24/0x20, another board
# variant on 0x44/0x40.  We learn which from the traffic instead of assuming.
PICM_CANDIDATES = (0x24, 0x26, 0x44, 0x46)      # what TLB pings while probing
# ...but only two of those are the running APPLICATION.  0x26/0x46 (and their
# PICL partners 0x22/0x42) are the PICs' BOOTLOADER addresses, which is also why
# they must never be written to -- see BOOTLOADER_ADDRS in pakonusb.py.  Latching
# the operational pair onto a bootloader address would silently point lamp-off
# and motor-stop at something that answers nothing on a running unit.
PICM_APPLICATION = (0x24, 0x44)
_picm = 0x24            # most common on the F-135; corrected by note_address()


def note_address(addr):
    """Called with every address seen on the wire so the decoder tracks whatever
    controller pair this scanner actually probed to.  Only application addresses
    move the operational pair; bootloader addresses are decoded but not latched."""
    global _picm
    if addr in PICM_APPLICATION and addr != _picm:
        _picm = addr
    return _picm


def board():
    """(PICM, PICL) for this scanner."""
    return _picm, _picm - 4


def role(addr):
    """Logical name for a PPB address, independent of the board variant."""
    picm, picl = board()
    if addr == 0x10:
        return "HOST"
    if addr == picm:
        return "PICM"
    if addr == picl:
        return "PICL"
    if addr == 0xFE:
        return "BCAST"
    return "0x%02x" % addr

# (role, reg) -> name.  Keyed on the LOGICAL controller so it works whatever
# addresses this board probed to.  Only entries we have evidence for.
REG = {
    ("PICL", 0x02): "status/wants-service",
    ("PICL", 0x06): "service-ack",
    ("PICL", 0x80): "LAMP",
    ("PICL", 0x81): "LED CURRENT",
    ("PICL", 0x82): "exposure",
    ("PICL", 0x83): "telemetry83",
    ("PICL", 0x84): "telemetry84",
    ("PICL", 0x88): "telemetry88",
    ("PICL", 0x89): "readout",
    ("PICL", 0x8a): "ARM pulse pt2",
    ("PICL", 0x8b): "ccd-8b",
    ("PICL", 0x8c): "ccd-8c",
    ("PICL", 0x8d): "ccd-8d",
    ("PICL", 0x8f): "ccd-8f",
    ("PICL", 0x90): "DX code",
    ("PICL", 0x91): "TRIGGER (line ctr reset, EP6 starts)",
    ("PICL", 0x92): "scan STOP",
    ("PICM", 0x02): "PICM status",
    ("PICM", 0x82): "geometry/exposure idx",
    ("PICM", 0x84): "gain/offset idx",
    ("PICM", 0xa0): "MOTOR GO fwd",
    ("PICM", 0xa1): "MOTOR reverse",
    ("PICM", 0xa2): "MOTOR idle/stop",
    ("PICM", 0xa5): "MOTOR RATE",
    ("HOST", 0x84): "ARM pulse pt1",
    ("HOST", 0x85): "host-85",
    ("HOST", 0x8f): "host-8f",
}

# PICM reg0x82 indexed writes -- indices from PakonKit/docs/scan-start.md
IDX82 = {0: "idx0", 4: "offset", 5: "offset+width", 6: "integration", 9: "mux",
         10: "idx10", 11: "idx11"}
IDX84 = {2: "A/D gain R", 3: "A/D gain G", 4: "A/D gain B",
         5: "offset trim R", 6: "offset trim G", 7: "offset trim B"}


def _u16(b, i):
    return b[i] | (b[i + 1] << 8) if i + 1 < len(b) else None


def decode(pkt, resp=None):
    """One line describing a PPB packet and, if given, its response."""
    if not pkt:
        return "(empty)"
    t = pkt[0]
    if t == 3 and len(pkt) >= 3:                      # POLL
        note_address(pkt[2])
        s = f"POLL {role(pkt[2])}"
        if resp:
            s += f"  -> {resp.hex()}"
        return s
    if len(pkt) < 5:
        return f"type{t} {pkt.hex()}"
    addr, count, reg = pkt[2], pkt[3], pkt[4]
    note_address(addr)
    an = role(addr)
    rn = REG.get((an, reg), f"reg0x{reg:02x}")
    body = bytes(pkt[5:])

    if t == 4:                                        # WRITE2: [4][3][addr][0][reg]
        return f"WRITE2 {an} {REG.get((an, count if count else reg), rn)}"
    if t == 1:                                        # READ  [1][len][addr][count][reg]
        s = f"READ  {an} {rn} x{count}"
        if resp:
            s += f"  -> {resp.hex()}"
        return s
    if t == 2:                                        # WRITE
        extra = ""
        if (an, reg) == ("PICL", 0x81) and len(body) >= 5:
            extra = (f"  B={body[0]} IR={body[1]} R={body[2]} G={body[4]}")
        elif (an, reg) == ("PICL", 0x80) and body:
            bits = []
            if body[0] & 1:
                bits.append("visible")
            if body[0] & 2:
                bits.append("IR")
            extra = "  " + ("+".join(bits) if bits else "OFF")
        elif (an, reg) == ("PICM", 0x82) and len(body) >= 3:
            extra = f"  {IDX82.get(body[0], 'idx%d' % body[0])}=0x{_u16(body,1):04x}"
        elif (an, reg) == ("PICM", 0x84) and len(body) >= 3:
            extra = f"  {IDX84.get(body[0], 'idx%d' % body[0])}=0x{_u16(body,1):04x}"
        elif (an, reg) == ("PICM", 0xa5) and len(body) >= 2:
            extra = f"  rate=0x{_u16(body,0):04x}"
        elif (addr, reg) == ("PICL", 0x91):
            extra = f"  {body.hex()}"
        elif body:
            extra = f"  {body.hex()}"
        return f"WRITE {an} {rn}{extra}"
    return f"type{t} {pkt.hex()}"


def decode_vendor(data, resp=None):
    """The 10-byte EP0 control struct used by IOCTL 0x222059."""
    if len(data) < 10:
        return f"CTRL short {data.hex()}"
    direction, rtype, recip = data[0], data[1], data[2]
    req = data[4]
    val = data[6] | (data[7] << 8)
    idx = data[8] | (data[9] << 8)
    what = ""
    if idx == 0x1234:
        what = {0xA4: "EEPROM read-select", 0xA9: f"EEPROM read @0x{val:04x}"}.get(
            req, f"EEPROM req0x{req:02x}")
    else:
        what = f"vendor req0x{req:02x} val0x{val:04x} idx0x{idx:04x}"
    s = f"CTRL {'IN ' if direction else 'OUT'} {what}"
    if resp:
        s += f"  -> {len(resp)}B {resp[:16].hex()}"
    return s


def selftest():
    assert board() == (0x24, 0x20)
    assert role(0x24) == "PICM" and role(0x20) == "PICL"
    note_address(0x44)                       # a different board variant
    assert board() == (0x44, 0x40), board()
    assert role(0x44) == "PICM" and role(0x40) == "PICL"
    note_address(0x24)                       # back to the F-135 pair
    assert board() == (0x24, 0x20)
    assert "LED CURRENT" in decode(bytes([2, 8, 0x20, 5, 0x81, 1, 2, 3, 0, 4]))
    assert "B=1 IR=2 R=3 G=4" in decode(bytes([2, 8, 0x20, 5, 0x81, 1, 2, 3, 0, 4]))
    assert "visible+IR" in decode(bytes([2, 4, 0x20, 1, 0x80, 0x03]))
    assert "OFF" in decode(bytes([2, 4, 0x20, 1, 0x80, 0x00]))
    assert "rate=0x0613" in decode(bytes([2, 5, 0x24, 2, 0xA5, 0x13, 0x06]))
    assert "integration=0x0ffd" in decode(bytes([2, 6, 0x24, 3, 0x82, 6, 0xFD, 0x0F]))
    assert "TRIGGER" in decode(bytes([2, 6, 0x20, 3, 0x91, 0x10, 0x00, 0x01]))
    assert decode(bytes([3, 1, 0x10])).startswith("POLL HOST")
    assert "MOTOR" in decode(bytes([4, 3, 0x24, 0, 0xA0]))
    assert "EEPROM read @0x0008" in decode_vendor(
        bytes([1, 2, 0, 0, 0xA9, 0, 0x08, 0x00, 0x34, 0x12]))
    print("ppb selftest OK")


if __name__ == "__main__":
    selftest()
