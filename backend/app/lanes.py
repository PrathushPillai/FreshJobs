"""Polling lanes in their own processes.

One Python process runs Python code on one core at a time (the GIL). With the API, the three polling lanes'
downloads and the save step all in the backend process, it ran at 112% of one core and saving got a slice of
it: a board that saves in 0.09 s on its own took 0.4–0.5 s in a poll (2026-11-10). So by default the backend
starts one worker process per lane (and one for discovery), each with its own interpreter:

    python -m app.lanes fast|slow|bulk|discovery      # started by main.lifespan; not meant to be run by hand

A worker watches the backend process (FRESHJOBS_PARENT_PID): when the backend stops (or is killed), the
worker exits within seconds. Saving commits per source, so stopping mid-poll loses nothing.

The worker writes `lanes/{lane}.json` next to the database after every poll:
{"pid", "polling", "started_at", "last": <poll summary>, "heartbeat"}. The API reads these files
(`lane_status`) for /api/status, and runs its own after-poll work (clearing the page cache, the location
picker counts, role suggestions) when a lane's `last.finished_at` changes (`watch_lanes`). "Check now" writes
`lanes/fast.now`, which the fast worker picks up within POLL_CHECK_SECONDS.

settings.poll_workers = false keeps the old way: every lane in a thread of the backend process.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

LANES = ("fast", "slow", "bulk")  # bulk: rate-limited ATSs with thousands of boards (Workable)
WORKERS = (*LANES, "discovery")
POLL_CHECK_SECONDS = 5
HEARTBEAT_SECONDS = 30  # main._lane_busy trusts polling=true for 2 minutes after a heartbeat


def lanes_dir() -> Path:
    from app.matching import _models_dir

    d = Path(_models_dir()).parent / "lanes"  # next to the database, like autodiscover.json
    d.mkdir(parents=True, exist_ok=True)
    return d


def lane_status(lane: str) -> dict:
    try:
        return json.loads((lanes_dir() / f"{lane}.json").read_text())
    except (OSError, ValueError):
        return {}


def _write_status(lane: str, data: dict) -> None:
    path = lanes_dir() / f"{lane}.json"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({**data, "pid": os.getpid(), "heartbeat": datetime.now(timezone.utc).isoformat()}))
    except OSError:
        log.warning("couldn't write %s", tmp, exc_info=True)
        return
    for attempt in range(20):
        try:
            os.replace(tmp, path)  # readers never see a half-written file
            return
        except PermissionError:  # Windows: the API is reading the file this instant
            time.sleep(0.05)
        except OSError:
            break
    log.warning("couldn't replace %s", path)


def request_poll(lane: str = "fast") -> None:
    """Ask a lane's worker to poll now ("Check now")."""
    (lanes_dir() / f"{lane}.now").write_text(datetime.now(timezone.utc).isoformat())


def _take_request(lane: str) -> bool:
    path = lanes_dir() / f"{lane}.now"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def checkpoint() -> None:
    """Fold the WAL back into the database (see main._checkpoint)."""
    from app.db import engine, is_sqlite

    if not is_sqlite:
        return
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        log.warning("WAL checkpoint failed", exc_info=True)


def poll_lane(lane: str) -> dict:
    """One poll of a lane, with the after-poll work that belongs in the worker (not the API)."""
    from app.pipeline import scoring
    from app.pipeline.alerts import deliver_alerts
    from app.pipeline.poller import poll_once

    summary = asyncio.run(poll_once(lane))
    checkpoint()
    if lane == "fast":
        scoring.refresh_in_background()  # score this poll's new jobs against each resume
        deliver_alerts()  # each user's due alerts (opt-in, per their frequency and daily cap)
    return summary


def run_discovery_once() -> dict:
    from app.pipeline import autodiscover

    summary = asyncio.run(autodiscover.run_once())
    summary["jobdiva_added"] = asyncio.run(autodiscover.jobdiva_portals())  # weekly
    summary["crawl_added"] = asyncio.run(autodiscover.crawl_new_index())  # only when a new index is out
    return summary


