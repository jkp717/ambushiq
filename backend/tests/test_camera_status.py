"""Camera status parsing (battery / signal / SD card / temperature / model) and saving it on the camera."""
from app.cameras import providers
from app.cameras.models import Camera
from app.cameras.service import apply_camera_status

# trimmed from real FLEX camera responses (GPS and SIM left out)
AA_CAMERA = {
    "batteries": [64, 0, 0], "batteryType": "AA", "activePowerSource": 0,
    "powerSources": [{"location": "TRAY1", "type": "AA", "percentage": 64, "voltage": 10854, "level": "medium"}],
    "memory": {"size": 28492, "used": 7414}, "model": "FLEX-DARK",
    "signal": {"bar": 3, "dBm": -99, "type": "LTE",
               "processed": {"percentage": 90, "bar": 4, "lowSignal": False, "level": "medium"}},
    "temperature": {"unit": "F", "value": 76}, "lastUpdate": "2026-10-15T10:16:22.000Z",
}
SOLAR_CAMERA = {
    "batteries": [100, 0, 100], "batteryType": "SOLAR", "activePowerSource": 1,
    "powerSources": [{"location": "TRAY1", "type": "AA", "percentage": 100, "level": "high"},
                     {"location": "INTERNAL", "type": "SOLAR", "percentage": 97, "level": "high"}],
    "memory": {"size": 28618, "used": 8022}, "model": "FLEX-S-DARK",
    "signal": {"processed": {"percentage": 90, "bar": 4, "lowSignal": False}},
    "temperature": {"unit": "F", "value": 101},
}


def test_aa_camera_reads_every_field():
    st = providers.spypoint_status(AA_CAMERA)
    assert st == {"battery_pct": 64, "battery_level": "medium", "power_type": "AA", "signal_pct": 90,
                  "signal_bars": 4, "low_signal": 0, "sd_used_mb": 7414, "sd_size_mb": 28492,
                  "temperature_f": 76.0, "model": "FLEX-DARK"}


def test_solar_camera_reads_the_power_source_in_use():
    st = providers.spypoint_status(SOLAR_CAMERA)
    assert st["battery_pct"] == 97 and st["power_type"] == "SOLAR" and st["battery_level"] == "high"


def test_celsius_is_converted_and_low_signal_is_kept():
    st = providers.spypoint_status({**AA_CAMERA, "temperature": {"unit": "C", "value": 20},
                                    "signal": {"processed": {"percentage": 15, "bar": 1, "lowSignal": True}}})
    assert st["temperature_f"] == 68.0 and st["low_signal"] == 1 and st["signal_pct"] == 15


def test_batteries_list_is_the_fallback_without_power_sources():
    st = providers.spypoint_status({"batteries": [0, 0, 43]})
    assert st["battery_pct"] == 43 and st["battery_level"] is None


def test_missing_or_odd_fields_give_none_instead_of_failing():
    assert providers.spypoint_status({}) == dict.fromkeys(providers.STATUS_KEYS)
    st = providers.spypoint_status({"powerSources": "?", "activePowerSource": 5, "memory": {"size": 0},
                                    "signal": None, "temperature": {"value": "hot"}})
    assert st == dict.fromkeys(providers.STATUS_KEYS)
    assert providers.spypoint_status(None) == dict.fromkeys(providers.STATUS_KEYS)


def test_reveal_reads_only_obvious_fields():
    st = providers.reveal_status({"cameraId": "x", "batteryLevel": 55, "signalStrength": 70, "model": "Reveal X"})
    assert st["battery_pct"] == 55 and st["signal_pct"] == 70 and st["model"] == "Reveal X"
    assert st["sd_size_mb"] is None


def test_apply_camera_status_writes_health_and_status_fields():
    cam = Camera(name="c", brand="spypoint", region_id=1)
    apply_camera_status(cam, {"last_seen_at": "2026-10-15T10:16:22Z", "photo_count": 5, "photo_limit": 250,
                              **providers.spypoint_status(AA_CAMERA)})
    assert cam.last_seen_at == "2026-10-15T10:16:22Z" and cam.photo_limit == 250
    assert cam.battery_pct == 64 and cam.sd_size_mb == 28492 and cam.model == "FLEX-DARK"
    d = cam.to_dict()
    assert d["status"]["battery_pct"] == 64 and d["status"]["low_signal"] is False
    assert "credentials_json" not in d and "sim" not in d["status"]
