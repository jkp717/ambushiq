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
_CLASSIFIER = None
_FEATURE_FALLBACK = None   # (timm model, transform) used only when DFNE's own features can't be reached
_LOCK = threading.Lock()

MAX_ANIMALS = 8            # animal boxes kept per photo (each deer gets its own buck label)
_MODE = os.environ.get("DETECTOR_MODE", "fallback")  # "megadetector" | "fallback"

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

# MegaDetector native class IDs: 0 = animal, 1 = person, 2 = vehicle.
ANIMAL_CLASS_ID = 0
CONF_THRESHOLD = float(os.environ.get("DETECTOR_CONF", "0.2"))

# The DFNE classifier's exact label for white-tailed deer (see the CLASS_NAMES
# map in PytorchWildlife.models.classification.timm_base.DFNE). Used to decide
# which sightings should count toward a stand's trail-camera score boost.
DEER_SPECIES = "White-tailed Deer"


def is_deer(species: str | None) -> bool:
    return bool(species) and species.strip().lower() == DEER_SPECIES.lower()


def species_available() -> bool:
    """True if species classification can actually run (megadetector mode).
    In fallback mode every sighting's species is always None, so anything
    gated on a confirmed species (the camera boost/penalty) should be
    disabled entirely rather than silently treating everything as
    unconfirmed/non-deer."""
    return _MODE == "megadetector"


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


def _load_classifier(device="cpu"):
    """Lazy-load the species classification model."""
    global _CLASSIFIER
    if _CLASSIFIER is not None:
        return _CLASSIFIER
    with _LOCK:
        if _CLASSIFIER is not None:
            return _CLASSIFIER

        from PytorchWildlife.models import classification as pw_classification

        log.info("detection: loading DFNE Classifier device=%s", device)
        _CLASSIFIER = pw_classification.DFNE(device=device)

        log.info("detection: classifier loaded")
        return _CLASSIFIER


def unload_models() -> None:
    """Release MegaDetector + the species classifier from memory.
    Both are heavy (MegaDetector is a YOLO model; the species classifier is a
    ViT-Large backbone) and this app runs on a low-RAM box with no GPU, so we
    don't keep them resident between sync batches — only load happens lazily
    when a batch actually has a new photo to process, so this is a no-op on
    ticks where nothing needed detection."""
    global _MODEL, _CLASSIFIER, _FEATURE_FALLBACK
    with _LOCK:
        if _MODEL is None and _CLASSIFIER is None and _FEATURE_FALLBACK is None:
            return
        _MODEL = None
        _CLASSIFIER = None
        _FEATURE_FALLBACK = None
    import gc
    gc.collect()
    log.info("detection: models unloaded")


def _classify(classifier, crop) -> tuple[str | None, float | None]:
    """Species + confidence for one animal crop. PytorchWildlife's TIMM-based classifiers (DFNE,
    Deepfaune) return {"prediction": ..., "confidence": ...}, not "class_name" — keyed off the actual
    `results_generation()` output shape, not assumed."""
    res = classifier.single_image_classification(crop)
    if isinstance(res, dict) and "prediction" in res:
        conf = res.get("confidence")
        return res.get("prediction"), (round(float(conf), 3) if conf is not None else None)
    return str(res), None


FEATURE_SOURCE = "dfne_pad"   # network + crop deer vectors should come from (DFNE on pad_box); others get recomputed

# MegaDetector boxes hug the body and often clip antler tips. Deer are outlined, shown and fed to the buck
# model with this much room around the box (fractions of the box size), most of it on top for the rack.
PAD_SIDES, PAD_TOP, PAD_BOTTOM = 0.10, 0.20, 0.08


def pad_box(box: list[float]) -> list[float]:
    """A detector box [x1, y1, x2, y2] (fractions of the image) grown by the PAD_* margins, kept inside the photo."""
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    return [round(max(0.0, x1 - bw * PAD_SIDES), 4), round(max(0.0, y1 - bh * PAD_TOP), 4),
            round(min(1.0, x2 + bw * PAD_SIDES), 4), round(min(1.0, y2 + bh * PAD_BOTTOM), 4)]


