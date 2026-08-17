#!/bin/bash
# Wire the shim into your own copy of the OEM stack.
#
# The only change made to Kodak code is renaming one import string inside
# TLB.dll and tlx.dll: "VERSION.dll" -> "pkusb.dll". That import is three
# version-info functions used for a single log line, which pkusb re-exports as
# stubs, and it gets our DllMain called early for free. No code is modified and
# no PE surgery is done. A backup is taken first.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
INSTALL="${PAKON_INSTALL:-$WINEPREFIX/drive_c/Program Files/Pakon/F-X35 COM Server}"

[ -d "$INSTALL" ] || { echo "install not found: $INSTALL"; echo "set PAKON_INSTALL"; exit 1; }
python3 "$HERE/doctor.py" --install "$INSTALL" --quiet || {
    echo; echo "doctor reported problems above -- fix those first."; exit 1; }

WINE="$("$HERE/bootstrap.sh" --wine || true)"

# Run a command but never let it wedge setup.  regsvr32 on these DLLs can block
# for ever (see below), and a hung installer is worse than a failed one.
run_capped() {                      # run_capped <seconds> <cmd...>
    local secs="$1"; shift
    "$@" >/dev/null 2>&1 & local pid=$! i=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$i" -ge "$secs" ]; then
            kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 1; i=$((i + 1))
    done
    wait "$pid"
}

# --- COM registration, BEFORE the import patch ------------------------------
# tlx.dll is a COM server; the client does CoCreateInstance on a CLSID that only
# exists once DllRegisterServer has run.  Skip this and a fresh install dies at
# start-up with "class {ea82986b-...} not registered" and no dialog.  All four of
# TLA/TLB/TLC/tlx register a CLSID in a working install, so all four are done.
#
# Order matters and cost us a hang: registering AFTER the patch makes TLB load
# pkusb.dll, which then waits for a USB server that is not running during setup.
# Registration records the DLL's PATH, not its contents, so patching afterwards
# is fine -- and this way the DLLs are still pristine when they self-register.
if [ -n "$WINE" ]; then
    # regsvr32 writes the keys and then, for some of these DLLs, never exits --
    # so it gets capped.  And Wine buffers the registry, flushing it when
    # wineserver shuts down, so the keys are not in system.reg until we force
    # that.  Both together mean the only honest test is: register, flush, look.
    WS="$(dirname "$WINE")/wineserver"
    flush_registry() {
        [ -x "$WS" ] && MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WS" -k >/dev/null 2>&1
        sleep 1
        return 0
    }
    registered() {                  # registered <dll>  -- is it in system.reg?
        python3 - "$WINEPREFIX/system.reg" "$1" <<'PY'
import re, sys
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    raise SystemExit(1)
want, cur = sys.argv[2].lower(), None
for line in text.splitlines():
    if line.startswith("["):
        cur = line
    elif cur and "InprocServer32" in cur:
        m = re.search(r"([A-Za-z0-9_.-]+\.dll)", line)
        if m and m.group(1).lower() == want:
            raise SystemExit(0)
raise SystemExit(1)
PY
    }

    todo="tlx.dll TLA.dll TLB.dll TLC.dll"
    for cap in 45 120; do           # second pass is slower and only for leftovers
        left=""
        for dll in $todo; do
            [ -f "$INSTALL/$dll" ] || continue
            run_capped "$cap" env MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all \
                       WINEPREFIX="$WINEPREFIX" sh -c \
                       "cd '$INSTALL' && exec '$WINE' regsvr32 '$dll'" || true
        done
        flush_registry
        for dll in $todo; do
            [ -f "$INSTALL/$dll" ] || continue
            registered "$dll" || left="$left $dll"
        done
        todo="$(echo $left)"
        [ -z "$todo" ] && break
        [ "$cap" = 45 ] && echo "   retrying:$todo"
    done

    if [ -z "$todo" ]; then
        echo "COM registered: tlx.dll TLA.dll TLB.dll TLC.dll"
    else
        echo "COM NOT registered:$todo"
        case "$todo" in
            *tlx.dll*) echo "   tlx.dll is the CLSID the client creates -- it will not start."
                       exit 1 ;;
            *)         echo "   tlx.dll is registered, so the client should start." ;;
        esac
    fi
else
    echo "wine not found -- skipping COM registration; re-run after installing it"
fi

[ -f "$ROOT/src/pkusb.dll" ] || { echo "build it first:  make -C src"; exit 1; }
cp -f "$ROOT/src/pkusb.dll" "$INSTALL/pkusb.dll"
echo "installed pkusb.dll"

for dll in TLB.dll tlx.dll; do
    f="$INSTALL/$dll"
    if [ ! -f "$f.orig" ]; then
        # A backup taken after patching is worthless -- never make one.
        if grep -qa "pkusb.dll" "$f"; then
            echo "   $dll is already patched and has no .orig -- refusing to make"
            echo "   a backup of a patched file. Restore it from your OEM media if"
            echo "   you ever need the original."
        else
            cp -p "$f" "$f.orig"; echo "backed up $dll -> $dll.orig"
        fi
    fi
    python3 - "$f" <<'PY'
