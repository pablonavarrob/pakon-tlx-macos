# Pakon F-135 — protocol notes

Everything here was determined by observing the OEM software's behaviour and
disassembling `TLB.dll` for interoperability. Addresses are for the build whose
SHA-256 is recorded in `setup/manifest.json`; if yours differs the offsets will
move, but the structures won't.

Nothing in this document is guesswork. Where something is unverified it says so.

---

## 1. The device seam

`TLB.dll` reaches the scanner through **one handle and five calls**:
`CreateFileW("\\\\.\\Pakon135")`, `DeviceIoControl`, `ReadFile`,
`GetOverlappedResult`, `CancelIo` (+ `CloseHandle`). Intercepting those five is
the entire driver surface.

Two IOCTLs, and only two:

| code | meaning |
|---|---|
| `0x222059` | EP0 vendor/class control transfer. 10-byte input struct: `direction, requestType(1=class,2=vendor), recipient, reserved, bRequest, pad, wValue:u16, wIndex:u16` |
| `0x222090` | bulk OUT EP `0x01`, then bulk IN EP `0x81` — the command/response channel |

Image data is **not** an IOCTL: it is `ReadFile` on bulk IN **EP `0x86`**, which
the OEM kernel driver names `RingRead`.

### Win32 semantics that are not optional

* `DeviceIoControl` **must complete synchronously** — return TRUE with the data
  already in the output buffer. Returning `FALSE`/`ERROR_IO_PENDING`, which
  `FILE_FLAG_OVERLAPPED` would normally imply, makes TLB log
  `EC_WIN_DeviceIoControl (165) 997` and abort. It must still signal
  `OVERLAPPED.hEvent` and leave the count in `InternalHigh`.
* `ReadFile` **must be asynchronous**: return `FALSE` and set
  `ERROR_IO_PENDING`. TLB ignores the return value and tests `GetLastError()`
  against `0x3E5` unconditionally; anything else yields `EC_WIN_FileRead (168)`
  with the debug string *"Driver ReadFile() returned TRUE for Overlapped
  operation"*.
* `GetOverlappedResult` **must return FALSE + `ERROR_IO_INCOMPLETE`** while the
  operation is still pending. Returning TRUE with a partial count tells TLB the
  transfer finished having delivered almost nothing.

---

## 2. PPB packets (the command channel)

    [type][len][addr][count][reg][payload...]

`type`: 1 = READ, 2 = WRITE, 3 = POLL, 4 = WRITE2 (`[4][3][addr][0][reg]`).
`len` = count + 3. `type` is 4 when count == 0, else 2.

Addresses are **probed at start-up**, not fixed (`0x1000afd0`): it pings `0x44`,
then `0x46`, then `0x24`, then `0x26`, and takes **PICL = PICM − 4**. On the
F-135 tested here the result is `device+0x2f8 = 0x24` (PICM) and
`device+0x2f9 = 0x20` (PICL), but a `0x44`-board variant gives `0x44`/`0x40`.

**Do not hardcode these.** The register tables below are therefore keyed on the
logical controller (HOST / PICL / PICM), and `server/ppb.py` learns the actual
pair from the traffic. Getting it wrong silently breaks trigger detection and
the motor stop.

Registers observed on the wire:

| addr | reg | meaning |
|---|---|---|
| PICL `0x20` | `0x02` | status; bit `0x80` = wants service |
| | `0x06` | service acknowledge |
| | `0x80` | **lamp**: bit0 visible, bit1 IR |
| | `0x81` | **LED current**, payload `[B, IR, R, 0, G]` |
| | `0x82` | exposure, 6×u16 `[B, IR, R, 0, G, period]` |
| | `0x8a` | arm pulse, part 2 |
| | `0x90` | DX code |
| | `0x91` | **trigger** — resets the line counter, EP6 stream starts |
| | `0x92` | scan stop |
| PICM `0x24` | `0x82` | indexed geometry/exposure (`idx4` offset, `idx5` offset+width, `idx6` integration, `idx9` mux) |
| | `0x84` | indexed A/D gain (idx 2/3/4) and offset trim (idx 5/6/7) |
| | `0xa0` | motor GO forward (WRITE2) |
| | `0xa2` | motor idle/stop (WRITE2) |
| | `0xa5` | motor rate, `[lo,hi]` u16 |
| HOST `0x10` | `0x84` | arm pulse, part 1 |

**Motor stop order matters**: `rate=0 → go → idle`. A bare stop does not halt
the drive.

`POLL HOST` returns 5 bytes: `[03][03][10][status][aa]`.

---

## 3. The scan-line ring — the thing that makes or breaks it

TLB allocates one buffer (`0x10028af0`) and posts **one** `ReadFile` over the
whole thing. The first page is a control block; the image data starts at
`base + 0x1000`.

