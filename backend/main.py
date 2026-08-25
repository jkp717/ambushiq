"""AmbushIQ API."""
from __future__ import annotations
import os
import math
import json
import logging
import secrets
import time
import asyncio

log = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, String, Float, Integer, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

import terrain as terrain_mod
import scoring
import deer_rating
import cameras as cameras_mod
import detection as detection_mod

DB_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://ambush:ambush@db:5432/ambushiq")
APP_TOKEN = os.environ.get("APP_TOKEN", "")  # shared secret; required in prod

def _read_version() -> str:
    for p in ("/app/VERSION", os.path.join(os.path.dirname(__file__), "..", "VERSION"),
              os.path.join(os.path.dirname(__file__), "VERSION")):
        try:
            with open(p) as f:
                return f.read().strip()
        except OSError:
            continue
    return "unknown"

APP_VERSION = _read_version()
engine = create_engine(DB_URL, pool_pre_ping=True)

# v2.15: trail cameras. credentials_json holds a Fernet-encrypted blob (never plaintext).
CAMERA_BRANDS = ("spypoint", "reveal", "moultrie", "stealth_cam", "browning", "spartan")

CAMERA_IMAGE_DIR = os.environ.get("CAMERA_IMAGE_DIR", "/app/data/camera_images")

# home/hunt region center — stored separately; absent until the user sets it
HOME_KEYS = ("home_lat", "home_lon")

# default proximity weights + falloffs (meters)
DEFAULT_SETTINGS = {
    "weight_corridor": 0.15, "falloff_corridor": 150,
    "weight_food": 0.15, "falloff_food": 200,
    "weight_bedding": 0.10, "falloff_bedding": 250,
    # deer day-rating weather factor weights (relative; normalized at use)
    "rate_w_pressure": 0.32, "rate_w_wind": 0.20, "rate_w_rain": 0.28, "rate_w_temp": 0.20,
    # v2.15: trail-camera + rut-date settings
    "camera_sync_interval_minutes": 30,
    "image_retention_days": 60,
    "max_camera_boost_pct": 15.0,
    "rut_peak_month": 12,
    "rut_peak_day": 5,
    "camera_image_dir": CAMERA_IMAGE_DIR,
}


def _sanitize_path_component(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in (name or "unnamed")).strip()
    return safe or "unnamed"


def get_camera_dir(brand: str, camera_name: str, base_dir: str | None = None) -> str:
    if not base_dir:
        settings = get_settings()
        base_dir = str(settings.get("camera_image_dir") or CAMERA_IMAGE_DIR)
    safe_brand = _sanitize_path_component(brand)
    safe_name = _sanitize_path_component(camera_name)
    return os.path.join(base_dir, safe_brand, safe_name)


# ---------- credential encryption (Fernet key derived from an existing secret) ----------
# We derive a stable key from POSTGRES_PASSWORD (already managed by the owner) so no new
# secret is needed. Tradeoff: rotating that password invalidates stored camera credentials
# (they'd need re-entry). Fine for a single-user self-hosted app.
def _fernet():
    from cryptography.fernet import Fernet
    import base64
    import hashlib
    secret = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DATABASE_URL", "ambushiq-fallback")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_credentials(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(blob: Optional[str]) -> dict:
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(blob.encode()).decode())
    except Exception:
        return {}


class Base(DeclarativeBase):
    pass


class Stand(Base):
    __tablename__ = "stands"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    downhill_deg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deer_approach_deg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    terrain_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon,
            "downhill_deg": self.downhill_deg, "deer_approach_deg": self.deer_approach_deg,
            "terrain": json.loads(self.terrain_json) if self.terrain_json else None,
        }


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # "bedding" | "food"
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=80)
    # food-zone quality 1 (poor) – 10 (premium); scales proximity contribution via steeper curve
    quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "lat": self.lat, "lon": self.lon, "radius_m": self.radius_m,
                "quality": self.quality}


class Corridor(Base):
    __tablename__ = "corridors"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # usage frequency 1 (rarely used) – 10 (heavily used); scales proximity contribution
    usage: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    # per-corridor falloff distance in metres; NULL → use global falloff_corridor setting
    falloff_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # polyline as JSON list of [lat, lon] points
    points_json: Mapped[str] = mapped_column(Text)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "usage": self.usage if self.usage is not None else 5,
                "falloff_m": self.falloff_m,
                "points": json.loads(self.points_json)}


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[str] = mapped_column(String(32))
    credentials_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    stand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # FK stands.id
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    last_sync_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def to_dict(self) -> dict:
        # NEVER expose credentials
        return {
            "id": self.id, "name": self.name, "brand": self.brand,
            "stand_id": self.stand_id, "is_active": bool(self.is_active),
            "last_sync_at": self.last_sync_at, "created_at": self.created_at,
            "has_credentials": bool(self.credentials_json),
        }


class CameraSighting(Base):
    __tablename__ = "camera_sightings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    camera_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[str] = mapped_column(String(32))  # ISO of the sighting
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    image_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # NULL after cleanup
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def to_dict(self) -> dict:
        img = None
        if self.image_path and os.path.exists(self.image_path):
            img = f"/api/camera-sightings/{self.id}/image"
        return {
            "id": self.id, "stand_id": self.stand_id, "camera_id": self.camera_id,
            "timestamp": self.timestamp, "confidence_score": self.confidence_score,
            "image_url": img, "created_at": self.created_at,
        }


def init_db(retries: int = 30):
    from sqlalchemy import text
    for attempt in range(retries):
        try:
            Base.metadata.create_all(engine)
            with engine.connect() as conn:
                try:
                    conn.execute(text("ALTER TABLE settings ALTER COLUMN value TYPE TEXT;"))
                    conn.commit()
                except Exception:
                    pass
                # v2.16: per-corridor usage rating and falloff distance
                try:
                    conn.execute(text("ALTER TABLE corridors ADD COLUMN IF NOT EXISTS usage INTEGER NOT NULL DEFAULT 5"))
                    conn.execute(text("ALTER TABLE corridors ADD COLUMN IF NOT EXISTS falloff_m FLOAT"))
                    conn.commit()
                except Exception:
                    pass
                # v2.16.1: food zone quality rating
                try:
                    conn.execute(text("ALTER TABLE zones ADD COLUMN IF NOT EXISTS quality INTEGER"))
                    conn.commit()
                except Exception:
                    pass
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)


