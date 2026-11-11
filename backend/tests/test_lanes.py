import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import lanes


@pytest.fixture
def lane_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lanes, "lanes_dir", lambda: tmp_path)
    monkeypatch.setattr(lanes, "POLL_CHECK_SECONDS", 0.01)
    return tmp_path


def test_worker_polls_and_reports(lane_dir, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "poll_on_startup", True)
    calls = []
    monkeypatch.setattr(lanes, "poll_lane", lambda lane: calls.append(lane) or {"lane": lane, "new_jobs": 3})
    lanes.serve("slow", max_polls=1)
    st = lanes.lane_status("slow")
    assert calls == ["slow"]
    assert st["polling"] is False and st["last"]["new_jobs"] == 3 and st["last"]["finished_at"]
    assert st["pid"] and st["heartbeat"]


def test_check_now_polls_the_fast_lane_before_it_is_due(lane_dir, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "poll_on_startup", False)  # not due for poll_minutes
    monkeypatch.setattr(lanes, "poll_lane", lambda lane: {"lane": lane})
    lanes.request_poll("fast")
    lanes.serve("fast", max_polls=1)  # returns only because the request started a poll
    assert lanes.lane_status("fast")["last"]["lane"] == "fast"
    assert not (lane_dir / "fast.now").exists()  # the request is used up


def test_a_failed_poll_is_reported_and_the_worker_goes_on(lane_dir, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "poll_on_startup", True)

    def boom(lane):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(lanes, "poll_lane", boom)
    lanes.serve("bulk", max_polls=1)
    assert "error" in lanes.lane_status("bulk")["last"]


def test_watch_lanes_reports_each_finished_poll_once(lane_dir):
    lanes._write_status("fast", {"polling": False, "last": {"finished_at": "old"}})
    seen, stop = [], threading.Event()
    t = threading.Thread(target=lanes.watch_lanes, args=(lambda lane, s: seen.append((lane, s["finished_at"])), stop, 0.01))
    t.start()
    try:
        lanes._write_status("fast", {"polling": False, "last": {"finished_at": "new"}})
        for _ in range(200):
            if seen:
                break
            threading.Event().wait(0.01)
    finally:
        stop.set()
        t.join()
    assert seen == [("fast", "new")]  # the poll from before the watch started isn't reported


def test_polling_flag_needs_a_recent_heartbeat(lane_dir, monkeypatch):
    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "poll_workers", True)
    (lane_dir / "fast.json").write_text(
        '{"polling": true, "heartbeat": "%s"}' % (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
    assert main._lane_busy("fast") is False  # a worker that died mid-poll
    lanes._write_status("fast", {"polling": True})
    assert main._lane_busy("fast") is True


def test_check_now_asks_the_worker_in_worker_mode(lane_dir, monkeypatch, session):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from tests.conftest import sign_up

    with TestClient(app) as client:
        sign_up(client)  # the first account is the admin
        monkeypatch.setattr(settings, "poll_workers", True)
        monkeypatch.setattr(settings, "scheduler_enabled", True)
        assert client.post("/api/poll").json() == {"started": True}
    assert (lane_dir / "fast.now").exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="the POSIX watcher follows the real parent pid")
def test_worker_notices_when_the_backend_is_gone():
    import subprocess
    import sys

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    alive = lanes._parent_watcher(parent.pid)
    assert alive()
    parent.wait()
    assert not alive()


def test_heartbeat_continues_during_a_long_poll(lane_dir, monkeypatch):
    import json
    import time as _time

    from app.config import settings

    monkeypatch.setattr(settings, "poll_on_startup", True)
    monkeypatch.setattr(lanes, "HEARTBEAT_SECONDS", 0.02)
    beats = []

    def slow_poll(lane):
        first = json.loads((lane_dir / "slow.json").read_text())["heartbeat"]
        _time.sleep(0.2)
        beats.append((first, json.loads((lane_dir / "slow.json").read_text())))
        return {}

    monkeypatch.setattr(lanes, "poll_lane", slow_poll)
    lanes.serve("slow", max_polls=1)
    first, during = beats[0]
    assert during["polling"] is True and during["heartbeat"] > first
