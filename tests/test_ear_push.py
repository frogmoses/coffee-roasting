"""Ear -> Artisan first-crack push (Phase D): message, server send, session seam."""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ear"))

import artisan_sync  # noqa: E402


class _FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def test_push_message_matches_artisan_defaults():
    assert artisan_sync.FC_PUSH_MESSAGE == {"pushMessage": "addEvent",
                                            "data": {"event": "firstCrackBeginningEvent"}}


def test_push_fc_sends_from_another_thread_only_when_connected():
    server = artisan_sync.ArtisanServer(port=0)
    assert server.push_fc() is False                       # nothing connected yet

    async def run():
        server._loop = asyncio.get_running_loop()
        sock = _FakeSocket()
        server._websocket = sock
        result = {}

        def from_audio_thread():
            result["sent"] = server.push_fc()
        t = threading.Thread(target=from_audio_thread)
        t.start()
        t.join()
        await asyncio.sleep(0.05)                          # let the scheduled send run
        return result["sent"], sock.sent

    sent, messages = asyncio.run(run())
    assert sent is True
    assert json.loads(messages[0]) == artisan_sync.FC_PUSH_MESSAGE


def test_register_crack_pushes_once_and_never_in_record_only():
    pytest.importorskip("numpy")
    pytest.importorskip("sounddevice")
    import ear_session

    class _Tracker:
        armed_at = 0.0
        armed_source = "manual"

        def __init__(self):
            self.calls = 0

        def add(self, elapsed, epoch):
            self.calls += 1
            # declare on the first call, then never again (like the real one)
            if self.calls == 1:
                return {"elapsed": elapsed, "epoch": epoch, "count_in_window": 7, "window_s": 20.0}
            return None

        def cracks_per_minute(self, elapsed):
            return 21.0

    class _Artisan:
        def __init__(self, connected):
            self.connected = connected
            self.pushes = 0

        def push_fc(self):
            self.pushes += 1
            return self.connected

    def make(push_artisan, record_only=False, connected=True):
        s = object.__new__(ear_session.EarSession)
        s.charge_epoch = time.time() - 600
        s.rule_epoch = s.charge_epoch
        s.record_only = record_only
        s.push_artisan = push_artisan and not record_only
        s.fc_pushed = None
        s.cracks = []
        s._lock = threading.Lock()
        s.tracker = _Tracker()
        s.artisan = _Artisan(connected)
        return s

    def crack():
        return {"epoch": time.time(), "peak_db": 20.0, "dur_ms": 3.0, "flatness": 0.4}

    # alerts are silenced for the test
    ear_session.alert.announce_fc = lambda *a, **k: None

    s = make(push_artisan=True)
    s._register_crack(crack())
    s._register_crack(crack())
    assert s.artisan.pushes == 1 and s.fc_pushed["sent"] is True
    assert s.fc_pushed["latency_s"] is not None

    s = make(push_artisan=True, record_only=True)
    s._register_crack(crack())
    assert s.artisan.pushes == 0 and s.fc_pushed is None

    s = make(push_artisan=False)
    s._register_crack(crack())
    assert s.artisan.pushes == 0

    s = make(push_artisan=True, connected=False)
    s._register_crack(crack())
    assert s.artisan.pushes == 1 and s.fc_pushed["sent"] is False

    # a late-started ear counts on a provisional clock: alert, never push
    s = make(push_artisan=True)
    s.charge_epoch = None
    s.rule_epoch = time.time() - 600
    s._register_crack(crack())
    assert s.artisan.pushes == 0 and s.fc_pushed is None