def _parent_watcher(parent_pid: int):
    """A function that says whether the backend process is still running.

    Not a pipe on stdin: on Windows a thread blocked reading a pipe stalls other threads that touch the
    standard handles, and the worker hung for good while importing numpy (2026-11-10). On Windows it keeps a
    handle to the backend process (so a reused pid can't fool it); elsewhere it watches the parent pid."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if not handle:
            return lambda: False
        return lambda: kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    return lambda: os.getppid() == parent_pid


def _exit_when_parent_goes(parent_pid: int) -> None:
    alive = _parent_watcher(parent_pid)
    while alive():
        time.sleep(2)
    log.info("backend gone; worker exiting")
    os._exit(0)


PROFILE_INTERVAL = 0.01  # seconds between samples


def sample_stacks(seconds: float, *, interval: float = PROFILE_INTERVAL, skip: set[int] = frozenset()) -> str:
    """A text report of where this process's threads spend their time, from sampling their stacks.

    Started by writing a number of seconds to lanes/{lane}.profile (see _profile_watcher); the report goes to
    lanes/{lane}.profile.txt. Written because the slow worker ran at 100% of a core and saved only ~35 boards a
    minute (2026-11-11) while each board's fetch and save measured well under a second, and py-spy isn't installed.
    """
    import collections
    import sys

    me = threading.get_ident()
    own: collections.Counter = collections.Counter()  # innermost frame in our code (or the first one)
    leaf: collections.Counter = collections.Counter()  # innermost frame anywhere
    samples = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for ident, frame in sys._current_frames().items():
            if ident == me or ident in skip:
                continue
            stack = []
            f = frame
            while f is not None and len(stack) < 60:
                stack.append(f)
                f = f.f_back
            if not stack or stack[0].f_code.co_name in ("wait", "select", "_worker", "sleep", "_wait_for_tstate_lock"):
                continue  # idle threads
            top = stack[0].f_code
            leaf[f"{top.co_filename.rsplit(chr(92), 1)[-1]}:{top.co_name}"] += 1
            mine = next((x for x in stack if f"{os.sep}app{os.sep}" in x.f_code.co_filename), stack[0])
            own[f"{mine.f_code.co_filename.split(os.sep + 'app' + os.sep)[-1]}:{mine.f_code.co_name}:{mine.f_lineno}"] += 1
        samples += 1
        time.sleep(interval)
    lines = [f"{samples} samples over {seconds:.0f} s", "", "innermost app frame:"]
    lines += [f"  {n:6d}  {k}" for k, n in own.most_common(25)]
    lines += ["", "innermost frame:"] + [f"  {n:6d}  {k}" for k, n in leaf.most_common(15)]
    return "\n".join(lines)


def _profile_watcher(lane: str) -> None:
    trigger = lanes_dir() / f"{lane}.profile"
    while True:
        time.sleep(POLL_CHECK_SECONDS)
        try:
            seconds = float(trigger.read_text().strip() or 30)
            trigger.unlink()
        except (OSError, ValueError):
            continue
        try:
            (lanes_dir() / f"{lane}.profile.txt").write_text(sample_stacks(min(seconds, 600)))
        except Exception:
            log.warning("%s worker: profiling failed", lane, exc_info=True)


def serve(lane: str, *, max_polls: int | None = None) -> None:
    """Run a lane's polls (or discovery) on its schedule until the backend goes away."""
    from app.config import settings

    threading.Thread(target=_profile_watcher, args=(lane,), daemon=True).start()

    if lane == "discovery":
        interval, first = settings.autodiscover_minutes * 60, time.monotonic() + 600  # 10 minutes after start
        work = run_discovery_once
    else:
        interval = settings.poll_minutes * 60
        first = time.monotonic() if settings.poll_on_startup else time.monotonic() + interval
        work = lambda: poll_lane(lane)  # noqa: E731
    state = lane_status(lane)
    state.update(polling=False)
    _write_status(lane, state)
    next_run, polls = first, 0
    while max_polls is None or polls < max_polls:
        if time.monotonic() < next_run and not (lane == "fast" and _take_request(lane)):
            _write_status(lane, state)  # heartbeat
            time.sleep(POLL_CHECK_SECONDS)
            continue
        started = datetime.now(timezone.utc)
        state.update(polling=True, started_at=started.isoformat())
        _write_status(lane, state)
        # Heartbeats during the poll too: the API trusts polling=true only with a recent one (main._lane_busy).
        done = threading.Event()

        def beat() -> None:
            while not done.wait(HEARTBEAT_SECONDS):
                _write_status(lane, state)

        beater = threading.Thread(target=beat, daemon=True)
        beater.start()
        try:
            summary = work()
        except Exception:
            log.exception("%s worker: run failed", lane)
            summary = {"error": "run failed, see server logs"}
        finally:
            done.set()
            beater.join()
        state.update(polling=False, last={**summary, "started_at": started.isoformat(),
                                          "finished_at": datetime.now(timezone.utc).isoformat()})
        _write_status(lane, state)
        polls += 1
        # The next poll is due an interval after this one started; a long poll runs the next one at once.
        next_run = max(time.monotonic(), next_run + interval) if interval > 0 else time.monotonic()


