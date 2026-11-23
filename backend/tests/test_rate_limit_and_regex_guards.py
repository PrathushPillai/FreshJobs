"""Guards for two failures found on the live server on 2026-11-23.

- A page pattern that retried from every position of a long run of letters and digits (Zoho's PAGE_MARKER on
  wmu.com's 224,591-character run) froze the discovery worker on every hourly run.
- Greenhouse rate-limited us by volume, and we kept sending 25 requests at a time through every refusal.
"""

import time

import httpx
import pytest
import respx

from app.pipeline.detect import detect_in_html, plugins
from app.sources import greenhouse, http_util

LONG_RUN = "<html><body><img src=\"data:image/png;base64," + "aB3-" * 75_000 + "\"></body></html>"


def test_every_page_marker_is_fast_on_a_long_run_of_letters_and_digits():
    for mod in plugins():
        marker = getattr(mod, "PAGE_MARKER", None)
        if marker is None:
            continue
        t = time.monotonic()
        marker.search(LONG_RUN)
        assert time.monotonic() - t < 0.5, mod.__name__


def test_detect_in_html_is_fast_on_a_long_run_of_letters_and_digits():
    t = time.monotonic()
    assert detect_in_html(LONG_RUN, "https://wmu.com/") is None
    assert time.monotonic() - t < 2


def test_zoho_marker_still_finds_a_portal():
    from app.sources import zoho

    assert zoho.PAGE_MARKER.search('<a href="https://acme-staffing.zohorecruit.com/jobs/Careers">Jobs</a>')
    assert zoho.PAGE_MARKER.search('<script src="https://static.zohocdn.com/recruit/embed.js"></script>')


@pytest.fixture
def fresh_pause(monkeypatch):
    monkeypatch.setattr(greenhouse, "_PAUSE", http_util.Spacer(0.0))
    monkeypatch.setattr(greenhouse, "_gentle", {"left": 0})


@pytest.mark.asyncio
@respx.mock
async def test_greenhouse_429_pauses_every_request_then_comes_back_gently(fresh_pause, monkeypatch):
    monkeypatch.setattr(greenhouse, "GENTLE_SUCCESSES", 2)
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    route.mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await greenhouse.GreenhouseAdapter().fetch(client, "acme", known_ids={"1"})
        assert greenhouse._PAUSE._paused_until > time.monotonic() + 30  # the whole lane waits
        assert greenhouse._PAUSE.seconds == greenhouse.GENTLE_SPACING
        greenhouse._PAUSE._paused_until = 0.0  # skip the wait in the test
        greenhouse._PAUSE.seconds = 0.0  # and the spacing; the counter is what's checked below
        route.mock(return_value=httpx.Response(200, json={"jobs": []}))
        await greenhouse.GreenhouseAdapter().fetch(client, "acme", known_ids={"1"})
        assert greenhouse._gentle["left"] == 1
        await greenhouse.GreenhouseAdapter().fetch(client, "acme", known_ids={"1"})
    assert greenhouse._gentle["left"] == 0 and greenhouse._PAUSE.seconds == 0.0
