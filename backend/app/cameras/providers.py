"""
Trail-camera providers for AmbushIQ (v2.15).

Pluggable architecture: each brand implements CameraProvider. SpyPoint and Reveal
are real implementations against their app-backed cloud APIs (restapi.spypoint.com
and api.reveal.ishareit.net, respectively). The other four brands are STUBS with
the full structure in place but no working endpoints yet — none of these vendors
publishes a documented public API, so a real implementation requires capturing the
mobile app's traffic (e.g. with mitmproxy) and filling in the endpoints/auth/
response mapping in the marked TODO sections.

Providers return a list of "photo" dicts:
    {"url": str, "taken_at": iso8601 str, "camera_ref": str, "id": str | None}
("id" is the provider's own photo id when it has one; the sync engine falls back to the URL.)
The caller downloads images, runs detection, and records sightings.

NOTHING here is verified against a live account in the build sandbox (no network).
Treat SpyPoint and Reveal as best-effort-real and expect to adjust once run against
a real login.
"""
from __future__ import annotations
from typing import Optional
import datetime as _dt
import logging

import httpx

log = logging.getLogger(__name__)


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

    async def fetch_cameras(self) -> list[dict]:
        """Return [{id, name, last_seen_at, photo_count, photo_limit}] for all
        cameras on this account. The health fields (last_seen_at/photo_count/
        photo_limit) are best-effort — brands that don't expose them (or
        stubs) should just omit them; callers treat missing values as
        "unknown, assume healthy" rather than a failure."""
        raise NotImplementedProvider(f"{self.brand} camera listing not implemented yet")

    async def fetch_recent_photos(self, since: Optional[_dt.datetime] = None,
                                  camera_ref: Optional[str] = None) -> list[dict]:
        """Return recent photos as [{url, taken_at, camera_ref, id}, ...]. `camera_ref`, when given,
        limits the request to that one camera where the provider supports it (callers still filter)."""
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

    async def fetch_cameras(self) -> list[dict]:
        """Return [{id, name, last_seen_at, photo_count, photo_limit}] for every
        camera on this Spypoint account. last_seen_at comes from status.lastUpdate
        (when the camera itself last checked in — distinct from when WE last
        synced it); photo_count/photo_limit come from the account's current
        subscription entry for that camera (community-documented shape, not
        officially published — adjust if a real account's response differs)."""
        async with httpx.AsyncClient() as client:
            token = await self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get(f"{self.BASE}/api/v3/camera/all", headers=headers, timeout=30)
            resp.raise_for_status()
            out = []
            for c in resp.json():
                if not c.get("id"):
                    continue
                status = c.get("status") or {}
                subs = c.get("subscriptions") or []
                sub = subs[0] if subs else {}
                out.append({
                    "id": c["id"],
                    "name": c.get("config", {}).get("name") or c["id"],
                    "last_seen_at": status.get("lastUpdate"),
                    "photo_count": sub.get("photoCount"),
                    "photo_limit": sub.get("photoLimit"),
                })
            return out

    PHOTO_LIMIT = 500                              # the API returns at most this many photos per request
    MIN_WINDOW = _dt.timedelta(hours=1)            # smallest date window worth splitting further

    @staticmethod
    def _iso(dt: _dt.datetime) -> str:
        dt = dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async def _photos_between(self, client: httpx.AsyncClient, headers: dict, cam_ids: list,
                              begin: _dt.datetime, end: _dt.datetime) -> list[dict]:
        """All photos for `cam_ids` between two moments. One request returns at most PHOTO_LIMIT photos
        and does not say what it left out, so a full response is treated as possibly truncated and the
        window is halved and re-requested until each piece comes back under the limit."""
        payload = {"camera": cam_ids, "dateBegin": self._iso(begin), "dateEnd": self._iso(end),
                   "limit": self.PHOTO_LIMIT}
        log.info("SpyPoint: fetching photos with payload %s", payload)
        resp = await client.post(f"{self.BASE}/api/v3/photo/all", headers=headers, timeout=30, json=payload)
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if len(photos) < self.PHOTO_LIMIT:
            return photos
        if end - begin <= self.MIN_WINDOW:
            log.warning("SpyPoint: %d photos in a window of %s (%s to %s) — some may be missing",
                        len(photos), end - begin, payload["dateBegin"], payload["dateEnd"])
            return photos
        mid = begin + (end - begin) / 2
        log.info("SpyPoint: response hit the %d-photo limit — splitting the window", self.PHOTO_LIMIT)
        return (await self._photos_between(client, headers, cam_ids, begin, mid)
                + await self._photos_between(client, headers, cam_ids, mid, end))

    async def fetch_recent_photos(self, since: Optional[_dt.datetime] = None,
                                  camera_ref: Optional[str] = None) -> list[dict]:
        out: list[dict] = []
        async with httpx.AsyncClient() as client:
            log.info("SpyPoint: logging in as %s", self.credentials.get("username"))
            token = await self._login(client)
            log.info("SpyPoint: login OK — fetching camera list")
            headers = {"Authorization": f"Bearer {token}"}
            cams = await client.get(f"{self.BASE}/api/v3/camera/all", headers=headers, timeout=30)
            cams.raise_for_status()
            cam_list = cams.json()
            cam_ids = [c.get("id") for c in cam_list if c.get("id")]
            log.info("SpyPoint: found %d camera(s) on account: %s",
                     len(cam_ids), [c.get("config", {}).get("name", c.get("id")) for c in cam_list])
            if camera_ref:
                # Ask for just this camera: on a shared request a busy camera can use up the photo limit
                # and push a quiet camera's photos out of the response.
                cam_ids = [c for c in cam_ids if str(c) == str(camera_ref)]
            if not cam_ids:
                log.warning("SpyPoint: no matching camera on account — nothing to fetch")
                return out
            now = _dt.datetime.now(_dt.timezone.utc)
            begin = since if since else now - _dt.timedelta(days=7)
            raw_photos = await self._photos_between(client, headers, cam_ids, begin, now + _dt.timedelta(days=1))
            log.info("SpyPoint: API returned %d photo(s)", len(raw_photos))
            # Log the first raw photo in full so we can see the actual field structure
            if raw_photos:
                log.info("SpyPoint: first raw photo keys=%s full=%s",
                         list(raw_photos[0].keys()), raw_photos[0])
            seen: set = set()
            for p in raw_photos:
                # Spypoint returns large/medium/small as top-level keys, each a dict
                # with {host, path} pointing to a pre-signed S3 URL.
                # Prefer large, fall back to medium, then small.
                url = None
                for size in ("large", "medium", "small"):
                    img = p.get(size)
                    if isinstance(img, dict) and img.get("host") and img.get("path"):
                        url = f"https://{img['host']}/{img['path']}"
                        break
                taken = p.get("date") or p.get("originDate")
                photo_id = p.get("id") or p.get("_id")
                key = photo_id or url
                if key in seen:
                    continue            # a photo on the boundary of two split windows
                seen.add(key)
                log.info("SpyPoint: photo cam=%s taken=%s url=%s", p.get("camera"), taken, url[:80] if url else None)
                out.append({"url": url, "taken_at": taken, "camera_ref": str(p.get("camera")),
                            "id": str(photo_id) if photo_id else None})
        log.info("SpyPoint: returning %d photo(s) to sync engine", len(out))
        return out