import sys
p = sys.argv[1]
d = bytearray(open(p, "rb").read())
old, new = b"VERSION.dll\x00", b"pkusb.dll\x00\x00\x00"   # same length, NUL-padded
if d.find(new) >= 0:
    print("   %s already patched" % p.split("/")[-1]); raise SystemExit
i = d.find(old)
if i < 0:
    print("   %s: no VERSION.dll import found -- not patched" % p.split("/")[-1])
    raise SystemExit(1)
d[i:i+len(old)] = new
open(p, "wb").write(d)
print("   %s: import redirected to pkusb.dll" % p.split("/")[-1])
PY
done

mkdir -p "$INSTALL/Logs" "$WINEPREFIX/drive_c/Logs" "$WINEPREFIX/drive_c/Buffers"
# TLB opens its logs with _wfopen and never checks the result; a failed open
# faults the process. Pre-create them, in both locations the paths can resolve to.
for f in PakonErrorLogMain.txt PakonErrorLogScan.txt PakonErrorLogSave.txt \
         PakonErrorLogPI.txt PakonDxLog.txt DxCode.txt; do
    for d in "$INSTALL/Logs" "$WINEPREFIX/drive_c/Logs"; do
        [ -f "$d/$f" ] || : > "$d/$f"
    done
done
echo "created Logs, C:\\Logs and C:\\Buffers"

# --- Ansel capability directories ------------------------------------------
# PakonImau aborts on the first missing one with only "Can't open install
# directory!".  Several of them ship EMPTY, and git does not track empty
# directories -- so anyone who sources the OEM tree from a git repo is missing
# exactly those.  Create any that PakonImau.dll names but the tree lacks.
# Empty directories the OEM install ships and git therefore drops.
[ -d "$INSTALL/exif" ] || { mkdir -p "$INSTALL/exif"; echo "created exif/"; }

AINST="$(find "$INSTALL" -maxdepth 1 -iname "anselinstalldir" -type d | head -1)"
if [ -n "$AINST" ]; then
    # Ansel's own scratch directories.  Both ship EMPTY, so git drops them too --
    # and without them AnsCache's ctor throws AnsError:301 "Can't write to cache
    # directory", PakonImau's bInit fails, and the client dies at start-up with
    # EC_PI_INVALID_FILE_FORMAT (2015) naming an unrelated path. Nothing in that
    # chain mentions a missing directory.
    for d in anselCacheDir anselTempDir; do
        [ -d "$AINST/$d" ] || { mkdir -p "$AINST/$d"; echo "created anselinstalldir/$d"; }
        chmod u+rwx "$AINST/$d" 2>/dev/null || true
    done
fi

ANSEL="$AINST/dataPathItems"
if [ -n "$AINST" ] && [ -d "$ANSEL" ]; then
    made=0
    for cap in $(strings -a "$INSTALL/PakonIMAu.dll" 2>/dev/null \
                 || strings -a "$INSTALL/PakonImau.dll" 2>/dev/null); do
        case "$cap" in
            afterSCPLutFos|afterSCPLutSba|citras|ast)
                [ -d "$ANSEL/$cap" ] || { mkdir -p "$ANSEL/$cap"; made=$((made+1)); } ;;
        esac
    done
    # 'ast' is too short to survive as a standalone string in every build
    [ -d "$ANSEL/ast" ] || { mkdir -p "$ANSEL/ast"; made=$((made+1)); }
    echo "ansel capability dirs: created $made missing"
fi

# --- registry ---------------------------------------------------------------
if [ -z "$WINE" ]; then
    echo "wine not found -- skipping the registry step; re-run after installing it"
