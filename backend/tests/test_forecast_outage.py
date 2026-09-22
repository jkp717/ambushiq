import asyncio
import json

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

import app.main as main
from app.core.database import Base, engine
from app.forecast import service
from app.forecast.models import ForecastCache
from app.forecast.providers import WeatherError
from app.regions.models import Region
from app.stands.models import Stand

LAT, LON = 34.7, -92.3


def _forecast(tag="fresh"):
    return {"hourly": {"time": ["2025-11-10T00:00"], "wind_direction_10m": [180.0], "wind_speed_10m": [5.0],
                       "wind_gusts_10m": [7.0], "shortwave_radiation": [0.0], "temperature_2m": [8.0]},
            "daily": {"sunrise": [], "sunset": []}, "utc_offset_seconds": -21600, "tag": tag}


def _multi_day_forecast(tag, n_days, start_day=10):
    times = [f"2025-11-{start_day + d:02d}T{h:02d}:00" for d in range(n_days) for h in range(24)]
    n = len(times)
    hourly = {
        "time": times,
        "wind_direction_10m": [180.0] * n,
        "wind_speed_10m": [5.0] * n,
        "wind_gusts_10m": [7.0] * n,
        "shortwave_radiation": [0.0] * n,
        "temperature_2m": [8.0] * n,
    }
    daily = {
        "sunrise": [f"2025-11-{start_day + d:02d}T06:30" for d in range(n_days)],
        "sunset": [f"2025-11-{start_day + d:02d}T18:00" for d in range(n_days)],
    }
    return {"hourly": hourly, "daily": daily, "utc_offset_seconds": -21600, "tag": tag}


def _timeouts(n):
    return [httpx.ReadTimeout("slow") for _ in range(n)]


class FlakyProvider:
    has_solar = True
    label = "Test"

    def __init__(self, outcomes, delay=0.0):
        self.outcomes, self.calls, self.delay = list(outcomes), 0, delay

    async def fetch(self, lat, lon, days, tz):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture()
def setup(monkeypatch):
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(ForecastCache))
        s.commit()
    service._fc_cache.clear()
    service._hist_cache.clear()
    service._inflight.clear()
    monkeypatch.setattr(service, "_RETRY_DELAY_S", 0)
    monkeypatch.setattr(service, "get_settings", lambda: {"weather_provider": "open_meteo"})

    def install(provider):
        monkeypatch.setattr(service, "get_weather_provider", lambda *_a, **_k: provider)
        return provider

    def install_by_id(providers_by_id):
        monkeypatch.setattr(service, "get_weather_provider", lambda pid, *_a, **_k: providers_by_id[pid])
        return providers_by_id

    install.by_id = install_by_id
    return install


def run(coro):
    return asyncio.run(coro)


def _age_cache(seconds):
    for k, (ts, fc) in list(service._fc_cache.items()):
        service._fc_cache[k] = (ts - seconds, fc)


def test_transient_timeout_is_retried(setup):
    prov = setup(FlakyProvider([httpx.ReadTimeout("slow"), _forecast()]))
    out = run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == 2 and out["tag"] == "fresh"


def test_5xx_is_retried_but_4xx_is_not(setup):
    def status_error(code):
        return httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"),
                                     response=httpx.Response(code))
    setup(FlakyProvider([status_error(503), _forecast()]))
    assert run(service.get_forecast(LAT, LON, days=14))["tag"] == "fresh"
    service._fc_cache.clear()
    with Session(engine) as s:
        s.execute(delete(ForecastCache))
        s.commit()
    prov = setup(FlakyProvider([status_error(404), _forecast()]))
    with pytest.raises(service.ForecastUnavailable):
        run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == 1


def test_persistent_failure_without_cache_raises_forecast_unavailable(setup):
    prov = setup(FlakyProvider(_timeouts(service.FETCH_ATTEMPTS)))
    with pytest.raises(service.ForecastUnavailable) as exc:
        run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == service.FETCH_ATTEMPTS and "unreachable" in str(exc.value)


def test_a_hung_provider_is_abandoned_at_the_attempt_timeout(setup, monkeypatch):
    monkeypatch.setattr(service, "ATTEMPT_TIMEOUT_S", 0.01)
    prov = setup(FlakyProvider([_forecast()] * service.FETCH_ATTEMPTS, delay=5))
    with pytest.raises(service.ForecastUnavailable):
        run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == service.FETCH_ATTEMPTS