app = FastAPI(title="AmbushIQ")


@app.get("/api/camera-sightings/{sighting_id}/image")
def get_sighting_image(sighting_id: int):
    with Session(engine) as s:
        sighting = s.get(CameraSighting, sighting_id)
        if not sighting or not sighting.image_path or not os.path.exists(sighting.image_path):
            raise HTTPException(404, "image not found")
        return FileResponse(sighting.image_path)


@app.get("/static/camera_images/{subpath:path}")
def get_static_camera_image(subpath: str):
    settings = get_settings()
    base_dir = str(settings.get("camera_image_dir") or CAMERA_IMAGE_DIR)
    candidate = os.path.join(base_dir, subpath)
    if os.path.isfile(candidate):
        return FileResponse(candidate)
    candidate_default = os.path.join(CAMERA_IMAGE_DIR, subpath)
    if os.path.isfile(candidate_default):
        return FileResponse(candidate_default)
    raise HTTPException(404, "image not found")


@app.on_event("startup")
def _startup():
    init_db()
    try:
        start_scheduler()
    except Exception:
        pass  # scheduler is best-effort; app must boot regardless


def require_token(authorization: str = Header(default="")):
    # If APP_TOKEN is empty or "unused", authentication is handled by an upstream
    # reverse proxy (e.g. Authentik forward auth) or disabled.
    # Otherwise, require the Bearer token.
    if not APP_TOKEN or APP_TOKEN == "unused":
        return
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else authorization.strip()
    if not token or not secrets.compare_digest(token, APP_TOKEN):
        raise HTTPException(401, "unauthorized")


# ---------- schemas ----------
class StandIn(BaseModel):
    name: str
    lat: float
    lon: float
    downhill_deg: Optional[int] = None
    deer_approach_deg: Optional[int] = None


class SitRankIn(BaseModel):
    sit_idxs: list[int]
    sunrise_h: float
    sunset_h: float


class ManualRankIn(BaseModel):
    wind_dir: str
    wind_speed: float
    gust: float
    period: str  # morning|midday|evening


class ZoneIn(BaseModel):
    kind: str
    name: Optional[str] = None
    lat: float
    lon: float
    radius_m: int = 80
    quality: Optional[int] = None  # food zones only; 1=poor … 10=premium


class CorridorIn(BaseModel):
    name: Optional[str] = None
    points: list[list[float]]
    usage: int = 5          # 1 = rarely used, 10 = heavily used
    falloff_m: Optional[float] = None  # None → inherit global falloff_corridor


class HourRankIn(BaseModel):
    time_index: int  # index into the forecast hourly arrays


# ---------- auth check (frontend pings this) ----------
@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(APP_TOKEN and APP_TOKEN != "unused"), "version": APP_VERSION}


@app.get("/api/verify")
def verify(_=Depends(require_token)):
    return {"ok": True}


# ---------- stand CRUD ----------
@app.get("/api/stands")
def list_stands(_=Depends(require_token)):
    with Session(engine) as s:
        return [r.to_dict() for r in s.scalars(select(Stand).order_by(Stand.name)).all()]


@app.post("/api/stands")
def create_stand(body: StandIn, _=Depends(require_token)):
    with Session(engine) as s:
        st = Stand(**body.model_dump())
        s.add(st)
        s.commit()
        s.refresh(st)
        return st.to_dict()