# ─────────────────────────── Reveal (real) ───────────────────────────
class RevealProvider(CameraProvider):
    """
    Tactacam Reveal's web app (account.revealcellcam.com) authenticates via AWS
    Cognito (USER_PASSWORD_AUTH, called as a raw HTTPS request — not boto3) and
    then talks to https://api.reveal.ishareit.net/v1. Flow (from community-
    reverse-engineered behavior, specifically the open-source Home Assistant
    integration github.com/SethCalkins/HomeAssistant-Tactacam — Tactacam
    publishes no official API):
      POST https://cognito-idp.us-east-1.amazonaws.com/
           (X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth)
           {AuthFlow: USER_PASSWORD_AUTH, AuthParameters: {USERNAME, PASSWORD},
            ClientId: <Reveal's public Cognito app client id>}
        -> AuthenticationResult.AccessToken (bearer token, ~12h default expiry)
      GET  /cameras                              -> response.cameras[{cameraId, settings{...}}]
      GET  /photos?size=&page=&includeWeatherData=false -> response.photos[{photoUrl, photoDateUtc, cameraId}]
    photoUrl is a direct, unauthenticated pre-signed S3 link (no further auth
    needed to download it; expires in a matter of days, which is fine since we
    download immediately during sync).
    This is undocumented/unofficial and may violate Tactacam's ToS — same
    caveat as SpyPoint above. NOT verified against a live account in this build
    sandbox (no network); expect to adjust field names (camera name, the
    photo->camera id key, page ordering) once run against a real login.
    """
    brand = "reveal"
    credential_fields = ("username", "password")
    implemented = True
    COGNITO_URL = "https://cognito-idp.us-east-1.amazonaws.com/"
    COGNITO_CLIENT_ID = "6r9tpojvgvkci5trla0ip14mon"
    BASE = "https://api.reveal.ishareit.net/v1"

    async def _login(self, client: httpx.AsyncClient) -> str:
        r = await client.post(self.COGNITO_URL, headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }, json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "AuthParameters": {
                "USERNAME": self.credentials.get("username"),
                "PASSWORD": self.credentials.get("password"),
            },
            "ClientId": self.COGNITO_CLIENT_ID,
        }, timeout=30)
        if r.status_code != 200:
            raise CameraError(f"Reveal login failed ({r.status_code})")
        auth_result = (r.json() or {}).get("AuthenticationResult") or {}
        tok = auth_result.get("AccessToken")
        if not tok:
            raise CameraError("Reveal login returned no access token")
        return tok

    async def verify(self) -> bool:
        async with httpx.AsyncClient() as client:
            await self._login(client)
        return True

    async def fetch_cameras(self) -> list[dict]:
        """Return [{id, name, last_seen_at, photo_count, photo_limit}] for every
        camera on this Reveal account. Field names below (settings.name,
        lastReportDate, etc.) are best-effort from the reverse-engineered
        integration's response shape — adjust once run against a real account,
        same as every non-SpyPoint provider in this file."""
        async with httpx.AsyncClient() as client:
            token = await self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get(f"{self.BASE}/cameras", headers=headers, timeout=30)
            resp.raise_for_status()
            cams = ((resp.json() or {}).get("response") or {}).get("cameras", [])
            log.info("Reveal: found %d camera(s) on account", len(cams))
            out = []
            for c in cams:
                cam_id = c.get("cameraId")
                if not cam_id:
                    continue
                settings = c.get("settings") or {}
                out.append({
                    "id": cam_id,
                    "name": settings.get("name") or c.get("name") or cam_id,
                    "last_seen_at": c.get("lastReportDate") or c.get("lastSeenAt"),
                    "photo_count": c.get("photoCount"),
                    "photo_limit": c.get("photoLimit"),
                })
            return out

    async def fetch_recent_photos(self, since: Optional[_dt.datetime] = None,
                                  camera_ref: Optional[str] = None) -> list[dict]:
        """Return recent photos as [{url, taken_at, camera_ref, id}, ...].
        (`camera_ref` is accepted for interface parity; this endpoint lists the whole account.)
        Reveal's /photos endpoint (unlike SpyPoint's single date-ranged request)
        only offers size/page paging with no documented date filter, so this
        walks pages newest-first (the reverse-engineered integration's own
        assumption — unverified) and stops once a page is entirely older than
        `since`, or comes back empty."""
        out: list[dict] = []
        since_utc = since if (since is None or since.tzinfo) else since.replace(tzinfo=_dt.timezone.utc)
        async with httpx.AsyncClient() as client:
            log.info("Reveal: logging in as %s", self.credentials.get("username"))
            token = await self._login(client)
            log.info("Reveal: login OK — fetching photos")
            headers = {"Authorization": f"Bearer {token}"}
            page = 1
            while True:
                resp = await client.get(f"{self.BASE}/photos", headers=headers, timeout=30,
                                         params={"size": 100, "page": page, "includeWeatherData": "false"})
                resp.raise_for_status()
                photos = ((resp.json() or {}).get("response") or {}).get("photos", [])
                log.info("Reveal: page %d returned %d photo(s)", page, len(photos))
                if not photos:
                    break
                if page == 1 and photos:
                    log.info("Reveal: first raw photo keys=%s full=%s", list(photos[0].keys()), photos[0])
                page_has_newer = False
                for p in photos:
                    taken = p.get("photoDateUtc")
                    if since_utc and taken:
                        try:
                            taken_dt = _dt.datetime.fromisoformat(taken.replace("Z", "+00:00"))
                        except ValueError:
                            taken_dt = None
                        if taken_dt and taken_dt <= since_utc:
                            continue
                    page_has_newer = True
                    url = p.get("photoUrl")
                    if not url:
                        continue
                    photo_id = p.get("photoId") or p.get("id")
                    out.append({"url": url, "taken_at": taken, "camera_ref": str(p.get("cameraId")),
                                "id": str(photo_id) if photo_id else None})
                if len(photos) < 100 or not page_has_newer:
                    break
                page += 1
        log.info("Reveal: returning %d photo(s) to sync engine", len(out))
        return out


# ─────────────────────────── Stubs (structure only) ───────────────────────────
# To implement: capture the brand app's API traffic, then fill _login/fetch below,
# set implemented=True, and adjust credential_fields to what the brand needs.

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