| offset | field | owner |
|---|---|---|
| `0x00` | `0x38` — header size | app |
| `0x04` | total allocation size | app |
| `0x0c` | **packet count N** (observed: 409) | app |
| `0x14` | **Reading** — consumer tail, in packets | app |
| `0x18` | ToRead | app |
| `0x1c` | **Writing** — producer head, in packets | **driver** |
| `0x20` | NF | driver |
| `0x24` | **packet size** in bytes (observed: `0x5000` = 20480) | app |
| `0x28` | threshold, in packets (observed: 3) | app |
| `0x2c` | **`HANDLE` EventScanPacketReady** | app creates, **driver signals** |
| `0x30` | StopTransfer (byte) | app |
| `0x31` | **TransferInProgress** (byte) | **driver** |
| `0x32` | **OverFlow** (byte) | **driver** |
| `0x34` | data pointer = `base + 0x1000` | app |

`Reading` and `Writing` are **packet indices, not byte offsets.**

### What the driver must do

1. Set `+0x31 = 1` when the transfer starts, `0` when it ends.
2. Write whole packets at `data + Writing * pktsz`, then advance `+0x1c`
   modulo N.
3. `SetEvent(*(HANDLE*)(base+0x2c))` after advancing `Writing`.
4. Honour `+0x30` StopTransfer; raise `+0x32` on overrun; never fill the last
   slot — availability is computed mod N, so a completely full ring reads as
   empty.
5. **Do not complete the read.** Complete it only on `CancelIo`.

### Why (5) matters

The consumer's wait predicate (`0x1002f020`) is

    avail = (Reading >= Writing) ? N - Reading + Writing : Writing - Reading
    wait  = avail < threshold

`uiGetScanLines` (thread proc `0x1002f550`) waits on the handle at `+0x2c`. The
OVERLAPPED event means something different: when it fires, TLB does
`ResetEvent` → *"bScanStrips before CancelIo()"* → `CancelIo`
(`0x10029ea4`). **Completing the read tells TLB the scan is over.**

### The silent hang

`uiGetCorrections` (thread proc `0x1001cea0`) opens with

    while (!ring->TransferInProgress(+0x31) && !(stop & 2)) Sleep(1);

Nothing in TLB ever writes `+0x31` — it is the driver's. Until it is set, that
thread spins on its first statement for ever: no error, no timeout, no log
entry. The UI simply sits at "Corrections". This is the single most important
fact in this document.

### A real deadlock in TLB

`uiGetCorrections` computes `need = min(request, N - ToRead)` and loops until
`need <= (Writing - ToRead) mod N`. That value can never exceed `N - 1`, so a
request of N or more packets can never be satisfied. Its
`WaitForSingleObject` return value is discarded, so a timeout is
indistinguishable from success and there is no bailout.

### Line framing

The only content validation is **bit 0 of a 16-bit sample**, set by the firmware
on the first sample of each line (`0x1002ff12 test byte ptr [edx], 1`). A packet
with no marker is fatal: `EC_DRV_CannotFindStartOfScanLine (1001)`.

Line period: **6000 samples** for 3-channel RGB, **8000** for 4-channel RGB+IR,
**6108** during calibration (2036 px × 3). Dark-region LSB noise sets bit0 at
random positions too, so locate line 0 by the **dominant** position modulo the
period, not the first set bit.

`reg0x91` resets the scanner's **line counter**, not the byte stream. The OEM
issues a second trigger mid-transfer; clearing or re-aligning the byte stream
there costs you sync (`EC_DRV_LostSync (1003)`).

---

## 4. Illumination

There is **no light calibration in the EEPROM.** The 398-byte block is fully
accounted for: 3 ID dwords (hardware version, type, serial), 9 motor u16s, 60
colour-matrix floats, and 120 never-written bytes. Light settings live only in
`HKLM\Software\Pakon\TLB\Scan\DpiBase<N>_35\<mode>` and default to
`Current=1`, `DutyCycle=0.000000` — which the config getter then *persists*, so
a blank install stays blank and the lamp produces nothing.

`FullLightCorrections=1` makes TLB derive them itself.

### The LED-current servo (`0x1001e7b0`)

Per channel, per iteration:

    if (!settled && peak <= threshold && current < ceiling) current++;
    else settled = 1;

then re-send `reg0x81`/`reg0x82` and re-measure. Thresholds: R `0xFA00`,
G `0xFA00`, B `0xFFDC`, Ir `0x9C40`. It needs peak samples **from the ring**, so
it cannot run until the ring works.

Ceilings are hardcoded and board-dependent (`0x100203c0`), selected by the same
`device+0x2f8` byte the PPB probe sets:

| `+0x2f8` | IR | maxR | maxG | maxB | maxIr |
|---|---|---|---|---|---|
| `0x44` | yes | 8 | 24 | 24 | 8 |
| `0x44` | no | 4 | 20 | 20 | 0 |
| **`0x24` (F-135)** | yes | **8** | **8** | **8** | **8** |
| `0x24` | no | 6 | 8 | 8 | 0 |

There are **two** duty-cycle key sets, not one: `DutyCycleOpenGate_*` is used
with the gate empty and `DutyCycle_*` with film in it, selected by
`FN_bBeforeScan`. The two differ by the film's own base density — with-film =
open-gate x 10^D. Seeding only one set leaves the other at `0.000000`, i.e. zero
lamp drive on exactly the pass that matters. Letting TLB derive both via
`FullLightCorrections=1` avoids the trap entirely.

