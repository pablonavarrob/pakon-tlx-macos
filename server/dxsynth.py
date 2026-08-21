#!/usr/bin/env python3
"""dxsynth -- stand in for a dead DX edge-code sensor.

35 mm film carries a DX barcode along its edge.  The scanner's light
controller (PICL) decodes it and the host polls the result with READ_SENSOR
(PICL reg 0x90).  From it TLB derives two things: the film product/generation
code (which picks the ISO and film-specific processing in Ansel) and the frame
number of every picture.  With a dead sensor every read comes back zero, TLB
labels every frame "DX_Error", and SaveToDisk collapses the whole roll into one
file because every default filename is the same.

This module rewrites READ_SENSOR responses on their way back to the OEM stack
so that TLB sees a decoded type-3 DX entry.  It is OFF unless a config file
exists (see CONFIG_PATH), and even then it only touches responses to reg 0x90
between DX-scan start (reg 0x91) and stop (reg 0x92); nothing is ever sent to
the scanner.

Two stages, matching what has actually been proven:

  product / generation   PROVEN.  Byte layout verified live on the OEM stack
                         under Windows (a rewritten kernel driver, June 2026):
                         injecting product 2 / generation 1 made TLXClientDemo
                         report Product=2, Specifier=1.
  frame numbers          PROVEN.  Run 9 (2026-08-19, serial 16402, Base 4):
                         type-7 film start + type-5/type-3 pairs every 810
                         positions gave "DX Read: Good", frames labeled 1-4,
                         one file per frame.  The client's Frame Num shows
                         frame_raw (half-frame units, the documented COM
                         range: 24 frames -> 2..49); File Name shows the
                         label.  docs/DX-OVERRIDE.md has the details.

Wire format (confirmed from USB captures of a healthy command exchange):
READ_SENSOR answers with a 34-byte frame -- 4-byte header, then a 30-byte
payload.  Full-buffer offsets:

    [0:4]    header  (01 20 <picl> 08)
    [4:6]    position counter, big-endian u16
    [6]      entry count
    [7]      entry type; 3 = DX barcode entry
    [8]      entry data 0: bit0 = half-frame ("A") flag
    [9]      entry data 1: frame_raw[5:0] in bits 7..2, bit1 = scanner state
                           (0), bit0 = parity spare
    [10]     entry data 2: product[1:0] in bits 7..6, bit5 = INVALID (0),
                           generation[3:0] in bits 4..1, frame_raw[6] in bit0
    [11]     entry data 3: product[6:2] in bits 4..0
    [12:34]  zero

Parity: popcount(d1) + popcount(d2) + popcount(d3 & 0x1F) must be even; the
spare bit0 of d1 is flipped to make it so.  Frame numbers are in half frames,
frame_raw = frame * 2 + half, so picture "1" is 2 and "1A" is 3 -- exactly the
"24 frames -> 2..49" range in the F235 COM reference manual.

Config file, `key = value` per line, `#` comments:

    product = 79        # DX part 1, 0..127.  REQUIRED to synthesise anything
    generation = 11     # DX part 2, 0..15.   REQUIRED
    start = 1           # first label: 1, 1A, 5, ...
    # Frames carry across the strips of one scan (strip 1 -> 1..4, strip 2
    # -> 5..8): they are consecutive pieces of one roll, since a roll is
    # scanned whole and then cut for the binder.  Stopping the scan resets to
    # `start`, which is what happens between rolls.
    pitch = 0           # 0: film code only -- the product/generation the
                        # engine votes on, no frame numbers.  Correct for any
                        # roll and nothing to get wrong.
                        # > 0: also number the frames.  Codes are placed on the
                        # reply's own position counter, one per half frame
                        # of `pitch` positions (about 1620 at Base 4, 4x at
                        # Base 16), each as a type-5 position event followed
                        # by the type-3 code, frame_raw +1 per code.
    a_flag = 1          # type-5 flag bit0 / e0 that marks the "A" code; set
                        # 0 to swap if every label comes out one off
    film_lead = 0       # positions before the first code at which the film's
                        # leading edge is reported (type-7 film-start event,
                        # sent once per transport run, before the first code)

How TLB consumes this (documented at the Pakon reference, dx-barcode.md, from
TLB.dll 3.1.0.28 decompilation): a type-3 entry has no position of its own; the
type-3 handler takes it from a slot filled by a preceding type-5 event whose
flag bit0 picks the slot, and the type-3's byte 0 must name the same slot
(e0 = 2 plain, e0 = 1 "A"; e0 = 0 is discarded).  The numbering pass then
wants at least three consecutive genuine codes spaced one half pitch apart
within 1/8, frame values stepping by 1.  Product/generation are voted before
that test, which is why they worked while numbering did not.

TLB does not poll the DX code on a timer: it reads it when the PICL raises
its service flag (POLL HOST reply bit 0x80, READ PICL reg 2 bit 0x02), which
on a unit that decodes nothing happens about once per scan (seen 2026-08-18:
one read, 11 s after motor start, on a 4-frame strip).  Numbering needs a
code every half frame, so the module asserts the flag itself.  The cadence is
derived, never configured: the position counter in every reply gives its own
rate, and the interval follows from that and the code spacing.

(79, 11) is a generic Kodak entry in the OEM's 2006 product table giving
ISO 160.  With no config file the module is inert; with the file present but
product/generation missing it only traces the DX traffic, which is the way to
see what the dead sensor actually says.
"""
import os
import sys
import time

CONFIG_PATH = os.environ.get(
    "PAKON_DX_CONFIG",
    os.path.expanduser("~/.local/share/psix/dx-override.conf"))
# What the file was called before the feature was named the DX override.
LEGACY_CONFIG_PATH = os.path.expanduser("~/.local/share/psix/dx.conf")

REG_READ_SENSOR = 0x90
REG_DX_START = 0x91         # also the line-counter reset that begins a scan
REG_DX_STOP = 0x92
WRITE_TYPES = (2, 4)        # WRITE and WRITE2: scan STOP goes out as WRITE2
TYPE_POLL, TYPE_READ = 3, 1
HOST_ADDR = 0x10
REG_STATUS = 0x02           # READ PICL reg2 x1: service flags, bit1 = DX data
SERVICE_BIT = 0x02
POLL_SERVICE_BIT = 0x80     # POLL HOST reply byte 3: some PIC wants service
REG_MOTOR_GO = 0xA0         # WRITE2 PICM: transport starts; fake events only after
REG_MOTOR_IDLE = 0xA2       # WRITE2 PICM: transport stops.  Watched because the
                            # frame count carries across the strips of one scan
                            # and must reset for a new one, and the transport
                            # itself is the honest signal for which is which.
                            # Inferring it from the DX scan-stop command failed:
                            # an aborted scan can leave that unseen, after which
                            # a fresh scan looked like another strip and
                            # continued the previous count (labels starting at 5,
                            # 2026-08-19).  A motor stop is issued even when a
                            # scan aborts, so this does not go stale.
MAX_ENTRIES = 5             # 2 position + 1 count + 5 x 5 bytes = 28 <= 30

