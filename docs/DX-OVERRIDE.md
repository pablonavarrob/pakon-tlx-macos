# DX override: override the values detected from the film edge DX code

Supplies the film's product code and frame numbers from a config file, in the
format a working DX sensor would have produced. The scanner is never written
to and no OEM binary is modified.

Useful when there is no DX code to read:

- **the film's edge carries no barcode.** Motion-picture stock respooled into
  cassettes is the clear case: Vision3 and its kin carry KEYKODE edge
  markings, not a DX barcode. Whether a given still film has one is a
  property of that emulsion, so check your own stock rather than assuming;
- **the barcode is there but unreadable:** a dead or dirty DX sensor, or
  damaged or badly fogged film edges.

The edge barcode is a latent image on the film itself, and is not the same
thing as the DX contacts on the cassette that a camera reads. Bulk-loading
into an unnotched cassette loses the contacts but not the edge barcode. For
the structure of the edge code itself, see
[35mm-dx-edge-code](https://github.com/alibosworth/35mm-dx-edge-code).

`server/dxsynth.py` ("synth" is the mechanism: it synthesises the sensor
replies), hooked into `pakonusb.py`. Off unless its config file exists.

## The problem it solves

35 mm film carries a DX barcode along the edge. The scanner reads it with an
IR sensor and the OEM stack uses it for two things: the **film product and
generation code**, which picks the ISO and the film-specific processing in
Ansel, and the **frame number of every picture**.

With nothing to read, every frame is labelled `DX_Error`, the scan warning
`SCANW_DX_BAD` is raised, and — the part that actually hurts — every frame's
default filename is identical, so **SaveToDisk writes a single file for the
whole strip**. The usual workaround is renumbering every frame by hand in the
client before saving.

The scanner is not the only place the answer can come from. Every command
between the OEM stack and the scanner passes through `pakonusb.py`, so the
sensor reply can be rewritten on its way back to carry a decoded DX entry
built from your config. The engine then labels the frames, saves one file
each, and processes for the film you named.

## Quick start

```sh
mkdir -p ~/.local/share/psix
cp docs/dx-override.conf.example ~/.local/share/psix/dx-override.conf
```

Set `start` to the number printed on the film next to the strip's first
frame, and `product` / `generation` to your film (or delete them: see below).
Restart the server once so it picks up the module, then scan as usual. The config is re-read at
the start of every scan, so between rolls you only edit the file.

To switch it off, delete or rename the config. The next scan passes through
untouched, no restart needed.

## Config

`~/.local/share/psix/dx-override.conf`, or wherever `PAKON_DX_CONFIG`
points. (`dx.conf` is still read if it is there, from before the feature had
its name, with a line in the log saying so.)
`key = value`, one per line, `#` comments.

```
override_numbers = yes   # supply the frame numbers.  The main switch; `no` leaves
                         # the labels to the client and overrides only the film type
start = 1                # label of the scan's first frame: 1, 1A, 5 ...  Frames carry
                         # on across the strips of one scan; a new scan starts here
product = 79             # DX part 1, 0..127.  Optional, but all or nothing with the
generation = 11          # DX part 2, 0..15.   next line: set both or neither
```

Three more exist for tuning, and should stay unset unless you are chasing a
specific symptom: `pitch` (position counts per frame, otherwise worked out
from the scan width), `film_lead` (where the film's leading edge is reported)
and `a_flag` (which flag marks a half-frame code). See
[If the labels are wrong](#if-the-labels-are-wrong).

Every setting has a working default: an empty file numbers the frames from 1
with no film type asserted.

A commented version to copy is
[`dx-override.conf.example`](dx-override.conf.example).

What each combination does:

| Config | Effect |
|---|---|
| no file | inert, nothing logged |
| empty file, or `start` only | frame numbers only; the entries carry product 0 / generation 0, which the client then shows as Product 0 / Specifier 0 |
| `product` + `generation` | film type and frame numbers |
| `override_numbers = no` + a film code | film type only; the frame labels stay the client's |
| `override_numbers = no`, no film code | trace only: every sensor reply logged raw, nothing rewritten |

**Frame numbers across strips.** They carry on within one scan (strip 1 gets
1–4, strip 2 gets 5–8), because the transport does not stop between strips
and they are therefore consecutive pieces of one roll — which is what a
binder page holds after a whole roll is scanned and then cut. Stopping the
scan resets to `start`, which is what happens between rolls. There is nothing
to configure for this.

**Frame spacing and resolution: nothing to set.** The scan trigger the OEM
software sends (`0x91`, SetScanLineParams) carries a value naming the
configuration it is about to run, and the module reads it. That gives the
engine's divisor and the frame spacing directly, before the pass starts, for
every combination of resolution and Digital ICE:

| Resolution | Digital ICE | `0x91` | divisor | frame pitch (counts) |
|---|---|---|---|---|
| Base 4 | off | `0x0107` | 4 | 1620 |
| Base 4 | on | `0x00c5` | 8 | 3240 |
| Base 8 | off | `0x0075` | 6 | 2430 |
| Base 8 | on | `0x004d` | 12 | 4860 |
| Base 16 | off | `0x003c` | 8 | 3240 |
| Base 16 | on | `0x0031` | 16 | 6480 |

Each value was read from a labelled capture of the OEM software running that
configuration, and three of them match an independent OEM Windows capture of
the same unit. The pitch is `405 x divisor`, which is the engine's own
`width x 38400 / 23700` restated, and every one is within about 1% of a figure
proven by a scan that numbered correctly.

Earlier versions worked the spacing out from the image stream's line size
instead. That is abandoned: the calibration pass's channel count does not
reliably match the imaging pass that follows (the engine toggles the IR lamp
partway through calibration), so the line size measured there could not tell
Base 16 from Base 8 with ICE. The trigger says what the scan is, before it
starts, and does not have to be inferred.

If a scan reports a `0x91` value not in the table, the module says so in the
log and asks for the configuration to be recorded, then falls back to the Base
4 pitch default. That is the one case where `pitch` may need setting by hand
(the lead defaults to 0, which is right at every configuration).

Digital ICE doubles both the pitch and the divisor: the transport runs at half
speed for the IR pass, so a frame spans twice as many counts. The divisor is
not a guess — divide the film-start position by the `FilmFoundDx` line in
`DxCode.txt` and it falls out of any scan.

The engine rejects the numbering outright if the spacing is more than about an
eighth out, so another configuration's value will not do. If a configuration
ever needs measuring, `DxCode.txt` prints both the divisor (as the film-start
position over `FilmFoundDx`) and the picture spacing, and the pitch is their
product.

**`lead`** is how many half-frames early the numbering starts, so that the
number in the config lands on the first picture. With the codes anchored to
the film edge the module needs no lead at any configuration (the edge-to-
picture distance is carried by the anchor, and it is the same fraction of a
pitch at every resolution), so it defaults to 0. If a unit's labels come out
a consistent half-frame off, set `lead` to 1.

**Short strips may not number at all.** The engine accepts numbering only
when it finds at least three consecutive codes it did not have to interpolate,
and the F-135 service manual gives the matching hardware requirement: "to
ensure DX code reading, the strips must be a minimum of 4 frames". A shorter
strip can leave too few codes whatever this module sends, so a 3-frame strip
may keep its `DX_Error` labels.

**Which label lands on which picture.** Codes go out every half frame, as
they are on real film, so they run 1, 1A, 2, 2A … and your pictures, a full
frame apart, take every second one: `start = 1` gives pictures 1, 2, 3, while
`start = 1A` gives 1A, 2A, 3A. Half-frame rolls and phase corrections are
what the second form is for.

## Film codes

Entries that exist in the OEM's 2006 product table:

| Film | product | generation | ISO |
|---|---|---|---|
| Kodak ISO 100 (Gold/Max class) | 78 | 1 | 100 |
| Generic Kodak ISO 160 (Portra 160 stand-in) | 79 | 11 | 160 |
| Kodak ISO 200 | 78 | 2 | 200 |
| Kodak ISO 400 | 78 | 0 | 400 |
| Kodak Royal Gold 400 | 95 | 3 | 400 |
| Fuji Superia 100 | 35 | 14 | 100 |
| Fuji Superia 400 | 35 | 2 | 400 |
| Fuji Superia 800 | 35 | 1 | 800 |

The table was last revised in 2006, so modern films are mostly absent (Portra
encodes as 95/14, which is unallocated) and fall back to a default ISO. To
look up what a given film actually encodes, see
[35mm-dx-edge-code](https://github.com/alibosworth/35mm-dx-edge-code) for the
code structure and [The Big Film
Database](https://thebigfilmdatabase.merinorus.com) for community-decoded
entries including modern emulsions. Pick a
listed entry at the right speed. Vision3 has no code at all, so choose by ISO
as well: 250D is nearest 78/2 or 79/11, 500T nearest 78/0. If you export the
`.raw` planar file, Ansel is bypassed entirely and only the frame numbers
matter.

## How it works

**The engine does not poll the DX sensor.** It reads it when the scanner
raises a service flag (`POLL HOST` reply bit 0x80, then `READ PICL status`
bit 0x02, then a service acknowledge, then the sensor read). A sensor that
decodes nothing raises it about once per scan, and only one code fits in a
reply, so one read means one frame. The module therefore raises the flag
itself and the engine reads again. How often is measured, not configured:
every reply carries the scanner's position counter, two of them give its
rate, and the interval is set to two reads per code slot, clamped to
0.15–1 s. So it follows the transport speed without being told the
resolution. The only write this provokes is the engine's own service
acknowledge.

**A decoded code has no position of its own.** The engine takes it from a
slot filled by a preceding type-5 event, whose flag bit picks the slot, and
the code's own byte must name the same slot. A code without one is discarded
before the frame table. That is why the module emits a type-5 position event
immediately before every code.

**Numbering needs a film start.** Before it looks at any code, the engine
requires a film-start position, recorded by a type-7 event with bit 1 of its
flag byte set. Without one it declares the DX unusable and never examines the
codes at all. The module sends one ahead of the first code of each strip. An
early film start only shifts which picture a code is attached to, so
`film_lead` is a tuning knob rather than a correctness one.

**Where the codes go: the film's own edge.** The scanner's position counter
is zeroed by the scan trigger, but the film arrives whenever the strip is
fed, so the counter alone says nothing about where the film is. The motor
controller reports the film edge as it passes the input sensor (a type-7/8
event pair; the type byte's high nibble distinguishes the stations, and only
`0x07`, the input sensor, is used). The module anchors the code grid there:
the code carrying `start` is placed a fixed fraction of a frame pitch past
the edge, so the same strip gets the same numbers on every scan regardless
of feed timing.

**If no edge is ever reported** -- a failing DX sensor may simply stay
silent -- the module waits three frame pitches and then falls back to a grid
laid from the counter's zero, and says so in the log. The phase of that grid
within the frame is whatever the feed timing made it, so the labels can be
offset by a frame or a half frame -- but every picture still gets a distinct
sequential number and therefore its own file. The alternative, no codes at
all, is DX_Error: every frame shares one default filename and saving the
roll writes a single file. Wrong numbers are a rename; lost files are lost.
An edge arriving after the fallback does not move the grid, since codes
already sent cannot be recalled.

**What the engine then requires** is at least three consecutive codes it did
not have to interpolate: spaced one half pitch apart within about an eighth,
frame values stepping by one, and the run starting on a half-frame ("A")
code. Missing codes are interpolated and surplus ones dropped, and each
picture takes the code whose position, converted to lines and shifted by the
fixed distance between the DX sensor and the CCD, falls inside the picture
the image framing found.

All of the above is from decompilation of the OEM engine `TLB.dll` 3.1.0.28
for interoperability, confirmed against live hardware. A protocol-level
writeup independent of any implementation is in the [Pakon F-X35
reference](https://github.com/alibosworth/pakon-reference).

## How far it is proven

| Part | Status |
|---|---|
| Wire format of the sensor reply and entry offsets | Confirmed from USB captures of a healthy command exchange |
| Product / generation | Proven live, twice: under Windows with a rewritten kernel driver (June 2026) and through this module under Wine (August 2026). The client shows the injected code and its "Product And Specifier" warning clears |
| Frame numbers | Proven live on an F-135+ at Base 4 (August 2026): DX Read good, a 4-frame strip labelled 1–4, one file per frame on save |
| Frame numbers, all six configurations | Numbering was reproduced at Base 4, 8 and 16 with Digital ICE off and on (2026-08-19), and the codes are anchored to the film edge the controller reports (2026-08-21), which makes the phase repeatable: Base 8 + ICE was confirmed anchored to 1,2,3,4, and Base 4 tracks it. Base 16 could not be confirmed anchored -- the DX sensor on the reference unit would not report an edge at that resolution -- but on that unit Base 16 falls back to the counter grid, where the numbers are still sequential |
| Frame numbers with no film code | Confirmed live: the client shows Product 0 / Specifier 0, an unallocated code, so the engine falls back to its default processing as it does when no code is read. There is no alternative source for the film type: the client's product and specifier fields are read-only |
| Multi-strip carry-over | Implemented and unit-tested; not yet confirmed on a physical multi-strip scan |

## If the labels are wrong

- **Still `DX_Error`.** Check the server log shows ` film start at N,` on the
  first code after the transport starts. Then get the engine's own table:
  set `DxCreateDebugFiles` to 1 under `…\Pakon\TLB\Scan\Test` (on a 64-bit
  host it must go in the 32-bit registry view), restart the client, scan, and
  read `Logs/DxCode.txt` in the OEM install directory. It prints every code
  the engine accepted with its line number, the good-code count, and the
  picture framing, which is far better than guessing.

  ```sh
  wine reg add 'HKLM\Software\Pakon\TLB\Scan\Test' /v DxCreateDebugFiles \
      /t REG_DWORD /d 1 /f /reg:32
  ```

  (`PakonDxLog.txt` is a different file and stays empty even on a successful
  scan.)
- **Every label is one frame off.** Swap `a_flag`.
- **Numbers are right but attached to the wrong pictures.** Raise
  `film_lead`.
- **Nothing in the log at all.** The config has `override_numbers = no` and
  no film code, so there is nothing to inject; or the server was started
  before the config file existed.

Watch the module's own lines with:

```sh
tail -f /tmp/pakonusb.log | grep --line-buffered "DX:"
```

## Is the sensor actually broken?

If you expected the film to have a code, the client's **Film Track Test**
(a menu item; needs DX-coded film in the gate) measures the voltage swing on
each DX sensor and writes `Logs/PakonFilmTrackTestLog.txt`. It names the
sensor at fault and distinguishes a dead sensor from a failed gain
calibration (`EC_DXBadSwing`), which the pot-adjust commands may recover
without any of this. The test runs the transport until it decides it is
finished; cancelling writes only a header.
