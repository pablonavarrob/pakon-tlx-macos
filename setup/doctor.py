#!/usr/bin/env python3
"""Check (and optionally assemble) the OEM files this project needs.

This project does NOT contain any Kodak/Pakon code. It drives the genuine
F-X35 COM Server stack, which you must supply yourself from your own scanner's
software. `doctor` tells you exactly which files are needed, whether yours match
the versions this was developed against, and what each one is for.

    doctor.py                          check the default install path
    doctor.py --install <dir>          check a specific directory
    doctor.py --from <dir|url> [...]   copy/fetch missing files from a source
                                       YOU are entitled to use, then verify

There is deliberately no built-in download URL. If you point --from at a
location, you are asserting you have the right to those files.

The recorded hashes are from one working install; they are a courtesy, not a
requirement. A different version is reported but does not fail -- there is no
canonical build of this software.

Exit status: 0 = every required file present, 1 = something missing,
2 = usage error.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
DEFAULT_INSTALL = os.path.expanduser(
    "~/.wine/drive_c/Program Files/Pakon/F-X35 COM Server")

# Firmware is handled separately and never copied anywhere: the scanner's
# application firmware lives in RAM and is uploaded from YOUR file at runtime.
FIRMWARE_NOTE = """
Firmware: the scanner loses its application firmware at every power cycle, so it
is uploaded at start-up from a file on your disk. This project never bundles or
copies it. Put your own pakon7.hex (or pakon5/pakon8) in
    ~/.local/share/psix/firmware
or point $PSIX_FIRMWARE_DIR somewhere else.
"""


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def fetch(src, name, dest):
    """src is a directory or a base URL; returns True if it landed a file."""
    target = os.path.join(dest, name)
    if src.startswith(("http://", "https://")):
        url = src.rstrip("/") + "/" + name
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(target, "wb") as o:
                shutil.copyfileobj(r, o)
            return True
        except Exception as e:
            print("      fetch failed: %s" % e)
            return False
    cand = os.path.join(os.path.expanduser(src), name)
    if os.path.isfile(cand):
        shutil.copy2(cand, target)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", default=os.environ.get("PAKON_INSTALL", DEFAULT_INSTALL))
    ap.add_argument("--from", dest="source", default=None,
                    help="directory or base URL to copy/fetch missing files from")
    ap.add_argument("--quiet", action="store_true",
                    help="omit the firmware note (bootstrap.sh prints its own)")
    args = ap.parse_args()

    if not os.path.isfile(MANIFEST):
        print("missing %s" % MANIFEST, file=sys.stderr)
        return 2
    man = json.load(open(MANIFEST))

    inst = os.path.expanduser(args.install)
    print("install dir: %s" % inst)
    if not os.path.isdir(inst):
        if args.source:
            os.makedirs(inst, exist_ok=True)
        else:
            print("  ...does not exist. Create it and put the OEM files there, "
                  "or pass --from <dir|url>.")
            return 1
    print()

    ok = missing = differs = 0
    width = max(len(n) for n in man)
    for name in sorted(man):
        info = man[name]
        path = os.path.join(inst, name)
        if not os.path.isfile(path) and args.source:
            print("  %-*s fetching..." % (width, name))
            fetch(args.source, name, inst)
        if not os.path.isfile(path):
            print("  %-*s  MISSING      %s" % (width, name, info["purpose"]))
            missing += 1
            continue
        got = sha256(path)
        # setup.sh renames one import string in TLB.dll/tlx.dll, so a correctly
        # wired install ALWAYS mismatches the recorded (unpatched) hash. Saying
        # DIFFERENT there trains people to ignore this column.
        if (info["sha256"] and got != info["sha256"]
                and os.path.getsize(path) == info.get("bytes", -1)
                and b"pkusb.dll" in open(path, "rb").read()):
            print("  %-*s  ok           %s [patched by setup.sh]"
                  % (width, name, info["purpose"].split(" [hash")[0]))
            ok += 1
            continue
        if info["sha256"] and got != info["sha256"]:
            print("  %-*s  DIFFERENT    %s" % (width, name, info["purpose"]))
            print("  %-*s               yours %s" % (width, "", got[:16]))
            print("  %-*s               known %s" % (width, "", info["sha256"][:16]))
            differs += 1
        else:
            print("  %-*s  ok           %s" % (width, name, info["purpose"]))
            ok += 1

    print()
    print("  %d ok, %d missing, %d different version" % (ok, missing, differs))
    if differs:
        print("\n  A different version is not necessarily broken -- it just is not the\n"
              "  build these hashes came from. Everything this project needs is\n"
              "  discovered at runtime -- the ring layout from the header, the\n"
              "  controller addresses from the traffic, the error reporter by\n"
              "  call-count -- so a different version should still work.\n"
              "  Please do report back if it does or does not.")
    if missing:
        print("\n  These come from your own Pakon/Kodak software installation.\n"
              "  This project cannot and will not supply them.")
    if not args.quiet:
        print(FIRMWARE_NOTE)
    # Only MISSING files are a failure.  A different version is expected -- these
    # hashes are from one particular install and there is no canonical build.
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