ENTRY_TYPE_DX = 3
UNKNOWN_PRODUCT, UNKNOWN_GENERATION = 0, 0    # sent when only frames are wanted
READS_PER_CODE = 2          # prompt this many sensor reads per code slot
# A frame plus its gap, in position counts, proven at Base 4.  It scales with
# the scan resolution and nothing the bridge sees measures that cleanly: the
# image stream's line size is width x channels, so Digital ICE alone would
# move it.  So this is a default, and `pitch` overrides it.
DEFAULT_PITCH = 1620
# The engine does not place an "A" code where the event says.  It adds
# this+0x58 = width * 0x695F / 23700 to any code taken from the half-frame
# slot: 1138 / 1707 / 2276 counts at Base 4 / 8 / 16.  Against the expected
# pitch at each of those (1603 / 2405 / 3206) that is a constant ratio, so it
# follows from the pitch and needs no setting of its own.  Codes are sent
# early by this much so they land where they were meant to.
A_SLOT_SHIFT = 0.70997
# Where the code carrying `start` sits, measured from the film edge the
# controller reports, as a fraction of the frame pitch.  Anchoring to the edge
# rather than to the position counter's zero is what makes the phase
# repeatable: the counter is reset by the scan trigger, and the film sits
# wherever it sits at that moment.  Measured 915 on one scan and 5855 on the
# next, more than a frame apart, which is why identical settings produced
# 1, 2, 3, 4 on one run and 1A, 2A, 3A on another.
#
# Calibrated 2026-08-20 against the input sensor (entry byte 0x07): a scan
# whose edge was at 4908 put its four pictures at 8845, 13705, 18565 and
# 23425, so the first picture is 3937 counts past the edge.  A scan that read
# 0.31 was latching a downstream station 4014 counts further on, which puts
# it at 1.14 -- the same constant within the half-frame the labels quantise
# to.  One configuration so far.
EDGE_TO_START_CODE = 0.81
# How long to wait for that edge before concluding the sensor will not report
# it, in frame pitches from the counter's zero.  The film cannot be loaded
# before the motor starts (that trips a trigger error) and then has to be
# pushed about two inches before the input sensor sees it, so on a hand feed
# the edge lands around three pitches in.  This is counted in pitches, not
# raw counts, because an IR pass advances the counter twice as fast: the feed
# lands at about the same pitch with IR or without, at a given resolution.
# The ceiling is the fallback's own need for room -- once it gives up it lays
# a grid from the counter's zero, and that grid must still fit at least three
# codes before the film leaves the gate or the engine rejects the run; with
# scans running to about five and a half pitches, three is the most that
# leaves that room.  The sensor on the reference unit is marginal and
# reported nothing on about half its scans, so the fallback is not rare here.
# Its grid phase is whatever the feed timing made it, so the labels can be
# offset -- but every picture still gets a distinct sequential number and its
# own file, where DX_Error gives every frame the same filename and saving the
# roll writes one file.  Wrong numbers are a rename; lost files are lost.
EDGE_WAIT_PITCHES = 3
# Deriving the pitch from the scan, so it need not be configured per
# resolution.  Two measurements the server already has: the image stream's
# line size, and whether the IR lamp is on for Digital ICE.
#
# The line size is the scan width times the channel count times two bytes,
# and the engine's own divisor is the width over 250, doubled for ICE (the
# transport runs at half speed for the IR pass).  So the divisor falls out of
# the line size alone, once the channel count is known:
#
#     divisor = line_bytes / 1500   without ICE (3 channels)
#     divisor = line_bytes / 1000   with ICE    (4 channels)
#
# and the frame pitch is the picture spacing in the engine's line-units,
# which was ~404 in every scan at every resolution, times that divisor.
# Checked against all six configurations measured on hardware 2026-08-19;
# every one lands within 0.8% of a pitch proven to give correct labels.
# pitch = width * 38400 / 23700 (sub_10015520), and width = 250 * divisor
# (this+8), so the pitch is 405 counts per unit of divisor.  Matches all six
# configurations measured on hardware.
PICTURE_PITCH_LINES = 405
LINE_BYTES_PER_DIVISOR = {False: 1500, True: 1000}
KNOWN_DIVISORS = (4, 6, 8, 12, 16)      # Base 4, 8, 16, each doubled for IR
# The scan trigger (0x91, SetScanLineParams) carries a 16-bit little-endian
# value that identifies the configuration outright: the host announces the
# scan it is about to run, through us, in the very command the DX window
# keys on.  Read from the OEM's own labelled Windows captures and from
# Wine sessions, which emit the identical stream (same TLB.dll).  The value maps
# to (engine divisor, picture lead in half-frames).  The divisor is proven on
# hardware; the lead is 0 at every configuration once the codes are anchored
# to the film edge (see picture_lead), so it is the same for all six.
SCAN_PARAMS = {
    0x0107: (4, 0),     # Base 4,  no IR
    0x00c5: (8, 0),     # Base 4,  IR
    0x0075: (6, 0),     # Base 8,  no IR
    0x004d: (12, 0),    # Base 8,  IR
    0x003c: (8, 0),     # Base 16, no IR
    0x0031: (16, 0),    # Base 16, IR (labelled capture, 2026-08-20)
}


def scan_divisor(line_bytes, ir_on):
    """The engine's per-configuration divisor, or 0 if it cannot be told.

    The channel count, three or four, comes from whether the imaging pass
    uses IR (Digital ICE).  The lamp bit is not a reliable proxy: the engine
    toggles IR during calibration too, so a reading taken at the wrong moment
    can misreport it.  The state sampled when the transport starts is the one
    that describes the pass.

    From the decompilation: this+8 = width / divisor = 250, so the divisor is
    the width over 250, and the line size is width x channels x 2.
    """
    if not line_bytes:
        return 0
    # Only these divisors exist: 4, 6, 8 at Base 4, 8, 16, doubled for an IR
    # pass.  Reading the line size as three channels and as four gives two
    # candidates, and usually only one of them is a divisor the scanner has.
    # That decides it without trusting the lamp.  It is a fallback only: the
    # calibration stream starts visible-only (three channels) and the engine
    # lights IR partway through it, so the channel count there depends on when
    # the line is sampled (seen live on an ICE scan: 9234 bytes, three
    # channels, then the lamp lit IR).  The 0x91 trigger names the
    # configuration outright, so that is what the module uses.
    candidates = []
    for channels_divisor, assumed_ir in ((1500, False), (1000, True)):
        value = round(line_bytes / channels_divisor)
        if value in KNOWN_DIVISORS:
            candidates.append((value, assumed_ir))
    if not candidates:
        return 0
    if len(candidates) == 1:
        return candidates[0][0]
    # Both readings are possible -- 12312 bytes is Base 16 without an IR pass
    # or Base 8 with one -- so the lamp breaks the tie.
    for value, assumed_ir in candidates:
        if assumed_ir == bool(ir_on):
            return value
    return candidates[0][0]


def derived_pitch(line_bytes, ir_on):
    """Frame pitch in position counts, or 0 if the scan has not been measured
    yet."""
    divisor = scan_divisor(line_bytes, ir_on)
    return PICTURE_PITCH_LINES * divisor if divisor else 0


def picture_lead(pitch, line_bytes=0, ir_on=None):
    """Half-frames to start the sequence early, so the number in the config
    lands on the first picture.

    With the codes anchored to the film edge (see EDGE_TO_START_CODE) this is
    0 at every configuration: the edge-to-first-picture distance is carried by
    that offset, which is a fraction of a pitch and so constant across
    resolution, leaving nothing for the lead to correct.  Confirmed on Base 8
    with Digital ICE (2026-08-21, labels 1, 2, 3, 4); the others follow from
    the offset being pitch-relative.  (Base 16 could not be confirmed
    directly -- the DX sensor on the reference unit would not report an edge
    at that resolution -- but on that unit Base 16 always falls back anyway,
    where the lead only sets an already-arbitrary phase.)

    The earlier per-resolution leads (1, 1, 2, measured 2026-08-19) compensated
    the sensor-to-CCD distance under the abandoned grid anchoring; the edge
    anchor now measures that distance directly, so they are gone.  `lead` in
    the config still overrides this if a unit ever needs it.

    The signature keeps line_bytes/ir_on for callers; they no longer matter.
    """
    return 0
SEED_INTERVAL = 0.3         # seconds, until the counter rate has been measured
MIN_INTERVAL, MAX_INTERVAL = 0.15, 1.0        # clamp on the measured cadence
ENTRY_TYPE_CODE_POS = 5     # event carrying the position of the code that follows
ENTRY_TYPE_FILM_EDGE = 7    # film/picture edge event; with byte 1 bit 1 set and no
FILM_START_BIT = 0x02       # film start recorded yet, its position IS the film start
E0_HALF, E0_PLAIN = 1, 2    # type-3 byte 0: which position slot the code takes
RESP_ENTRY_END = 12         # a reply shorter than this cannot hold one entry
RESP_LEN = 34