@app.put("/api/stands/{stand_id}")
def update_stand(stand_id: int, body: StandIn, _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if not st:
            raise HTTPException(404, "not found")
        moved = (st.lat != body.lat) or (st.lon != body.lon)
        for k, v in body.model_dump().items():
            setattr(st, k, v)
        if moved:
            st.terrain_json = None  # invalidate cached terrain on move
        s.commit()
        s.refresh(st)
        return st.to_dict()


@app.delete("/api/stands/{stand_id}")
def delete_stand(stand_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if st:
            s.delete(st)
            s.commit()
    return {"ok": True}


# ---------- terrain (cached) ----------
@app.post("/api/stands/{stand_id}/terrain")
async def analyze_stand_terrain(stand_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        if not st:
            raise HTTPException(404, "not found")
        lat, lon = st.lat, st.lon
    try:
        terrain = await terrain_mod.fetch_terrain(lat, lon)
    except Exception as e:
        raise HTTPException(502, f"elevation source unreachable: {e}")
    with Session(engine) as s:
        st = s.get(Stand, stand_id)
        st.terrain_json = json.dumps(terrain)
        st.downhill_deg = terrain["downhill_deg"]
        s.commit()
        s.refresh(st)
        return st.to_dict()


def get_settings() -> dict:
    with Session(engine) as s:
        rows = {}
        for r in s.scalars(select(Setting)).all():
            val = r.value
            if r.key in DEFAULT_SETTINGS:
                def_val = DEFAULT_SETTINGS[r.key]
                if isinstance(def_val, float):
                    try:
                        rows[r.key] = float(val)
                    except (ValueError, TypeError):
                        rows[r.key] = def_val
                elif isinstance(def_val, int):
                    try:
                        rows[r.key] = int(float(val))
                    except (ValueError, TypeError):
                        rows[r.key] = def_val
                else:
                    rows[r.key] = str(val)
            else:
                rows[r.key] = val
    return {**DEFAULT_SETTINGS, **rows}


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, asin, sqrt
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def _point_to_segment_m(plat, plon, alat, alon, blat, blon) -> float:
    """Approx distance (m) from point P to segment AB using a local equirectangular
    projection — fine at property scale."""
    from math import radians, cos
    lat0 = radians((alat + blat) / 2)
    mlon = 111320.0 * cos(lat0)
    mlat = 110540.0
    ax, ay = (alon - plon) * mlon, (alat - plat) * mlat
    bx, by = (blon - plon) * mlon, (blat - plat) * mlat
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return (ax * ax + ay * ay) ** 0.5
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return (cx * cx + cy * cy) ** 0.5


def proximity_bonus(stand: dict, zones: list, corridors: list, settings: dict) -> dict:
    """Stacking, bonus-only proximity boost. Each feature contributes
    max(0, 1 - dist/falloff); summed per type and scaled by that type's weight."""
    slat, slon = stand["lat"], stand["lon"]

    def zone_factor(kind, falloff):
        total = 0.0
        for z in zones:
            if z["kind"] != kind:
                continue
            d = max(0.0, _haversine_m(slat, slon, z["lat"], z["lon"]) - (z.get("radius_m") or 0))
            contrib = max(0.0, 1 - d / falloff) if falloff > 0 else 0
            # food zones: scale by quality using a steeper curve so top ratings stand out
            # (quality/10)^1.5 → rating 5 ≈ 35%, rating 8 ≈ 72%, rating 10 = 100%
            if kind == "food":
                quality = max(1, min(10, z.get("quality") or 5))
                contrib *= (quality / 10) ** 1.5
            total += contrib
        return total

    def corridor_factor():
        total = 0.0
        for c in corridors:
            # per-corridor falloff; fall back to global setting when not set
            falloff = c.get("falloff_m") or settings["falloff_corridor"]
            # usage 1-10 scales the contribution linearly (usage/10)
            usage_scale = max(1, min(10, c.get("usage") or 5)) / 10.0
            pts = c["points"]
            dmin = None
            for i in range(len(pts) - 1):
                d = _point_to_segment_m(slat, slon, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                dmin = d if dmin is None else min(dmin, d)
            if dmin is not None:
                contrib = max(0.0, 1 - dmin / falloff) if falloff > 0 else 0
                total += contrib * usage_scale
        return total

    b_cor = corridor_factor() * settings["weight_corridor"]
    b_food = zone_factor("food", settings["falloff_food"]) * settings["weight_food"]
    b_bed = zone_factor("bedding", settings["falloff_bedding"]) * settings["weight_bedding"]
    return {"corridor": b_cor, "food": b_food, "bedding": b_bed, "total": b_cor + b_food + b_bed}


# ---------- forecast (server-side, short cache) ----------
_fc_cache: dict[str, tuple[float, dict]] = {}
FC_TTL = 1800  # 30 min


async def get_forecast(lat: float, lon: float, days: int = 3) -> dict:
    key = f"{lat:.3f},{lon:.3f}:{days}"
    now = time.time()
    if key in _fc_cache and now - _fc_cache[key][0] < FC_TTL:
        return _fc_cache[key][1]
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&hourly=wind_direction_10m,wind_speed_10m,wind_gusts_10m,shortwave_radiation,temperature_2m,cloud_cover,surface_pressure,precipitation"
        f"&daily=sunrise,sunset&wind_speed_unit=mph&timezone=auto&forecast_days={days}"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        j = r.json()
    _fc_cache[key] = (now, j)
    return j


def build_sits(forecast: dict) -> list[dict]:
    times = forecast["hourly"]["time"]
    sun = forecast["daily"]
    sits = []
    for day_i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][day_i])
        ss = datetime.fromisoformat(sun["sunset"][day_i])
        sr_h = sr.hour + sr.minute / 60
        ss_h = ss.hour + ss.minute / 60
        day_str = sun["sunrise"][day_i][:10]
        label = datetime.fromisoformat(day_str + "T12:00").strftime("%a %b %-d")
        for tag, frm, to in (("morning", sr_h - 1, sr_h + 3), ("evening", ss_h - 3, ss_h + 0.5)):
            idxs = []
            lo, hi = math.floor(frm), math.ceil(to)
            for i, tstr in enumerate(times):
                if tstr[:10] != day_str:
                    continue
                h = datetime.fromisoformat(tstr).hour
                if lo <= h <= hi:
                    idxs.append(i)
            if idxs:
                sits.append({"label": f"{label} — {tag}", "idxs": idxs, "sunrise_h": sr_h, "sunset_h": ss_h})
    return sits


@app.get("/api/forecast")
async def forecast_endpoint(_=Depends(require_token)):
    with Session(engine) as s:
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    try:
        fc = await get_forecast(lat, lon)
    except Exception as e:
        raise HTTPException(502, f"forecast unreachable: {e}")
    return {"sits": build_sits(fc)}


@app.post("/api/rank/sit")
async def rank_sit(body: SitRankIn, _=Depends(require_token)):
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(select(Stand)).all()]
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon)
    h = fc["hourly"]
    mid = (body.sunrise_h + body.sunset_h) / 2
    results = []
    for st in stands:
        agg, n, sample = 0.0, 0, None
        for i in body.sit_idxs:
            hour = {
                "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
                "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
                "time_h": datetime.fromisoformat(h["time"][i]).hour,
                "sunrise_h": body.sunrise_h, "sunset_h": body.sunset_h,
            }
            sc = scoring.score_stand_hour(st, hour)
            agg += sc["total"]
            n += 1
            dist = abs(hour["time_h"] - mid)
            if sample is None or dist < sample["dist"]:
                sample = {"hour": hour, "score": sc, "dist": dist}
        results.append({"stand": st, "avg": round(agg / max(1, n), 3), "sample": sample})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


