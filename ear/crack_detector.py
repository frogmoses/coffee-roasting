"""Click detector and first-crack rule for the roaster microphone ("ear").

Classical DSP, numpy only. A coffee crack is a 1-5 ms broadband click; the
Hottop's drum motor and fan are low-frequency and tonal. So: frame the audio,
measure energy in a high band (3-12 kHz by default), track an adaptive noise
floor, and call a crack when a short, spectrally flat burst pops above it.
The same ClickDetector runs live (fed 100 ms blocks) and offline (tune.py
over a saved WAV), so parameters tuned on recordings apply unchanged.

FirstCrackTracker turns individual cracks into an FC-onset decision: after
arming (DRY event from Artisan, or a fallback time), the first time N cracks
land inside a rolling window, first crack is declared at the time of the
first crack in that window. Second crack is not distinguished in v1 — the
tracker declares once, and SC starts minutes later.

Defaults are starting points. thresh_db, band, max_dur_ms, min_flatness and
the n/window_s rule must be tuned on real recordings (see tune.py).
"""

import math

import numpy as np

# Detector parameters. Copied verbatim into every sidecar so a recording can
# always be re-run with the settings that produced its live verdict.
DETECTOR_DEFAULTS = {
    "sample_rate": 48000,
    "frame": 512,            # samples per analysis frame (10.7 ms @ 48 kHz)
    "hop": 128,              # frame step (2.7 ms) — time resolution of onsets
    "band": [3000, 12000],   # Hz; above motor/fan harmonics, below codec hiss
    "thresh_db": 12.0,       # onset when band energy exceeds floor by this
    "floor_window_s": 2.0,   # history length for the adaptive floor
    "floor_percentile": 20,  # low percentile so clicks never lift the floor
    "min_dur_ms": 1.0,       # shorter than this is a single-sample glitch
    "max_dur_ms": 30.0,      # longer is a beep, speech, fan surge, handling
    "min_flatness": 0.15,    # spectral flatness at peak; tonal beeps are < 0.1
    "tonal_blank_ms": 300,   # ignore onsets after a rejected long/tonal event
    "min_gap_ms": 40,        # merge onsets closer than this (drum reverberation)
    "warmup_s": 0.5,         # no detections until this much floor history exists
}

# First-crack declaration rule
FC_RULE_DEFAULTS = {
    "n": 4,                  # accepted cracks ...
    "window_s": 20.0,        # ... inside this rolling window declares FC
    "min_elapsed_s": 240.0,  # never declare before this many seconds after CHARGE
}

_EPS = 1e-12


def _spectral_flatness(power):
    """Geometric mean / arithmetic mean of a power spectrum slice (0..1).

    White noise and clicks score high (~0.4-0.6); a sine or beep scores
    near zero. Guarded against empty/zero input.
    """
    if power.size == 0:
        return 0.0
    p = power + _EPS
    return float(math.exp(np.mean(np.log(p))) / np.mean(p))


