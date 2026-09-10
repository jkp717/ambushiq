"""
Animal detection for trail-camera photos.

Uses MegaDetector via the PytorchWildlife package to filter camera triggers to
real animal detections. The model + PyTorch are heavy (~hundreds of MB), so the
model is LAZY-loaded on first use — the app boots fine without it.

DETECTOR_MODE env var:
  "fallback"    (default) — treat every photo as a sighting with confidence 0.0.
                            Photos always show up; no ML required. Good default
                            until you confirm the server can handle the model.
  "megadetector"          — run MegaDetector via PytorchWildlife. Requires the
                            PytorchWildlife package and a working PyTorch install.
                            On any model/dependency error the photo is still saved
                            at conf=0.0 (never silently discarded).

MEGADETECTOR_MODEL env var:
  "MDV5A"  (default) — MegaDetector v5a (lighter, well-tested)
  "MDV5B"            — MegaDetector v5b
  "MDV6-yolov10-c"   — MegaDetector v6 (heavier, more accurate)

DETECTOR_CONF env var: minimum confidence to count as a sighting (default 0.2).
"""
from __future__ import annotations
import os
import logging
import threading

_MODEL = None
_LOCK = threading.Lock()
_MODE = os.environ.get("DETECTOR_MODE", "megadetector")  # "megadetector" | "fallback"

log = logging.getLogger(__name__)

# ── Startup dependency pre-flight ────────────────────────────────────────────
# Runs once at import time so the very first line of `docker compose logs`
# tells you whether megadetector mode will work.
_MEGADETECTOR_DEPS = ("torch", "PytorchWildlife", "PIL", "numpy", "soundfile", "librosa")

def _preflight():
    missing = []
    for mod in _MEGADETECTOR_DEPS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if _MODE == "fallback":
        log.info("detection: DETECTOR_MODE=fallback — skipping ML, every photo recorded")
    elif missing:
        log.warning("detection: DETECTOR_MODE=megadetector but missing deps: %s "
                    "— photos will still be saved (conf=0); set DETECTOR_MODE=fallback to silence this",
                    missing)
    else:
        log.info("detection: DETECTOR_MODE=megadetector — all deps present, model loads on first sync")

_preflight()

# MegaDetector class_id 0 == animal (1 = person, 2 = vehicle).
ANIMAL_CLASS_ID = 0
CONF_THRESHOLD = float(os.environ.get("DETECTOR_CONF", "0.2"))


def _load_model():
    """Lazy-load MegaDetector via PytorchWildlife. Raises on failure."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        # PytorchWildlife is the current home of MegaDetector (replaces the old
        # standalone `megadetector` package). Import lazily so the app boots without it.
        from PytorchWildlife.models import detection as pw_detection  # type: ignore
        import torch  # type: ignore

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_name = os.environ.get("MEGADETECTOR_MODEL", "MDV5A")
        log.info("detection: loading MegaDetector model=%s device=%s", model_name, device)

        if model_name.upper().startswith("MDV6"):
            _MODEL = pw_detection.MegaDetectorV6(device=device, pretrained=True, version=model_name)
        else:
            # V5 variants: MDV5A (default), MDV5B
            _MODEL = pw_detection.MegaDetectorV5(device=device, pretrained=True)

        log.info("detection: model loaded")
        return _MODEL


def detect_animal(image_path: str) -> dict:
    """
    Return {"is_animal": bool, "confidence": float, "detector": str}.

    Never raises. On model failure returns is_animal=False with detector="error(...)"
    so the caller can decide what to do (currently: save at conf=0 so photos are
    never silently discarded).
    """
    if _MODE == "fallback":
        return {"is_animal": True, "confidence": 0.0, "detector": "fallback"}

    try:
        import numpy as np          # type: ignore
        from PIL import Image       # type: ignore

        model = _load_model()

        img = np.array(Image.open(image_path).convert("RGB"))
        results = model.single_image_detection(img, img_path=image_path)

        # PytorchWildlife returns a supervision Detections object.
        # .class_id is an int array (1=animal, 2=person, 3=vehicle).
        # .confidence is a float array of matching scores.
        dets = results.get("detections")
        best = 0.0
        if dets is not None and dets.confidence is not None and dets.class_id is not None:
            for cls_id, conf in zip(dets.class_id, dets.confidence):
                if int(cls_id) == ANIMAL_CLASS_ID:
                    best = max(best, float(conf))

        return {"is_animal": best >= CONF_THRESHOLD, "confidence": round(best, 3),
                "detector": "megadetector"}

    except Exception as e:
        log.warning("MegaDetector failed on %s: %s: %s", image_path, type(e).__name__, e)
        return {"is_animal": False, "confidence": 0.0, "detector": f"error ({type(e).__name__})"}
