"""
Animal detection for trail-camera photos (v2.15).

Uses MegaDetector (v5) to filter camera triggers to real animal detections. The
model + PyTorch are heavy (~hundreds of MB), so it is LAZY-loaded on first use —
the app boots fine without it, and if the model/deps are unavailable the detector
degrades to a permissive fallback (treats every photo as a low-confidence sighting)
so the pipeline still records data rather than crashing.

DETECTOR_MODE env var:
  "megadetector" (default) — run MegaDetector; on any model/dependency error, skip
                              the photo rather than silently recording it as a sighting.
  "fallback"               — explicit opt-in to treat every photo as a sighting with
                              confidence 0.0 (useful for testing without a model).

NOTE: inference is UNTESTED in the build sandbox (no model, no GPU/CPU torch there).
First real run happens on the server. If MegaDetector proves too heavy on the
target machine, set DETECTOR_MODE=fallback to skip it, or swap in a lighter model
inside _run_megadetector without touching callers.
"""
from __future__ import annotations
import os
import logging
import threading

_MODEL = None
_LOCK = threading.Lock()
_MODE = os.environ.get("DETECTOR_MODE", "megadetector")  # "megadetector" | "fallback"

log = logging.getLogger(__name__)

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
    Return {"is_animal": bool, "confidence": float, "detector": str}.

    Never raises — but on model failure returns is_animal=False so that broken
    or missing models do NOT silently flood the sightings table with false positives.
    Only the explicit DETECTOR_MODE=fallback opt-in returns is_animal=True without
    running the model.
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
    except Exception as e:
        # Model missing, deps missing, bad image, CUDA error, etc.
        # Return is_animal=False so the sync pipeline skips rather than records
        # everything. The error is logged so ops can diagnose the root cause.
        log.warning("MegaDetector failed on %s: %s: %s", image_path, type(e).__name__, e)
        return {"is_animal": False, "confidence": 0.0, "detector": f"error ({type(e).__name__})"}
