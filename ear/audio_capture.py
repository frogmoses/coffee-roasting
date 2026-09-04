"""Microphone capture for the ear: PortAudio stream -> queue -> WAV + detector.

The PortAudio callback does nothing but copy the block into a queue and
count overflows, so it can never stall the audio driver. The consumer
(EarSession's audio thread) calls read(), which also appends the block to
the roast WAV and keeps level statistics for the sidecar.

sounddevice is imported lazily so tune.py and the tests can import the
detector without PortAudio present.
"""

import queue
import time
import wave


def _sd():
    """Import sounddevice on demand with a clear error if it is missing."""
    try:
        import sounddevice
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "sounddevice is not installed (uv pip install sounddevice; "
            "needs libportaudio2 on the roaster)"
        ) from e
    return sounddevice


def list_input_devices():
    """All input-capable devices as dicts {index, name, channels, samplerate, hostapi}."""
    sd = _sd()
    apis = sd.query_hostapis()
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        api = apis[dev["hostapi"]]["name"] if dev.get("hostapi") is not None else ""
        out.append({
            "index": idx,
            "name": dev["name"],
            "channels": dev["max_input_channels"],
            "samplerate": dev.get("default_samplerate"),
            "hostapi": api,
        })
    return out


def select_device(spec):
    """Resolve a device spec to a PortAudio index.

    Accepts an integer index, or a case-insensitive substring of an
    input device's name (e.g. "Sound Blaster", "hw:1", "pipewire").
    Raises ValueError listing the candidates when 0 or >1 match.
    """
    devices = list_input_devices()
    if spec is None or str(spec).strip() == "":
        raise ValueError("no device given; set EAR_DEVICE or pass --device")
    text = str(spec).strip()
    if text.isdigit():
        idx = int(text)
        if any(d["index"] == idx for d in devices):
            return idx
        raise ValueError(f"device index {idx} is not an input device")
    matches = [d for d in devices if text.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]["index"]
    names = ", ".join(f"[{d['index']}] {d['name']}" for d in devices) or "(none)"
    if not matches:
        raise ValueError(f"no input device matches {text!r}; inputs: {names}")
    raise ValueError(f"{text!r} matches several devices: "
                     + ", ".join(f"[{d['index']}] {d['name']}" for d in matches))


class AudioCapture:
    """Mono int16 capture with an optional WAV recording."""

    def __init__(self, device, sample_rate=48000, blocksize=4800, wav_path=None):
        self.device = device
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.wav_path = wav_path
        self._queue = queue.Queue(maxsize=200)  # ~20 s of 100 ms blocks
        self._stream = None
        self._wav = None
        self.running = False
        self.start_epoch = None
        self.stop_epoch = None
        # Statistics for the sidecar
        self.blocks = 0
        self.overflows = 0
        self.dropped = 0        # blocks lost because the consumer fell behind
        self.clipped_blocks = 0
        self.peak = 0           # max |sample| seen (int16 units)

    def _callback(self, indata, frames, time_info, status):
        """PortAudio thread: copy and enqueue, never block."""
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        # Epoch of the block's first sample, from the wall clock
        epoch = time.time() - frames / self.sample_rate
        try:
            self._queue.put_nowait((indata[:, 0].copy(), epoch))
        except queue.Full:
            self.dropped += 1

    def _candidate_rates(self):
        """Requested rate first, then the device default, then common rates."""
        sd = _sd()
        rates = [self.sample_rate]
        try:
            default = int(sd.query_devices(self.device)["default_samplerate"])
            rates.append(default)
        except Exception:  # device query is best-effort
            pass
        rates += [48000, 44100, 32000, 16000]
        seen = []
        for r in rates:
            if r not in seen:
                seen.append(r)
        return seen

    def start(self):
        """Start the stream, then open the WAV at the rate that actually opened.

        Not every input accepts 48 kHz through ALSA (a USB headset may only
        do 16 kHz), so the requested rate is tried first and the device's
        default and common rates after it. self.sample_rate and blocksize
        are updated to what was opened; the detector must be built after.
        """
        sd = _sd()
        last_err = None
        for rate in self._candidate_rates():
            blocksize = max(256, int(rate / 10))  # keep ~100 ms blocks
            try:
                stream = sd.InputStream(
                    device=self.device,
                    channels=1,
                    samplerate=rate,
                    blocksize=blocksize,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
            except Exception as e:  # PortAudioError or ValueError from checks
                last_err = e
                continue
            self._stream = stream
            self.sample_rate = rate
            self.blocksize = blocksize
            break
        if self._stream is None:
            raise RuntimeError(f"could not open input {self.device!r} at any sample rate: {last_err}")
        if self.wav_path:
            self._wav = wave.open(str(self.wav_path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self.sample_rate)
        self.start_epoch = time.time()
        self.running = True

    def read(self, timeout=0.5):
        """Next (block, epoch) from the queue, or None on timeout.

        Writes the block to the WAV and updates level statistics.
        """
        try:
            block, epoch = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        self.blocks += 1
        peak = int(abs(block).max()) if block.size else 0
        if peak > self.peak:
            self.peak = peak
        if peak >= 32767:
            self.clipped_blocks += 1
        if self._wav is not None:
            self._wav.writeframes(block.tobytes())
        return block, epoch

    def drain(self):
        """Consume whatever is queued (used while stopping)."""
        out = []
        while True:
            item = self.read(timeout=0.0)
            if item is None:
                return out
            out.append(item)

    def stop(self):
        """Stop the stream and close the WAV (header is written on close)."""
        if not self.running:
            return
        self.running = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        finally:
            self._stream = None
            self.stop_epoch = time.time()
            if self._wav is not None:
                self._wav.close()
                self._wav = None

    def peak_dbfs(self):
        """Peak level so far in dBFS (None before any audio)."""
        if self.peak <= 0:
            return None
        import math
        return round(20 * math.log10(self.peak / 32768.0), 1)

    def duration_s(self):
        """Seconds of audio consumed so far."""
        return round(self.blocks * self.blocksize / self.sample_rate, 1)

    def stats(self):
        """Capture statistics for the sidecar."""
        return {
            "peak_dbfs": self.peak_dbfs(),
            "clipped_blocks": self.clipped_blocks,
            "overflows": self.overflows,
            "dropped_blocks": self.dropped,
            "duration_s": self.duration_s(),
        }
