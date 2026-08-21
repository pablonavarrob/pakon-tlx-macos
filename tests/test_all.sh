#!/bin/bash
# Every self-check in the project. No hardware required.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$("$ROOT/setup/bootstrap.sh" --python || echo "${PYTHON:-python3}")"
fail=0
run() { echo "--- $1"; shift; "$@" || fail=1; }
run "PPB decoder"        $PY "$ROOT/server/ppb.py"
run "TLB name tables"    $PY "$ROOT/server/pknames.py"
run "USB server logic"   $PY "$ROOT/server/pakonusb.py" --selftest
run "DX synthesiser"     $PY "$ROOT/server/dxsynth.py" --selftest
run "line-sync analysis" $PY "$ROOT/tools/framing.py" --selftest
run "registry scan"      $PY "$ROOT/setup/regscan.py" --selftest
echo "--- ring protocol (needs wine + mingw)"
WINE="$("$ROOT/setup/bootstrap.sh" --wine || true)"
if ! command -v i686-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "    skipped: no mingw toolchain (brew install mingw-w64)"
elif [ -z "$WINE" ]; then
    echo "    skipped: wine not found (./run.sh doctor --install)"
else
    make -C "$ROOT/src" test WINE="$WINE" || fail=1
fi
echo
[ $fail -eq 0 ] && echo "ALL SELF-CHECKS PASSED" || echo "SOMETHING FAILED"
exit $fail