def _dfne_parts(classifier):
    """(timm network, transform, class names) of a PytorchWildlife TIMM classifier, or None. DFNE keeps
    its ViT in `.predictor` (see TIMM_BaseClassifierInference)."""
    net = getattr(classifier, "predictor", None)
    tf = getattr(classifier, "transform", None)
    names = getattr(classifier, "CLASS_NAMES", None)
    if net is None or tf is None or not names or not hasattr(net, "forward_head"):
        return None
    return net, tf, names


def _dfne_pre(parts, crop):
    """DFNE's pre-classification features for a crop (one forward pass)."""
    from PIL import Image  # type: ignore
    net, tf, _names = parts
    x = tf(Image.fromarray(crop)).unsqueeze(0).to(next(net.parameters()).device)
    return net.forward_head(net.forward_features(x), pre_logits=True)


def _dfne_species(parts, crop) -> tuple[str, float]:
    """DFNE's species and its confidence for a crop — what single_image_classification gives."""
    import torch  # type: ignore
    net, _tf, names = parts
    probs = torch.softmax(net.get_classifier()(_dfne_pre(parts, crop)), dim=1)[0]
    i = int(probs.argmax())
    return names[i], round(float(probs[i]), 3)


def _dfne_features(parts, crop) -> list[float]:
    """A deer's feature vector for the buck model: DFNE's features for its padded crop."""
    return [float(v) for v in _dfne_pre(parts, crop).flatten().cpu().numpy()]


def _crop_frac(img, box):
    """The part of an image array inside a box given as fractions of the image."""
    h, w = img.shape[:2]
    return _crop(img, (box[0] * w, box[1] * h, box[2] * w, box[3] * h))


def _crop(img, box_px):
    x1, y1, x2, y2 = map(int, box_px)
    return img[max(0, y1):y2, max(0, x1):x2]


def deer_vectors(image_path: str, boxes: list[list[float]]) -> list[tuple[list[float] | None, str | None]]:
    """Feature vectors for deer already outlined in a photo (boxes as fractions), without re-running
    the detector — used to bring older vectors up to FEATURE_SOURCE. Each is cut from pad_box(box). Never raises."""
    try:
        import numpy as np          # type: ignore
        import torch                # type: ignore
        from PIL import Image       # type: ignore
        img = np.array(Image.open(image_path).convert("RGB"))
        device = "cuda" if torch.cuda.is_available() else "cpu"
        classifier = _load_classifier(device=device)
        parts = _dfne_parts(classifier)
        out = []
        with torch.inference_mode():
            for b in boxes:
                crop = _crop_frac(img, pad_box(b))
                if crop.size == 0:
                    out.append((None, None))
                elif parts:
                    out.append((_dfne_features(parts, crop), FEATURE_SOURCE))
                else:
                    out.append(deer_features(classifier, crop, device))
        return out
    except Exception as e:
        log.warning("detection: deer vectors failed on %s: %s: %s", image_path, type(e).__name__, e)
        return [(None, None)] * len(boxes)


def _load_feature_fallback(device: str):
    """A small ImageNet backbone (convnext_tiny), used for deer feature vectors only if DFNE's own
    network can't be reached. Loaded lazily and released by unload_models like the others."""
    global _FEATURE_FALLBACK
    if _FEATURE_FALLBACK is not None:
        return _FEATURE_FALLBACK
    with _LOCK:
        if _FEATURE_FALLBACK is None:
            import timm  # type: ignore
            from timm.data import create_transform, resolve_data_config  # type: ignore
            net = timm.create_model("convnext_tiny", pretrained=True, num_classes=0).to(device).eval()
            _FEATURE_FALLBACK = (net, create_transform(**resolve_data_config({}, model=net)))
            log.info("detection: using convnext_tiny for deer feature vectors")
    return _FEATURE_FALLBACK


def deer_features(classifier, crop, device: str) -> tuple[list[float] | None, str | None]:
    """Fallback deer vector from convnext_tiny, for when the classifier isn't a TIMM model whose
    features can be read (see _dfne_parts). Returns (vector, source) or (None, None)."""
    try:
        from PIL import Image  # type: ignore
        net, tf = _load_feature_fallback(device)
        x = tf(Image.fromarray(crop)).unsqueeze(0).to(device)
        f = net.forward_head(net.forward_features(x), pre_logits=True)
        return [float(v) for v in f.flatten().cpu().numpy()], "convnext_tiny"
    except Exception as e:
        log.warning("detection: deer feature extraction failed: %s: %s", type(e).__name__, e)
        return None, None


