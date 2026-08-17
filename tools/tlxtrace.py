#!/usr/bin/env python3
"""Live trace of a TLX session: USB traffic, ring state and the OEM's own logs,
merged into one stream.

    ./.venv/bin/python tlxtrace.py            # follow live
    ./.venv/bin/python tlxtrace.py --replay   # re-read what is already there

Sources
  /tmp/pakonusb.log   our USB server: every PPB packet decoded, EP6 reads
  /tmp/tlxclient.log  Wine's +debugstr: pkusb ring tracing, and TLB.dll's own
                      internal messages once they are hooked
  <install>/Logs/*.txt the OEM's own error logs

Lines that matter are marked so they are findable in a wall of polling:
  >>>  scan-sequence milestones (trigger, lamp, LED current, motor, EP6)
  !!!  errors and stalls
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pknames

SRV = os.environ.get("PAKON_SRVLOG", "/tmp/pakonusb.log")
CLI = os.environ.get("PAKON_CLILOG", "/tmp/tlxclient.log")
OEM = os.path.join(os.environ.get("PAKON_INSTALL",
    os.path.expanduser("~/.wine/drive_c/Program Files/Pakon/F-X35 COM Server")), "Logs")

MILESTONE = re.compile(
    r"TRIGGER|LAMP|LED CURRENT|MOTOR|EP6|ring |armed|aligned|scan STOP|"
    r"DX code|wants-service|integration|offset trim|A/D gain", re.I)
BAD = re.compile(
    r"\[ERR\]|FAIL|error|overflow|timeout|BLOCKED|clamped|no image|not created|"
    r"cancel|abort|fault", re.I)
# Wine wraps debug output; pull out just the message
DBGSTR = re.compile(r'OutputDebugString[AW]?\s+"(.*)"\s*$')
# every internal TLB error, decoded by name via the hook at TLB.dll RVA 0x1acd0
TLBERR = re.compile(r"TLBERR cls=(-?\d+) fn=(-?\d+) ec=(-?\d+) extra=(\d+)")


def mark(line):
    if BAD.search(line):
        return "!!! " + line
    if MILESTONE.search(line):
        return ">>> " + line
    return "    " + line


def clean(src, line):
    line = line.rstrip("\n")
    if src == "cli":
        m = DBGSTR.search(line)
        if not m:
            return None                     # ignore Wine's own chatter
        line = m.group(1).replace("\\n", "").replace("\\r", "")
        if not line.strip():
            return None
        m = TLBERR.search(line)
        if m:
            c, f, e, x = (int(g) for g in m.groups())
            return "[ERR] " + pknames.decode(c, f, e, x)
        return "[tlb] " + line
    return "[usb] " + line


def follow(paths, replay):
    handles = {}
    for tag, p in paths:
        try:
            f = open(p, "r", errors="replace")
        except OSError:
            continue
        if not replay:
            f.seek(0, os.SEEK_END)
        handles[tag] = f
    if not handles:
        print("no logs yet -- start the client first", file=sys.stderr)
        return
    while True:
        idle = True
        for tag, f in handles.items():
            for line in f:
                out = clean(tag, line)
                if out:
                    print(mark(out), flush=True)
                idle = False
        if idle:
            time.sleep(0.2)


def main():
    replay = "--replay" in sys.argv
    try:
        follow([("usb", SRV), ("cli", CLI)], replay)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
