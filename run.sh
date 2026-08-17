#!/bin/bash
# Launch the Kodak/Pakon TLX Client natively on macOS.
#
#   real OEM software (TLXClientDemo.exe + tlx.dll + TLB.dll + PakonImau ...)
#     running under Wine 11
#       with pkusb.dll intercepting the five \\.\Pakon135 device calls
#         forwarded over a local socket to pakonusb.py
#           which drives the scanner with libusb and enforces the safety rules.
#
# Usage:  ./run.sh          start server (if needed) and the client
#         ./run.sh install  ONE COMMAND: prerequisites, OEM stack, firmware,
#                           build, patch, registry -- everything but the scanner
#         ./run.sh doctor   check prerequisites; --install to fix them
#         ./run.sh import-reg <file.reg>   import a .reg using a macOS path
#         ./run.sh stop     stop both
#         ./run.sh log      tail both logs
#         ./run.sh trace    decoded live view of the hardware conversation
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
export WINEDEBUG="${WINEDEBUG:-+debugstr}"
export MVK_CONFIG_LOG_LEVEL=0
# TLB error tracing patches OEM code in memory: opt in explicitly.
#   PAKON_ERRHOOK=1 ./run.sh
export PAKON_ERRHOOK="${PAKON_ERRHOOK:-0}"
APP="${PAKON_INSTALL:-$WINEPREFIX/drive_c/Program Files/Pakon/F-X35 COM Server}"
SRVLOG="${PAKON_SRVLOG:-/tmp/pakonusb.log}"
CLILOG="${PAKON_CLILOG:-/tmp/tlxclient.log}"

# A repo-local .venv is used if it has the libusb1 binding; PYTHON overrides.
PYTHON="$("$HERE/setup/bootstrap.sh" --python || echo python3)"

case "${1:-start}" in
install)
    # Clean machine -> ready to scan, in one command.  Each step is idempotent,
    # so re-running it after a failure resumes rather than starting over.
    set -e
    echo "installing into:"
    echo "  prefix   $WINEPREFIX"
    echo "  OEM      $APP"
    echo "  firmware ${PSIX_FIRMWARE_DIR:-$HOME/.local/share/psix/firmware}"
    echo
    echo "== 1/5  prerequisites (Homebrew: wine, mingw-w64, libusb; a repo .venv)"
    "$HERE/setup/bootstrap.sh" --install || true
    # bootstrap reports missing OEM files too, which install fixes further down,
    # so its exit status is not fatal here.  A python that cannot import usb1 IS
    # fatal for scanning, so say it now rather than at first launch.
    if ! "$("$HERE/setup/bootstrap.sh" --python || echo python3)" \
            -c "import usb1" >/dev/null 2>&1; then
        echo
        echo "  !! no python here can 'import usb1' -- the USB server will not run."
        echo "     The rest of the install continues; re-run this once that is"
        echo "     fixed, or run ./setup/bootstrap.sh --install on its own."
    fi
    WINE="$("$HERE/setup/bootstrap.sh" --wine)"
    [ -n "$WINE" ] || { echo "wine still missing -- cannot continue"; exit 1; }

    echo
    echo "== 2/5  Wine prefix at $WINEPREFIX"
    if [ ! -d "$WINEPREFIX/drive_c" ]; then
        MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" wineboot -u >/dev/null 2>&1 || true
        echo "        created"
    else
        echo "        already exists"
    fi

    echo
    echo "== 3/5  OEM stack + firmware"
    if [ -f "$APP/TLXClientDemo.exe" ]; then
        echo "        already installed at $APP"
    else
        "$HERE/setup/fetch-oem.sh" "${2:-}" "${3:-}"
    fi

    echo
    echo "== 4/5  build the shim"
    make -s -C "$HERE/src"

    echo
    echo "== 5/5  patch imports, registry, Ansel dirs, logs"
    "$HERE/setup/setup.sh"

    echo
    echo "Ready. Power on the scanner, then:  $HERE/run.sh"
    echo "First run on a fresh prefix: Scan -> Light Correction, gate EMPTY."
    exit 0
    ;;
import-reg)
    # Import a .reg file given an ORDINARY macOS path.  Wine addresses the mac
    # filesystem as drive Z:, so the path has to be translated and the
    # separators flipped -- nobody should have to do that by hand.
    f="${2:?usage: run.sh import-reg /path/to/file.reg}"
    [ -f "$f" ] || { echo "no such file: $f"; exit 1; }
    f="$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"      # make it absolute
    WINE="$("$HERE/setup/bootstrap.sh" --wine)"
    [ -n "$WINE" ] || { echo "wine not found"; exit 1; }
    win="$(printf 'Z:%s' "$f" | tr '/' '\\')"
    echo "importing $f"
    echo "  (Wine sees it as $win)"
    for view in 32 64; do
        MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all "$WINE" reg import "$win" /reg:$view \
            >/dev/null 2>&1 && echo "  imported into the ${view}-bit view" \
            || echo "  ${view}-bit view FAILED"
    done
    MVK_CONFIG_LOG_LEVEL=0 WINEDEBUG=-all \
        "$(dirname "$WINE")/wineserver" -k >/dev/null 2>&1
    exit 0
    ;;
doctor)
    exec "$HERE/setup/bootstrap.sh" "${2:-}"
    ;;
stop)
    pkill -f TLXClientDemo 2>/dev/null
    pkill -f pakonusb.py 2>/dev/null
    echo "stopped"
    exit 0
    ;;
trace)
    exec "${PYTHON:-python3}" "$HERE/tools/tlxtrace.py" "${2:-}"
    ;;