@app.post("/api/rank/manual")
def rank_manual(body: ManualRankIn, _=Depends(require_token)):
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(select(Stand)).all()]
    wind_from = scoring.compass_to_deg(body.wind_dir)
    time_h = {"morning": 7, "midday": 13, "evening": 18}.get(body.period, 13)
    solar = 500 if body.period == "midday" else 50
    results = []
    for st in stands:
        hour = {"wind_dir": wind_from, "wind_speed": body.wind_speed, "gust": body.gust,
                "solar": solar, "time_h": time_h, "sunrise_h": 6.5, "sunset_h": 19}
        sc = scoring.score_stand_hour(st, hour)
        results.append({"stand": st, "avg": sc["total"], "sample": {"hour": hour, "score": sc}})
    results.sort(key=lambda x: x["avg"], reverse=True)
    return {"ranked": results}


# ---------- zones ----------
@app.get("/api/zones")
def list_zones(_=Depends(require_token)):
    with Session(engine) as s:
        return [z.to_dict() for z in s.scalars(select(Zone)).all()]


@app.post("/api/zones")
def create_zone(body: ZoneIn, _=Depends(require_token)):
    with Session(engine) as s:
        z = Zone(**body.model_dump())
        s.add(z)
        s.commit()
        s.refresh(z)
        return z.to_dict()


@app.put("/api/zones/{zone_id}")
def update_zone(zone_id: int, body: ZoneIn, _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if not z:
            raise HTTPException(404, "not found")
        for k, v in body.model_dump().items():
            setattr(z, k, v)
        s.commit()
        s.refresh(z)
        return z.to_dict()


@app.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        z = s.get(Zone, zone_id)
        if z:
            s.delete(z)
            s.commit()
    return {"ok": True}


# ---------- corridors ----------
@app.get("/api/corridors")
def list_corridors(_=Depends(require_token)):
    with Session(engine) as s:
        return [c.to_dict() for c in s.scalars(select(Corridor)).all()]


@app.post("/api/corridors")
def create_corridor(body: CorridorIn, _=Depends(require_token)):
    if len(body.points) < 2:
        raise HTTPException(400, "a corridor needs at least 2 points")
    with Session(engine) as s:
        c = Corridor(name=body.name, points_json=json.dumps(body.points),
                     usage=max(1, min(10, body.usage)), falloff_m=body.falloff_m)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.put("/api/corridors/{corridor_id}")
def update_corridor(corridor_id: int, body: CorridorIn, _=Depends(require_token)):
    with Session(engine) as s:
        c = s.get(Corridor, corridor_id)
        if not c:
            raise HTTPException(404, "not found")
        c.name = body.name
        c.usage = max(1, min(10, body.usage))
        c.falloff_m = body.falloff_m
        if body.points and len(body.points) >= 2:
            c.points_json = json.dumps(body.points)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.delete("/api/corridors/{corridor_id}")
def delete_corridor(corridor_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        c = s.get(Corridor, corridor_id)
        if c:
            s.delete(c)
            s.commit()
    return {"ok": True}


# ---------- hourly conditions for the map + synced ranking ----------
@app.get("/api/hours")
async def list_hours(_=Depends(require_token)):
    """Forecast hours grouped by day, for the day picker + hourly slider."""
    with Session(engine) as s:
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    try:
        fc = await get_forecast(lat, lon, days=14)
    except Exception as e:
        raise HTTPException(502, f"forecast unreachable: {e}")
    times = fc["hourly"]["time"]
    sun = fc["daily"]
    sun_by_day = {}
    for i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][i])
        ss = datetime.fromisoformat(sun["sunset"][i])
        sun_by_day[sun["sunrise"][i][:10]] = {
            "sunrise_h": sr.hour + sr.minute / 60,
            "sunset_h": ss.hour + ss.minute / 60,
            "sunrise": sun["sunrise"][i][11:16],
            "sunset": sun["sunset"][i][11:16],
        }
    days = {}
    for idx, tstr in enumerate(times):
        day = tstr[:10]
        days.setdefault(day, {"day": day,
                              "label": datetime.fromisoformat(day + "T12:00").strftime("%a %b %-d"),
                              **sun_by_day.get(day, {"sunrise_h": 6.5, "sunset_h": 19, "sunrise": "", "sunset": ""}),
                              "hours": []})
        dt = datetime.fromisoformat(tstr)
        days[day]["hours"].append({"index": idx, "hour": dt.hour,
                                   "label": dt.strftime("%-I %p").lower()})
    from datetime import date as _date
    day_list = list(days.values())
    for dd in day_list:
        y, m, dnum = (int(x) for x in dd["day"].split("-"))
        days_out = (_date(y, m, dnum) - _date.today()).days
        dd["confidence"] = "high" if days_out <= 7 else "low"
        dd["days_out"] = days_out
    return {"days": day_list}


