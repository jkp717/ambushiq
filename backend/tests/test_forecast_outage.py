import asyncio
import json

import httpx
import pytest

import app.main as main
from app.forecast import service
from app.forecast.providers import WeatherError


def _forecast(tag="fresh"):
    return {"hourly": {"time": ["2025-11-10T00:00"], "wind_direction_10m": [180.0], "wind_speed_10m": [5.0],
                       "wind_gusts_10m": [7.0], "shortwave_radiation": [0.0], "temperature_2m": [8.0]},
            "daily": {"sunrise": [], "sunset": []}, "utc_offset_seconds": -21600, "tag": tag}


class FlakyProvider:
    has_solar = True

    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    async def fetch(self, lat, lon, days, tz):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture()
def setup(monkeypatch):
    service._fc_cache.clear()
    monkeypatch.setattr(service, "_RETRY_DELAY_S", 0)
    monkeypatch.setattr(service, "get_settings", lambda: {"weather_provider": "open_meteo"})

    def install(provider):
        monkeypatch.setattr(service, "get_weather_provider", lambda *_a, **_k: provider)
        return provider
    return install


def run(coro):
    return asyncio.run(coro)


def test_transient_timeout_is_retried_once(setup):
    prov = setup(FlakyProvider([httpx.ReadTimeout("slow"), _forecast()]))
    out = run(service.get_forecast(34.7, -92.3, days=14))
    assert prov.calls == 2 and out["tag"] == "fresh"


def test_5xx_is_retried_but_4xx_is_not(setup):
    def status_error(code):
        return httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"),
                                     response=httpx.Response(code))
    prov = setup(FlakyProvider([status_error(503), _forecast()]))
    assert run(service.get_forecast(34.7, -92.3, days=14))["tag"] == "fresh"
    service._fc_cache.clear()
    prov = setup(FlakyProvider([status_error(404), _forecast()]))
    with pytest.raises(service.ForecastUnavailable):
        run(service.get_forecast(34.7, -92.3, days=14))
    assert prov.calls == 1


def test_persistent_failure_without_cache_raises_forecast_unavailable(setup):
    prov = setup(FlakyProvider([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]))
    with pytest.raises(service.ForecastUnavailable) as exc:
        run(service.get_forecast(34.7, -92.3, days=14))
    assert prov.calls == 2 and "unreachable" in str(exc.value)


def test_expired_cache_is_served_when_the_provider_is_down(setup):
    setup(FlakyProvider([_forecast("old")]))
    run(service.get_forecast(34.7, -92.3, days=14))
    for k, (ts, fc) in list(service._fc_cache.items()):
        service._fc_cache[k] = (ts - service.FC_TTL - 60, fc)          # expire it
    setup(FlakyProvider([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]))
    out = run(service.get_forecast(34.7, -92.3, days=14))
    assert out["tag"] == "old" and out["stale"] is True


def test_provider_rejecting_the_key_is_not_retried(setup):
    prov = setup(FlakyProvider([WeatherError("rejected the API key"), _forecast()]))
    with pytest.raises(service.ForecastUnavailable) as exc:
        run(service.get_forecast(34.7, -92.3, days=14))
    assert prov.calls == 1 and "rejected the API key" in str(exc.value)


def test_outage_maps_to_a_clean_502():
    handler = main.app.exception_handlers[service.ForecastUnavailable]
    resp = run(handler(None, service.ForecastUnavailable("weather provider unreachable: ReadTimeout")))
    assert resp.status_code == 502
    assert "forecast unreachable" in json.loads(resp.body)["detail"]
