"""
Trail-camera providers for AmbushIQ (v2.15).

Pluggable architecture: each brand implements CameraProvider. SpyPoint is a real,
complete implementation against the app-backed cloud API (restapi.spypoint.com).
The other five brands are STUBS with the full structure in place but no working
endpoints yet — none of these vendors publishes a documented public API, so a real
implementation requires capturing the mobile app's traffic (e.g. with mitmproxy)
and filling in the endpoints/auth/response mapping in the marked TODO sections.

Providers return a list of "photo" dicts:
    {"url": str, "taken_at": iso8601 str, "camera_ref": str}
The caller downloads images, runs detection, and records sightings.

NOTHING here is verified against a live account in the build sandbox (no network).
Treat SpyPoint as best-effort-real and expect to adjust once run against a real login.
"""
from __future__ import annotations
from typing import Optional
import datetime as _dt

import httpx


class CameraError(Exception):
    pass


class NotImplementedProvider(CameraError):
    """Raised by stub providers that aren't wired to real endpoints yet."""


class CameraProvider:
    brand: str = "base"
    # human-facing: what the setup wizard should collect for this brand
    credential_fields = ("username", "password")
    implemented = False

    def __init__(self, credentials: dict):
        self.credentials = credentials or {}

    async def verify(self) -> bool:
        """Confirm credentials work. Raises CameraError on failure."""
        raise NotImplementedProvider(f"{self.brand} verification not implemented yet")

    async def fetch_recent_photos(self, since: Optional[_dt.datetime] = None) -> list[dict]:
        """Return recent photos as [{url, taken_at, camera_ref}, ...]."""
        raise NotImplementedProvider(f"{self.brand} photo fetch not implemented yet")


# ─────────────────────────── SpyPoint (real) ───────────────────────────
class SpyPointProvider(CameraProvider):
    """
    SpyPoint's mobile app talks to https://restapi.spypoint.com.
    Flow (from community-documented behavior):
      POST /api/v3/user/login {username,password} -> {token}
      GET  /api/v3/camera/all  (Bearer token)     -> [{id, config{name}...}]
      POST /api/v3/photo/all   {camera:[ids], ...} -> {photos:[{date, urls{...}}]}
    Photo URLs are usually pre-signed storage links assembled from a host + path.
    This is undocumented/unofficial and may break or violate ToS. Adjust as needed
    once run against a real account.
    """
    brand = "spypoint"
    credential_fields = ("username", "password")
    implemented = True
    BASE = "https://restapi.spypoint.com"

    async def _login(self, client: httpx.AsyncClient) -> str:
        r = await client.post(f"{self.BASE}/api/v3/user/login", json={
            "username": self.credentials.get("username"),
            "password": self.credentials.get("password"),
        }, timeout=30)
        if r.status_code != 200:
            raise CameraError(f"SpyPoint login failed ({r.status_code})")
        tok = r.json().get("token")
        if not tok:
            raise CameraError("SpyPoint login returned no token")
        return tok

    async def verify(self) -> bool:
        async with httpx.AsyncClient() as client:
            await self._login(client)
        return True

    async def fetch_recent_photos(self, since: Optional[_dt.datetime] = None) -> list[dict]:
        out: list[dict] = []
        async with httpx.AsyncClient() as client:
            token = await self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            cams = await client.get(f"{self.BASE}/api/v3/camera/all", headers=headers, timeout=30)
            cams.raise_for_status()
            cam_ids = [c.get("id") for c in cams.json() if c.get("id")]
            if not cam_ids:
                return out
            payload: dict = {
                "camera": cam_ids,
                "dateEnd": "2100-01-01T00:00:00.000Z",
                "limit": 100,
            }
            if since:
                # Ensure UTC-aware; format as the SpyPoint API expects.
                since_utc = since if since.tzinfo else since.replace(tzinfo=_dt.timezone.utc)
                payload["dateBegin"] = since_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            resp = await client.post(f"{self.BASE}/api/v3/photo/all", headers=headers,
                                     timeout=30, json=payload)
            resp.raise_for_status()
            for p in resp.json().get("photos", []):
                urls = p.get("urls") or {}
                host = p.get("hd", {}).get("host") or urls.get("host")
                path = p.get("hd", {}).get("path") or urls.get("path")
                url = None
                if host and path:
                    url = f"https://{host}/{path}"
                elif isinstance(urls.get("large"), str):
                    url = urls["large"]
                taken = p.get("date") or p.get("originDate")
                out.append({"url": url, "taken_at": taken, "camera_ref": str(p.get("camera"))})
        return out


# ─────────────────────────── Stubs (structure only) ───────────────────────────
# To implement: capture the brand app's API traffic, then fill _login/fetch below,
# set implemented=True, and adjust credential_fields to what the brand needs.

class RevealProvider(CameraProvider):
    brand = "reveal"; credential_fields = ("username", "password"); implemented = False
    # TODO: Tactacam Reveal cloud endpoints + auth + photo listing.


class MoultrieProvider(CameraProvider):
    brand = "moultrie"; credential_fields = ("username", "password"); implemented = False
    # TODO: Moultrie Mobile endpoints + auth + photo listing.


class StealthCamProvider(CameraProvider):
    brand = "stealth_cam"; credential_fields = ("username", "password"); implemented = False
    # TODO: Stealth Cam Command / Tactacam endpoints.


class BrowningProvider(CameraProvider):
    brand = "browning"; credential_fields = ("username", "password"); implemented = False
    # TODO: Browning Trail Cameras app endpoints.


class SpartanProvider(CameraProvider):
    brand = "spartan"; credential_fields = ("username", "password"); implemented = False
    # TODO: Spartan Camera cloud endpoints.


_PROVIDERS = {
    "spypoint": SpyPointProvider,
    "reveal": RevealProvider,
    "moultrie": MoultrieProvider,
    "stealth_cam": StealthCamProvider,
    "browning": BrowningProvider,
    "spartan": SpartanProvider,
}


def get_provider(brand: str, credentials: dict) -> CameraProvider:
    cls = _PROVIDERS.get(brand)
    if not cls:
        raise CameraError(f"unknown camera brand: {brand}")
    return cls(credentials)


def provider_meta() -> list[dict]:
    """For the frontend wizard: brands + whether each is implemented + fields."""
    out = []
    for brand, cls in _PROVIDERS.items():
        out.append({"brand": brand, "implemented": cls.implemented,
                    "credential_fields": list(cls.credential_fields)})
    return out
