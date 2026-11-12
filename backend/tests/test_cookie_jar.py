import http.cookiejar
import time

import httpx
import pytest
import respx

from app.sources import http_util


def _cookie(domain: str, name: str = "sid") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(0, name, "v", None, False, domain, True, False, "/", True, False, None, False,
                                 None, None, {})


def test_the_jar_keeps_only_the_most_recent_sites():
    jar = http_util.BoundedCookieJar(max_domains=3)
    for d in ("a.example", "b.example", "c.example"):
        jar.set_cookie(_cookie(d))
    jar.set_cookie(_cookie("a.example", "csrf"))  # a is used again: b is now the oldest
    jar.set_cookie(_cookie("d.example"))
    assert sorted(jar._cookies) == ["a.example", "c.example", "d.example"]
    assert len(list(jar)) == 4  # a's two cookies, c and d


@pytest.mark.asyncio
@respx.mock
async def test_a_site_still_gets_its_own_cookie_back():
    respx.get("https://acme.wd5.myworkdayjobs.com/start").mock(
        return_value=httpx.Response(200, headers={"set-cookie": "PLAY_SESSION=abc; Path=/"}))
    second = respx.get("https://acme.wd5.myworkdayjobs.com/jobs").mock(return_value=httpx.Response(200))
    async with http_util.client() as client:
        await client.get("https://acme.wd5.myworkdayjobs.com/start")
        await client.get("https://acme.wd5.myworkdayjobs.com/jobs")
    assert second.calls[0].request.headers.get("cookie") == "PLAY_SESSION=abc"


def test_a_request_costs_the_same_after_thousands_of_sites():
    # httpx copies the client's jar on every request; with every site's cookies kept, that grew with the pass.
    bounded, unbounded = http_util.BoundedCookieJar(), http.cookiejar.CookieJar()
    for i in range(3000):
        for name in ("sid", "csrf", "lb"):
            bounded.set_cookie(_cookie(f"t{i}.example", name))
            unbounded.set_cookie(_cookie(f"t{i}.example", name))

    def merge_cost(jar):
        started = time.perf_counter()
        for _ in range(20):
            httpx.Cookies(httpx.Cookies(jar))  # what Client._merge_cookies does per request
        return time.perf_counter() - started

    assert len(list(bounded)) == http_util.COOKIE_DOMAINS * 3
    assert merge_cost(bounded) * 10 < merge_cost(unbounded)


@pytest.mark.asyncio
@respx.mock
async def test_a_dropped_connection_is_tried_again(monkeypatch):
    monkeypatch.setattr(http_util, "TRANSIENT_BACKOFF_SECONDS", 0)
    route = respx.post("https://cvshealth.wd1.myworkdayjobs.com/wday/cxs/cvshealth/CVS_Health_Careers/jobs").mock(
        side_effect=[httpx.RemoteProtocolError("Server disconnected without sending a response."),
                     httpx.ReadError("connection reset"), httpx.Response(200, json={"total": 0})])
    async with http_util.client() as client:
        resp = await http_util.post(
            client, "https://cvshealth.wd1.myworkdayjobs.com/wday/cxs/cvshealth/CVS_Health_Careers/jobs", json={})
    assert resp.json() == {"total": 0} and route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_a_connection_that_keeps_failing_still_raises(monkeypatch):
    monkeypatch.setattr(http_util, "TRANSIENT_BACKOFF_SECONDS", 0)
    route = respx.get("https://gone.example/jobs").mock(side_effect=httpx.ConnectError("refused"))
    async with http_util.client() as client:
        with pytest.raises(httpx.ConnectError):
            await http_util.get(client, "https://gone.example/jobs")
    assert route.call_count == http_util.TRANSIENT_RETRIES + 1


@pytest.mark.asyncio
@respx.mock
async def test_long_run_clients_keep_many_connections_open():
    # httpx keeps 20 idle connections by default; a poll's 25-per-platform concurrency closed and reopened them
    # constantly (new HTTPS handshakes were the fast lane's biggest CPU cost).
    async with http_util.client() as client:
        pool = client._transport._pool
        assert pool._max_keepalive_connections >= 150 and pool._max_connections >= 200
