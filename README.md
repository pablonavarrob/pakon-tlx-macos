# Pakon F-135 on macOS

Run the **real Kodak TLX Client** — Kodak's own colour science and Digital ICE —
against a Pakon F-135 on macOS, including Apple Silicon. No Windows VM.

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

The scanner's software is 32-bit Windows XP era and needs a kernel-mode i386
driver that can never load on Apple Silicon. Instead of rewriting the software,
this replaces **only** the driver. Five calls are its whole surface:
`CreateFileW`, `DeviceIoControl`, `ReadFile`, `GetOverlappedResult`, `CancelIo`.
Everything above them is Kodak's, running unmodified.

**What that buys you:** exact Kodak output — Digital ICE, the Ansel colour
engine, framing, DX decoding — 16-bit planar RAW, and no VM. **What it costs:**
you need the OEM software and Wine.

---

## Status

Works. Developed on a base F-135 (`0x24` board), since confirmed by other owners
on an **F-135 Plus** (`0x44` board) and a second base unit. Nothing is hardcoded
per model. F-235 and F-335 are untried.

---

## Specifications

Measured from the hardware or read out of the OEM stack.

| | |
|---|---|
| **Scanner** | Pakon F-135 and F-135 Plus, 35 mm |
| **Host** | macOS 15+, Apple Silicon or Intel. Wine 11 (WoW64), Rosetta 2 on ARM |
| **USB** | 2.0 high speed. `0f05:f235` cold, `0f05:f135` with firmware loaded |
| **Endpoints** | bulk OUT `0x01`, bulk IN `0x81` (commands), bulk IN `0x86` (image) |
| **Driver surface** | 5 Win32 calls, 2 IOCTLs (`0x222059` EP0, `0x222090` bulk) |
| **Sensor** | trilinear CCD, 2000 px per line, R/G/B on separate rows |
| **Line format** | 6000 words RGB, 8000 words RGB+IR, 6108 in calibration. Sync = bit 0 of each line's first sample |
| **Resolutions** | Base 4 → 1500×1000 · Base 8 → 2100×1400 · Base 16 → 3000×2000 |
| **Output** | 16-bit planar RAW. 16-byte header (size, width, height, bit count), then whole channel planes. Bit count 48 = RGB, 64 = RGB+IR |
| **Sample range** | ~0–4095 with colour correction on (clips), ~0–11800 with it off. Never full scale |
| **Image ring** | 409 packets × 20480 bytes ≈ 8 MiB, packet-indexed |
| **Digital ICE** | Kodak `DMLDICELib`. Needs the IR channel captured at scan time |
| **Firmware** | uploaded to 8051 RAM at every power cycle, from your own HEX |
| **Calibration** | per-unit. Read from the scanner's EEPROM, derived by the LED servo. The EEPROM is never written |

---

## Install

```sh
git clone https://github.com/pablonavarrob/pakon-tlx-macos
cd pakon-tlx-macos
./run.sh install
```

About a minute. It installs `wine-stable`, `mingw-w64` and `libusb` via Homebrew,
builds a `.venv`, creates the Wine prefix, downloads the OEM stack and your
firmware, builds `pkusb.dll`, registers the COM servers, patches two import
strings, and creates the directories the OEM install is missing.

Then power on the scanner and run `./run.sh`.

Re-running `install` is safe. Nothing goes on your `PATH`; nothing of this
project's is installed system-wide.

**Requirements:** macOS on Apple Silicon or Intel · Python 3.10+ · a real USB
cable · your own copy of the OEM software and `Pakon7.hex`.

If your `python3` is an old system one:
`PYTHON=/opt/homebrew/bin/python3 ./run.sh install`

#### Where the OEM files come from

This repo contains no Kodak code, binaries or firmware. Either point at a copy
you already have:

```sh
./run.sh install --from ~/my-own-copy     # a folder containing "F-X35 COM SERVER"
```

or let it fetch from a public archive of the F-X35 distribution
(`github.com/plonsker/pakon-scanning-software` by default, `PAKON_OEM_URL` to
change it). It pulls ~50 MB with a sparse clone.

> [!WARNING]
> Those DLLs then execute under Wine as you, and nothing verifies them.
> `setup/manifest.json` has the SHA-256 of each file from a known-good install,
> but `install` only checks they exist. Run `./run.sh doctor` and read the hash
> column before the first launch. Prefer `--from` with a copy you trust.

