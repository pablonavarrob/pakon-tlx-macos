#!/bin/bash
# Check (and optionally install) everything needed to run the OEM stack.
#
#   ./setup/bootstrap.sh            report what is missing
#   ./setup/bootstrap.sh --install  install the missing pieces via Homebrew
#
# Wine is looked for in every place it normally ends up on macOS, because
# `brew install --cask wine-stable` does NOT put it on PATH.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
DO_INSTALL=0
[ "${1:-}" = "--install" ] && DO_INSTALL=1

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mmiss\033[0m  %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
missing=0

# ---------------------------------------------------------------- wine
find_wine() {
    [ -n "${PAKON_WINE:-}" ] && [ -x "$PAKON_WINE" ] && { echo "$PAKON_WINE"; return; }
    command -v wine64 2>/dev/null && return
    command -v wine   2>/dev/null && return
    local c
    for c in \
        "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine" \
        "/Applications/Wine Devel.app/Contents/Resources/wine/bin/wine" \
        "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine" \
        "$HOME/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine" \
        "$HOME/wine/Wine Stable.app/Contents/Resources/wine/bin/wine" \
        "/opt/homebrew/bin/wine" "/usr/local/bin/wine" \
        "/opt/homebrew/Caskroom/wine-stable/"*/"Wine Stable.app/Contents/Resources/wine/bin/wine"
    do
        [ -x "$c" ] && { echo "$c"; return; }
    done
    return 1
}

# -------------------------------------------------------------- python
# The server needs the libusb1 binding.  Prefer a venv inside the repo so a
# fresh clone is self-contained and nothing is installed system-wide.
find_python() {
    local c
    for c in "${PYTHON:-}" "$ROOT/.venv/bin/python3" python3; do
        [ -n "$c" ] || continue
        "$c" -c "import usb1" >/dev/null 2>&1 && { echo "$c"; return; }
    done
    return 1
}

# run.sh asks us where things are; print the path and nothing else.
case "${1:-}" in
--wine)   find_wine   || exit 1; exit 0 ;;
--python) find_python || exit 1; exit 0 ;;
esac

echo "prerequisites"
WINE="$(find_wine || true)"
if [ -n "$WINE" ]; then
    ok "wine        $("$WINE" --version 2>/dev/null || echo '?')  [$WINE]"
else
    bad "wine        not found"
    missing=1
    if [ $DO_INSTALL -eq 1 ]; then
        echo "        installing wine-stable (this is a large download)..."
        brew install --cask wine-stable && WINE="$(find_wine || true)"
    fi
fi

# ------------------------------------------------------------- toolchain
for pair in "i686-w64-mingw32-gcc:mingw-w64" "python3:python3"; do
    cmd="${pair%%:*}"; pkg="${pair##*:}"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$(printf '%-12s' "$pkg")$("$cmd" --version 2>/dev/null | head -1)"
    else
        bad "$(printf '%-12s' "$pkg")$cmd not found (brew install $pkg)"
        missing=1
        [ $DO_INSTALL -eq 1 ] && brew install "$pkg"
    fi
done

# ---------------------------------------------------------------- libusb
PY="$(find_python || true)"
if [ -z "$PY" ] && [ $DO_INSTALL -eq 1 ]; then
    echo "        creating .venv and installing requirements.txt (nothing system-wide)"
    brew list libusb >/dev/null 2>&1 || brew install libusb
    python3 -m venv "$ROOT/.venv" \
        && "$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
    PY="$(find_python || true)"
fi
if [ -n "$PY" ]; then
    ok "libusb1     ok  [$PY]"
else
    bad "libusb1     no python here can 'import usb1'"
    missing=1
fi

# ------------------------------------------------------------- our build
if [ -f "$ROOT/src/pkusb.dll" ]; then
    ok "pkusb.dll   built"
else
    warn "pkusb.dll   not built yet (make -C src)"
fi

# ------------------------------------------------------------- the scanner
echo
echo "scanner"
"${PY:-python3}" - <<'PY' 2>/dev/null || echo "  (need libusb1 before the USB bus can be checked)"
try:
    import usb1
except ImportError:
    raise SystemExit(1)
with usb1.USBContext() as c:
    found = [(d.getVendorID(), d.getProductID())
             for d in c.getDeviceList(skip_on_error=True) if d.getVendorID() == 0x0F05]
if not found:
    print("  \033[33mwarn\033[0m  no Pakon on the USB bus (0f05:f135 loaded / 0f05:f235 unloaded)")
else:
    for v, p in found:
        state = {0xF135: "firmware loaded", 0xF235: "no firmware yet (uploaded at start-up)"}
        print("  \033[32mok\033[0m    found %04x:%04x -- %s" % (v, p, state.get(p, "?")))
PY

# -------------------------------------------------------------- firmware
# Lives in the scanner's RAM, so it is uploaded at every power cycle -- from
# YOUR file. Never bundled here.
echo
echo "firmware"
FWDIR="${PSIX_FIRMWARE_DIR:-$HOME/.local/share/psix/firmware}"
fw=$(ls "$FWDIR"/[Pp]akon[0-9].hex 2>/dev/null | head -3)
if [ -n "$fw" ]; then
    for f in $fw; do ok "$(basename "$f")  [$FWDIR]"; done
else
    bad "no pakon<N>.hex in $FWDIR"
    echo "        Copy your own pakon7.hex there (pakon5/pakon8 for other revs),"
    echo "        or set PSIX_FIRMWARE_DIR. It comes from your Pakon software CD."
    missing=1
fi

# ---------------------------------------------------------------- OEM files
echo
echo "OEM files"
docout="$(python3 "$HERE/doctor.py" --quiet 2>/dev/null)"; docrc=$?
printf '%s\n' "$docout" | sed 's/^/  /'
[ $docrc -ne 0 ] && missing=1

echo
[ -n "$WINE" ]     && echo "wine:   $WINE"
[ -n "${PY:-}" ]   && echo "python: $PY"
echo "        (run.sh finds both of these itself; no PATH changes needed)"
if [ $missing -ne 0 ]; then
    echo
    echo "Something above is missing."
    echo "  tools   -- './run.sh doctor --install' installs them with Homebrew"
    echo "  OEM/fw  -- only you can supply those, from your own Pakon software"
    exit 1
fi
echo "All prerequisites present."