def test_provider_rejecting_the_key_is_not_retried(setup):
    prov = setup(FlakyProvider([WeatherError("rejected the API key"), _forecast()]))
    with pytest.raises(service.ForecastUnavailable) as exc:
        run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == 1 and "rejected the API key" in str(exc.value)


def test_short_primary_is_extended_with_secondary_when_secondary_configured(setup, monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: {
        "weather_provider": "open_meteo", "weather_secondary_provider": "tomorrow_io",
    })
    primary = FlakyProvider([_multi_day_forecast("short", 3)])
    primary.label = "Primary"
    secondary = FlakyProvider([_multi_day_forecast("long", 14)])
    secondary.label = "Secondary"
    setup.by_id({"open_meteo": primary, "tomorrow_io": secondary})

    out = run(service.get_forecast(LAT, LON, days=14))

    assert service._hourly_day_count(out["hourly"]) == 14
    assert len(out["daily"]["sunrise"]) == 14
    assert out["daily"]["source"][:3] == ["Primary"] * 3
    assert out["daily"]["source"][3:] == ["Secondary"] * 11


def test_extension_is_skipped_when_secondary_is_none(setup, monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: {"weather_provider": "open_meteo"})
    setup(FlakyProvider([_multi_day_forecast("short", 3)]))

    out = run(service.get_forecast(LAT, LON, days=14))

    assert service._hourly_day_count(out["hourly"]) == 3


def test_extension_failure_falls_back_to_primary_only(setup, monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: {
        "weather_provider": "open_meteo", "weather_secondary_provider": "tomorrow_io",
    })
    primary = FlakyProvider([_multi_day_forecast("short", 3)])
    secondary = FlakyProvider([httpx.ReadTimeout("slow")])
    setup.by_id({"open_meteo": primary, "tomorrow_io": secondary})

    out = run(service.get_forecast(LAT, LON, days=14))

    assert service._hourly_day_count(out["hourly"]) == 3
    assert out["tag"] == "short"


def test_fresh_cache_is_served_without_a_fetch(setup):
    prov = setup(FlakyProvider([_forecast("one"), _forecast("two")]))
    run(service.get_forecast(LAT, LON, days=14))
    out = run(service.get_forecast(LAT, LON, days=14))
    assert prov.calls == 1 and out["tag"] == "one" and "stale" not in out


def test_expired_cache_is_served_at_once_and_refreshed_in_the_background(setup):
    setup(FlakyProvider([_forecast("old")]))
    run(service.get_forecast(LAT, LON, days=14))
    _age_cache(service.FC_TTL + 60)
    prov = setup(FlakyProvider([_forecast("new")], delay=0.05))

    async def scenario():
        first = await service.get_forecast(LAT, LON, days=14)      # must not wait for the provider
        assert prov.calls == 0 or first["tag"] == "old"
        await asyncio.gather(*service._inflight.values())
        second = await service.get_forecast(LAT, LON, days=14)
        return first, second

    first, second = run(scenario())
    assert first["tag"] == "old" and first["stale"] is True and first["fetched_at"] > 0
    assert second["tag"] == "new" and "stale" not in second and prov.calls == 1


def test_failed_background_refresh_keeps_serving_the_old_forecast(setup):
    setup(FlakyProvider([_forecast("old")]))
    run(service.get_forecast(LAT, LON, days=14))
    _age_cache(service.FC_TTL + 60)
    setup(FlakyProvider(_timeouts(service.FETCH_ATTEMPTS)))

    async def scenario():
        first = await service.get_forecast(LAT, LON, days=14)
        await asyncio.gather(*service._inflight.values(), return_exceptions=True)
        return first, await service.get_forecast(LAT, LON, days=14)

    first, second = run(scenario())
    assert first["tag"] == second["tag"] == "old" and second["stale"] is True


def test_cache_older_than_max_stale_is_still_used_when_the_provider_is_down(setup):
    setup(FlakyProvider([_forecast("old")]))
    run(service.get_forecast(LAT, LON, days=14))
    _age_cache(service.FC_MAX_STALE + 60)
    setup(FlakyProvider(_timeouts(service.FETCH_ATTEMPTS)))
    out = run(service.get_forecast(LAT, LON, days=14))
    assert out["tag"] == "old" and out["stale"] is True


