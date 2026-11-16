"""In-memory sliding-window limits (one API process, so in memory is enough).

Used for the API as a whole (per visitor), for sign-ups and resent confirmation emails (every one of them sends an
email from the site's mail account), and checked by notify.email for the daily email cap. Added 2026-11-11 before
the portal was shared: until then anyone could sign up in a loop and make the site send hundreds of emails from the
owner's Gmail, which would get that account flagged, and a script could keep the 2-vCPU server's CPUs busy.
"""

import threading
import time
from collections import defaultdict, deque

_MAX_KEYS = 50_000  # past this many keys, empty ones are dropped (memory stays bounded)


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, seconds: float) -> bool:
        """Count one hit for `key` and say whether it's within `limit` hits per `seconds`. limit <= 0: no limit."""
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._hits) > _MAX_KEYS:
                for k in [k for k, q in self._hits.items() if not q]:
                    del self._hits[k]
            return True

    def retry_after(self, key: str, seconds: float) -> int:
        """Seconds until `key`'s oldest counted hit leaves the window (for a Retry-After header)."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 1
            return max(1, int(seconds - (time.monotonic() - hits[0])) + 1)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


limits = SlidingWindow()