@app.post("/api/map/conditions")
async def map_conditions(body: HourRankIn, _=Depends(require_token)):
    """Per-stand wind + thermal vectors at one forecast hour, plus a ranked list
    in sync with that same hour. Drives the map indicators and the list together.
    Camera boost is applied when configured so the map rank matches /api/day/ranked."""
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(select(Stand)).all()]
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14)
    h = fc["hourly"]
    i = body.time_index
    if i < 0 or i >= len(h["time"]):
        raise HTTPException(400, "time_index out of range")
    day = h["time"][i][:10]
    sun = fc["daily"]
    sr_h, ss_h = 6.5, 19.0
    for k in range(len(sun["sunrise"])):
        if sun["sunrise"][k][:10] == day:
            sr = datetime.fromisoformat(sun["sunrise"][k])
            ss = datetime.fromisoformat(sun["sunset"][k])
            sr_h = sr.hour + sr.minute / 60
            ss_h = ss.hour + ss.minute / 60
    hour = {
        "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
        "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
        "time_h": datetime.fromisoformat(h["time"][i]).hour,
        "sunrise_h": sr_h, "sunset_h": ss_h,
    }

    settings = get_settings()
    max_cam_boost = float(settings.get("max_camera_boost_pct", 0.0) or 0.0)
    utc_offset = int(fc.get("utc_offset_seconds", 0))
    period = scoring.period_for_hour(hour["time_h"])

    # Load recent sightings per stand when boost is configured — keeps map rank in sync
    # with /api/day/ranked which already applies camera boost.
    sightings_by_stand: dict[int, list] = {}
    if period and max_cam_boost:
        with Session(engine) as s:
            for row in s.scalars(select(CameraSighting)).all():
                sightings_by_stand.setdefault(row.stand_id, []).append(
                    {"timestamp": row.timestamp, "confidence_score": row.confidence_score})

    items = []
    for st in stands:
        vec = scoring.stand_hour_vectors(st, hour)
        if period and max_cam_boost:
            sightings = sightings_by_stand.get(st["id"], [])
            if sightings:
                boost = scoring.camera_boost(period, sightings, max_cam_boost, utc_offset)
                vec = dict(vec)  # don't mutate the original
                vec["total"] = round(vec["total"] * boost["multiplier"], 3)
                vec["camera_boost"] = boost
        items.append({"stand": st, "vectors": vec})

    ranked = sorted(
        [{"stand": it["stand"], "avg": it["vectors"]["total"],
          "sample": {"hour": hour, "score": {
              "scent_to_deg": it["vectors"]["scent_to_deg"],
              "scent_score": it["vectors"]["scent_score"],
              "thermal_phase": it["vectors"]["thermal_phase"],
              "drainage_deg": it["vectors"]["thermal_to_deg"],
          }}} for it in items],
        key=lambda x: x["avg"], reverse=True,
    )
    return {
        "time": {"index": i, "iso": h["time"][i],
                 "label": datetime.fromisoformat(h["time"][i]).strftime("%a %b %-d, %-I %p"),
                 "temp": h["temperature_2m"][i], "cloud": h["cloud_cover"][i]},
        "stands": items,
        "ranked": ranked,
    }


class DayRankIn(BaseModel):
    day: str  # "YYYY-MM-DD"
    use_corridor: bool = True
    use_food: bool = True
    use_bedding: bool = True


@app.post("/api/day/ranked")
async def day_ranked(body: DayRankIn, _=Depends(require_token)):
    """For a given day, score every stand across morning / midday / evening and
    return the full ranked list (by best period score) with each stand tagged for
    any period it wins."""
    with Session(engine) as s:
        stands = [r.to_dict() for r in s.scalars(select(Stand)).all()]
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "no stands")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14)
    h = fc["hourly"]
    sun = fc["daily"]
    day = body.day
    sr_h, ss_h = 6.5, 19.0
    for k in range(len(sun["sunrise"])):
        if sun["sunrise"][k][:10] == day:
            sr = datetime.fromisoformat(sun["sunrise"][k])
            ss = datetime.fromisoformat(sun["sunset"][k])
            sr_h = sr.hour + sr.minute / 60
            ss_h = ss.hour + ss.minute / 60

    # UTC offset for the property's location — used to convert stored UTC sighting
    # timestamps to local time before period matching in camera_boost.
    utc_offset = int(fc.get("utc_offset_seconds", 0))

    # period hour windows (clamped to available forecast hours for the day)
    periods = {
        "morning": (int(sr_h - 1), int(sr_h + 3)),
        "midday": (max(int(sr_h + 3) + 1, 10), 15),
        "evening": (int(ss_h - 3), int(ss_h)),
    }
    # index hours of this day
    day_idxs = [i for i, t in enumerate(h["time"]) if t[:10] == day]
    if not day_idxs:
        raise HTTPException(400, "no forecast for that day")

    # proximity inputs
    with Session(engine) as s:
        zones = [z.to_dict() for z in s.scalars(select(Zone)).all()]
        corridors_l = [c.to_dict() for c in s.scalars(select(Corridor)).all()]
    settings = get_settings()
    # honor the per-type enable toggles from the rank list
    settings = dict(settings)
    if not body.use_corridor: settings["weight_corridor"] = 0.0
    if not body.use_food: settings["weight_food"] = 0.0
    if not body.use_bedding: settings["weight_bedding"] = 0.0
    max_cam_boost = float(settings.get("max_camera_boost_pct", 0.0) or 0.0)

    # recent sightings per stand (last 72h handled inside camera_boost)
    sightings_by_stand: dict[int, list] = {}
    with Session(engine) as s:
        for row in s.scalars(select(CameraSighting)).all():
            sightings_by_stand.setdefault(row.stand_id, []).append(
                {"timestamp": row.timestamp, "confidence_score": row.confidence_score})

    def score_period(stand, lo, hi, bonus, period_name, sightings):
        best = None
        for i in day_idxs:
            hh = datetime.fromisoformat(h["time"][i]).hour
            if hh < lo or hh > hi:
                continue
            hour = {
                "wind_dir": h["wind_direction_10m"][i], "wind_speed": h["wind_speed_10m"][i],
                "gust": h["wind_gusts_10m"][i], "solar": h["shortwave_radiation"][i],
                "time_h": hh, "sunrise_h": sr_h, "sunset_h": ss_h,
            }
            det = scoring.score_with_breakdown(
                stand, hour, period=period_name, sightings=sightings,
                max_boost_pct=max_cam_boost, proximity=bonus,
                utc_offset_seconds=utc_offset)
            sc = {
                "total": det["final_score"],
                "base_total": det["base_score"],
                "proximity_bonus": det["proximity_bonus"],
                "camera": det["camera"],
                "breakdown": det["breakdown"],
                "scent_score": det["scent_score"],
                "scent_to_deg": det["scent_to_deg"],
                "thermal_phase": det["thermal_phase"],
            }
            if best is None or sc["total"] > best["score"]["total"]:
                best = {"hour": hour, "score": sc}
        return best

    # score each stand per period
    rows = []
    period_best = {p: None for p in periods}  # (stand_id, total)
    for st in stands:
        bonus = proximity_bonus(st, zones, corridors_l, settings)
        sightings = sightings_by_stand.get(st["id"], [])
        per = {}
        for p, (lo, hi) in periods.items():
            b = score_period(st, lo, hi, bonus, p, sightings)
            per[p] = b
            if b and (period_best[p] is None or b["score"]["total"] > period_best[p][1]):
                period_best[p] = (st["id"], b["score"]["total"])
        # the stand's headline score = its best period
        best_overall = max(
            [(p, per[p]["score"]["total"]) for p in periods if per[p]],
            key=lambda x: x[1], default=(None, 0),
        )
        rows.append({"stand": st, "periods": per, "best_period": best_overall[0], "best_score": best_overall[1],
                     "proximity": {k: round(v, 3) for k, v in bonus.items()}})

    wins = {p: (period_best[p][0] if period_best[p] else None) for p in periods}
    for row in rows:
        row["wins"] = [p for p in periods if wins[p] == row["stand"]["id"]]

    rows.sort(key=lambda r: r["best_score"], reverse=True)
    day_label = datetime.fromisoformat(day + "T12:00").strftime("%a %b %-d")
    return {"day": day, "day_label": day_label, "winners": wins, "ranked": rows}