# --- In the API process ------------------------------------------------------------------------------------

_procs: dict[str, "subprocess.Popen"] = {}  # noqa: F821
_stop = threading.Event()


def start_workers() -> None:
    """Start one worker process per lane, and a thread that restarts any that die."""
    for lane in WORKERS:
        _spawn(lane)
    threading.Thread(target=_supervise, daemon=True).start()


def _spawn(lane: str) -> None:
    import subprocess

    from app.config import settings

    if lane == "discovery" and settings.autodiscover_minutes <= 0:
        return
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # its logs go to the backend's window
    # FRESHJOBS_PARENT_PID: this process, which the worker watches (sys.executable may be the venv's launcher,
    # so the worker's own parent isn't necessarily us).
    env = {**os.environ, "FRESHJOBS_PARENT_PID": str(os.getpid())}
    _procs[lane] = subprocess.Popen([sys.executable, "-m", "app.lanes", lane], stdin=subprocess.DEVNULL, env=env,
                                    cwd=str(Path(__file__).resolve().parent.parent), **kwargs)
    log.info("started the %s worker (pid %d)", lane, _procs[lane].pid)


def _supervise() -> None:
    while not _stop.wait(15):
        for lane, proc in list(_procs.items()):
            if proc.poll() is not None:
                log.warning("the %s worker stopped (exit %s); starting it again", lane, proc.returncode)
                _spawn(lane)


def stop_workers() -> None:
    """Stop the supervisor. The workers exit by themselves within seconds of this process ending (they watch
    it), which also covers the backend being killed."""
    _stop.set()
    for proc in _procs.values():
        try:
            proc.terminate()
        except OSError:
            pass
    _procs.clear()


def watch_lanes(on_finished, stop: threading.Event | None = None, every: float = 5.0) -> None:
    """Call on_finished(lane, summary) whenever a lane worker finishes a poll (its last.finished_at changes)."""
    seen = {lane: (lane_status(lane).get("last") or {}).get("finished_at") for lane in WORKERS}
    stop = stop or _stop
    while not stop.wait(every):
        for lane in WORKERS:
            last = lane_status(lane).get("last") or {}
            if last.get("finished_at") and last["finished_at"] != seen.get(lane):
                seen[lane] = last["finished_at"]
                try:
                    on_finished(lane, last)
                except Exception:
                    log.exception("after-poll work for the %s lane failed", lane)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s [{sys.argv[1]}] %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if len(sys.argv) != 2 or sys.argv[1] not in WORKERS:
        sys.exit(f"usage: python -m app.lanes {'|'.join(WORKERS)}")
    parent = int(os.environ.get("FRESHJOBS_PARENT_PID") or 0)
    if parent:
        threading.Thread(target=_exit_when_parent_goes, args=(parent,), daemon=True).start()
    serve(sys.argv[1])