If firmware ends up missing, copy `Pakon7.hex` into
`~/.local/share/psix/firmware/` by hand — the OEM package hides it under
`FX35Driver/`.

**If something goes wrong:** `./run.sh doctor` lists what is present, what is
missing, and whether the scanner is on the USB bus.

---

## Back up your EEPROM first

Your scanner has a small EEPROM holding its serial number, optical offsets,
motor speeds and 60 colour-matrix floats. It was written at the factory.
**It cannot be regenerated and no other unit's copy helps you.**

```sh
# power-cycle the scanner first, then:
./run.sh stop                              # the dump tool needs the USB interface
./.venv/bin/python tools/eedump.py
```

You get `/tmp/eeprom.bin` and four section copies beside it. Copy them off this
machine.

**Do it twice, power-cycling in between, and compare the files.** Reads have been
seen to disagree between runs. Two matching dumps from separate power cycles are
trustworthy; if they differ, dump again.

**A CRC mismatch on one bank with the other bank intact is normal.** Kodak stores
everything twice for exactly that reason and the OEM software falls back
silently. It is not a fault and not a reason to write anything.

The tool only ever reads. Nothing in this project writes the EEPROM.

---

## Scanning

1. **Power on the scanner first.** Firmware lives in RAM and is uploaded at every
   power cycle.
2. `./run.sh` and wait for the client window. It opens behind the terminal.
3. **First time only:** *Scan → Light Correction*, gate **empty**. This measures
   your unit. Skip it and the lamp produces nothing usable.
