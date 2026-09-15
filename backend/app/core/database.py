"""DB engine setup and session creation."""
from __future__ import annotations

import time

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
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)