def test_concurrent_requests_share_one_upstream_fetch(setup):
    prov = setup(FlakyProvider([_forecast()], delay=0.05))

    async def scenario():
        return await asyncio.gather(*[service.get_forecast(LAT, LON, days=14) for _ in range(6)])

    outs = run(scenario())
    assert prov.calls == 1 and all(o["tag"] == "fresh" for o in outs)


def test_forecast_survives_a_restart_via_the_database(setup):
    setup(FlakyProvider([_forecast("persisted")]))
    run(service.get_forecast(LAT, LON, days=14))
    service._fc_cache.clear()                       # what a container restart does to memory
    prov = setup(FlakyProvider(_timeouts(service.FETCH_ATTEMPTS)))
    out = run(service.get_forecast(LAT, LON, days=14))
    assert out["tag"] == "persisted" and "stale" not in out and prov.calls == 0


def test_forecast_schema_version_isolates_old_rows(setup):
    setup(FlakyProvider([_forecast("x")]))
    run(service.get_forecast(LAT, LON, days=14))
    with Session(engine) as s:
        keys = [r.key for r in s.query(ForecastCache).all()]
    assert keys and all(k.startswith(service.FC_SCHEMA + ":") for k in keys)


def test_historical_failure_falls_back_to_the_last_persisted_value(setup):
    key = "highs:34.700,-92.300:2025-11-09:7"
    assert service._hist_finish(key, {"2025-11-08": 60.0}) == {"2025-11-08": 60.0}
    service._hist_cache.clear()
    assert service._hist_finish(key, None) == {"2025-11-08": 60.0}     # provider failed: keep the old data
    assert service._hist_cache_get(key) == (True, {"2025-11-08": 60.0})
    assert service._hist_finish("never-seen", None) is None


def test_persisted_historical_values_are_reused_after_a_restart(setup):
    key = "hday:34.700,-92.300:2025-11-09"
    service._hist_finish(key, {"wind_mph": 5.0})
    service._hist_cache.clear()
    assert service._hist_lookup(key) == (True, {"wind_mph": 5.0})


def test_warm_up_refreshes_every_region_forecast(setup, monkeypatch):
    with Session(engine) as s:
        s.execute(delete(Stand))
        s.execute(delete(Region))
        s.add(Region(id=1, name="r", lat=LAT, lon=LON, property_timezone="America/Chicago"))
        s.add(Stand(region_id=1, name="a", lat=LAT, lon=LON, is_active=1))
        s.commit()
    prov = setup(FlakyProvider([_forecast("w14"), _forecast("w3")]))
    calls = []

    async def fake_highs(*a, **k):
        calls.append("highs")

    async def fake_day(*a, **k):
        calls.append("day")

    monkeypatch.setattr(service, "get_historical_highs_f", fake_highs)
    monkeypatch.setattr(service, "get_historical_day_weather", fake_day)
    run(service.warm_forecasts())
    assert prov.calls == 2 and len(service._fc_cache) == 2 and sorted(calls) == ["day", "highs"]
    with Session(engine) as s:
        s.execute(delete(Stand))
        s.execute(delete(Region))
        s.commit()


def test_warm_up_never_raises_when_the_provider_is_down(setup):
    with Session(engine) as s:
        s.execute(delete(Stand))
        s.execute(delete(Region))
        s.add(Region(id=1, name="r", lat=LAT, lon=LON, property_timezone="America/Chicago"))
        s.add(Stand(region_id=1, name="a", lat=LAT, lon=LON, is_active=1))
        s.commit()
    setup(FlakyProvider(_timeouts(service.FETCH_ATTEMPTS * 2)))
    run(service.warm_forecasts())                    # must swallow ForecastUnavailable
    with Session(engine) as s:
        s.execute(delete(Stand))
        s.execute(delete(Region))
        s.commit()


def test_outage_maps_to_a_clean_502():
    handler = main.app.exception_handlers[service.ForecastUnavailable]
    resp = run(handler(None, service.ForecastUnavailable("weather provider unreachable: ReadTimeout")))
    assert resp.status_code == 502
    assert "forecast unreachable" in json.loads(resp.body)["detail"]
