"""Street name for a road USGS leaves unnamed, from Esri's World Geocoder (reverse geocode).

USGS's transportation layer (app.roads.service) often has no name for small rural roads that basemaps still label
(e.g. "Scott Ln" in Pope County, IL is an unnamed 2019 Census line there, and also unnamed in current Census TIGER and
OpenStreetMap). Esri's address data knows many of them. The map asks only when a road without a USGS name is hovered or
clicked, for a point on that road. Nothing is stored: Esri's free geocoding doesn't allow saving results."""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)

REVERSE_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
REQUEST_TIMEOUT_S = 8
# Esri snaps to the nearest address range; farther than this from the asked point (which the map snaps onto our
# road line) it's probably a different road, so its name isn't borrowed.
MAX_MATCH_M = 25.0
_HOUSE_NUMBER = re.compile(r"^\s*\d+[A-Za-z]?(-\d+[A-Za-z]?)?\s+")


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * k, lat2 - lat1) * 111_320.0


def street_from_address(address: dict) -> Optional[str]:
    """'197 Scott Ln' -> 'Scott Ln'; None when Esri gave no street (e.g. it matched a city or postal area)."""
    street = _HOUSE_NUMBER.sub("", (address.get("Address") or "").strip()).strip()
    return street or None


async def lookup(lat: float, lon: float) -> Optional[str]:
    """The street name at (lat, lon), or None if Esri has none close enough or can't be reached."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(REVERSE_URL, params={
                "location": f"{lon},{lat}", "featureTypes": "StreetAddress,StreetName", "f": "json",
            }, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        log.info("street name lookup failed at %.5f,%.5f", lat, lon, exc_info=True)
        return None
    address, loc = data.get("address") or {}, data.get("location") or {}
    if "error" in data or not address or loc.get("x") is None or loc.get("y") is None:
        return None
    if _distance_m(lat, lon, loc["y"], loc["x"]) > MAX_MATCH_M:
        return None
    return street_from_address(address)