log)
    echo "=== USB server ($SRVLOG) ==="; tail -25 "$SRVLOG" 2>/dev/null
    echo; echo "=== client debug ($CLILOG) ==="
    grep -aiE 'pkusb|pakon|TLA |firmware|scanner' "$CLILOG" 2>/dev/null | tail -15
    echo; echo "=== OEM's own logs ==="
    for f in "$APP/Logs/"*.txt; do [ -f "$f" ] && { echo "--- $(basename "$f")"; tail -12 "$f"; }; done
    exit 0
    ;;
esac

# 0. find wine.  `brew install --cask wine-stable` does not put it on PATH, so
#    look inside the .app bundles too; offer to install it if there is none.
WINE="$("$HERE/setup/bootstrap.sh" --wine)"
if [ -z "$WINE" ]; then
    echo "Wine is not installed (or not anywhere I know to look)."
    echo "It is a ~1 GB download from Homebrew."
    printf "Install it now with 'brew install --cask wine-stable'? [y/N] "
    read -r yn
    case "$yn" in
    [yY]*)  command -v brew >/dev/null || {
                echo "Homebrew first: https://brew.sh"; exit 1; }
            brew install --cask wine-stable || exit 1
            WINE="$("$HERE/setup/bootstrap.sh" --wine)"
            [ -n "$WINE" ] || { echo "still cannot find wine, sorry"; exit 1; } ;;
    *)      echo "Set PAKON_WINE=/path/to/wine if you have it elsewhere."; exit 1 ;;
    esac
fi
echo "wine: $WINE"

# Scans must land somewhere you can see. Map a drive letter straight at a real
# folder, so the Save dialog writes through it -- no copying, no watcher.
SCANS="${PAKON_SCANS:-$HOME/Desktop/pakon-scans}"
mkdir -p "$SCANS"
if [ ! -e "$WINEPREFIX/dosdevices/p:" ]; then
    ln -s "$SCANS" "$WINEPREFIX/dosdevices/p:" && echo "mapped P: -> $SCANS"
fi
# The Save Settings box ships pre-filled with C:\Temp\Test0.bmp, so redirect
# that too -- otherwise a default save is buried in the prefix. rmdir only
# succeeds on an empty dir, so this never touches scans already sitting there.
WTEMP="$WINEPREFIX/drive_c/Temp"
if [ -L "$WTEMP" ]; then :
elif [ ! -e "$WTEMP" ] || rmdir "$WTEMP" 2>/dev/null; then
    ln -s "$SCANS" "$WTEMP" && echo 'mapped C:\Temp -> '"$SCANS"
else
    echo "note: $WTEMP holds files, left alone -- save to P:\\ instead"
fi

# Missing OEM stack?  Install it, rather than telling the user to go and read a
# diagnostic.  This is the same work './run.sh install' does and every step of it
# is idempotent, so it is safe to reach from the start path.
if [ ! -f "$APP/TLXClientDemo.exe" ]; then
    echo "No OEM client at: $APP"
    echo "Installing it now -- same thing as './run.sh install'."
    echo
    "$0" install || { echo; echo "install failed; nothing was started."; exit 1; }
    echo
    if [ ! -f "$APP/TLXClientDemo.exe" ]; then
        echo "install finished but there is still no client at:"
        echo "  $APP"
        echo "If your stack lives elsewhere, set PAKON_INSTALL to point at it."
        exit 1
    fi
fi

# 0b. the server needs the libusb1 binding.  Resolving PYTHON falls back to a
#     bare `python3` that may not have it, and the failure then arrives as a
#     ModuleNotFoundError traceback from a backgrounded process, which is a
#     terrible way to learn you are one command from working.
if ! "$PYTHON" -c "import usb1" >/dev/null 2>&1; then
    echo "The USB server needs the libusb1 Python binding and $PYTHON lacks it."
    echo "Installing it now (libusb + a .venv in the repo)..."
    "$HERE/setup/bootstrap.sh" --install >/dev/null 2>&1 || true
    PYTHON="$("$HERE/setup/bootstrap.sh" --python || echo python3)"
    if ! "$PYTHON" -c "import usb1" >/dev/null 2>&1; then
        echo
        echo "That did not work. Run this and send me what it prints:"
        echo "    $HERE/setup/bootstrap.sh --install"
        echo
        echo "By hand:"
        echo "    brew install libusb"
        echo "    cd $HERE && python3 -m venv .venv"
        echo "    $HERE/.venv/bin/pip install -r $HERE/requirements.txt"
        exit 1
    fi
    echo "ok: $PYTHON"
fi

# 1. the scanner needs application firmware after every power cycle (it lives in
#    RAM).  pakonusb.py says so plainly if it is missing.
if ! pgrep -f pakonusb.py >/dev/null; then
    echo "starting USB server..."
    ( cd "$HERE/server" && nohup "${PYTHON:-python3}" -u pakonusb.py > "$SRVLOG" 2>&1 < /dev/null & )
    sleep 4
    grep -q "serving" "$SRVLOG" || { echo "USB server failed:"; cat "$SRVLOG"; exit 1; }
fi
echo "USB server: $(grep -c . "$SRVLOG") log lines, listening"

# 2. the client, from the path the OEM installer would have used, because
#    TLB.dll and PakonImau resolve Config/, Logs/ and the Ansel data from it.
pkill -f TLXClientDemo 2>/dev/null; sleep 1
cd "$APP" || exit 1
nohup "$WINE" TLXClientDemo.exe > "$CLILOG" 2>&1 < /dev/null &
disown
echo "TLX Client launching (log: $CLILOG)"
echo "run './run.sh log' to see what it is doing, './run.sh stop' to stop."