def detect_animal(image_path: str) -> dict:
    """
    Return {"is_animal": bool, "confidence": float, "detector": str,
            "species": str | None, "species_confidence": float | None,
            "detections": [{box, det_conf, species, species_confidence, features, feature_source}]}.

    The top-level fields describe the most confident animal (as before). `detections` lists every
    animal box (up to MAX_ANIMALS), with `box` as fractions of the image (x1, y1, x2, y2), and a
    feature vector for each white-tailed deer — used by the buck model.

    Never raises. On model failure returns is_animal=False with detector="error(...)"
    so the caller can decide what to do (currently: save at conf=0 so photos are
    never silently discarded).
    """
    if _MODE == "fallback":
        return {"is_animal": True, "confidence": 0.0, "detector": "fallback",
                "species": None, "species_confidence": None, "detections": []}

    try:
        import torch                # type: ignore
        # PytorchWildlife doesn't turn off autograd itself; inference mode is faster and leaner
        with torch.inference_mode():
            return _detect(image_path)
    except Exception as e:
        log.warning("Detection/Classification failed on %s: %s: %s", image_path, type(e).__name__, e)
        return {"is_animal": False, "confidence": 0.0, "detector": f"error ({type(e).__name__})",
                "species": None, "species_confidence": None, "detections": []}


_feature_source_logged = False


def _detect(image_path: str) -> dict:
    global _feature_source_logged
    import numpy as np          # type: ignore
    from PIL import Image       # type: ignore

    model = _load_model()
    img = np.array(Image.open(image_path).convert("RGB"))
    h, w = img.shape[:2]
    results = model.single_image_detection(img, img_path=image_path)

    # 1. Every animal box at or above the threshold, most confident first
    dets = results.get("detections")
    boxes: list[tuple[float, object]] = []
    if dets is not None and dets.confidence is not None and dets.class_id is not None:
        for i, (cls_id, conf) in enumerate(zip(dets.class_id, dets.confidence)):
            if int(cls_id) == ANIMAL_CLASS_ID:
                boxes.append((float(conf), dets.xyxy[i]))
    boxes.sort(key=lambda b: b[0], reverse=True)
    best_conf = boxes[0][0] if boxes else 0.0

    is_animal = best_conf >= CONF_THRESHOLD
    response = {
        "is_animal": is_animal,
        "confidence": round(best_conf, 3),
        "detector": "megadetector",
        "species": None,
        "species_confidence": None,
        "detections": [],
    }

    # 2. Classify each animal on its tight box (what DFNE was trained on). Deer also get a feature vector for
    #    the buck model, from a second pass on the padded box so the whole rack is in it.
    if is_animal:
        # Determine the device MegaDetector is currently utilizing
        device = "cuda" if "cuda" in str(getattr(model, "device", "cpu")) else "cpu"
        classifier = _load_classifier(device=device)
        parts = _dfne_parts(classifier)
        if not _feature_source_logged:
            log.info("detection: deer feature vectors from %s", FEATURE_SOURCE if parts else "convnext_tiny")
            _feature_source_logged = True
        for conf, box in [b for b in boxes if b[0] >= CONF_THRESHOLD][:MAX_ANIMALS]:
            x1, y1, x2, y2 = map(int, box)
            x1, y1 = max(0, x1), max(0, y1)
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            frac = [round(x1 / w, 4), round(y1 / h, 4), round(min(x2, w) / w, 4), round(min(y2, h) / h, 4)]
            species, species_conf = _dfne_species(parts, crop) if parts else _classify(classifier, crop)
            features, source = None, None
            if is_deer(species):
                padded = _crop_frac(img, pad_box(frac))
                features, source = ((_dfne_features(parts, padded), FEATURE_SOURCE) if parts
                                    else deer_features(classifier, padded, device))
            response["detections"].append({
                "box": frac,
                "det_conf": round(conf, 3), "species": species, "species_confidence": species_conf,
                "features": features, "feature_source": source,
            })
        if response["detections"]:
            top = response["detections"][0]
            response["species"], response["species_confidence"] = top["species"], top["species_confidence"]

    return response
