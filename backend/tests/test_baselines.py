from app.forecast import service


def test_baseline_uses_own_trailing_week_from_observed_then_forecast():
    past = {f"2025-11-{d:02d}": 60.0 for d in range(1, 8)}          # observed Nov 1-7
    forecast = {"2025-11-08": 50.0, "2025-11-09": 50.0, "2025-11-10": 50.0}
    out = service.rolling_baselines_f(["2025-11-08", "2025-11-10"], forecast, past)
    assert out["2025-11-08"] == 60.0                       # all-observed week
    assert out["2025-11-10"] == (5 * 60.0 + 2 * 50.0) / 7  # a day is never in its own baseline


def test_baseline_is_none_when_too_little_history():
    assert service.rolling_baselines_f(["2025-11-08"], {}, None) == {"2025-11-08": None}
    out = service.rolling_baselines_f(["2025-11-08"], {}, {"2025-11-07": 55.0, "2025-11-06": 57.0})
    assert out["2025-11-08"] is None


def test_failed_lookups_are_retried_sooner_than_successes(monkeypatch):
    monkeypatch.setattr(service.time, "time", lambda: 1000.0)
    service._hist_cache["ok"] = (1000.0 - 3600, {"2025-11-01": 60.0})
    service._hist_cache["bad"] = (1000.0 - 3600, None)
    assert service._hist_cache_get("ok")[0] is True
    assert service._hist_cache_get("bad")[0] is False
