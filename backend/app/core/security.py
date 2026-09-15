"""Password hashing (n/a), credential encryption, and bearer-token verification."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import APP_TOKEN


def require_token(authorization: str = Header(default="")):
    # If APP_TOKEN is empty or "unused", authentication is handled by an upstream
    # reverse proxy (e.g. Authentik forward auth) or disabled.
    # Otherwise, require the Bearer token.
    if not APP_TOKEN or APP_TOKEN == "unused":
        return
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else authorization.strip()
    if not token or not secrets.compare_digest(token, APP_TOKEN):
        raise HTTPException(401, "unauthorized")


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

