import asyncio

import httpx
import pytest

from app.forecast import providers
from app.forecast.providers import NWSProvider, WeatherError

POINTS = {"properties": {"timeZone": "America/Chicago", "forecastGridData": "https://api.weather.gov/gridpoints/LZK/1,1"}}
GRID = {"properties": {}}


def _install(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _run(lat=34.71234567, lon=-92.34567891):
    return asyncio.run(NWSProvider().fetch(lat, lon, 2, "America/Chicago"))


def test_points_lookup_uses_four_decimals_so_nws_does_not_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/points/"):
            if request.url.path != "/points/34.7123,-92.3457":
                return httpx.Response(301, headers={"Location": "https://api.weather.gov/points/34.7123,-92.3457"})
            return httpx.Response(200, json=POINTS)
        return httpx.Response(200, json=GRID)

    _install(monkeypatch, handler)
    out = _run()
    assert seen[0] == "/points/34.7123,-92.3457" and len(out["hourly"]["time"]) == 48


def test_a_redirect_is_followed_if_nws_still_sends_one(monkeypatch):
    def handler(request):
        if request.url.path == "/points/34.7123,-92.3457":
            return httpx.Response(301, headers={"Location": "https://api.weather.gov/points/34.7123,-92.3457/"})
        if request.url.path.startswith("/points/"):
            return httpx.Response(200, json=POINTS)
        return httpx.Response(200, json=GRID)

    _install(monkeypatch, handler)
    assert len(_run()["hourly"]["time"]) == 48


def test_outside_us_coverage_gets_the_coverage_message(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(404, json={}))
    with pytest.raises(WeatherError, match="outside US coverage"):
        _run()


def test_nws_server_errors_are_left_as_http_errors_so_they_get_retried(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(503, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        _run()