class ClickDetector:
    """Streaming click detector. Feed int16 (or float) mono blocks in order."""

    def __init__(self, params=None):
        self.params = dict(DETECTOR_DEFAULTS)
        if params:
            self.params.update(params)
        p = self.params
        self.sr = int(p["sample_rate"])
        self.frame = int(p["frame"])
        self.hop = int(p["hop"])
        self._window = np.hanning(self.frame).astype(np.float32)
        # FFT bins covered by the analysis band
        freqs = np.fft.rfftfreq(self.frame, 1.0 / self.sr)
        lo, hi = p["band"]
        self._band = np.where((freqs >= lo) & (freqs <= hi))[0]
        if self._band.size == 0:
            raise ValueError(f"band {p['band']} selects no FFT bins at frame {self.frame}")

        # Frame-count conversions for the time-based parameters
        ms_per_hop = 1000.0 * self.hop / self.sr
        self._max_dur_frames = max(1, int(round(p["max_dur_ms"] / ms_per_hop)))
        self._min_dur_frames = max(1, int(round(p["min_dur_ms"] / ms_per_hop)))
        self._blank_frames = int(round(p["tonal_blank_ms"] / ms_per_hop))
        self._gap_frames = int(round(p["min_gap_ms"] / ms_per_hop))
        self._floor_len = max(8, int(round(p["floor_window_s"] * self.sr / self.hop)))
        self._warmup_frames = int(round(p["warmup_s"] * self.sr / self.hop))
        self._ms_per_hop = ms_per_hop

        # Streaming state
        self._carry = np.zeros(0, dtype=np.float32)  # tail samples not yet framed
        self._samples_in = 0          # total samples consumed (stream position)
        self._frames_out = 0          # frames analyzed so far
        self._stream_epoch0 = None    # wall-clock epoch of stream sample 0
        self._floor_hist = np.full(self._floor_len, np.nan, dtype=np.float32)
        self._floor_pos = 0
        self._floor_count = 0
        self._floor_db = None
        self._event = None            # open burst: dict(start, n, peak, flat)
        self._blank_until = -1        # frame index until which onsets are ignored
        self._last_accept = -10 ** 9  # frame index of the last accepted crack
        self._level_db = None         # most recent frame band level

    # ---- public API ----

    def floor_db(self):
        """Current adaptive noise floor in dB (None before warm-up)."""
        return self._floor_db

    def level(self):
        """Band level of the most recent frame in dB (None before any audio)."""
        return self._level_db

    def feed(self, block, block_epoch=None):
        """Analyze the next block of samples.

        Args:
            block: 1-D numpy array, int16 or float in [-1, 1].
            block_epoch: wall-clock time (time.time()) of the block's first
                sample. Optional; when omitted, crack epochs are seconds from
                stream start.

        Returns:
            List of crack dicts {epoch, stream_time, peak_db, dur_ms, flatness}
            for cracks that closed during this block.
        """
        x = np.asarray(block)
        if x.dtype.kind in "iu":
            x = x.astype(np.float32) / 32768.0
        else:
            x = x.astype(np.float32)
        if x.ndim > 1:
            x = x.reshape(-1)

        # Re-derive the stream's epoch origin from every block so clock drift
        # between PortAudio and time.time() stays bounded.
        if block_epoch is not None:
            self._stream_epoch0 = block_epoch - self._samples_in / self.sr
        self._samples_in += x.size

        buf = np.concatenate([self._carry, x]) if self._carry.size else x
        n_frames = (buf.size - self.frame) // self.hop + 1
        if n_frames <= 0:
            self._carry = buf
            return []

        # Batched STFT over all complete frames in the buffer
        frames = np.lib.stride_tricks.as_strided(
            buf,
            shape=(n_frames, self.frame),
            strides=(buf.strides[0] * self.hop, buf.strides[0]),
        )
        spec = np.fft.rfft(frames * self._window, axis=1)
        power = (spec.real ** 2 + spec.imag ** 2)[:, self._band]
        band_db = 10.0 * np.log10(power.sum(axis=1) + _EPS)

        # Keep the unframed tail for the next block
        consumed = n_frames * self.hop
        self._carry = buf[consumed:].copy()

        cracks = []
        for i in range(n_frames):
            crack = self._step(band_db[i], power[i])
            if crack is not None:
                cracks.append(crack)
        self._level_db = float(band_db[-1])
        return cracks

    # ---- internals ----

    def _push_floor(self, db):
        """Append a frame level to the floor history and refresh the floor."""
        self._floor_hist[self._floor_pos] = db
        self._floor_pos = (self._floor_pos + 1) % self._floor_len
        if self._floor_count < self._floor_len:
            self._floor_count += 1
        # Recompute once per hop is cheap enough (~750 values); percentile of
        # the filled part only, so warm-up doesn't see NaN padding
        if self._floor_count >= self._warmup_frames:
            hist = self._floor_hist[:self._floor_count] if self._floor_count < self._floor_len else self._floor_hist
            self._floor_db = float(np.percentile(hist, self.params["floor_percentile"]))

    def _frame_time(self, frame_idx):
        """Stream time (s) of a frame's center — the first hot frame starts
        up to one frame before the click, so its center is the better onset."""
        return (frame_idx * self.hop + self.frame / 2) / self.sr

    def _step(self, db, power):
        """Advance one frame; return a crack dict when a burst closes as accepted."""
        f = self._frames_out
        self._frames_out += 1
        result = None

        # The floor tracks only while no burst is open, so a burst never
        # drags its own level into the floor estimate
        if self._event is None:
            self._push_floor(db)
        floor = self._floor_db
        if floor is None:
            return None
        hot = db > floor + self.params["thresh_db"]

        if self._event is None:
            if hot and f >= self._blank_until:
                self._event = {"start": f, "n": 1, "peak": db - floor,
                               "flat": _spectral_flatness(power)}
            return None

        ev = self._event
        if hot:
            ev["n"] += 1
            rel = db - floor
            if rel > ev["peak"]:
                ev["peak"] = rel
                ev["flat"] = _spectral_flatness(power)
            # Too long to be a crack: reject now and blank the tail
            if ev["n"] > self._max_dur_frames:
                self._event = None
                self._blank_until = f + self._blank_frames
            return None

        # Burst closed — judge it
        self._event = None
        dur_frames = ev["n"]
        tonal = ev["flat"] < self.params["min_flatness"]
        if tonal:
            self._blank_until = f + self._blank_frames
            return None
        if dur_frames < self._min_dur_frames:
            return None
        if ev["start"] - self._last_accept < self._gap_frames:
            return None  # merged into the previous crack
        self._last_accept = ev["start"]
        stream_time = self._frame_time(ev["start"])
        epoch = (self._stream_epoch0 + stream_time) if self._stream_epoch0 is not None else stream_time
        result = {
            "epoch": round(epoch, 3),
            "stream_time": round(stream_time, 3),
            "peak_db": round(float(ev["peak"]), 1),
            "dur_ms": round(dur_frames * self._ms_per_hop, 1),
            "flatness": round(float(ev["flat"]), 3),
        }
        return result


