"""Hour-of-day activity and gallery filtering for camera sightings.

Sighting timestamps are stored as UTC ISO strings. "Hour of day" only means something in the
property's own timezone (and DST-aware), so everything here converts to the region's local time
before bucketing or filtering."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

UNCLASSIFIED = "__none__"          # query-string stand-in for "no species recorded"
BURST_GAP = timedelta(minutes=5)   # photos of one species on one camera closer than this are one sighting


def region_tz(name: Optional[str]) -> tzinfo:
    try:
        return ZoneInfo(name or "America/Chicago")
    except Exception:
        return timezone.utc


def parse_utc(raw: Optional[str]) -> Optional[datetime]:
    """A stored sighting timestamp as an aware UTC datetime (None if unparsable)."""
    if not raw:
        return None
    s = str(raw).strip()
    try:
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.upper().endswith("Z") else s)
    except ValueError:
        return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def species_matches(species: Optional[str], wanted: Iterable[str]) -> bool:
    """Empty `wanted` matches everything; UNCLASSIFIED matches sightings with no species."""
    wanted = list(wanted)
    if not wanted:
        return True
    if not species:
        return UNCLASSIFIED in wanted
    return species in wanted


def collapse_bursts(rows: list[tuple[int, Optional[str], datetime]]) -> list[tuple[int, Optional[str], datetime]]:
    """Merge photos of the same camera + species whose consecutive gaps are within BURST_GAP into one
    sighting, dated at its first photo. A deer that lingers and triggers the camera every minute is
    one visit, not thirty."""
    groups: dict[tuple[int, Optional[str]], list[datetime]] = defaultdict(list)
    for cam, sp, t in rows:
        groups[(cam, sp)].append(t)
    out = []
    for (cam, sp), times in groups.items():
        times.sort()
        prev = None
        for t in times:
            if prev is None or t - prev > BURST_GAP:
                out.append((cam, sp, t))
            prev = t
    return out


def hourly_activity(rows: list[tuple[int, Optional[str], datetime]], *, tz: tzinfo, today: date,
                    date_from: Optional[date], date_to: Optional[date],
                    recorded_since: Optional[date]) -> dict:
    """Average sightings per day for each local hour of day.

    `rows` are the sightings that match the camera + species filters. `recorded_since` is the local
    date of the earliest photo from the selected cameras (whatever the species): the camera can't have
    been watching before that, so a 30-day range on a camera installed two days ago averages over two
    days. The average divides by every calendar day in the (clamped) range, quiet days included."""
    empty = {"hours": [{"hour": h, "avg": 0.0, "total": 0} for h in range(24)],
             "days": 0, "total": 0, "date_from": None, "date_to": None}
    if recorded_since is None:
        return empty
    start = max(date_from, recorded_since) if date_from else recorded_since
    end = min(date_to, today) if date_to else today
    if end < start:
        return empty
    days = (end - start).days + 1

    local = [(cam, sp, t.astimezone(tz)) for cam, sp, t in rows]
    in_range = [(cam, sp, lt) for cam, sp, lt in local if start <= lt.date() <= end]
    counts = [0] * 24
    for _cam, _sp, lt in collapse_bursts(in_range):
        counts[lt.hour] += 1
    return {"hours": [{"hour": h, "avg": round(counts[h] / days, 3), "total": counts[h]} for h in range(24)],
            "days": days, "total": sum(counts), "date_from": start.isoformat(), "date_to": end.isoformat()}


def in_hour_window(local_dt: datetime, hour_from: Optional[int], hour_to: Optional[int]) -> bool:
    """Is a local time inside [hour_from, hour_to)? A window that ends before it starts wraps
    midnight (20 -> 5 is 8 pm to 5 am). hour_to may be 24 (end of day); either end None = no limit."""
    if hour_from is None or hour_to is None:
        return True
    minute = local_dt.hour * 60 + local_dt.minute
    lo, hi = hour_from * 60, hour_to * 60
    if lo == hi:
        return True
    return lo <= minute < hi if lo < hi else (minute >= lo or minute < hi)


def utc_date_bounds(date_from: Optional[date], date_to: Optional[date]) -> tuple[Optional[str], Optional[str]]:
    """Generous UTC date-string bounds for SQL prefiltering (a local day can touch two UTC dates,
    so pad by one day; the exact local-date test is done in Python afterwards)."""
    lo = (date_from - timedelta(days=1)).isoformat() if date_from else None
    hi = (date_to + timedelta(days=2)).isoformat() if date_to else None
    return lo, hi
