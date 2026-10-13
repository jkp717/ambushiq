import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.cameras import bucks, detection
from app.cameras.models import DeerDetection


def test_pad_box_leaves_room_above_for_antlers_and_stays_in_the_photo():
    assert detection.pad_box([0.25, 0.4, 0.5, 0.9]) == [0.225, 0.3, 0.525, 0.94]   # 10% sides, 20% top, 8% bottom
    assert detection.pad_box([0.0, 0.05, 1.0, 0.98]) == [0.0, 0.0, 1.0, 1.0]       # clamped at the edges


@pytest.fixture()
def photo(tmp_path, monkeypatch):
    """A 200x100 photo, and a stand-in DFNE that records the size of each crop it's given."""
    path = tmp_path / "p.jpg"
    Image.new("RGB", (200, 100), (90, 120, 60)).save(path)
    seen = {"species": [], "features": []}
    monkeypatch.setattr(detection, "_load_classifier", lambda device="cpu": object())
    monkeypatch.setattr(detection, "_dfne_parts", lambda classifier: ("dfne",))
    monkeypatch.setattr(detection, "_dfne_species",
                        lambda parts, crop: seen["species"].append(crop.shape[:2]) or (detection.DEER_SPECIES, 0.9))
    monkeypatch.setattr(detection, "_dfne_features",
                        lambda parts, crop: seen["features"].append(crop.shape[:2]) or [0.0] * 4)
    return str(path), seen


def test_species_uses_the_tight_box_and_deer_features_the_padded_one(photo, monkeypatch):
    path, seen = photo
    dets = SimpleNamespace(class_id=np.array([detection.ANIMAL_CLASS_ID]), confidence=np.array([0.9]),
                           xyxy=np.array([[50.0, 40.0, 100.0, 90.0]]))
    model = SimpleNamespace(single_image_detection=lambda img, img_path=None: {"detections": dets}, device="cpu")
    monkeypatch.setattr(detection, "_load_model", lambda: model)
    out = detection._detect(path)
    d = out["detections"][0]
    assert d["box"] == [0.25, 0.4, 0.5, 0.9] and d["feature_source"] == detection.FEATURE_SOURCE
    assert seen["species"] == [(50, 50)]            # the detector's own box
    assert seen["features"] == [(64, 60)]           # padded: 30..94 tall, 45..105 wide


def test_refreshing_a_saved_deer_crops_the_padded_box(photo):
    path, seen = photo
    out = detection.deer_vectors(path, [[0.25, 0.4, 0.5, 0.9]])
    assert out == [([0.0] * 4, detection.FEATURE_SOURCE)] and seen["features"] == [(64, 60)]


def test_deer_carry_a_roomier_outline_for_display():
    d = DeerDetection(id=1, box_json=json.dumps([0.25, 0.4, 0.5, 0.9]), species=detection.DEER_SPECIES, vec="x")
    out = bucks._det_dict(d)
    assert out["box"] == [0.25, 0.4, 0.5, 0.9] and out["outline"] == detection.pad_box(out["box"])