`SetLight` (`0x1002c5f0`) builds `reg0x82` as six u16
`[B, Ir, R, 0, G, period]`, each `round(base * duty_c)` with
`base = a8 * 1e6 / (2 * g)` and `period = clamp(a8, <= 0xFFD)`.

### Scan-control flags → device fields (`0x1002dd77`)

| `iScanControl` bit | field | meaning |
|---|---|---|
| `0x000008` | `+0x378` | **UseScratchRemoval — this is what turns the IR LED on** |
| `0x001000` | `+0x37c` | HasFilmDrag |
| `0x100000` | `+0x300` | UsePremiumColorPath (colour pipeline only; no effect on capture) |
| `0x000002` | `+0x380` | AggressiveFraming |

Without bit `0x8`, `bCalibrateFindLedCurrent` pre-marks IR as settled and its
current stays at 0 — no IR channel, so Digital ICE has nothing to work with no
matter what the save dialog says.

---

## 5. Hardware fault bits

From the client's own formatter (UTF-16 strings, MFC Unicode build):

| bit | meaning |
|---|---|
| `0x00040000` | Power Warning |
| `0x00080000` | Power Error |
| `0x00200000` | Stepper CCD Indeterminate |
| `0x00400000` | Stepper Lens Indeterminate |
| `0x00800000` | Motor Filter Wheel Indeterminate |
| `0x01000000` | Motor Film Guide Indeterminate |
| `0x02000000` | Film in Guide Error |
| `0x04000000` | Blower warning |
| `0x08000000` | Lens/Light Bar Cleaning Required |
| `0x10000000` | Film Emulsion Down |
| `0x20000000` | Film Tail First |

So: **emulsion up, head first.**

---

## 6. Internal error reporting

**834 call sites**, thiscall. The RVA differs per build (`0x1acd0` here), so it
is located by counting the targets of every direct `call rel32` in `.text` and
taking the busiest one whose prologue is `push -1; push <scopetable>` — the
reporter wins by roughly 13× over the runner-up. `PAKON_ERRHOOK_RVA=<hex>`
overrides the search.

    void Report(void *this, int classId, int fnId, int errCode,
                unsigned extra, const wchar_t *extraStr, int noAccumulate)

Most calls never reach a dialog — the client only receives the accumulated text
at the end — so hooking this is the only way to watch a failure as it happens.
`server/pknames.py` carries the recovered id→name tables (46 classes, 362
functions, ~240 error codes) so the numbers read as e.g.
`CN_CiScanner FN_bCalibrateFindCorrections EC_PreviousError (25)`.

`0x10022590` is a separate `void __cdecl DebugPrintf(const wchar_t *fmt, ...)`
that calls `OutputDebugStringW` and is **not** gated, so Wine's `+debugstr`
already captures those 27 trace messages without any hook.

### A caveat worth knowing

TLB calls `_wfopen` for its logs and **never checks the result**. A failed open
becomes `fwprintf(NULL)` → `_lock_file(NULL)` → a read of `0x34` → page fault.
Enabling the OEM's debug-log registry flags on a system where any of those paths
cannot be created will crash the process at the end of every scan.

---

## 7. Save formats

`SaveToDisk` is **8-bit only**; `iColorBits` cannot change that.

For 16-bit, use **SaveToClientMemory + Planar + Add File Header**, which writes:

    uint32 LE  16      size of THIS header, not the file
    uint32 LE  Width
    uint32 LE  Height
    uint32 LE  BitCount    48 = 3ch, 64 = 4ch (RGB+IR)
    then each plane, Width*Height uint16 LE

Values never fill the 16-bit word, and the ceiling depends on the save path.
Measured over 23 frames from one scanner:

| save path | observed maximum | clips? |
|---|---|---|
| colour corrections on | exactly 4095 (12-bit) | yes — up to 1.7 % of samples on the ceiling |
| colour corrections off ("raw") | 7200–11800 (~14-bit) | no |

So the 12-bit ceiling is imposed by the correction path, not by the sensor or
by this format. Scale from the data before handing it to tools that assume a
full 16-bit range, and do not hardcode either maximum.

Resolution tiers are multiples of a 500×750 base: Base 4 = 1500×1000,
Base 8 = 2100×1400, Base 16 = 3000×2000.

---

## 8. Things still unknown

* The `WaitForSingleObject` timeout in `uiGetScanLines` is
  `*(*(DWORD*)0x10075554 + 0x4b0)`. That displacement appears exactly once in
  the image — the read — with no writer, so another module populates it.
* `ring[0x10]` and `ring[0x20]` ("NF") semantics.
* Whether the raw (colour-corrections-off) save path applies Digital ICE at all.
* `MaxFilmLength` is settable only via `PutScannerInfo001(iMaxFilmLength_mm)`
  (valid 24..6400 mm), which the demo client never calls, so it sits at a
  compiled-in default. It is not a registry key.
