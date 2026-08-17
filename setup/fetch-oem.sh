#!/bin/bash
# Fetch the OEM stack and the scanner firmware, and put them where the rest of
# this project expects to find them.
#
#   ./setup/fetch-oem.sh                 pull from the public archive
#   ./setup/fetch-oem.sh --from <dir>    copy from a local copy you already have
#
# This project still contains no Kodak material.  It fetches it, at your
# request, from a public third-party archive of the F-X35 distribution -- the
# same thing you would do by hand.  Override the source with PAKON_OEM_URL.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

URL="${PAKON_OEM_URL:-https://github.com/plonsker/pakon-scanning-software.git}"
BRANCH="${PAKON_OEM_BRANCH:-master}"
# Path to the installer tree inside that archive.
SUB="Pakon Update/fx35install"
WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
APP="${PAKON_INSTALL:-$WINEPREFIX/drive_c/Program Files/Pakon/F-X35 COM Server}"
FWDIR="${PSIX_FIRMWARE_DIR:-$HOME/.local/share/psix/firmware}"

FORCE=0
for a in "$@"; do [ "$a" = "--force" ] && FORCE=1; done

# Never silently overwrite a working install.  The DLLs there may be patched and
# the Ansel tree may hold directories the archive does not carry.
if [ -f "$APP/TLXClientDemo.exe" ] && [ $FORCE -eq 0 ]; then
    echo "An OEM stack is already installed at:"
    echo "  $APP"
    echo "Refusing to overwrite it. Pass --force if that is really what you want."
    exit 0
fi

SRC=""
if [ "${1:-}" = "--from" ]; then
    SRC="${2:?--from needs a directory}"
    [ -d "$SRC/$SUB" ] && SRC="$SRC/$SUB"
    echo "source: $SRC (local)"
fi

tmp=""
# NOTE the explicit `return 0`: an EXIT trap whose last command fails overrides
# the script's own exit status, and `[ -n "" ]` fails whenever we did not clone.
cleanup() { if [ -n "$tmp" ]; then rm -rf "$tmp"; fi; return 0; }
trap cleanup EXIT

if [ -z "$SRC" ]; then
    echo "source: $URL ($BRANCH)"
    echo "        a public archive of the Kodak F-X35 distribution -- about 50 MB."
    echo "        Set PAKON_OEM_URL to use a different one, or --from a local copy."
    tmp="$(mktemp -d)"
    # Blob-filtered sparse clone: only the installer subtree is downloaded, not
    # the whole 279 MB archive.  Cone mode also gives us the files sitting
    # directly in fx35install/, which is where the firmware HEXes live.
    git clone --quiet --depth 1 --branch "$BRANCH" --filter=blob:none \
              --sparse "$URL" "$tmp/oem" \
        || { echo "clone failed -- no network, or the archive moved."; exit 1; }
    # Two paths: the COM SERVER tree, and System32 -- the installer scatters the
    # runtimes (mfc71u, msvcr71, kodakcms, ekjpegi, xerces) outside the app dir.
    # Cone mode also yields the files sitting directly in fx35install/, which is
    # where the firmware HEXes and the MFC/VC runtimes live.
    git -C "$tmp/oem" sparse-checkout set --cone \
        "$SUB/program files/Pakon/F-X35 COM SERVER" "$SUB/System32" >/dev/null
    SRC="$tmp/oem/$SUB"
fi

COM="$(find "$SRC" -maxdepth 4 -iname "F-X35 COM SERVER" -type d | head -1)"
[ -n "$COM" ] || { echo "no 'F-X35 COM SERVER' directory under $SRC"; exit 1; }

# ---------------------------------------------------------------- the stack
mkdir -p "$APP"
echo "installing the OEM stack into:"
echo "  $APP"
# -R and a trailing /. so the CONTENTS land in our canonical path, whatever the
# archive spells the directory as.  anselinstalldir and Config come too: TLB
# resolves them from its own module path and aborts without them.
cp -R "$COM/." "$APP/"
n=$(find "$APP" -type f | wc -l | tr -d ' ')
echo "  $n files from the COM SERVER tree"

# The installer scatters the runtimes: the MFC/VC ones at its root, and a further
# dozen (Jpegi, kdu_ek, ijl15, PCDLIB32, cosapi_IU ...) under System32, which a
# real install puts on the Windows system path.  We put them beside the client,
# which is first in the DLL search order and matches a known-good install.  Copy
# ALL of them rather than a chosen few: PakonImau pulls in the imaging ones
# lazily, so a missing one surfaces much later as a save failure, not a start-up
# error, and guessing which are needed is how you ship that bug.
extra=0
for dir in "$SRC" "$SRC/System32"; do
    [ -d "$dir" ] || continue
    while IFS= read -r hit; do
        base="$(basename "$hit")"
        [ -f "$APP/$base" ] && continue
        cp -f "$hit" "$APP/$base" && extra=$((extra + 1))
    done < <(find "$dir" -maxdepth 1 -type f \( -iname "*.dll" -o -iname "*.ocx" \) 2>/dev/null)
done
echo "  $extra runtime file(s) collected from the installer root and System32"

# Completeness assertion: everything the manifest names must now be present.
while IFS= read -r want; do
    [ -f "$APP/$want" ] || echo "  !! still missing after the sweep: $want"
done < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))))' "$HERE/manifest.json")

# ---------------------------------------------------------------- firmware
# Never bundled, always the user's own copy -- but fetching it for you is the
# same act as fetching the DLLs, so it happens here too.
mkdir -p "$FWDIR"
got=0
while IFS= read -r f; do
    cp -f "$f" "$FWDIR/" && got=$((got + 1))
done < <(find "$SRC" -maxdepth 2 -iname "[Pp]akon[0-9].hex")
echo "firmware: $got file(s) -> $FWDIR"
[ "$got" -gt 0 ] || echo "  !! none found; the scanner cannot start without one"

# ---------------------------------------------------------------- verify
echo
python3 "$HERE/doctor.py" --install "$APP" --quiet