def popcount(value):
    return bin(value & 0xFF).count("1")


def parse_frame_label(text):
    """"1" -> 2, "1A" -> 3, "12a" -> 25.  Half-frame units, as TLB stores them."""
    text = str(text).strip().upper()
    half = 0
    if text.endswith("A"):
        half, text = 1, text[:-1]
    if not text.isdigit():
        raise ValueError(f"frame label {text!r}: expected N or NA")
    frame_raw = int(text) * 2 + half
    if not 0 <= frame_raw <= 127:
        raise ValueError(f"frame label {text!r}: out of the 7-bit range")
    return frame_raw


def frame_label(frame_raw):
    return f"{frame_raw >> 1}{'A' if frame_raw & 1 else ''}"


def encode_entry(product, generation, frame_raw):
    """The four data bytes of a type-3 DX entry."""
    if not 0 <= product <= 127:
        raise ValueError(f"product {product}: 0..127")
    if not 0 <= generation <= 15:
        raise ValueError(f"generation {generation}: 0..15")
    if not 0 <= frame_raw <= 127:
        raise ValueError(f"frame_raw {frame_raw}: 0..127")
    # The 7-bit field carries frame_raw itself (half-frame units: TLB's labeler
    # prints field/2 and appends "A" on bit 0).  Byte 0's half-frame flag is
    # set consistently with it; whether TLB uses one, the other or both is
    # part of what the frame experiment has to show.
    data0 = frame_raw & 1
    data1 = (frame_raw & 0x3F) << 2
    data2 = ((product & 0x03) << 6) | ((generation & 0x0F) << 1) | ((frame_raw >> 6) & 1)
    data3 = (product >> 2) & 0x1F
    if (popcount(data1) + popcount(data2) + popcount(data3 & 0x1F)) & 1:
        data1 |= 1
    return bytes([data0, data1, data2, data3])


def decode_entry(data):
    """Inverse of encode_entry, mirroring TLB's parser.  Returns
    (product, generation, frame_raw, valid)."""
    data0, data1, data2, data3 = data[:4]
    product = (data2 >> 6) + (data3 & 0x1F) * 4
    generation = (data2 >> 1) & 0x0F
    frame_raw = (data1 >> 2) + (data2 & 1) * 64
    parity_ok = ((popcount(data1) + popcount(data2) + popcount(data3 & 0x1F)) & 1) == 0
    valid = (parity_ok and not (data2 & 0x20) and not (data1 & 0x02)
             and (data0 & 1) == (frame_raw & 1))
    return product, generation, frame_raw, valid


def is_picl_write(pkt, picl, reg):
    return len(pkt) >= 5 and pkt[0] in WRITE_TYPES and pkt[2] == picl and pkt[4] == reg


def is_picl_read(pkt, picl, reg):
    return len(pkt) >= 5 and pkt[0] == TYPE_READ and pkt[2] == picl and pkt[4] == reg


def is_host_poll(pkt):
    return len(pkt) >= 3 and pkt[0] == TYPE_POLL and pkt[2] == HOST_ADDR


def is_motor_go(pkt, picm):
    return len(pkt) >= 5 and pkt[0] in WRITE_TYPES and pkt[2] == picm and pkt[4] == REG_MOTOR_GO


def is_motor_stop(pkt, picm):
    return (len(pkt) >= 5 and pkt[0] in WRITE_TYPES and pkt[2] == picm
            and pkt[4] == REG_MOTOR_IDLE)


