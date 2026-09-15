/* ───────── timezone-aware "today" helper ───────── */
// Returns the current date as a YYYY-MM-DD string in the property's local
// timezone.  utcOffsetSeconds comes from Open-Meteo's forecast response for the
// property coordinates (utc_offset_seconds field).  Using it avoids the UTC
// calendar-date mismatch that occurs during the 5–8 hour UTC→local midnight gap
// common for US hunting properties.
function localDate(utcOffsetSeconds = 0) {
  return new Date(Date.now() + utcOffsetSeconds * 1000).toISOString().slice(0, 10);
}

/* ── corridor length helper ── */
function corridorLengthFt(points) {
  if (!points || points.length < 2) return 0;
  let total = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const [lat1, lon1] = points[i], [lat2, lon2] = points[i + 1];
    const R = 6371000, toR = Math.PI / 180;
    const dLat = (lat2 - lat1) * toR, dLon = (lon2 - lon1) * toR;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLon / 2) ** 2;
    total += R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }
  return Math.round(total * 3.28084); // metres → feet
}

function formatRelTime(iso) {
  if (!iso) return "never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diffMs / 3600000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function formatDateTime(iso) {
  if (!iso) return "unknown";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  } catch { return iso; }
}

/* ── helper: slot position (quarter-hour units) closest to 15 min before sunrise ── */
function morningStartIdx(day) {
  if (!day) return 0;
  const targetH = Math.max(0, (day.sunrise_h ?? 6.5) - 0.25);
  let best = 0, bestDist = Infinity;
  day.hours.forEach((h, i) => { const d = Math.abs(h.hour - targetH); if (d < bestDist) { bestDist = d; best = i; } });
  return best * 4; // return in quarter-hour slot units
}

export { localDate, corridorLengthFt, formatRelTime, formatDateTime, morningStartIdx };