4. Pick a resolution. Base 16 is 3000×2000.
5. For Digital ICE, tick **Scratch Removal in the Scan dialog** — not at save
   time. See [Gotchas](#gotchas).
6. Press **Scan with the gate empty**, then feed the strip when the transport
   pulls. **Emulsion up, head first. 4–6 frames.**
7. Save to **`P:\`**. For 16-bit: *Save Settings → To Client Memory, Planar, Add
   File Header*.

### Where your files land

`~/Desktop/pakon-scans` (override with `PAKON_SCANS`). `run.sh` points both
`P:\` and `C:\Temp` there, so the client's hardcoded default already lands
somewhere you can see. Wine writes straight through the symlink — nothing copies
anything.

If `drive_c/Temp` already had files, `run.sh` leaves it alone and says so. Save
to `P:\` in that case.

---

## Gotchas

All of this is the OEM software's behaviour, not this project's.

### Film

- **Emulsion up, head first.** The scanner detects both and refuses:
  `Film Emulsion Down (0x10000000)`, `Film Tail First (0x20000000)`, or
  `0xF0000000` for both at once.
- **4–6 frames per strip.** Longer exceeds `MaxFilmLength`: the transport halts
  mid-strip and the save stage then discards the whole roll with
  `EC_FilmInGuides (129)`. You scan it and get nothing. `MaxFilmLength` can only
  be raised through an API the demo client never calls.
- **Gate empty when you press Scan.** Feeding first is how you jam it.
- If it jams: stop the client, advance the film out by hand, don't pull against
  the drive.

### Client settings

- **Digital ICE is a scan-time setting.** *Scratch Removal* in the Scan dialog
  turns the IR LED on. Ticking it at save time does nothing — the IR channel was
  never captured. `LED CURRENT ... IR=0` in the trace means no ICE.
- **`SaveToDisk` is 8-bit only**, whatever you set `iColorBits` to. The only
  16-bit route is *To Client Memory* + *Planar* + *Add File Header*.
- **Never hand-edit the light calibration.** `Current=1`, `DutyCycle=0.000000`
  on a fresh install are placeholders; `FullLightCorrections=1` makes TLB derive
  the real values itself. Filling in numbers that look sensible gives you pure
  white frames.
- Wrong output size means the wrong Base. 2100×1400 is Base 8.

### The file you get

- **Planar, not interleaved.** 16-byte header of four `uint32` LE — header size,
  width, height, bit count — then each channel plane in full as `uint16` LE.
- **Samples never fill the 16-bit word.** 0–4095 with corrections on (clips
  there), 0–11800 with them off. Don't assume 65535 *or* 4095. Scale from the
  data.
- **Corrections on = already a positive.** Corrections off = a negative. Unsure?
  Correlate against the `.bmp` the client wrote for the same frame.

### Wine and macOS

- **Homebrew's Wine is not on your `PATH`** — it lives inside
  `Wine Stable.app`. `run.sh` finds it. `PAKON_WINE` overrides.
- **Use a normal 64-bit prefix**, not `WINEARCH=win32`. Wine 11 runs 32-bit code
  through WoW64.
- **32-bit apps read `HKLM\Software\WOW6432Node\...`** — if registry edits seem
  to do nothing, you're editing the 64-bit view.
- **Power-cycled the scanner? Restart the bridge.** `./run.sh stop`, then
  `./run.sh`. It reconnects on its own now, but a client that fails in a
  *fraction of a second* is talking to a dead handle, not to the scanner. A
  failure after ~10 s of real traffic is something else.

### If the OEM files came from a git repo

- **Empty directories are missing.** Git doesn't track them, and several of
  Ansel's capability folders ship empty. PakonImau then aborts with only
  `Can't open install directory!`. `setup.sh` recreates them.
- **`reg import`, never `regedit /S`.** The latter silently drops every key when
  the file has a BOM, and reports success.
- **`FullLightCorrections` is write-to-request, not a stored flag.** TLB replaces
  your `1` with a completion timestamp once the correction has run. A key holding
  `0x6a72df76` is done, not corrupt. Forcing it back to `1` redoes the whole
  correction.

### Hardware safety

> [!WARNING]
> Out of production for over a decade. Parts do not exist. These are enforced in
> `server/pakonusb.py`, in the request path — if you write your own tooling, they
> are on you.

- **The EEPROM is never written.** On `wIndex 0x1234` only known reads pass.
  [Back it up](#back-up-your-eeprom-first) anyway.
- **LED ceilings depend on the board *and* on whether IR is lit.** A `0x24`
  F-135 allows R8/G8/B8/IR8 with IR on, R6/G8/B8/IR0 without — a quarter of the
  `0x44` board's G and B limits. The server picks the row from the probed board
  and the last lamp write, and uses the strictest row when either is unknown.
  LED wear is how these units die, and more current doesn't help: at an open gate
  the sensor already saturates at 2–3.
- **Motor stop order is `rate=0 → go → idle`.** A bare stop does not halt the
  drive. Neither does lamp-off.
- **Don't flash the PICs.** The bootloader that can erase PIC flash is reachable
  over this same command channel — a type-4 packet to `0x46` with the right bits
  is a 64-byte row erase, and one real unit lost a row of its motor firmware that
  way. The server logs any write to `0x22`/`0x26`/`0x42`/`0x46` but does not
  block it, because TLB probes those addresses at start-up. The 8051 application
  firmware is in RAM and can't be bricked; the PICs can.

---

## Usage

```sh
./run.sh                  # start the bridge if needed, then the client
./run.sh trace            # live decoded view of the hardware conversation
./run.sh log              # tail the bridge, the client, and Kodak's own logs
./run.sh stop             # stop both
./run.sh doctor           # check everything, including the USB bus
./run.sh doctor --install # install missing prerequisites
```

| variable | meaning |
|---|---|
| `PAKON_SCANS` | where `P:` points (default `~/Desktop/pakon-scans`) |
| `PAKON_WINE` | path to the wine binary |
| `PAKON_INSTALL` | the OEM install directory |
| `PSIX_FIRMWARE_DIR` | where your `Pakon7.hex` lives |
| `PAKON_ERRHOOK=1` | hook TLB's internal error reporter (patches OEM code in memory) |
| `PYTHON` | interpreter for the bridge |

**Debugging.** `./run.sh trace` decodes every packet in plain language
(`WRITE PICL LAMP visible+IR`, `LED CURRENT B=3 IR=2 R=2 G=2`, `MOTOR GO fwd`),
plus the ring state and TLB's own errors by name. Failures usually show up as a
*missing* packet rather than an error.

---

## Configuration

TLB keeps its settings in the registry. Two kinds, and the split is what makes
this work on a scanner other than the one it was built on:

| | where it comes from |
|---|---|
| serial, type, hardware version | your unit's **EEPROM** |
| `DpiBase*` motor speeds, adjust words, `StepperLens`, `StepperCCD` | your unit's **EEPROM** |
| ~23 light values × 18 scan modes (`Current_*`, `DutyCycle*`, `Gain_*`, `Offset_*`) | the **LED servo**, during Light Correction |
| 114 software flags (`Scan\Test`, the 65 `ColorKodak` entries) | shipped as `setup/base-config.reg` |

**TLB writes the per-unit values itself.** `setup.sh` never creates a scan-mode
key — creating one with a single value in it convinces TLB it is already
configured, and it never writes the other 22.

The shipped file is Kodak's configuration, not ours, and carries no unit data.
Kodak's installer would write it; we copy files out of that installer rather than
running it. **Whether it is actually needed is unresolved** — to test, in a
throwaway prefix:

```sh
WINEPREFIX=~/pakon-test PAKON_SKIP_BASE_CONFIG=1 ./run.sh install
WINEPREFIX=~/pakon-test ./run.sh
```

If the client initialises, the file can be deleted.

---

## Portability

Nothing is hardcoded per unit or per OEM build. Discovered at runtime:

- **ring geometry** — from the control block TLB fills in, validated against its
  `0x38` magic
- **controller addresses** — TLB probes `0x44`/`0x46`/`0x24`/`0x26`, PICL =
  PICM − 4; the decoder learns which pair answers
- **LED ceilings** — the firmware's own table, indexed by probed board and lamp
  state
- **line period and phase** — measured from the sync markers on every scan
- **TLB's error reporter** — found by call count (~834 sites), not by address
- **light calibration** — TLB's servo
- **firmware, scanner identity** — your HEX, your EEPROM

That is why one build runs on both a base F-135 and a Plus with no model flag.

The only fixed thing is the *structure* of TLB's ring control block — a driver
ABI — and it is validated at runtime before use.

---

## Tests

```sh
./tests/test_all.sh   # everything, no hardware needed
make -C src test      # just the ring protocol
```

`tests/ringtest.c` is worth reading: its consumer is transcribed from the
disassembly, so it holds the shim to TLB's real conditions. Three of the bugs
that made scanning impossible were caught by those assertions.

---

## FAQ

**The client fails instantly with `WTO_InitializeError`.**
In under a second, it's a dead USB handle — you power-cycled the scanner while
the bridge was running. `./run.sh stop`, then `./run.sh`. Failing after ~10 s of
real traffic is a different problem: check `/tmp/pakonusb.log` for the last
packet.

**It hangs on "Corrections".**
Run `./run.sh trace`. A stall with no packets moving is a different fault from
one where the LED servo is still stepping.

**`EC_DRV_LostSync (1003)` mid-scan.**
The image stream must stay continuous across triggers. If you've modified the
bridge, check `arm_stream()` isn't clearing the buffer during an active transfer.

**Does it work on the F-135 Plus?**
Yes, confirmed by another owner. The controller pair is probed and the LED
ceilings are chosen to match. F-235 and F-335 are untried.

**Why not reimplement the software?**
Digital ICE and the Ansel colour engine are why you own this scanner, and they're
20 years of work. The driver is five calls. Replacing the small part is the easy
one.

---

## Related work

Several people are working on these scanners from different angles:

- **[alibosworth/pakon-reference](https://github.com/alibosworth/pakon-reference)**
  — implementation-agnostic reference for the protocol, image stream,
  calibration and colour, plus per-unit data and safety. Start here for *how the
  hardware works*.
- **[gazzdingo/pakon-mac](https://github.com/gazzdingo/pakon-mac)** — a full
  userspace reimplementation with its own colour pipeline, plus PIC firmware and
  EEPROM repair tooling.
- **[ktkaufman03/FX35](https://github.com/ktkaufman03/FX35)** — the Windows
  driver work that established the endpoint map.
- **[veroc/psix](https://github.com/veroc/psix)** — the firmware-load sequence
  and line-sync framing method used here both follow psix.

This project takes the narrowest slot: host Kodak's DLLs, replace the driver.
Worth having both approaches for preservation.

Also: [docs/PROTOCOL.md](docs/PROTOCOL.md) for the hardware,
[docs/SETUP.md](docs/SETUP.md) for the long-form install and troubleshooting.

---

## Contributing

Wanted, especially:

- reports from other units and board revisions — F-235 and F-335 have never been
  tried, nor any OEM build other than the one in `setup/manifest.json`
- hash mismatches from a different OEM build: say what worked
- anything in [Gotchas](#gotchas) that's wrong on your machine
- settling the `base-config.reg` question above — needs one run on real hardware

No PRs adding Kodak or Pakon files. Keep `./tests/test_all.sh` green.

---

## Licence

MIT for this project's code — see [LICENSE](LICENSE), including its note on the
Kodak/Pakon software, which is not covered and not distributed here. Protocol
details in `docs/` came from observing and disassembling that software for
interoperability only.