class SettingsIn(BaseModel):
    weight_corridor: float | None = None
    falloff_corridor: float | None = None
    weight_food: float | None = None
    falloff_food: float | None = None
    weight_bedding: float | None = None
    falloff_bedding: float | None = None
    rate_w_pressure: float | None = None
    rate_w_wind: float | None = None
    rate_w_rain: float | None = None
    rate_w_temp: float | None = None
    max_camera_boost_pct: float | None = None
    camera_sync_interval_minutes: float | None = None
    image_retention_days: float | None = None
    rut_peak_month: float | None = None
    rut_peak_day: float | None = None
    camera_image_dir: str | None = None


@app.get("/api/settings")
def read_settings(_=Depends(require_token)):
    return get_settings()


@app.put("/api/settings")
def write_settings(body: SettingsIn, _=Depends(require_token)):
    with Session(engine) as s:
        for k, v in body.model_dump().items():
            if v is None:
                continue
            str_val = str(v)
            row = s.get(Setting, k)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=k, value=str_val))
        s.commit()
    result = get_settings()
    # Live-reschedule the sync job if the interval changed — no restart required.
    if body.camera_sync_interval_minutes is not None:
        _reschedule_sync(int(body.camera_sync_interval_minutes) or 30)
    return result


class HomeIn(BaseModel):
    lat: float
    lon: float


@app.get("/api/home")
def read_home(_=Depends(require_token)):
    with Session(engine) as s:
        rows = {}
        for r in s.scalars(select(Setting)).all():
            if r.key in HOME_KEYS:
                try:
                    rows[r.key] = float(r.value)
                except (ValueError, TypeError):
                    pass
    if "home_lat" in rows and "home_lon" in rows:
        return {"lat": rows["home_lat"], "lon": rows["home_lon"], "set": True}
    return {"lat": None, "lon": None, "set": False}


@app.put("/api/home")
def write_home(body: HomeIn, _=Depends(require_token)):
    if not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180):
        raise HTTPException(400, "lat/lon out of range")
    with Session(engine) as s:
        for k, v in (("home_lat", body.lat), ("home_lon", body.lon)):
            str_val = str(v)
            row = s.get(Setting, k)
            if row:
                row.value = str_val
            else:
                s.add(Setting(key=k, value=str_val))
        s.commit()
    return {"lat": body.lat, "lon": body.lon, "set": True}


# ---------- deer movement day ratings ----------
@app.get("/api/deer-ratings")
async def deer_ratings(_=Depends(require_token)):
    """1-5 deer movement rating per forecast day, optimized for daytime movement."""
    from datetime import date as _date
    with Session(engine) as s:
        first = s.scalars(select(Stand).order_by(Stand.name)).first()
        if not first:
            raise HTTPException(400, "add a stand first")
        lat, lon = first.lat, first.lon
    fc = await get_forecast(lat, lon, days=14)
    h = fc["hourly"]
    sun = fc["daily"]
    times = h["time"]
    _set = get_settings()
    rate_weights = {
        "pressure": _set.get("rate_w_pressure"), "wind": _set.get("rate_w_wind"),
        "rain": _set.get("rate_w_rain"), "temp": _set.get("rate_w_temp"),
    }
    # surface pressure may be absent depending on the forecast params; fetch defensively
    pressures = h.get("surface_pressure") or h.get("pressure_msl") or [None] * len(times)

    # sunrise/sunset per day for daytime windows
    sun_by_day = {}
    for i in range(len(sun["sunrise"])):
        sr = datetime.fromisoformat(sun["sunrise"][i]); ss = datetime.fromisoformat(sun["sunset"][i])
        sun_by_day[sun["sunrise"][i][:10]] = (sr.hour + sr.minute / 60, ss.hour + ss.minute / 60)

    # group hourly indices by day
    by_day = {}
    for i, t in enumerate(times):
        by_day.setdefault(t[:10], []).append(i)

    # trailing baseline high (mean of available daily highs) for temp-shift
    daily_highs = []
    day_keys = sorted(by_day.keys())
    for dk in day_keys:
        highs = [h["temperature_2m"][i] for i in by_day[dk]]
        daily_highs.append(max(highs) if highs else None)
    valid_highs = [x for x in daily_highs if x is not None]
    baseline_c = sum(valid_highs) / len(valid_highs) if valid_highs else None
    baseline_f = (baseline_c * 9 / 5 + 32) if baseline_c is not None else None

    out = []
    for di, dk in enumerate(day_keys):
        idxs = by_day[dk]
        sr_h, ss_h = sun_by_day.get(dk, (6.5, 19.0))
        # daytime indices (sunrise..sunset)
        day_idxs = [i for i in idxs if sr_h <= datetime.fromisoformat(times[i]).hour <= ss_h]
        if not day_idxs:
            day_idxs = idxs

        def davg(arr):
            vals = [arr[i] for i in day_idxs if arr[i] is not None]
            return sum(vals) / len(vals) if vals else None

        wind_mph = davg(h["wind_speed_10m"])
        rain_mm = sum((h["precipitation"][i] if h.get("precipitation") else 0) or 0 for i in day_idxs) if h.get("precipitation") else \
                  sum((h["rain"][i] if h.get("rain") else 0) or 0 for i in day_idxs)
        high_c = max((h["temperature_2m"][i] for i in day_idxs), default=None)
        high_f = (high_c * 9 / 5 + 32) if high_c is not None else None

        # pressure: daytime mean (hPa→inHg) and trend across the daytime window
        p_vals = [pressures[i] for i in day_idxs if pressures[i] is not None]
        p_inhg = (sum(p_vals) / len(p_vals) * deer_rating.HPA_TO_INHG) if p_vals else None
        p_trend = None
        if len(p_vals) >= 2:
            # inHg change per 3h, normalized over the window
            span = max(1, len(p_vals) - 1)
            p_trend = (p_vals[-1] - p_vals[0]) * deer_rating.HPA_TO_INHG / span * 3

        wx = {
            "pressure_inhg": round(p_inhg, 2) if p_inhg else None,
            "pressure_trend_inhg": round(p_trend, 3) if p_trend is not None else None,
            "wind_mph": round(wind_mph, 1) if wind_mph is not None else None,
            "rain_mm": round(rain_mm, 1),
            "day_high_f": round(high_f) if high_f is not None else None,
            "baseline_f": round(baseline_f) if baseline_f is not None else None,
        }
        y, m, d = (int(x) for x in dk.split("-"))
        rating = deer_rating.rate_day(_date(y, m, d), wx, rate_weights,
                                      rut_peak_month=int(_set.get("rut_peak_month", 12)),
                                      rut_peak_day=int(_set.get("rut_peak_day", 5)))
        label = datetime.fromisoformat(dk + "T12:00").strftime("%a %b %-d")
        rating["day"] = dk
        rating["label"] = label
        # weather forecast is reliable ~7 days; flag beyond that
        days_out = (_date(y, m, d) - _date.today()).days
        rating["confidence"] = "high" if days_out <= 7 else "low"
        rating["days_out"] = days_out
        out.append(rating)

    return {"ratings": out}


