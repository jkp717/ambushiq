"""DB engine setup and session creation."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase

from app.core.config import DB_URL

engine = create_engine(DB_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


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
                # v2.16.3: per-stand / per-zone / per-corridor active flag
                try:
                    conn.execute(text("ALTER TABLE stands ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1"))
                    conn.execute(text("ALTER TABLE zones ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1"))
                    conn.execute(text("ALTER TABLE corridors ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1"))
                    conn.commit()
                except Exception:
                    pass
                # v2.17.26: provider_ref + is_deleted on cameras
                try:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS provider_ref VARCHAR(64)"))
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS is_deleted INTEGER NOT NULL DEFAULT 0"))
                    conn.commit()
                except Exception:
                    pass
                # v2.20: corridor width + stand visibility for buffered edge-distance scoring
                try:
                    conn.execute(text("ALTER TABLE corridors ADD COLUMN IF NOT EXISTS width_m FLOAT"))
                    conn.execute(text("ALTER TABLE stands ADD COLUMN IF NOT EXISTS visibility_m FLOAT"))
                    conn.commit()
                except Exception:
                    pass
                # v2.21: species classification on camera sightings, so the camera
                # boost can be limited to actual deer instead of any animal
                try:
                    conn.execute(text("ALTER TABLE camera_sightings ADD COLUMN IF NOT EXISTS species VARCHAR(64)"))
                    conn.execute(text("ALTER TABLE camera_sightings ADD COLUMN IF NOT EXISTS species_confidence FLOAT"))
                    conn.commit()
                except Exception:
                    pass
                # v2.23: camera health (last check-in + photo quota), refreshed on
                # sync — lets the camera penalty skip stands whose camera can't
                # currently capture/transmit anything, instead of assuming "no deer"
                try:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS last_seen_at VARCHAR(32)"))
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS photo_count INTEGER"))
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS photo_limit INTEGER"))
                    conn.commit()
                except Exception:
                    pass
                # deer_sign table
                try:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS deer_sign (
                            id SERIAL PRIMARY KEY,
                            kind VARCHAR(20) NOT NULL,
                            name VARCHAR(120) NOT NULL DEFAULT '',
                            lat FLOAT NOT NULL DEFAULT 0,
                            lon FLOAT NOT NULL DEFAULT 0,
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at VARCHAR(32) NOT NULL DEFAULT ''
                        )
                    """))
                    conn.commit()
                except Exception:
                    pass

                # v3.0: multi-region support
                region_count = conn.execute(text("SELECT COUNT(*) FROM regions")).scalar()
                default_region_id = None
                if region_count == 0:
                    # Only auto-create a carried-over region if there's something to
                    # carry forward (an upgrade from a pre-region install) — a
                    # genuinely FRESH install must be left with zero regions so the
                    # frontend's normal "create your first region" onboarding screen
                    # runs, instead of handing a new user a bogus region at lat=0/lon=0.
                    legacy = {}
                    try:
                        for k, v in conn.execute(text(
                            "SELECT key, value FROM settings WHERE key IN "
                            "('home_lat','home_lon','rut_peak_month','rut_peak_day','property_timezone')"
                        )).all():
                            legacy[k] = v
                    except Exception:
                        pass  # settings table may not exist yet on a brand-new install
                    is_upgrade = "home_lat" in legacy and "home_lon" in legacy
                    if not is_upgrade:
                        for table in ("stands", "zones", "corridors", "deer_sign", "cameras"):
                            try:
                                if conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1")).first():
                                    is_upgrade = True
                                    break
                            except Exception:
                                pass
                    if is_upgrade:
                        default_region_id = conn.execute(text("""
                            INSERT INTO regions (name, lat, lon, rut_peak_month, rut_peak_day, property_timezone, is_default, created_at)
                            VALUES (:name, :lat, :lon, :rm, :rd, :tz, 1, :now) RETURNING id
                        """), {"name": "My Hunting Region",
                               "lat": float(legacy.get("home_lat") or 0.0), "lon": float(legacy.get("home_lon") or 0.0),
                               "rm": int(float(legacy.get("rut_peak_month") or 12)), "rd": int(float(legacy.get("rut_peak_day") or 5)),
                               "tz": legacy.get("property_timezone") or "America/Chicago",
                               "now": datetime.now(timezone.utc).isoformat()}).scalar()
                        conn.commit()
                else:
                    default_region_id = conn.execute(text("SELECT id FROM regions WHERE is_default = 1 ORDER BY id LIMIT 1")).scalar()
                    if default_region_id is None:
                        default_region_id = conn.execute(text("SELECT id FROM regions ORDER BY id LIMIT 1")).scalar()

                # region_id: add nullable, backfill (upgrade path only — a fresh
                # install has no rows to backfill, so this is a no-op there), THEN
                # enforce NOT NULL. Deliberately NOT wrapped in a swallowed
                # try/except like most other blocks in this function — a swallowed
                # failure would leave rows with NULL region_id, a state this
                # function must never produce silently (every ORM model below
                # declares region_id non-nullable).
                for table in ("stands", "zones", "corridors", "deer_sign", "cameras", "scouting_suggestions"):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS region_id INTEGER"))
                    conn.commit()
                    if default_region_id is not None:
                        conn.execute(text(f"UPDATE {table} SET region_id = :rid WHERE region_id IS NULL"), {"rid": default_region_id})
                        conn.commit()
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN region_id SET NOT NULL"))
                    conn.commit()

                # home_lat/home_lon/rut_peak_month/rut_peak_day/property_timezone
                # now live on `regions` instead of the global settings blob — safe
                # to swallow, worst case a few dead rows linger in `settings`.
                # No-op on a fresh install (nothing to delete).
                try:
                    conn.execute(text("DELETE FROM settings WHERE key IN "
                                       "('home_lat','home_lon','rut_peak_month','rut_peak_day','property_timezone')"))
                    conn.commit()
                except Exception:
                    pass
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)