class DxConfig:
    def __init__(self):
        self.product = None
        self.generation = None
        self.start = 2
        self.override_numbers = True    # supply frame numbers unless told not to
        self.pitch = 0                  # positions per frame; 0 = count reads
        self.a_flag = 1                 # which flag value marks the "A" code
        self.film_lead = 0              # film start this many positions before code 1
        self.lead = None                # half-frames; None = the measured default

    @property
    def synthesise(self):
        """Anything to inject at all: a film code, frame numbers, or both."""
        return self.film_code or self.override_numbers

    @property
    def film_code(self):
        return self.product is not None and self.generation is not None

    @property
    def code(self):
        """The (product, generation) to put in the entries.  With no film code
        configured these are zero: the frame number rides in the same entry as
        the product code, so numbering still has to send one."""
        if self.film_code:
            return self.product, self.generation
        return UNKNOWN_PRODUCT, UNKNOWN_GENERATION

    @classmethod
    def parse(cls, text):
        cfg = cls()
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError(f"line {lineno}: expected key = value")
            key, value = (part.strip() for part in line.split("=", 1))
            key = key.lower()
            if key == "product":
                cfg.product = int(value)
            elif key == "generation":
                cfg.generation = int(value)
            elif key == "start":
                cfg.start = parse_frame_label(value)
            elif key == "override_numbers":
                if value.lower() not in ("yes", "true", "on", "1",
                                         "no", "false", "off", "0"):
                    raise ValueError(f"override_numbers {value!r}: yes or no")
                cfg.override_numbers = value.lower() in ("yes", "true", "on", "1")
            elif key == "pitch":
                cfg.pitch = int(value)
                if cfg.pitch < 0:
                    raise ValueError("pitch: positions per frame, 0 = off")
            elif key == "a_flag":
                cfg.a_flag = int(value)
                if cfg.a_flag not in (0, 1):
                    raise ValueError("a_flag: 0 or 1")
            elif key == "lead":
                cfg.lead = int(value)
                if not 0 <= cfg.lead <= 8:
                    raise ValueError("lead: half-frames, 0..8")
            elif key == "film_lead":
                cfg.film_lead = int(value)
                if cfg.film_lead < 0:
                    raise ValueError("film_lead: positions, >= 0")
            else:
                raise ValueError(f"line {lineno}: unknown key {key!r}")
        if cfg.film_code:
            encode_entry(cfg.product, cfg.generation, cfg.start)   # range check
        if (cfg.product is None) != (cfg.generation is None):
            raise ValueError("product and generation: set both or neither")
        return cfg

    def half_pitch(self, line_bytes=0, ir_on=None):
        """Position counts between codes: half a frame pitch.

        `pitch` wins if set.  Otherwise it is derived from the scan itself
        (see derived_pitch), and DEFAULT_PITCH is the last resort for a scan
        whose line size was never measured."""
        pitch = self.pitch or derived_pitch(line_bytes, ir_on) or DEFAULT_PITCH
        return max(1, pitch // 2)

    def describe(self, pitch=None, source=""):
        if not self.synthesise:
            return "trace only (no film code, numbers off)"
        s = (f"product {self.product} generation {self.generation}"
             if self.film_code else "no film code (numbers only)")
        if self.override_numbers:
            pitch = pitch or self.pitch or DEFAULT_PITCH
            s += (f", frames from {frame_label(self.start)}, codes every"
                  f" {max(1, pitch // 2)} positions"
                  + (f" ({source})" if source else "")
                  + f", A flag {self.a_flag}"
                  f", film start {self.film_lead} before code 1")
        else:
            s += ", film code only (override_numbers = no)"
        return s

class DxSynth:
    """Per-scanner state.  on_command() sees every command packet before it
    goes out; on_response() sees the reply and may rewrite it."""

    def __init__(self, say=print, config_path=CONFIG_PATH):
        self.say = say
        self.config_path = config_path
        self.cfg = None
        self.active = False
        self.frame_raw = 0
        self.polls = 0
        self.last_read = 0.0            # time of the last READ_SENSOR
        self.faking = False             # a fake service flag is being asserted
        self.faked = 0
        self.moving = False             # MOTOR GO seen since scan start
        self.pos_hi = 0                 # 16-bit position counter, unwrapped
        self.pos_last = None
        self.next_code = None           # position of the next code to emit
        self.film_start_sent = False    # type-7 film start sent this run
        self.film_edge = None           # where the controller saw the film edge
        self.line_bytes = 0             # line size, set by the server at align
        self.params_divisor = None      # from the 0x91 payload, if recognised
        self.params_lead = None
        self.line_bytes_ir = None       # the IR state it was measured under
        self.ir_on = None               # lamp IR bit now, set by the server
        self.ir_pass = None             # ...as it was when the transport started
        self.rate_from = None           # (time, position) the rate is measured against
        self.interval = SEED_INTERVAL   # seconds between prompted reads

    def load(self):
        text = None
        candidates = [self.config_path]
        if self.config_path == CONFIG_PATH:      # not an explicit override
            candidates.append(LEGACY_CONFIG_PATH)
        for path in candidates:
            try:
                with open(path) as fh:
                    text = fh.read()
            except FileNotFoundError:
                continue
            if path != self.config_path:
                self.say(f"  DX: using {path}; rename it to"
                         f" {os.path.basename(self.config_path)}")
            break
        if text is None:
            self.cfg = None
            return None
        try:
            self.cfg = DxConfig.parse(text)
        except ValueError as e:
            self.say(f"  DX: {self.config_path}: {e} -- synthesiser off")
            self.cfg = None
        return self.cfg

    def on_command(self, pkt, picl, picm=None):
        if picm is not None and is_motor_stop(pkt, picm):
            self.moving = False         # the transport really has stopped
            return
        if picm is not None and is_motor_go(pkt, picm):
            if self.active and not self.moving:
                self.last_read = time.time()      # cadence counts from here
                # The lamp is lit for the imaging pass by now, and this is the
                # reading that says whether it is an IR pass.  Calibration
                # lights IR too, so an earlier sample means nothing.
                self.ir_pass = self.ir_on
            self.moving = True
            return
        if is_picl_write(pkt, picl, REG_DX_START):
            if len(pkt) >= 7:
                params = pkt[5] | (pkt[6] << 8)
                known = SCAN_PARAMS.get(params)
                if known:
                    self.params_divisor, self.params_lead = known
                else:
                    self.params_divisor = self.params_lead = None
                    self.say(f"  DX: 0x91 scan params 0x{params:04x} not in the"
                             " table -- a configuration not seen before."
                             " Note the client's resolution and ICE setting"
                             " and record this value.")
            cfg = self.load()             # re-read: start frame changes per roll
            if cfg is None:
                self.active = False
                return
            # A trigger while the transport is running is the next strip of a
            # multi-strip scan: the motor never stops between strips, so the
            # frame counter carries over (strip 1 -> 1..4, strip 2 -> 5..8).
            # Strips within one scan are consecutive pieces of one roll by
            # construction; a different roll means stopping, which stops the
            # transport, and the next trigger then resets to `start`.
            # Everything else is per strip: the PICL's position counter
            # restarts and each strip is a fresh CiDxCode in the engine, so
            # the film start is re-sent.
            new_strip = self.active and self.moving
            self.active = True
            if not new_strip:
                lead = self.lead_now()
                self.frame_raw = (max(0, cfg.start - lead)
                                  if cfg.override_numbers else cfg.start)
            self.polls = 0
            self.pos_hi = 0
            self.pos_last = None
            self.next_code = None
            self.film_start_sent = False
            self.film_edge = None
            self.rate_from = None
            self.interval = SEED_INTERVAL
            self.faked = 0
            self.faking = False
            self.last_read = time.time()
            if self.line_bytes and not self.measured():
                self.say("  DX: the scan's line size was measured with Digital"
                         " ICE the other way round; ignoring it. Restart the"
                         " server to re-measure, or set `pitch` by hand.")
            pitch, source = self.frame_pitch()
            self.say(f"  DX: scan start -- {cfg.describe(pitch, source)}"
                     + (f" (transport running; next frame {frame_label(self.frame_raw)})"
                        if self.moving else ""))
        elif is_picl_write(pkt, picl, REG_DX_STOP):
            if self.cfg is not None and self.active:
                self.say(f"  DX: scan stop after {self.polls} sensor reads"
                         + (f" ({self.faked} prompted by a faked service flag)" if self.faked else ""))
            self.active = False
            self.faking = False
            self.moving = False

    def _fake_event(self, pkt, resp, picl):
        """TLB only reads the DX code when the PICL raises its service flag
        (POLL HOST reply bit 0x80, then READ PICL reg2 bit 0x02), and on a
        unit that decodes nothing that is about once per scan -- far too rare
        to carry a code every half frame.  So assert the flag ourselves; TLB
        acks it and reads, and the read is answered by on_response.

        The cadence is not configured.  Only one code can ride in one reply,
        so reads have to outpace the codes; how fast that is depends on the
        transport speed, which varies with the resolution.  Rather than keep
        a table of rates per base, measure it: every reply carries the
        position counter, so the counter's rate falls out of two of them, and
        the interval follows from the code spacing (see _retime)."""
        cfg = self.cfg
        if not cfg.override_numbers or not self.moving:
            return resp                 # never during calibration: film is still
        now = time.time()
        if not self.faking and now - self.last_read >= self.interval:
            self.faking = True
        if not self.faking:
            return resp
        raw = bytes(resp)
        if is_host_poll(pkt) and len(raw) >= 4:
            out = bytearray(raw)
            out[3] |= POLL_SERVICE_BIT
            return bytes(out)
        if is_picl_read(pkt, picl, REG_STATUS) and len(raw) >= 5:
            out = bytearray(raw)
            out[3] |= POLL_SERVICE_BIT
            out[4] |= SERVICE_BIT
            return bytes(out)
        return resp

    def frame_pitch(self):
        """Position counts per frame for the scan in progress, best source
        first: an explicit `pitch`, the 0x91 scan-params table, the stream
        measurement, the Base 4 default."""
        if self.cfg is not None and self.cfg.pitch:
            return self.cfg.pitch, "set"
        if self.params_divisor:
            return PICTURE_PITCH_LINES * self.params_divisor, "from the 0x91 params"
        divisor = scan_divisor(self.measured(), self.ir_pass)
        if divisor:
            return PICTURE_PITCH_LINES * divisor, "from the stream"
        return DEFAULT_PITCH, "default"

    def half_pitch_now(self):
        return max(1, self.frame_pitch()[0] // 2)

    def lead_now(self):
        if self.cfg is not None and self.cfg.lead is not None:
            return self.cfg.lead
        if self.params_lead is not None:
            return self.params_lead
        return picture_lead(self.frame_pitch()[0], self.measured(), self.ir_pass)

    def measured(self):
        """The line size, if it can still be trusted for the current scan.

        The stream aligns once and the measurement can outlive a change of
        settings: a line size taken with Digital ICE off means a different
        divisor once ICE is on, and pairing the old measurement with the new
        state derives a pitch that belongs to neither.  Seen live: 9234 bytes
        measured at Base 8 without ICE gave divisor 9 when ICE came on, where
        the truth was 12.
        """
        if not self.line_bytes:
            return 0
        if self.line_bytes_ir is None:
            # Measured before any lamp write, so the IR state was simply not
            # known yet -- not evidence that it has since changed.  The line
            # size alone cannot say: ~12300 bytes is Base 16 at three
            # channels or Base 8 at four.  Take the state in force now.
            return self.line_bytes
        return self.line_bytes

    def _retime(self, now, position):
        """Hold the prompt cadence at READS_PER_CODE reads per code slot,
        from the position counter's own measured rate."""
        if self.rate_from is None:
            self.rate_from = (now, position)
            return
        elapsed, advanced = now - self.rate_from[0], position - self.rate_from[1]
        if elapsed < 0.5 or advanced <= 0:
            return                      # too short a baseline to divide by
        rate = advanced / elapsed       # counts per second
        seconds_per_code = self.half_pitch_now() / rate
        self.interval = max(MIN_INTERVAL,
                            min(MAX_INTERVAL, seconds_per_code / READS_PER_CODE))
        self.rate_from = (now, position)

    def _film_reply(self, cfg, raw, out):
        """Real-film mode.  Codes sit at fixed positions on the scanner's own
        counter, one per half pitch.  When the counter has passed the next
        code position, answer with a type-5 event at that position and the
        type-3 code, both in this reply; otherwise leave the reply as it is."""
        pos = int.from_bytes(out[4:6], "big")
        if self.pos_last is not None and pos < self.pos_last - 0x8000:
            self.pos_hi += 0x10000
        self.pos_last = pos
        now = self.pos_hi + pos
        self._retime(time.time(), now)
        half = self.half_pitch_now()
        if self.next_code is None and self.film_edge is not None:
            pitch = self.frame_pitch()[0]
            # The code carrying `start` goes a fixed distance past the edge;
            # the sequence begins `lead` half-frames before that.
            at_start = self.film_edge + round(EDGE_TO_START_CODE * pitch)
            first = at_start - self.lead_now() * half
            # Nothing can be placed before the counter's zero, and a half-frame
            # code has to be sent a slot shift early on top of that.  Advance
            # whole frames until it fits, which keeps every label on the same
            # picture as before.
            shift = round(pitch * A_SLOT_SHIFT)
            while first - shift < 0:
                first += pitch
                self.frame_raw += 2
            self.next_code = first
            self.say(f"  DX: anchored to the film edge at {self.film_edge};"
                     f" first code at {first}, carrying"
                     f" {frame_label(self.frame_raw)}")
        if self.next_code is None:
            # The edge is the only thing that says where the film is: the
            # counter is zeroed by the scan trigger, and the film arrives
            # whenever the strip is fed (915, 5855, 16317 and 4908 counts on
            # one configuration).  So wait for it well past where the input
            # sensor has been seen to fire.  Only then fall back to a grid
            # from the counter's zero: its phase within the frame is
            # arbitrary, but sequential numbers on distinct files beat
            # DX_Error, which collapses the roll into one filename.
            if now < self.frame_pitch()[0] * EDGE_WAIT_PITCHES:
                self.say(f"  DX: read {self.polls} pos {now}: waiting for the film edge")
                return raw
            self.next_code = ((now + half - 1) // half) * half
            self.say(f"  DX: no film edge by {now}; falling back to the"
                     f" counter grid, first code at {self.next_code} --"
                     f" numbers will be sequential but their phase is not"
                     f" anchored to the film")
        if now < self.next_code:
            self.say(f"  DX: read {self.polls} pos {now}: no code yet (next at {self.next_code})")
            return raw
        if self.frame_raw > 127:
            return raw
        code_pos = self.next_code
        is_half = self.frame_raw & 1
        flag = cfg.a_flag if is_half else 1 - cfg.a_flag
        e0 = E0_HALF if flag else E0_PLAIN
        # Where the event has to say the code is, for the engine to place it
        # where it belongs.  A half-frame code is moved by A_SLOT_SHIFT when
        # the engine reads it, so send it that much early.  Decided before
        # anything is built: a code that cannot be placed is skipped whole,
        # and skipping must not consume the film start.
        sent_pos = code_pos
        if e0 == E0_HALF:
            sent_pos = code_pos - round(self.frame_pitch()[0] * A_SLOT_SHIFT)
            if sent_pos < 0:
                self.say(f"  DX: read {self.polls} pos {now}: skipping"
                         f" {frame_label(self.frame_raw)}, its shifted position"
                         f" would be before the scan start")
                self.frame_raw += 1
                self.next_code += half
                return raw
        entries = []
        film_note = ""
        if not self.film_start_sent:
            # TLB's numbering pass is "DX unusable" without a recorded film
            # start (type 7, byte 1 bit 1, its own position).  Send it once,
            # ahead of the first code; an early film start only shifts which
            # picture a code lands in, it cannot fail the pass.
            film_pos = max(0, code_pos - cfg.film_lead)
            entries.append(bytes([ENTRY_TYPE_FILM_EDGE, FILM_START_BIT])
                           + (film_pos & 0xFFFF).to_bytes(2, "big") + b"\x00")
            self.film_start_sent = True
            film_note = f" film start at {film_pos},"
        entries.append(bytes([ENTRY_TYPE_CODE_POS, flag])
                       + (sent_pos & 0xFFFF).to_bytes(2, "big") + b"\x00")
        entries.append(bytes([ENTRY_TYPE_DX, e0])
                       + encode_entry(*cfg.code, self.frame_raw)[1:])
        payload = b"".join(entries)
        out[6] = len(entries)
        out[7:7 + len(payload)] = payload
        shift_note = "" if sent_pos == code_pos else f" sent at {sent_pos}, A-slot shift"
        self.say(f"  DX: read {self.polls} pos {now}:{film_note}"
                 f" code {frame_label(self.frame_raw)}"
                 f" at {code_pos}{shift_note} (flag {flag}, e0 {e0}) raw {raw.hex()}")
        self.frame_raw += 1
        self.next_code += half
        return bytes(out)

    def on_response(self, pkt, resp, picl):
        if not self.active or self.cfg is None:
            return resp
        if not is_picl_read(pkt, picl, REG_READ_SENSOR):      # READ, type 1
            return self._fake_event(pkt, resp, picl)
        self.polls += 1
        if self.faking:
            self.faked += 1
        self.faking = False
        self.last_read = time.time()
        cfg = self.cfg
        raw = bytes(resp)
        if not cfg.synthesise:
            self.say(f"  DX: poll {self.polls} raw {raw.hex()}")
            return resp
        if len(raw) < RESP_ENTRY_END:
            self.say(f"  DX: poll {self.polls} response only {len(raw)} bytes, left alone: {raw.hex()}")
            return resp
        # Look at what the controller itself sent before overwriting it.  Its
        # own edge events carry film positions on the same counter as our
        # codes, which is the one thing that could anchor the code grid to the
        # film instead of to wherever the counter happened to reset.  Whether
        # they arrive during the transport pass at all is unknown, precisely
        # because this method has always zeroed them first.  Observation only.
        if len(raw) > 7 and raw[6] and raw[7]:
            seen, edge = [], None
            for i in range(min(raw[6], (len(raw) - 7) // 5)):
                at = 7 + 5 * i
                etype = raw[at] & 0x0f
                if etype:
                    where = int.from_bytes(raw[at + 2:at + 4], "big")
                    # Log the whole entry: if the two staggered sensors are
                    # distinguishable at all it will be in a byte we are not
                    # reading, and which one fired decides the anchor.
                    seen.append(f"type {etype} flag 0x{raw[at + 1]:02x} at {where}"
                                f" raw {raw[at:at + 5].hex()}")
                    # Only the input sensor anchors.  A scan reports edges
                    # from several stations -- 0x07 at 4908, 0x27 at 8922 and
                    # 0x07 again at 20788 on one pass -- and the low nibble
                    # alone cannot tell them apart, so latching whichever
                    # arrived first put the grid a whole frame out whenever
                    # the film was fed before the counter reset and the input
                    # sensor was already covered.  The other stations are
                    # logged, not used: their offsets are not measured yet.
                    if raw[at] == ENTRY_TYPE_FILM_EDGE and edge is None:
                        edge = where
            if edge is not None and self.moving and self.film_edge is None:
                self.film_edge = (self.pos_hi or 0) + edge
                seen.append(f"film edge taken as {self.film_edge}")
            if seen:
                self.say(f"  DX: controller sent {'; '.join(seen)}"
                         f" (reply pos {int.from_bytes(raw[4:6], 'big')},"
                         f" transport {'running' if self.moving else 'stopped'})")

        out = bytearray(raw)
        for i in range(6, len(out)):
            out[i] = 0                  # the scanner's own position stays
        if cfg.override_numbers:
            if not self.moving:
                self.say(f"  DX: read {self.polls}: transport not running, left alone: {raw.hex()}")
                return raw              # real film gives no codes while still
            return self._film_reply(cfg, raw, out)
        # Film type only: one type-3 entry, the frame fixed at `start`.  This
        # carries product/generation, which the engine votes on before it
        # looks at positions; numbering needs `pitch` (see the module notes).
        out[6] = 1
        out[7] = ENTRY_TYPE_DX
        out[8:12] = encode_entry(*cfg.code, self.frame_raw)
        self.say(f"  DX: read {self.polls} raw {raw.hex()} -> film code only"
                 f" (frame {frame_label(self.frame_raw)})"
                 f" pos {int.from_bytes(out[4:6], 'big')}")
        return bytes(out)


def selftest():
    # The two vectors proven live on the OEM stack: TLXClientDemo decoded
    # them to Product=2/Specifier=1 and Product=79/Specifier=11.
    assert encode_entry(2, 1, 3)[1:] == bytes([0x0C, 0x82, 0x00])
    assert encode_entry(79, 11, 2)[1:] == bytes([0x09, 0xD6, 0x13])
    assert encode_entry(2, 1, 3)[0] == 1 and encode_entry(79, 11, 2)[0] == 0

    # Round trip over the whole space, always valid, never setting the
    # invalid (d2 bit5) or scanner-state (d1 bit1) bits.
    for product in range(128):
        for generation in range(16):
            for frame_raw in (0, 1, 2, 3, 47, 48, 49, 126, 127):
                data = encode_entry(product, generation, frame_raw)
                assert not data[2] & 0x20 and not data[1] & 0x02
                assert decode_entry(data) == (product, generation, frame_raw, True), \
                    (product, generation, frame_raw, data.hex())

    # The pitch worked out from the scan, against all six configurations
    # measured on hardware 2026-08-19 (the lead is 0 now, checked below).  The
    # line sizes are the measured ones where a scan recorded them, nominal
    # otherwise.
    for line_bytes, ir_pass, worked, lead in ((6000, False, 1620, 0), (8000, True, 3240, 0),
                                              (9234, False, 2405, 0), (12312, True, 4810, 0),
                                              (12312, False, 3206, 0), (16000, True, 6412, 0)):
        got = derived_pitch(line_bytes, ir_pass)
        assert abs(got - worked) / worked < 0.015, (line_bytes, ir_pass, got, worked)
        assert picture_lead(got, line_bytes, ir_pass) == lead, (line_bytes, ir_pass)
    # One line size, two meanings: the IR pass is what separates them.
    assert scan_divisor(12312, False) == 8 and scan_divisor(12312, True) == 12
    assert derived_pitch(0, False) == 0                  # nothing measured yet
    assert DxConfig.parse("pitch = 2405\n").half_pitch(9234, False) == 1202   # override wins

    # The picture lead is 0 at every resolution once the codes are anchored
    # to the film edge (Base 8 confirmed 2026-08-21; the rest by the offset
    # being constant in pitch units).
    assert picture_lead(1603) == 0 and picture_lead(1620) == 0      # Base 4
    assert picture_lead(2405) == 0                                   # Base 8
    assert picture_lead(3206) == 0 and picture_lead(3232) == 0       # Base 16

    assert parse_frame_label("1") == 2 and parse_frame_label("1A") == 3
    assert parse_frame_label("0") == 0 and parse_frame_label("12a") == 25
    assert frame_label(49) == "24A" and frame_label(2) == "1"
    for bad in ("", "A", "-1", "64", "x"):
        try:
            parse_frame_label(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)

    cfg = DxConfig.parse("product = 79\ngeneration=11 # iso 160\n"
                         "start = 5A\npitch = 1620\n")
    assert (cfg.product, cfg.generation, cfg.start,
            cfg.pitch) == (79, 11, 11, 1620)
    # An empty file means "do the job": numbers from frame 1, no film code.
    assert DxConfig.parse("").synthesise is True
    assert DxConfig.parse("override_numbers = no\n").synthesise is False  # trace only
    for bad in ("product = 200\ngeneration = 1", "colour = 3", "product 79",
                "product = 79", "generation = 11",          # both or neither
                "product = 79\npitch = 1620"):
        try:
            DxConfig.parse(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)

    # State machine, driven with an in-memory config.
    picl = 0x40
    said = []
    synth = DxSynth(say=said.append, config_path="/nonexistent")
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1\noverride_numbers = no\n")) or synth.cfg
    start = bytearray([2, 3, picl, 0, REG_DX_START, 0])
    stop = bytearray([2, 3, picl, 0, REG_DX_STOP, 0])
    poll = bytearray([1, 3, picl, 0x1e, REG_READ_SENSOR])    # READ x30, as TLB sends it
    dead = bytes([1, 0x20, picl, 8, 0x12, 0x34]) + bytes(28)      # what a dead sensor returns

    def reply(pos):        # a sensor reply carrying `pos` in its counter
        return bytes([1, 0x20, picl, 8]) + pos.to_bytes(2, "big") + bytes([1]) + bytes(27)

    # Outside a DX scan: untouched.
    assert synth.on_response(poll, dead, picl) == dead
    synth.on_command(start, picl)
    r1 = synth.on_response(poll, dead, picl)
    r2 = synth.on_response(poll, dead, picl)
    assert len(r1) == RESP_LEN and r1[:6] == dead[:6]             # header + position kept
    assert r1[6:8] == bytes([1, ENTRY_TYPE_DX])
    # No pitch: film code only, the frame stays at `start`.
    assert decode_entry(r1[8:12]) == (79, 11, 2, True)             # "1"
    assert decode_entry(r2[8:12]) == (79, 11, 2, True)             # "1" again
    assert not any(r1[12:])
    # Non-sensor traffic and other addresses are never rewritten.
    other = bytearray([1, 3, picl, 1, 0x88])
    assert synth.on_response(other, dead, picl) == dead
    assert synth.on_response(bytearray([1, 3, 0x44, 0x1e, REG_READ_SENSOR]), dead, picl) == dead
    # A WRITE to reg 0x90 is not a sensor read and is never rewritten.
    assert synth.on_response(bytearray([2, 3, picl, 0, REG_READ_SENSOR, 0]), dead, picl) == dead
    # scan STOP goes out as WRITE2 (type 4) on the real unit.
    stop2 = bytearray([4, 3, picl, 0, REG_DX_STOP, 0])
    synth.on_command(stop2, picl)
    assert synth.on_response(poll, dead, picl) == dead
    # A new scan starts over from the configured label.
    synth.on_command(start, picl)
    assert decode_entry(synth.on_response(poll, dead, picl)[8:12])[2] == 2
    # Short responses are left alone rather than crashing the server.
    assert synth.on_response(poll, dead[:8], picl) == dead[:8]

    # No pitch: film code only, the frame fixed, position bytes untouched.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 2\ngeneration = 1\nstart = 1A\noverride_numbers = no\n")) or synth.cfg
    synth.on_command(start, picl)
    r1 = synth.on_response(poll, dead, picl)
    r2 = synth.on_response(poll, dead, picl)
    assert r1[8:12] == r2[8:12] == encode_entry(2, 1, 3)
    assert r1[4:6] == r2[4:6] == dead[4:6]
    for bad in ("event_every = 5", "pitch = -1", "film_lead = -1",
                "entries = 4", "increment = 2", "position = 100",
                "frames = yes", "continue = yes", "override_numbers = maybe"):
        try:
            DxConfig.parse(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)

    # Prompted reads: after the interval, the HOST poll and PICL status
    # replies carry the service bits until the next READ_SENSOR, then clear.
    # The interval is derived, not configured: it seeds at SEED_INTERVAL and
    # then tracks the position counter's measured rate.
    clock = [1000.0]
    real_time = time.time
    time.time = lambda: clock[0]
    try:
        synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
            "product = 79\ngeneration = 11\nstart = 1A\npitch = 1000\n")) or synth.cfg
        synth.on_command(start, picl)
        hostpoll = bytearray([3, 1, HOST_ADDR])
        idle_host = bytes([3, 3, HOST_ADDR, 0x00, 0xAA])
        status = bytearray([1, 3, picl, 1, REG_STATUS])
        idle_status = bytes([1, 3, picl, 0x08, 0x00])
        picm = picl + 4
        assert synth.interval == SEED_INTERVAL
        assert synth.on_response(hostpoll, idle_host, picl) == idle_host       # too early
        clock[0] += SEED_INTERVAL
        assert synth.on_response(hostpoll, idle_host, picl) == idle_host       # motor not running
        synth.on_command(bytearray([4, 3, picm, 1, REG_MOTOR_GO]), picl, picm)
        assert synth.on_response(hostpoll, idle_host, picl) == idle_host       # restarts at GO
        clock[0] += SEED_INTERVAL + 0.01        # + epsilon: binary 0.3 rounds low
        assert synth.on_response(hostpoll, idle_host, picl)[3] == 0x80
        assert synth.on_response(status, idle_status, picl)[3:5] == bytes([0x88, 0x02])
        r = synth.on_response(poll, reply(0), picl)                            # TLB reads
        # What is under test here is the prompting cadence, not the codes: with
        # no film edge reported yet the module is still waiting to anchor.
        assert synth.faked == 1
        assert synth.on_response(hostpoll, idle_host, picl) == idle_host       # cleared

        # 1000 counts/s with a 1000-count pitch: a code every half second, so
        # two reads per code is a quarter-second interval.
        clock[0] += 1.0
        synth.on_response(poll, reply(1000), picl)
        assert abs(synth.interval - 0.25) < 1e-9, synth.interval
        # A slower transport stretches it; the clamp is the ceiling.
        clock[0] += 10.0
        synth.on_response(poll, reply(1100), picl)                             # 10 counts/s
        assert synth.interval == MAX_INTERVAL
        # A faster one shortens it, down to the floor.
        clock[0] += 1.0
        synth.on_response(poll, reply(21100), picl)                            # 20000 counts/s
        assert synth.interval == MIN_INTERVAL
        # Too short a baseline to divide by: the interval is left alone.
        held = synth.interval
        clock[0] += 0.1
        synth.on_response(poll, reply(21200), picl)
        assert synth.interval == held
        synth.on_command(stop2, picl)
        assert synth.on_response(hostpoll, idle_host, picl) == idle_host       # inactive
    finally:
        time.time = real_time

    # pitch (real-film) mode: type-5 event + type-3 code, one per half pitch,
    # on the scanner's own counter; nothing until the counter reaches the code.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1A\npitch = 1000\n")) or synth.cfg
    synth.on_command(start, picl)
    def reply_at(pos):
        r = bytes([1, 0x20, picl, 8]) + pos.to_bytes(2, "big") + bytes([1]) + bytes(27)
        return synth.on_response(poll, r, picl)
    picm = picl + 4
    motor_go = bytearray([4, 3, picm, 1, REG_MOTOR_GO])
    def edge_at(pos, reply_pos=None):
        """A reply carrying the controller's own film-edge pair."""
        rp = reply_pos if reply_pos is not None else pos + 20
        r = (bytes([1, 0x20, picl, 8]) + rp.to_bytes(2, "big") + bytes([2])
             + bytes([ENTRY_TYPE_FILM_EDGE, 0x01]) + pos.to_bytes(2, "big") + bytes([0xcc])
             + bytes([8, 0x01]) + (pos + 4).to_bytes(2, "big") + bytes([0xcc]))
        return synth.on_response(poll, r + bytes(RESP_LEN - len(r)), picl)

    r = reply_at(50)                                    # calibration: no code
    assert r[6] == 1 and r[7] == 0
    synth.on_command(motor_go, picl, picm)

    # The codes are anchored to the film edge the controller reports, not to
    # the counter's zero: the counter is reset by the trigger and the film sits
    # wherever it sits, which made the phase differ between identical scans.
    edge_at(100)
    assert synth.film_edge == 100
    # The code carrying `start` sits a fixed distance past the edge; the
    # sequence begins `lead` half-frames before that, advanced by whole frames
    # if that would fall before the counter's zero.
    first, first_frame = synth.next_code, synth.frame_raw
    assert first is not None and first > 0
    r = reply_at(first - 200)
    assert r[6] == 1 and r[7] == 0                      # nothing due yet
    r = reply_at(first + 40)             # film start (type 7) then the code
    assert r[6] == 3 and r[7] == 7 and r[8] == 0x02
    assert r[12] == 5
    assert decode_entry(bytes([first_frame & 1]) + r[19:22])[:3] == (79, 11, first_frame)
    # The next code is a half pitch on, and carries the next half frame.
    r = reply_at(first + 540)
    assert r[6] == 2 and decode_entry(bytes([(first_frame + 1) & 1]) + r[14:17])[2] == first_frame + 1

    # A different film position moves the codes with it: the offset from the
    # edge is what stays fixed.
    synth.on_command(stop2, picl)
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    edge_at(300)
    moved = synth.next_code
    assert moved == first + 200, (moved, first)      # the edge moved 200, so did the codes
    r = reply_at(moved + 40)
    assert r[6] == 3 and r[12] == 5                   # film start + first code
    code_pos = int.from_bytes(r[14:16], "big")
    if r[13] & 1:                                     # an "A" code: sent a shift early
        code_pos += round(1000 * A_SLOT_SHIFT)
    assert code_pos == moved, (code_pos, moved)

    # With no edge reported it waits EDGE_WAIT_PITCHES frame pitches -- past
    # where the input sensor has been seen to fire -- then falls back to a
    # grid from the counter's zero, so a silent sensor still yields distinct
    # sequential numbers rather than DX_Error's single collapsed filename.
    synth.on_command(stop2, picl)
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    wait_end = EDGE_WAIT_PITCHES * 1000                  # pitch is 1000 here
    for pos in (400, wait_end - 1):                      # inside the wait
        r = reply_at(pos)
        assert r[6] == 1 and r[7] == 0 and synth.next_code is None, pos
    r = reply_at(wait_end + 50)                          # past it: the grid
    grid = ((wait_end + 50 + 499) // 500) * 500          # next half-pitch point
    assert synth.next_code == grid, synth.next_code
    r = reply_at(grid + 40)
    assert r[6] == 3 and r[7] == ENTRY_TYPE_FILM_EDGE    # and codes flow
    # An edge arriving after the fallback leaves the grid alone: the codes
    # already sent cannot be recalled, and moving the grid under a sequence
    # would break the spacing the engine checks.
    held_phase = synth.next_code % 500
    edge_at(3000)                       # its reply may consume a due code,
    assert synth.next_code % 500 == held_phase           # but the grid holds

    # 16-bit wrap on the counter is unwrapped; the code position is sent mod 2^16.
    synth.on_command(stop2, picl)                       # a fresh scan, not a strip
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    edge_at(0xFC00, reply_pos=0xFC10)                   # edge late in the counter
    shift = round(1000 * A_SLOT_SHIFT)
    half = 500
    at = lambda pos: reply_at(pos & 0xFFFF)             # the counter is 16-bit
    base = synth.next_code
    assert base is not None and base < 0x10000          # first code before the wrap
    # Find the first code that lands past the 16-bit roll-over.
    k = 1
    while base + k * half <= 0xFFFF:
        k += 1
    crosser = base + k * half
    r = at(base + 40)                    # first code: the film start rides with it
    assert r[6] == 3 and r[7] == ENTRY_TYPE_FILM_EDGE
    for j in range(1, k):                # any codes before the wrap
        at(base + j * half + 40)
    r = at(crosser - 100)                # not reached yet
    assert r[6] == 1 and r[7] == 0
    r = at(crosser + 40)                 # now past the wrap
    assert r[6] == 2 and r[7] == ENTRY_TYPE_CODE_POS
    emitted = int.from_bytes(r[9:11], "big")             # sent mod 2^16
    expected = crosser - shift if (r[8] & 1) else crosser   # "A" code sent early
    assert emitted == (expected & 0xFFFF), (emitted, expected & 0xFFFF)
    # a_flag = 0 swaps which code gets flag/e0 "A"
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1A\npitch = 1000\na_flag = 0\n")) or synth.cfg
    synth.on_command(stop2, picl)                       # a fresh scan, not a strip
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    # a_flag = 0 swaps which parity takes the half-frame slot.  The anchor
    # still keeps the first code far enough past the counter's zero that the
    # slot shift cannot push it before the scan start.
    edge_at(100)
    first, first_frame = synth.next_code, synth.frame_raw
    assert first - round(1000 * A_SLOT_SHIFT) >= 0
    r = reply_at(first - 100)
    assert r[6] == 1 and r[7] == 0
    r = reply_at(first + 40)
    assert r[7] == 7 and r[12] == 5                      # film start still sent
    half_slot = first_frame % 2 == 0                     # inverted by a_flag = 0
    assert (r[13] & 1 == 1) is half_slot
    assert (r[18] == E0_HALF) is half_slot
    assert decode_entry(bytes([first_frame & 1]) + r[19:22])[2] == first_frame
    synth.on_command(stop2, picl)

    # film_lead moves the reported film start back, clamped at zero, and the
    # type-7 goes out once per transport run (a new 0x91 re-arms it).
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1A\npitch = 1000\n"
        "film_lead = 300\n")) or synth.cfg
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    edge_at(100)
    lead_first = synth.next_code                          # the anchored first code
    r = reply_at(lead_first - 100)                        # not reached yet
    assert r[6] == 1 and r[7] == 0
    r = reply_at(lead_first + 40)        # film start sits 300 before that code
    assert r[6] == 3 and r[7] == 7
    assert r[9:11] == (lead_first - 300).to_bytes(2, "big")
    r = reply_at(synth.next_code + 40)
    assert r[6] == 2 and r[7] == 5                       # film start not repeated
    synth.on_command(start, picl)                        # second 0x91: re-armed
    synth.on_command(motor_go, picl, picm)
    edge_at(2000)
    r = reply_at(synth.next_code + 40)
    assert r[6] == 3 and r[7] == 7
    film_start = int.from_bytes(r[9:11], "big")
    code = int.from_bytes(r[14:16], "big")
    if r[13] & 1:                                        # an "A" code: sent early
        code += round(1000 * A_SLOT_SHIFT)
    assert film_start == code - 300                      # still 300 before the code
    r = reply_at(synth.next_code + 40)
    assert r[6] == 2 and r[7] == 5                       # film start not repeated
    for bad in ("film_lead = -1",):
        try:
            DxConfig.parse(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    synth.on_command(stop2, picl)

    # A scan that aborts without the module seeing its stop command must not
    # leave the next scan looking like another strip.  The motor stop is
    # issued either way, so tracking it keeps the carry honest.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1A\npitch = 1000\n")) or synth.cfg
    motor_stop = bytearray([4, 3, picl + 4, 1, REG_MOTOR_IDLE])
    synth.on_command(stop2, picl)
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    assert synth.moving
    edge_at(100)
    reply_at(950); reply_at(1450)                 # a couple of codes go out
    carried = synth.frame_raw
    synth.on_command(motor_stop, picl, picm)      # the scan ends, or aborts
    assert not synth.moving
    synth.on_command(start, picl)                 # the next scan begins
    assert synth.frame_raw != carried, "frame count carried across a new scan"
    assert synth.frame_raw == max(0, synth.cfg.start - synth.lead_now())

    # Strips of one scan continue the count (the motor never stops between
    # them, so they are one roll in order); a scan start with the transport
    # stopped is a new scan and resets to `start`.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "product = 79\ngeneration = 11\nstart = 1A\n"
        "pitch = 1000\n")) or synth.cfg
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    edge_at(100)
    base = synth.frame_raw
    r = reply_at(synth.next_code + 40)                   # strip 1: film start + first code
    assert r[7] == 7 and decode_entry(bytes([base & 1]) + r[19:22])[2] == base
    r = reply_at(synth.next_code + 40)
    assert decode_entry(bytes([(base + 1) & 1]) + r[14:17])[2] == base + 1
    synth.on_command(start, picl)                        # next strip, motor running
    edge_at(2000)                                        # the next strip's edge
    r = reply_at(synth.next_code + 40)
    assert r[6] == 3 and r[7] == 7                       # film start again, frames carry
    assert decode_entry(bytes([base & 1]) + r[19:22])[2] == base + 2
    synth.on_command(stop2, picl)                        # scan ends, motor stops
    synth.on_command(start, picl)                        # whole new scan
    synth.on_command(motor_go, picl, picm)
    edge_at(100)
    r = reply_at(synth.next_code + 40)
    assert decode_entry(bytes([base & 1]) + r[19:22])[2] == base   # reset to the start
    synth.on_command(stop2, picl)

    # The 0x91 scan-params table: the trigger's payload names the
    # configuration, so pitch and lead follow without measuring anything.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse("start = 1A\n")) or synth.cfg
    for params, half, lead in ((0x0107, 810, 0), (0x00c5, 1620, 0), (0x0075, 1215, 0),
                               (0x004d, 2430, 0), (0x003c, 1620, 0)):
        trig = bytearray([2, 6, picl, 3, REG_DX_START, params & 0xff, params >> 8, 1])
        synth.on_command(stop2, picl)
        synth.on_command(trig, picl)
        assert synth.half_pitch_now() == half, (hex(params), synth.half_pitch_now())
        assert synth.lead_now() == lead, hex(params)
    # An unknown value drops to the fallbacks and says so.
    said = []
    synth.say = said.append
    trig = bytearray([2, 6, picl, 3, REG_DX_START, 0x99, 0x00, 1])
    synth.on_command(stop2, picl)
    synth.on_command(trig, picl)
    assert synth.params_divisor is None
    assert any("not in the table" in m for m in said), said
    synth.say = lambda *a: None
    # An explicit pitch still beats the table.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse("start = 1A\npitch = 999\n")) or synth.cfg
    trig = bytearray([2, 6, picl, 3, REG_DX_START, 0x75, 0x00, 1])
    synth.on_command(stop2, picl)
    synth.on_command(trig, picl)
    assert synth.frame_pitch() == (999, "set")

    # No film code, only frames: the entries carry product/generation 0, since
    # a frame number cannot be sent without some product code, and numbering
    # still runs.
    synth.load = lambda: setattr(synth, "cfg", DxConfig.parse(
        "start = 1A\npitch = 1000\n")) or synth.cfg
    synth.on_command(stop2, picl)
    synth.on_command(start, picl)
    synth.on_command(motor_go, picl, picm)
    edge_at(100)
    base = synth.frame_raw
    r = reply_at(synth.next_code + 40)
    assert r[6] == 3 and r[7] == ENTRY_TYPE_FILM_EDGE and r[12] == ENTRY_TYPE_CODE_POS
    assert decode_entry(bytes([base & 1]) + r[19:22]) == (UNKNOWN_PRODUCT,
                                                          UNKNOWN_GENERATION, base, True)
    r = reply_at(synth.next_code + 40)
    assert decode_entry(bytes([(base + 1) & 1]) + r[14:17])[2] == base + 1   # still advancing
    synth.on_command(stop2, picl)

    # No config file at all: inert, no logging.
    quiet = []
    inert = DxSynth(say=quiet.append, config_path="/nonexistent/dx.conf")
    inert.on_command(start, picl)
    assert inert.on_response(poll, dead, picl) == dead and not quiet
    print("dxsynth selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
