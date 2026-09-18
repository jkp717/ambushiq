import os

# app.core.database builds its engine at import time; tests never touch the DB, so
# point it at an in-memory SQLite instead of requiring a Postgres driver/server.
os.environ.setdefault("DATABASE_URL", "sqlite://")
