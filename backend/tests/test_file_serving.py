"""File-serving routes must stay inside their base directory and, when APP_TOKEN is set,
require the token (as a header, or as `?t=` for <img> URLs)."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cameras import router as cam_router
from app.core import security
from app.core.security import safe_join

TOKEN = "s3cret"


@pytest.fixture()
def dirs(tmp_path):
    base = tmp_path / "images"
    (base / "region_1").mkdir(parents=True)
    (base / "region_1" / "a.jpg").write_bytes(b"photo")
    (tmp_path / "secret.txt").write_text("do not serve")
    return base


@pytest.fixture()
def client(dirs, monkeypatch):
    monkeypatch.setattr(cam_router, "get_settings", lambda: {"camera_image_dir": str(dirs)})
    monkeypatch.setattr(cam_router, "CAMERA_IMAGE_DIR", str(dirs))
    app = FastAPI()
    app.include_router(cam_router.router)
    return TestClient(app)


def test_safe_join_inside(dirs):
    assert safe_join(str(dirs), "region_1/a.jpg") == os.path.realpath(dirs / "region_1" / "a.jpg")


@pytest.mark.parametrize("sub", ["../secret.txt", "region_1/../../secret.txt", "..", os.path.abspath(os.sep)])
def test_safe_join_rejects_escape(dirs, sub):
    assert safe_join(str(dirs), sub) is None


def test_static_camera_image_serves_file(client):
    r = client.get("/static/camera_images/region_1/a.jpg")
    assert r.status_code == 200 and r.content == b"photo"


def test_static_camera_image_blocks_traversal(client):
    r = client.get("/static/camera_images/%2e%2e/secret.txt")
    assert r.status_code == 404


def test_static_camera_image_requires_token(client, monkeypatch):
    monkeypatch.setattr(security, "APP_TOKEN", TOKEN)
    assert client.get("/static/camera_images/region_1/a.jpg").status_code == 401
    assert client.get("/static/camera_images/region_1/a.jpg?t=wrong").status_code == 401
    assert client.get(f"/static/camera_images/region_1/a.jpg?t={TOKEN}").status_code == 200
    assert client.get("/static/camera_images/region_1/a.jpg",
                      headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_sighting_image_requires_token(client, monkeypatch):
    monkeypatch.setattr(security, "APP_TOKEN", TOKEN)
    # rejected before the database is touched
    assert client.get("/api/camera-sightings/1/image").status_code == 401
    assert client.get("/api/camera-sightings/1/image?t=wrong").status_code == 401
