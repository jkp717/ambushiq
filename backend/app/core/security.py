"""Password hashing (n/a), credential encryption, and bearer-token verification."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query

from app.core.config import APP_TOKEN


def _check_token(token: str) -> None:
    # If APP_TOKEN is empty or "unused", authentication is handled by an upstream
    # reverse proxy (e.g. Authentik forward auth) or disabled.
    # Otherwise, require the Bearer token.
    if not APP_TOKEN or APP_TOKEN == "unused":
        return
    if not token or not secrets.compare_digest(token, APP_TOKEN):
        raise HTTPException(401, "unauthorized")


def require_token(authorization: str = Header(default="")):
    _check_token(authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else authorization.strip())


def require_token_or_query(authorization: str = Header(default=""), t: str = Query(default="")):
    """For URLs loaded by <img> tags, which can't send an Authorization header: the
    token may come as a `t` query parameter instead."""
    if authorization:
        require_token(authorization)
    else:
        _check_token(t.strip())


def safe_join(base: str, subpath: str) -> Optional[str]:
    """Join `subpath` onto `base`, or return None if the result would land outside
    `base` (via "..", an absolute path, or a symlink)."""
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, subpath))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:  # different drives on Windows
        return None
    return candidate


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


def encrypt_settings_key(plaintext: str) -> str:
    """Encrypt a single secret value (e.g. a weather-provider API key) for
    storage in the generic app-settings key/value table."""
    return encrypt_credentials({"key": plaintext})


def decrypt_settings_key(blob: Optional[str]) -> Optional[str]:
    return decrypt_credentials(blob).get("key") if blob else None