else
    REG="$INSTALL/anselinstalldir/minilab.reg"
    if [ -f "$REG" ]; then
        # `regedit /S` silently drops every key when the file has a UTF-8 BOM.
        # Strip it, then import into BOTH views: Wine on macOS is WoW64-only and
        # a 32-bit app reads HKLM\Software\WOW6432Node\...
        td="$(mktemp -d)"; tmp="$td/minilab.reg"
        # Strip a UTF-8 BOM if present, byte-safely. The file is UTF-16 on some
        # installs, which sed refuses outright -- pass those through untouched.
        python3 -c 'import sys;d=open(sys.argv[1],"rb").read();open(sys.argv[2],"wb").write(d[3:] if d[:3]==b"\xef\xbb\xbf" else d)' "$REG" "$tmp"
        for view in 32 64; do
            MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all \
                "$WINE" reg import "$(printf 'Z:%s' "$tmp" | tr '/' '\\')" /reg:$view \
                >/dev/null 2>&1 && echo "imported minilab.reg into the ${view}-bit view" \
                || echo "minilab.reg import into the ${view}-bit view FAILED"
        done
        rm -rf "$td"
    else
        echo "no minilab.reg at $REG -- import yours by hand (see docs/SETUP.md)"
    fi

    # --- base scan configuration -------------------------------------------
    # TLB needs ~114 software settings that are NOT in the OEM archive and NOT
    # created by TLB itself: Scan\Test's driver flags (TlaControlLeds, SenseFilm,
    # DriverSimultaneousPackets, UseFixedPatternCorrection, WaitForLamp_*), the
    # 65 ColorKodak entries, and a handful under TLB/TLX. Without them
    # initialisation gets as far as the CCD geometry writes and then gives up.
    #
    # This file is unit-INDEPENDENT by construction. Everything a specific
    # scanner owns is deliberately absent and is derived on each machine:
    #   * ScannerSerialNumber / ScannerType / ScannerVersionHw -- read from the
    #     unit's own EEPROM by TLB
    #   * every DpiBase*_35 key -- MotorSpeed/MotorAdjust/Offset/Stepper* come
    #     from the EEPROM, and the light values (Current_*, DutyCycle*, Gain_*,
    #     Offset_*) from the LED servo when you run Scan -> Light Correction
    # So this is the F-135 software's configuration, not anybody's calibration.
    # PAKON_SKIP_BASE_CONFIG=1 leaves it out entirely, so it can be established
    # whether TLB writes these settings itself. See docs/SETUP.md.
    BASE="$HERE/base-config.reg"
    [ -n "${PAKON_SKIP_BASE_CONFIG:-}" ] && { BASE=""; echo "base scan config SKIPPED (PAKON_SKIP_BASE_CONFIG set)"; }
    if [ -n "$BASE" ] && [ -f "$BASE" ]; then
        if MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg query \
               "HKLM\\Software\\Pakon\\TLB\\Scan\\Test" /reg:32 >/dev/null 2>&1; then
            echo "base scan config already present"
        else
            for view in 32 64; do
                MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg import \
                    "$(printf 'Z:%s' "$BASE" | tr '/' '\\')" /reg:$view >/dev/null 2>&1
            done
            flush_registry
            if MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg query \
                   "HKLM\\Software\\Pakon\\TLB\\Scan\\Test" /reg:32 >/dev/null 2>&1; then
                echo "imported base scan config (114 settings, no unit data)"
            else
                echo "base scan config import FAILED -- init will not get past"
                echo "   the CCD geometry writes without it"
            fi
        fi
    fi

    # --- scan-mode keys ----------------------------------------------------
    # NEVER CREATE THESE KEYS.  A real mode key holds ~23 values (Current_*,
    # DutyCycle_*, DutyCycleOpenGate_*, Gain_*, Offset_*, DetectFilm_G ...) and
    # TLB writes them itself the first time it finds the key ABSENT.  Creating
    # one with a single value in it makes TLB believe it is already configured,
    # so it never writes the rest -- and init then dies building a device
    # command out of a value that is not there:
    #   EC_WIN_DeviceIoControl (165) Invalid function.  Type 4, PktLen 3, Address 10
    #
    # First, repair that damage if a previous run did it.  regscan.py lists any
    # mode key too thin to be TLB's own work; deleting it lets TLB rewrite it.
    flush_registry
    thin_count=0
    while IFS= read -r k; do
        [ -n "$k" ] || continue
        MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg delete "$k" \
            /f /reg:32 >/dev/null 2>&1 && thin_count=$((thin_count + 1))
    done <<REGFIX
$(python3 "$HERE/regscan.py" "$WINEPREFIX/system.reg")
REGFIX
    if [ "$thin_count" -gt 0 ]; then
        flush_registry
        echo "removed $thin_count half-configured scan-mode key(s) -- TLB will"
        echo "   rewrite them properly on its next run"
    fi

    # Now set the flag, but only on keys that ALREADY exist.  TLB replaces the
    # value with a completion timestamp once the correction has run, so a key
    # that already has a value is left alone: forcing it back to 1 would make
    # the unit redo the whole light correction.
    seeded=0; absent=0
    for base in 4 8 16; do
        for mode in ColNeg ColNegIr BnW BnWIr BnW_C41 BnW_C41Ir; do
            k="HKLM\\Software\\Pakon\\TLB\\Scan\\DpiBase${base}_35\\$mode"
            if ! MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg query "$k" \
                    /reg:32 >/dev/null 2>&1; then
                absent=$((absent + 1)); continue        # key absent: leave it to TLB
            fi
            MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg query "$k" \
                /v FullLightCorrections /reg:32 >/dev/null 2>&1 && continue
            MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg add "$k" \
                /v FullLightCorrections /t REG_DWORD /d 1 /f /reg:32 >/dev/null 2>&1 \
                && seeded=$((seeded + 1))
        done
    done
    echo "FullLightCorrections: set on $seeded mode(s), $absent awaiting TLB"
    if [ "$absent" -gt 0 ]; then
        echo "   TLB writes its own scan config on first run. Start the client"
        echo "   once, close it, then re-run this script to set the flag."
    fi
fi

cat <<'EOF'

Done. One step left, and it needs the scanner:

  Run ./run.sh, then Scan -> Light Correction with the film gate EMPTY.
  That runs the LED servo and writes this unit's light calibration.

See docs/SETUP.md.
EOF