# ---------- v2.15: trail cameras ----------
class CameraIn(BaseModel):
    name: str
    brand: str
    stand_id: int | None = None
    credentials: dict | None = None  # plaintext in; stored encrypted


class CameraUpdateIn(BaseModel):
    name: str | None = None
    stand_id: int | None = None
    is_active: bool | None = None
    credentials: dict | None = None


@app.get("/api/camera-providers")
def camera_providers(_=Depends(require_token)):
    """Brand metadata for the setup wizard (which are implemented + required fields)."""
    return {"providers": cameras_mod.provider_meta()}


@app.get("/api/cameras")
def list_cameras(_=Depends(require_token)):
    with Session(engine) as s:
        return [c.to_dict() for c in s.scalars(select(Camera)).all()]


@app.post("/api/cameras")
def create_camera(body: CameraIn, _=Depends(require_token)):
    if body.brand not in CAMERA_BRANDS:
        raise HTTPException(400, "unknown brand")
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        cam = Camera(
            name=body.name, brand=body.brand, stand_id=body.stand_id, is_active=1,
            created_at=now,
            credentials_json=encrypt_credentials(body.credentials) if body.credentials else None,
        )
        s.add(cam); s.commit(); s.refresh(cam)
        return cam.to_dict()


@app.put("/api/cameras/{camera_id}")
def update_camera(camera_id: int, body: CameraUpdateIn, _=Depends(require_token)):
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam:
            raise HTTPException(404, "not found")
        if body.name is not None:
            cam.name = body.name
        # Use model_fields_set to distinguish "field not sent" from "field sent as null".
        # {"stand_id": null}  → unassign camera from its stand (cam.stand_id = None)
        # {"stand_id": 3}     → assign to stand 3
        # {}                  → don't touch stand_id at all
        if "stand_id" in body.model_fields_set:
            cam.stand_id = body.stand_id
        if body.is_active is not None:
            cam.is_active = 1 if body.is_active else 0
        if body.credentials is not None:
            cam.credentials_json = encrypt_credentials(body.credentials)
        s.commit(); s.refresh(cam)
        return cam.to_dict()


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: int, _=Depends(require_token)):
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if cam:
            s.delete(cam); s.commit()
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/verify")
async def verify_camera(camera_id: int, _=Depends(require_token)):
    """Test stored credentials against the provider."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam:
            raise HTTPException(404, "not found")
        creds = decrypt_credentials(cam.credentials_json)
        brand = cam.brand
    try:
        prov = cameras_mod.get_provider(brand, creds)
        ok = await prov.verify()
        return {"ok": bool(ok), "implemented": prov.implemented}
    except cameras_mod.NotImplementedProvider as e:
        raise HTTPException(501, str(e))
    except cameras_mod.CameraError as e:
        raise HTTPException(400, str(e))


@app.post("/api/cameras/{camera_id}/sync")
async def sync_camera_now(camera_id: int, _=Depends(require_token)):
    """Manually trigger a sync for one camera."""
    n = await _sync_one_camera(camera_id)
    return {"ok": True, "new_sightings": n}


@app.get("/api/cameras/{camera_id}/sightings")
def camera_sightings(
    camera_id: int,
    limit: int = 100,       # max rows returned; capped at 1000
    since: Optional[str] = None,  # ISO timestamp — return only sightings newer than this
    _=Depends(require_token),
):
    """Paginated sighting list. Default: 100 most-recent. Use `since` for incremental
    loads (pass the last timestamp you received to get only newer records)."""
    with Session(engine) as s:
        q = select(CameraSighting).where(CameraSighting.camera_id == camera_id)
        if since:
            q = q.where(CameraSighting.timestamp > since)
        q = q.order_by(CameraSighting.timestamp.desc()).limit(max(1, min(limit, 1000)))
        return [r.to_dict() for r in s.scalars(q).all()]


async def _sync_one_camera(camera_id: int) -> int:
    """Fetch recent photos for a camera, run detection, record positive sightings.
    Returns count of new sightings. Best-effort: never raises to the scheduler."""
    with Session(engine) as s:
        cam = s.get(Camera, camera_id)
        if not cam or not cam.is_active:
            return 0
        creds = decrypt_credentials(cam.credentials_json)
        brand, camera_name, stand_id, cid = cam.brand, cam.name, cam.stand_id, cam.id
        last_sync = cam.last_sync_at
        # Deduplication: timestamps already recorded for this camera (incl. non-animal skips)
        existing_timestamps = set(
            s.scalars(
                select(CameraSighting.timestamp).where(CameraSighting.camera_id == cid)
            ).all()
        )

    # Parse last_sync_at into a timezone-aware datetime to send as `since` to the provider.
    # This means we only fetch new photos since the last successful sync rather than re-fetching
    # everything each time — important once a camera has accumulated thousands of photos.
    since_dt = None
    if last_sync:
        try:
            since_dt = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass  # malformed timestamp; fall back to full fetch

    try:
        prov = cameras_mod.get_provider(brand, creds)
        if not prov.implemented:
            return 0
        photos = await prov.fetch_recent_photos(since=since_dt)
    except Exception:
        return 0

    # User-defined directory structure: [User defined directory]/[Camera Brand]/[Camera Name]/
    cam_dir = get_camera_dir(brand, camera_name)
    os.makedirs(cam_dir, exist_ok=True)
    new = 0
    async with httpx.AsyncClient() as client:
        for p in photos:
            url = p.get("url")
            if not url:
                continue
            taken_at = p.get("taken_at")

            # Require a timestamp for reliable deduplication and period matching.
            # Photos without taken_at can't be deduplicated (they'd re-process every sync)
            # and can't be matched to a hunt period for camera boost. Skip and warn.
            if not taken_at:
                log.warning("cam %s (%s): photo from %s has no taken_at timestamp — skipped",
                            cid, brand, url[:80])
                continue

            if taken_at in existing_timestamps:
                continue  # already processed (animal or non-animal) — skip

            # download
            try:
                r = await client.get(url, timeout=60)
                if r.status_code != 200:
                    continue
            except Exception:
                continue
            fname = f"cam{cid}_{int(datetime.now(timezone.utc).timestamp()*1000)}_{new}.jpg"
            fpath = os.path.join(cam_dir, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(r.content)
            except Exception:
                continue

            # offload CPU-bound ML detection to a worker thread so event loop remains non-blocking
            det = await asyncio.to_thread(detection_mod.detect_animal, fpath)
            if not det.get("is_animal"):
                # not wildlife — discard the file, don't record a sighting row; but DO mark
                # the timestamp as seen so this photo is never re-downloaded on the next sync.
                try:
                    os.remove(fpath)
                except OSError:
                    pass
                existing_timestamps.add(taken_at)
                continue

            with Session(engine) as s:
                s.add(CameraSighting(
                    stand_id=stand_id, camera_id=cid,
                    timestamp=taken_at,
                    confidence_score=det.get("confidence", 0.0),
                    image_path=fpath, created_at=datetime.now(timezone.utc).isoformat(),
                ))
                s.commit()
            existing_timestamps.add(taken_at)
            new += 1

    # Record sync completion timestamp on camera
    with Session(engine) as s:
        cam2 = s.get(Camera, cid)
        if cam2:
            cam2.last_sync_at = datetime.now(timezone.utc).isoformat()
            s.commit()

    return new


async def sync_cameras_job():
    """Scheduler job: sync every active camera."""
    with Session(engine) as s:
        ids = [c.id for c in s.scalars(select(Camera).where(Camera.is_active == 1)).all()]
    for cid in ids:
        try:
            await _sync_one_camera(cid)
        except Exception:
            continue


def auto_cleanup_job():
    """Scheduler job (daily 3 AM): delete JPEGs older than retention; keep sighting rows."""
    import datetime as _dt
    settings = get_settings()
    retention = int(settings.get("image_retention_days", 60))
    cutoff = datetime.now(timezone.utc) - _dt.timedelta(days=retention)
    with Session(engine) as s:
        rows = s.scalars(select(CameraSighting).where(CameraSighting.image_path.isnot(None))).all()
        for row in rows:
            raw_ts = row.created_at or row.timestamp
            if not raw_ts:
                continue
            try:
                created = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if created < cutoff:
                if row.image_path and os.path.exists(row.image_path):
                    try:
                        os.remove(row.image_path)
                    except OSError:
                        pass
                row.image_path = None  # keep the sighting for model auto-tuning
        s.commit()


_scheduler = None


def _reschedule_sync(interval_minutes: int) -> None:
    """Live-update the camera sync job interval without restarting the process.
    Called from write_settings so changes take effect immediately."""
    if _scheduler is None:
        return
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        _scheduler.reschedule_job(
            "sync_cameras",
            trigger=IntervalTrigger(minutes=max(1, interval_minutes)),
        )
    except Exception:
        pass  # best-effort; scheduler may not be running yet


def start_scheduler():
    """Start APScheduler with the sync + cleanup jobs. Lazy import so app boots even
    if apscheduler isn't installed (jobs simply won't run)."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
    except Exception:
        return
    settings = get_settings()
    interval = int(settings.get("camera_sync_interval_minutes", 30)) or 30
    sched = AsyncIOScheduler()
    sched.add_job(sync_cameras_job, IntervalTrigger(minutes=interval), id="sync_cameras",
                  replace_existing=True, max_instances=1)
    sched.add_job(auto_cleanup_job, CronTrigger(hour=3, minute=0), id="auto_cleanup",
                  replace_existing=True, max_instances=1)
    sched.start()
    _scheduler = sched


# ---------- static frontend ----------
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = os.path.join(STATIC_DIR, path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
