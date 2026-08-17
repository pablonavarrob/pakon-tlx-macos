# Setup

## 1. Everything, in one command

```sh
./run.sh install
```

Prerequisites via Homebrew, the Wine prefix, the OEM stack and firmware, the
shim, COM registration, the import patch, the registry, the Ansel directories.
The sections below are what it does, for when you need to redo a step by hand.

Wine on macOS is **WoW64-only** — there are no 32-bit prefixes. The prefix is
win64 and a 32-bit app reads `HKLM\Software\WOW6432Node\...`. That matters for
every registry step below.

## 2. The OEM files

`setup/fetch-oem.sh` downloads them, or takes `--from <dir>`. Override the
archive with `PAKON_OEM_URL` / `PAKON_OEM_BRANCH`. It refuses to overwrite a
populated install without `--force`.

They must end up in a directory the stack recognises. TLB derives `Config\`,
`Logs\` and the Ansel data from its own module path, so the conventional
location is:

```
$WINEPREFIX/drive_c/Program Files/Pakon/F-X35 COM Server/
```

Override with `PAKON_INSTALL`. Firmware goes to `$PSIX_FIRMWARE_DIR`
(default `~/.local/share/psix/firmware`); the scanner loses it at every power
cycle and it is uploaded at start-up, never bundled.

## 2a. Base scan configuration

`setup.sh` imports `setup/base-config.reg` — 114 software settings TLB needs
that are in neither the OEM archive nor anything TLB creates for itself. Skip it
with `PAKON_SKIP_BASE_CONFIG=1`. Whether it is genuinely required is an open
question; see "Is base-config.reg actually necessary?" in the README.

Per-unit values are never in that file: the scanner's serial/type/hardware
version come from its EEPROM, and the light values from the LED servo during
*Scan → Light Correction*. `setup.sh` will not create a scan-mode key, only add
`FullLightCorrections` to one TLB has already written.

## 2b. COM registration

`tlx.dll` is a COM server. The client calls `CoCreateInstance` on a CLSID that
only exists once `DllRegisterServer` has run, so without this a fresh install
dies at start-up with `class {ea82986b-...} not registered` and no dialog.
All four of `tlx`/`TLA`/`TLB`/`TLC` register one.

Two traps, both of which cost time:

* **Register before patching the imports.** Afterwards, loading `TLB.dll` pulls
  in `pkusb.dll`, which waits for a USB server that is not running during setup.
  Registration records the DLL's path, not its bytes, so patching later is fine.
* **`regsvr32` writes the keys and then does not exit** for some of these DLLs.
  Judge it by the registry, not by its exit status — `setup.sh` caps the process
  and then verifies the CLSIDs are actually present.

## 3. Build and wire up

```sh
make -C src
./setup/setup.sh
```

`setup.sh` copies `pkusb.dll` in, backs up `TLB.dll`/`tlx.dll` as `.orig`, and
renames one import string in each — `VERSION.dll` → `pkusb.dll`. That import is
three version-info functions used for a single log line; `pkusb` re-exports them
as stubs, and being imported gets its `DllMain` called early. No code is
modified.

`tlx.dll` needs patching too, not just `TLB.dll`: it opens `\\.\Pakon135` itself
as a presence check before it loads TLB through COM, and without that you get
`EC_NoScannerDetected`. TLB also arrives *after* pkusb's `DllMain`, which is why
the shim runs a short watcher thread.

## 4. Registry — done for you by `setup.sh`

`setup.sh` imports `minilab.reg` (found in `anselinstalldir/`) into **both**
views and seeds `FullLightCorrections`. What it is doing, and why, in case you
need to redo it by hand:

Use `reg import`, **not** `regedit /S` — the latter silently ignores a UTF-8 BOM
and drops every key without an error. Import into both views: Wine on macOS is
WoW64-only and a 32-bit app reads `HKLM\Software\WOW6432Node\...`.

```
HKLM\Software\Wow6432Node\Pakon\TLB\Scan\DpiBase16_35\ColNegIr
    FullLightCorrections = 1  (REG_DWORD)
```

`FullLightCorrections` is write-to-request, not a stored boolean: TLB replaces
the `1` with a Unix timestamp once the correction has run. `setup.sh` therefore
only seeds modes with no value at all — resetting it to `1` forces the unit to
redo the correction.

Without it, the light values stay at their compiled-in defaults
(`Current=1`, `DutyCycle=0.000000`) — which the config getter then persists — and
the lamp produces nothing usable.

**Do not enable** `WriteEEPromDebugFile` or `WriteLightStabilityLog`. TLB opens
its log files with `_wfopen` and never checks the result, so any path it cannot
create crashes the process at the end of every scan.

Ansel also wants one directory per capability under
`anselinstalldir\dataPathItems\`, and aborts on the first missing one with only
"Can't open install directory!". Empty directories are enough.

## 5. Run

```sh
./run.sh                # firmware upload if needed, server, then the client
./run.sh trace          # decoded live view
./run.sh stop
```

First run on a fresh install: **Scan → Light Correction** with an empty gate.

The Save Settings box is hardcoded to default to `C:\Temp\Test0.bmp`, and files
are written as `<dir>\<frame>.raw`. `run.sh` symlinks both
`$WINEPREFIX/dosdevices/p:` and `$WINEPREFIX/drive_c/Temp` at `$PAKON_SCANS`, so
the default save and an explicit `P:\` both land outside the prefix. No copier
runs; Wine resolves the symlink at open time. A non-empty `drive_c/Temp` is left
untouched.

## Environment

| variable | default |
|---|---|
| `WINEPREFIX` | `~/.wine` |
| `PAKON_INSTALL` | `~/.wine/drive_c/Program Files/Pakon/F-X35 COM Server` |
| `PAKON_WINE` | auto-detected, incl. inside `Wine *.app` bundles |
| `PSIX_FIRMWARE_DIR` | `~/.local/share/psix/firmware` |
| `PAKON_SCANS` | `~/Desktop/pakon-scans`, mapped as `P:` and `C:\Temp` |
| `PAKON_ERRHOOK` | `0` — set to 1 to hook TLB's internal error reporter |
| `PAKON_SRVLOG` / `PAKON_CLILOG` | `/tmp/pakonusb.log`, `/tmp/tlxclient.log` |
| `PYTHON` | the repo `.venv` if it has `usb1`, else `python3` |

## Troubleshooting

Symptoms and their real causes, all seen during development:

| symptom | cause |
|---|---|
| `EC_NoScannerDetected` | `tlx.dll` not patched (it probes the device itself) |
| `EC_FileNotFound PakonImau.dll` | stack not at a path TLB accepts |
| Stuck at "Corrections" for ever, no error | `ring[0x31]` TransferInProgress never set |
| `EC_DRV_LostSync (1003)` | byte stream discontinuity — do not re-align on Trigger #2 |
| `EC_DRV_RingTailOverflow (1002)` | zero usable lines delivered |
| `EC_WIN_FileRead (168)` | `ReadFile` completed synchronously |
| `EC_WIN_DeviceIoControl (165) 997` | `DeviceIoControl` returned `ERROR_IO_PENDING` |
| Lamp lights, images black | light calibration blank — run Light Correction |
| Dust not removed | Scratch Removal not ticked **in the Scan dialog** — no IR captured |
| `Film Tail First` / `Film Emulsion Down` | film orientation; emulsion up, head first |
| `EC_FilmInGuides (129)` at save | strip longer than `MaxFilmLength`; use 4–6 frames |
| Crash at end of scan in `_wfopen` path | an OEM log path cannot be created |

`docs/PROTOCOL.md` explains each of these in terms of the actual hardware.
