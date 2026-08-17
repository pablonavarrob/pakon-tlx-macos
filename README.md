# Pakon F-135 on macOS

Run the **genuine Kodak TLX Client** — the real OEM software, with Kodak's own
colour science and Digital ICE — against a Pakon F-135 film scanner, natively on
macOS including Apple Silicon. No Windows VM, no USB passthrough, no rewritten
image pipeline.

Inline-style: 
![alt text](https://github.com/pablonavarrob/pakon-tlx-macos/blob/main/docs/screen-grab.png "View of TLX running on my Mac Mini M4")


```
TLXClientDemo.exe + tlx.dll + TLB.dll + PakonImau + DMLDICELib   (unmodified OEM)
        │
        ▼
     Wine  (x86_64 under Rosetta 2, WoW64 for the 32-bit code)
        │
        │   pkusb.dll — intercepts the five \\.\Pakon135 device calls
        ▼
   127.0.0.1:5140
        │
        ▼
   pakonusb.py  →  libusb  →  Pakon F-135
```

The Pakon F-135 is one of the best 35 mm scanners ever built, and its software is
Windows-XP-era, 32-bit, and needs a kernel-mode i386 driver. That driver can
never load on Apple Silicon. The usual answers are a Windows VM with USB
passthrough, which is fragile and slow, or an open-source reimplementation, which
throws away Kodak's colour processing and Digital ICE.

This takes a third route: **keep all the OEM code and replace only the driver.**
Five calls are the entire driver surface — `CreateFileW`, `DeviceIoControl`,
`ReadFile`, `GetOverlappedResult`, `CancelIo`. Everything above them — COM,
threads, MFC, the imaging pipeline, the window itself — is the real thing.

---

## Why use this?

1. **It is the actual Kodak software.** Digital ICE, the Ansel colour engine,
   the framing and DX decoding — all of it running as shipped, not approximated.
2. **No virtual machine.** No Windows licence, no USB passthrough to fight with,
   no 40 GB disk image. The client is a window on your Mac.
3. **Native speed on Apple Silicon.** Wine + Rosetta 2 runs the 32-bit client at
   a speed the hardware — not the emulation — limits.
4. **16-bit planar RAW out**, straight from TLB, unmodified — whatever you do
   with it afterwards is your business.
5. **The scanner is protected in software.** The EEPROM is never written, LED
   currents are clamped, the motor is stopped in the order the hardware needs,
   and an idle watchdog kills the lamp if a scan is abandoned. See
   [Hardware safety](#hardware-safety).
6. **You can see everything.** `./run.sh trace` decodes every packet on the wire
   in plain language while you scan.

---

## What this is not

- **It contains no Kodak or Pakon code, binaries or firmware.** Those are
  fetched, at your request, from a public archive of the F-X35 distribution, or
  supplied by you. One exception, stated up front: `setup/base-config.reg` holds
  189 of Kodak's own registry settings, because Kodak's installer would write
  them and we do not run it. It carries no unit data — see
  [Configuration](#configuration).
- **It is not a scanner driver you can use with anything else.** It speaks
  exactly enough of the protocol to satisfy TLB.dll. VueScan and SilverFast do
  not enter into it.
- **It is not a colour pipeline.** Nothing here touches your pixels. What TLB
  produces is written to disk byte for byte.
- **It is not widely tested.** It has physically run against exactly one F-135.
  Nothing about that unit is baked in (see [Portability](#portability)), but you
  are early.

---

## Requirements

- macOS on Apple Silicon or Intel
- Wine 11 or newer — the installer below fetches it
- `mingw-w64` and `libusb` from Homebrew, and Python 3.9+
- **Your own copy** of the Pakon F-X35 COM Server installation, and your
  scanner's firmware HEX
- A Pakon F-135, and a USB cable that is not a phone charger cable

---

## Installing

```sh
git clone https://github.com/<you>/pakon-f135-macos
cd pakon-f135-macos
./run.sh install
```

That is the whole thing. About a minute on a clean machine, and it does:

| step | what it does |
|---|---|
| 1 | `brew` installs `wine-stable`, `mingw-w64`, `libusb`, and builds a `.venv` in the repo |
| 2 | creates the Wine prefix if there isn't one |
| 3 | **downloads the OEM stack and your scanner's firmware** and puts them where the rest of the project expects them |
| 4 | cross-builds `pkusb.dll` |
| 5 | registers the COM servers, patches the two imports, seeds the registry, creates the directories the OEM install is missing |

Then power on the scanner and `./run.sh`. On a fresh prefix do
*Scan → Light Correction* with the gate empty once — that is the only step that
needs the hardware, because it measures your unit.

Nothing goes on your `PATH` and nothing is installed system-wide. Re-running
`install` is safe: every step is idempotent and it will not overwrite an OEM
stack you already have.

#### Where the OEM files come from

This repository contains no Kodak material and never will. `install` fetches it
at your request from a public third-party archive of the F-X35 distribution,
which is exactly what you would otherwise do by hand. Point it somewhere else if
you prefer:

```sh
PAKON_OEM_URL=https://... ./run.sh install      # a different archive
./run.sh install --from ~/my-own-copy           # a local copy you already have
```

The fetch is a blob-filtered sparse clone, so it pulls about 50 MB rather than
the whole 279 MB archive. It is manifest-driven: the installer scatters the
runtimes (`mfc71u`, `msvcr71`, `kodakcms`, `ekjpegi`, `xerces`) outside the
application directory, and the fetch finds each file wherever it lives.

#### If something goes wrong

```sh
./run.sh doctor            # what is present, what is missing, and the USB bus
./run.sh doctor --install  # just the Homebrew prerequisites
```

---

## Scanning

1. Power on the scanner **before** starting anything. Firmware lives in RAM and
   is uploaded at every power cycle — the device is `0f05:f235` cold and
   `0f05:f135` once loaded.
2. `./run.sh`, and wait for the client window.
3. **First time only:** *Scan → Light Correction*, with the film gate **empty**.
   This runs the LED servo and writes the light calibration. Without it the lamp
   produces nothing usable.
4. Set your resolution. Base 16 is 3000×2000; the tiers are multiples of a
   500×750 base.
5. If you want Digital ICE, tick **Scratch Removal in the Scan dialog** —
   see the gotcha below, this one catches everyone.
6. Press **Scan with the gate empty**, then feed the strip when the transport
   starts pulling. Emulsion up, head first.
7. Save to **`P:\`**. For 16-bit output: *Save Settings → To Client Memory,
   Planar, Add File Header*.

### Where the files actually go

The client's Save Settings box ships **pre-filled with `C:\Temp\Test0.bmp`** —
that string is hardcoded in `TLXClientDemo.exe`. Whatever directory is in that
box is where frames are written, as `<dir>\<frame name>.raw` / `.bmp`. Left
alone, that means `~/.wine/drive_c/Temp` — buried inside the Wine prefix, which
is where most people lose their first roll.

`run.sh` points **both** of those at one real folder, `~/Desktop/pakon-scans`
(override with `PAKON_SCANS`):

- `$WINEPREFIX/dosdevices/p:` → so you can type `P:\` in the box
- `$WINEPREFIX/drive_c/Temp` → so the shipped default already lands there

Wine resolves the symlink when it opens the file, so the client writes straight
into that folder at full speed. **Nothing copies anything and nothing runs in
the background.** Anywhere under `C:\users\<you>\Desktop` works too — Wine
already maps that to your real Desktop.

If `drive_c/Temp` already has files in it, `run.sh` leaves it completely alone
and says so; it will not move scans you already have. In that case save to `P:\`.

---

## Gotchas

Everything in this section cost real time to find. Most of it is the OEM
software's behaviour, not this project's.

### Film handling

- **Emulsion up, head first** — lowest frame numbers lead. The scanner detects
  both independently and refuses with `Film Emulsion Down (0x10000000)`,
  `Film Tail First (0x20000000)`, or `0xF0000000` if you managed both at once.
- **Feed strips of 4–6 frames.** Longer strips exceed `MaxFilmLength`; the
  transport halts mid-strip and the save stage then throws the entire roll away
  with `EC_FilmInGuides (129)`. You will have scanned it and get nothing.
- `MaxFilmLength` is only settable through `PutScannerInfo001`, which the demo
  client never calls. So it cannot be raised from the UI. Keep strips short.
- **Gate empty when you press Scan.** Feeding first and pressing Scan after is
  the reliable way to jam it.
- If a strip does jam: stop the client, advance the film out by hand, and do not
  pull against the drive.

### The client's settings

- **Digital ICE is a scan-time setting, not a save-time setting.** Ticking
  *Scratch Removal* in the Scan dialog is what turns the infrared LED on. Ticking
  it only when saving does nothing at all — the IR channel was never captured.
  Check the trace: `LED CURRENT ... IR=0` means no ICE, whatever the save dialog
  says.
- **`SaveToDisk` is 8-bit only**, regardless of what you set `iColorBits` to. The
  only 16-bit route is *To Client Memory* + *Planar* + *Add File Header*.
- **Do not hand-edit the light calibration in the registry.** The values look
  like placeholders on a fresh install (`Current=1`, `DutyCycle=0.000000`)
  because they are — `FullLightCorrections=1` makes TLB derive them with its own
  servo, per film type and resolution mode. Filling them in with numbers that
  look sensible produces pure white frames. A converged servo is data, not a
  guess to be second-guessed.
- Resolution silently affects output size. If you get 2100×1400 when you wanted
  3000×2000, you were on Base 8.

### The file you get

- **Planar, not interleaved.** 16-byte header of four `uint32` LE — header size,
  width, height, bit count (48 = 3 channels, 64 = RGB+IR) — then each channel
  plane in full, `width*height` `uint16` LE.
- **Samples never fill the 16-bit word.** Colour-corrected saves top out at
  exactly 4095 and clip there; saves with corrections off run to roughly 11800
  and do not clip. So don't assume 65535, and don't assume 4095 either — scale
  from the data. Documentation claiming 15 bits matches neither case.
- **Saves with corrections on are already positives**, corrections off are
  negatives. If you are unsure which you have, correlate against the `.bmp` the
  client wrote for the same frame.

### Installing the OEM stack

- **If you got the OEM files from a git repo, you are missing directories.**
  Git does not track empty directories, and several of Ansel's capability
  folders under `anselinstalldir\dataPathItems\` ship empty. PakonImau aborts
  on the first missing one with only `Can't open install directory!` and no clue
  which. `setup.sh` recreates them.
- **`FullLightCorrections` is not a boolean you can read back.** You set it to
  `1` to ask TLB to derive its own light calibration; once the correction has
  actually run, TLB **overwrites the value with a completion timestamp**. So a
  key holding `0x6a72df76` is not corrupt — it means the correction is done.
  Forcing it back to `1` makes the unit redo the whole correction.
- **`reg import`, never `regedit /S`.** The latter silently drops every key when
  the file has a UTF-8 BOM, and reports success.

### macOS and Wine

- **Homebrew's Wine is not on your `PATH`.** `brew install --cask wine-stable`
  drops it at
  `/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine`. This is why
  `run.sh` searches the bundles itself; if yours is somewhere unusual, set
  `PAKON_WINE=/path/to/wine`.
- **Use a 64-bit WoW64 prefix, not a `win32` one.** Wine 11 runs the 32-bit
  client through WoW64 in a normal prefix. A `WINEARCH=win32` prefix will not
  work under Rosetta.
- **32-bit apps read `HKLM\Software\WOW6432Node\...`.** If you are editing the
  registry and nothing changes, you are almost certainly editing the 64-bit view.
- TLB opens its log files with `_wfopen` and never checks the result, so a
  missing `Logs` directory turns into a null-pointer fault deep inside the CRT at
  the end of a scan. `pkusb.dll` catches that open and redirects it; if you see a
  crash at `0x34` in `_lock_file`, this is what it was.

### Hardware safety

> [!WARNING]
> This scanner has been out of production for over a decade. Parts do not exist.
> The rules below are enforced in `server/pakonusb.py`, in the request path — but
> if you write your own tooling against it, they are on you.

- **Never write the EEPROM.** It holds per-unit calibration that cannot be
  regenerated. On `wIndex 0x1234` only known reads are allowed through; anything
  else is dropped and logged.
- **The LED current ceilings depend on the board AND on whether IR is lit.** On
  a `0x24` F-135 the firmware's own limits are R8/G8/B8/IR8 with IR on and
  R6/G8/B8/IR0 with IR off — a *quarter* of the `0x44` board's G and B limits.
  Apply the wrong row and you permit 2.5× the vendor's own ceiling on two
  channels. `server/pakonusb.py` keys on the probed board and the last lamp
  write, and takes the strictest row of all when either is unknown. LED wear is
  this hardware's known failure mode, and pushing current does not help — at an
  open gate the sensor is already saturated at 2–3.
- **The motor stop order is `rate=0 → go → idle`.** A bare stop command does not
  halt the drive. Lamp-off does not halt it either.
- **Do not flash the PICs**, and know that the path is closer than it looks: the
  bootloader that can erase PIC flash is reachable over *this same command
  channel* — a type-4 packet to `0x46` with the right command bits is a 64-byte
  row erase, and a real unit lost a row of its motor controller's firmware that
  way. The server **reports** any write aimed at `0x22`/`0x26`/`0x42`/`0x46` but
  deliberately does not block it: TLB probes those same addresses at start-up,
  and blocking discovery to defend against Kodak's own software reflashing its
  own hardware is the wrong trade. The 8051 application firmware genuinely does
  live in RAM and cannot be bricked — the PICs are the part that can.
- **Do not tape the film gate open.**

---

## Usage

```sh
./run.sh                  # start the USB server if needed, then the client
./run.sh trace            # decoded live view of the hardware conversation
./run.sh log              # tail the server, client and the OEM's own logs
./run.sh stop             # stop both
./run.sh doctor           # check prerequisites and OEM files
./run.sh doctor --install # ...and install the missing ones
```

Environment overrides, all optional:

| variable | meaning |
|---|---|
| `PAKON_WINE` | path to the wine binary |
| `PAKON_INSTALL` | the OEM install directory |
| `PAKON_SCANS` | where drive `P:` points (default `~/Desktop/pakon-scans`) |
| `PSIX_FIRMWARE_DIR` | directory holding your `pakon7.hex` (default `~/.local/share/psix/firmware`) |
| `PAKON_ERRHOOK=1` | hook TLB's internal error reporter (patches OEM code in memory) |
| `PYTHON` | the interpreter for the server |

---

## Debugging

`./run.sh trace` gives one merged, decoded stream — every PPB packet in plain
language (`WRITE PICL LAMP visible+IR`, `LED CURRENT B=3 IR=2 R=2 G=2`,
`MOTOR GO fwd`), the ring state, and TLB's own internal errors by name. If a
scan misbehaves, this is where you look first: the failure is usually visible as
a missing packet rather than as an error message.

`PAKON_ERRHOOK=1 ./run.sh` additionally hooks TLB's internal error reporter, so
failures that never reach a dialog print as they happen. It patches OEM code in
memory, which is why it is off by default.

---

## Configuration

TLB keeps its settings in the registry, and there are two kinds. Getting this
distinction right is what makes the project usable on a scanner other than the
one it was developed on.

**Per-unit, measured on your machine — never shipped, never copied:**

- `ScannerSerialNumber`, `ScannerType`, `ScannerVersionHw` — TLB reads these
  from your unit's own EEPROM
- every `Scan\DpiBase<N>_35` key — `MotorSpeed*`, `MotorAdjust*`, `Offset`,
  `StepperLens`, `StepperCCD`, also from the EEPROM
- each scan mode's ~23 light values — `Current_*`, `DutyCycle*`,
  `DutyCycleOpenGate_*`, `Gain_*`, `Offset_*` — derived by the LED servo when
  you run *Scan → Light Correction*. **TLB writes these itself.** `setup.sh`
  never creates a mode key, because creating one with a single value in it
  convinces TLB it is already configured and it never writes the rest.

**Software configuration, identical on every F-135 — shipped as
`setup/base-config.reg`:** `Scan\Test`'s driver flags (`TlaControlLeds`,
`SenseFilm`, `DriverSimultaneousPackets`, `UseFixedPatternCorrection`,
`WaitForLamp_*`), the 65 `ColorKodak` entries, and a few paths under
`TLB`/`TLX`. Kodak's installer writes these; this project copies files out of
that installer rather than running it, so it supplies them instead.

#### Is base-config.reg actually necessary?

Unresolved, and worth resolving before anyone leans on it. It was added while
chasing an initialisation failure that turned out to be a stale USB handle, so
its contribution was never isolated. Against it being needed: TLB persists its
own defaults for missing values, and it demonstrably creates all 18 scan-mode
keys unaided. For it: a client run with no Pakon keys at all created only
`TLX` and `TLXClientDemo\General`, not `TLB\Scan\Test` or `ColorKodak` —
though that run had no scanner and may have died before reaching the code that
writes them.

To settle it, in a throwaway prefix so your working one is untouched:

```sh
WINEPREFIX=~/pakon-test PAKON_SKIP_BASE_CONFIG=1 ./run.sh install
WINEPREFIX=~/pakon-test ./run.sh
```

If the client initialises, the file is unnecessary and should be deleted — which
also restores this project to shipping nothing of Kodak's but code it fetches on
your behalf.

---

## Portability

Nothing here is hardcoded to one scanner or one build of the OEM software.
Everything that could differ is discovered at runtime:

- **Ring geometry** — packet count, packet size, threshold, data pointer — read
  from the control block TLB itself fills in, validated against its `0x38` magic
- **PPB controller addresses** — TLB probes for the PICM at `0x44`/`0x46`/`0x24`/
  `0x26` with PICL = PICM − 4, and the decoder learns which pair this board
  answers on
- **Line period and phase** — 3-channel vs 4-channel, calibration vs scan
  geometry — measured from the hardware line-sync markers on every scan
- **TLB's internal error reporter** — located by call-count, not by address: it
  has ~834 call sites, an order of magnitude more than anything else sharing its
  prologue
- **Light calibration** — derived by TLB's own servo, never supplied by us
- **Firmware** — uploaded from your own HEX at start-up
- **Scanner identity** — serial, type, hardware version — read from the EEPROM

The SHA-256s in `setup/manifest.json` come from one working install, as a
courtesy. A different version is reported but never treated as an error; there
is no canonical build of this software.

The one fixed thing is the *structure* of TLB's ring control block — the field
offsets within its first page. That is a driver ABI, so it should hold across
builds, and it is validated at runtime: if the magic or the data pointer do not
check out, the shim says so rather than corrupting memory.

---

## Tests

```sh
./tests/test_all.sh   # everything; no hardware required
make -C src test      # just the ring protocol
```

`tests/ringtest.c` is the one worth reading. Its consumer is transcribed from the
disassembly — the `TransferInProgress` spin, the `MustWait` predicate, the bit-0
line-marker check — so it holds the shim to TLB's *actual* conditions rather than
to someone's idea of them. Three of the bugs that made scanning impossible were
caught by exactly those assertions.

---

## FAQ

#### It hangs on "Corrections" and never gets anywhere.

Historically that was `TransferInProgress` never being set in the ring header,
which the shim now handles. If it still happens, run `./run.sh trace` — a stall
with no packets moving is a different fault from a stall with the LED servo
still stepping.

#### `EC_DRV_LostSync (1003)` in the middle of a scan.

The image stream must stay continuous across triggers. If you have modified the
server, check that `arm_stream()` does not clear or realign the buffer while a
transfer is already running.

#### Can I use this with the F-135 Plus, or a board that answers on `0x44`?

Probably — nothing is hardcoded for either — but neither has been tried. Reports
are genuinely useful.

#### Why not just reimplement the whole thing?

Because Digital ICE and the Ansel colour engine are the reason to own this
scanner, and they are 20 years of work you cannot rewrite in a weekend. The
driver is five calls. Rewriting the small part is the lazy option.

---

## Related work

This stands on a lot of prior reverse engineering, and vendors none of it:

- [ktkaufman03/FX35](https://github.com/ktkaufman03/FX35) — the Windows driver
  work that established the endpoint map

[docs/PROTOCOL.md](docs/PROTOCOL.md) documents how the hardware actually works;
[docs/SETUP.md](docs/SETUP.md) has the long-form install and a troubleshooting
table.

---

## Contributing

Issues and PRs welcome, especially:

- reports from other F-135 units, other board revisions, and the F-135 Plus
- reports from other builds of the OEM software (hash mismatches are expected —
  say what worked)
- anything in [Gotchas](#gotchas) that turns out to be wrong on your machine

Please do not open PRs that add Kodak or Pakon files to this repository. They
will be closed.

Every non-trivial change should keep `./tests/test_all.sh` green.

---

## Licence

MIT for this project's own code — see [LICENSE](LICENSE), including its note on
the Kodak/Pakon software, which is not covered and is not distributed here.
Protocol details in `docs/` were determined by observing and disassembling that
software for interoperability only.
