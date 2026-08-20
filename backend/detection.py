"""
Animal detection for trail-camera photos (v2.15).

Uses MegaDetector (v5) to filter camera triggers to real animal detections. The
model + PyTorch are heavy (~hundreds of MB), so it is LAZY-loaded on first use —
the app boots fine without it, and if the model/deps are unavailable the detector
degrades to a permissive fallback (treats every photo as a low-confidence sighting)
so the pipeline still records data rather than crashing.

NOTE: inference is UNTESTED in the build sandbox (no model, no GPU/CPU torch there).
First real run happens on the server. If MegaDetector proves too heavy on the
target machine, set DETECTOR_MODE=fallback to skip it, or swap in a lighter model
inside _run_megadetector without touching callers.
"""
from __future__ import annotations
import os
import threading

_MODEL = None
_LOCK = threading.Lock()
_MODE = os.environ.get("DETECTOR_MODE", "megadetector")  # "megadetector" | "fallback"

# MegaDetector category 1 == animal. We treat animal detections above threshold as
# a positive wildlife sighting. (Deer-species classification is a separate model;
# for hunting purposes "animal present in daylight at this stand" is the signal.)
ANIMAL_CATEGORY = "1"
CONF_THRESHOLD = float(os.environ.get("DETECTOR_CONF", "0.2"))


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        # Imported lazily so the app doesn't require torch/megadetector to boot.
        from megadetector.detection.run_detector import load_detector  # type: ignore
        model_path = os.environ.get("MEGADETECTOR_MODEL", "MDV5A")
        _MODEL = load_detector(model_path)
        return _MODEL


def detect_animal(image_path: str) -> dict:
    """
    Return {"is_animal": bool, "confidence": float}. Never raises for model issues —
    falls back permissively so the sync pipeline keeps working.
    """
    if _MODE == "fallback":
        return {"is_animal": True, "confidence": 0.0, "detector": "fallback"}
    try:
        model = _load_model()
        result = model.generate_detections_one_image(image_path)
        best = 0.0
        for det in result.get("detections", []):
            if det.get("category") == ANIMAL_CATEGORY:
                best = max(best, float(det.get("conf", 0.0)))
        return {"is_animal": best >= CONF_THRESHOLD, "confidence": round(best, 3),
                "detector": "megadetector"}
    except Exception as e:  # model missing, deps missing, bad image, etc.
        # Degrade gracefully: record as a low-confidence sighting rather than dropping it.
        return {"is_animal": True, "confidence": 0.0, "detector": f"fallback ({type(e).__name__})"}
