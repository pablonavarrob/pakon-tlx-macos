#!/usr/bin/env python3
"""Report Pakon scan-mode registry keys that are too thin to be TLB's own work.

TLB writes ~23 values into each `Scan\\DpiBase<N>_35\\<mode>` key -- Current_*,
DutyCycle_*, DutyCycleOpenGate_*, Gain_*, Offset_*, DetectFilm_G and friends --
and it does that the first time it finds the key ABSENT. So a key holding only
one or two values was created by something else (an earlier version of
setup.sh seeded FullLightCorrections into keys that did not exist), and its
presence stops TLB ever populating the rest. Initialisation then dies building
a device command from a value that is not there:

    EC_WIN_DeviceIoControl (165) Invalid function.  Type 4, PktLen 3, Address 10

Deleting the thin key lets TLB write the real thing on the next run.

    regscan.py <system.reg>            print thin keys, one reg path per line
    regscan.py <system.reg> --count    print how many mode keys exist in total

Paths are printed the way `wine reg ... /reg:32` wants them: the hive stores the
64-bit spelling with Wow6432Node in it, the 32-bit view addresses
HKLM\\Software\\... directly.
"""
import re
import sys

MIN_VALUES = 5          # a real TLB-written mode key has ~23; ours had 1
MODE_KEY = re.compile(r"Pakon\\+TLB\\+Scan\\+DpiBase\d+_\d+\\+\w+$")


def mode_keys(path):
    """{key: number of values} for every scan-mode key in the hive."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    found, cur = {}, None
    for line in text.splitlines():
        if line.startswith("["):
            cur = line[1:].split("]")[0]
            if MODE_KEY.search(cur):
                found[cur] = 0
            else:
                cur = None
        elif cur is not None and line.startswith('"'):
            found[cur] += 1
    return found


def reg_path(hive_key):
    """Hive spelling -> what `reg /reg:32` expects."""
    p = hive_key.replace("\\\\", "\\")
    p = re.sub(r"^Software\\Wow6432Node\\", "", p)
    p = re.sub(r"^Software\\", "", p)
    return "HKLM\\Software\\" + p


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    keys = mode_keys(sys.argv[1])
    if "--count" in sys.argv:
        print(len(keys))
        return 0
    for k, n in sorted(keys.items()):
        if n < MIN_VALUES:
            print(reg_path(k))
    return 0


def selftest():
    import tempfile, os
    hive = (
        'WINE REGISTRY Version 2\n'
        '\n'
        '[Software\\\\Wow6432Node\\\\Pakon\\\\TLB\\\\Scan\\\\DpiBase16_35\\\\ColNeg] 1\n'
        '"FullLightCorrections"=dword:00000001\n'
        '\n'
        '[Software\\\\Wow6432Node\\\\Pakon\\\\TLB\\\\Scan\\\\DpiBase16_35\\\\ColNegIr] 1\n'
        + "".join('"V%d"=dword:00000001\n' % i for i in range(23)) +
        '\n'
        '[Software\\\\Wow6432Node\\\\Pakon\\\\TLB\\\\Scan\\\\Test] 1\n'
        '"SenseFilm"=dword:00000001\n'
    )
    fd, p = tempfile.mkstemp(suffix=".reg")
    os.write(fd, hive.encode()); os.close(fd)
    try:
        keys = mode_keys(p)
        # Test is NOT a mode key: it has no DpiBase, so it must not be listed.
        assert len(keys) == 2, keys
        thin = [reg_path(k) for k, n in keys.items() if n < MIN_VALUES]
        assert thin == ["HKLM\\Software\\Pakon\\TLB\\Scan\\DpiBase16_35\\ColNeg"], thin
        # the well-populated one must be left alone
        assert not any("ColNegIr" in t for t in thin), thin
    finally:
        os.unlink(p)
    print("regscan selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