def detect_cracks(samples, sample_rate=None, params=None, start_epoch=0.0, block=4800):
    """Offline wrapper: run ClickDetector over a whole recording.

    Feeds the samples in `block`-sized chunks exactly as the live session
    does, so offline and live results are identical for the same audio.

    Args:
        samples: 1-D int16/float numpy array.
        sample_rate: Hz; overrides params["sample_rate"] when given.
        params: detector parameter overrides.
        start_epoch: wall-clock epoch of sample 0 (0.0 → epochs are stream time).
        block: samples per feed() call.

    Returns:
        List of crack dicts (see ClickDetector.feed).
    """
    p = dict(params or {})
    if sample_rate:
        p["sample_rate"] = sample_rate
    det = ClickDetector(p)
    cracks = []
    for i in range(0, len(samples), block):
        chunk = samples[i:i + block]
        cracks.extend(det.feed(chunk, start_epoch + i / det.sr))
    return cracks


class FirstCrackTracker:
    """Turns accepted cracks (with elapsed seconds since CHARGE) into an FC call."""

    def __init__(self, n=None, window_s=None, min_elapsed_s=None):
        self.n = int(n if n is not None else FC_RULE_DEFAULTS["n"])
        self.window_s = float(window_s if window_s is not None else FC_RULE_DEFAULTS["window_s"])
        self.min_elapsed_s = float(min_elapsed_s if min_elapsed_s is not None else FC_RULE_DEFAULTS["min_elapsed_s"])
        self.armed_at = None       # elapsed seconds when armed
        self.armed_source = None   # "DRY", "CHARGE+300", "manual"
        self.declared = None       # fc_detected dict once declared
        self.first_crack_elapsed = None
        self.peak_cpm = 0.0
        self.peak_cpm_elapsed = None
        self.accepted = []         # elapsed times of accepted cracks

    def rule(self):
        """The rule as a dict for the sidecar."""
        return {"n": self.n, "window_s": self.window_s, "min_elapsed_s": self.min_elapsed_s}

    def arm(self, elapsed, source):
        """Start counting cracks from `elapsed` seconds after CHARGE."""
        if self.armed_at is None:
            self.armed_at = float(elapsed)
            self.armed_source = source

    def add(self, elapsed, epoch=None):
        """Register an accepted crack at `elapsed` seconds after CHARGE.

        Returns the fc_detected dict on the call that declares first crack,
        otherwise None. Cracks before arming or before min_elapsed_s are
        ignored (drying-phase tumble clicks).
        """
        if self.armed_at is None or elapsed < self.armed_at or elapsed < self.min_elapsed_s:
            return None
        self.accepted.append(float(elapsed))
        if self.first_crack_elapsed is None:
            self.first_crack_elapsed = float(elapsed)
        in_window = [t for t in self.accepted if t > elapsed - self.window_s]
        cpm = len(in_window) * 60.0 / self.window_s
        if cpm > self.peak_cpm:
            self.peak_cpm = cpm
            self.peak_cpm_elapsed = float(elapsed)
        if self.declared is None and len(in_window) >= self.n:
            onset = in_window[0]
            self.declared = {
                "elapsed": round(onset, 1),
                "epoch": round(epoch - (elapsed - onset), 3) if epoch is not None else None,
                "first_crack_elapsed": round(self.first_crack_elapsed, 1),
                "count_in_window": len(in_window),
                "window_s": self.window_s,
                "n": self.n,
                "declared_at_elapsed": round(elapsed, 1),
                "peak_cpm": round(self.peak_cpm, 1),
                "peak_cpm_elapsed": round(self.peak_cpm_elapsed, 1),
            }
            return self.declared
        return None

    def cracks_per_minute(self, now_elapsed, span_s=60.0):
        """Accepted cracks in the last `span_s` seconds, scaled to per minute."""
        recent = [t for t in self.accepted if t > now_elapsed - span_s]
        return len(recent) * 60.0 / span_s

    def finalize(self):
        """Refresh peak figures into the declared dict at save time."""
        if self.declared is not None:
            self.declared["peak_cpm"] = round(self.peak_cpm, 1)
            self.declared["peak_cpm_elapsed"] = round(self.peak_cpm_elapsed, 1) if self.peak_cpm_elapsed is not None else None
        return self.declared

    def status(self, now_elapsed=None):
        """Badge dict for the display (sentinel_display's crack_status shape)."""
        if self.declared is None:
            return None
        cpm = self.cracks_per_minute(now_elapsed) if now_elapsed is not None else self.declared["peak_cpm"]
        return {
            "crack_type": "first",
            "cracks_per_minute": round(cpm, 1),
            "elapsed_seconds": self.declared["elapsed"],
        }
