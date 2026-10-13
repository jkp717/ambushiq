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

/* Relative heading for a photo's date, for the gallery's dividers: Today, Yesterday, Earlier this week,
   Last week, Earlier this month, Last month, then the month name ("August", or "August 2025" in an
   earlier year). Local time, like formatDateTime; weeks start on Sunday. A date takes the first heading
   whose start it falls after, so newest-first photos always get headings in order. */
function dateGroupLabel(iso, now = new Date()) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return "Unknown date";
  const day = (y, m, dd) => new Date(y, m, dd);
  const today = day(now.getFullYear(), now.getMonth(), now.getDate());
  const shift = (n) => day(today.getFullYear(), today.getMonth(), today.getDate() + n);
  const weekStart = shift(-today.getDay());
  const groups = [
    ["Today", today],
    ["Yesterday", shift(-1)],
    ["Earlier this week", weekStart],
    ["Last week", day(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() - 7)],
    ["Earlier this month", day(today.getFullYear(), today.getMonth(), 1)],
    ["Last month", day(today.getFullYear(), today.getMonth() - 1, 1)],
  ];
  for (const [label, start] of groups) if (d >= start) return label;
  return d.toLocaleDateString("en-US", d.getFullYear() === now.getFullYear() ? { month: "long" } : { month: "long", year: "numeric" });
}

/* ── helper: slot position (quarter-hour units) closest to 15 min before sunrise ── */
function morningStartIdx(day) {
  if (!day) return 0;
  const targetH = Math.max(0, (day.sunrise_h ?? 6.5) - 0.25);
  let best = 0, bestDist = Infinity;
  day.hours.forEach((h, i) => { const d = Math.abs(h.hour - targetH); if (d < bestDist) { bestDist = d; best = i; } });
  return best * 4; // return in quarter-hour slot units
}

/* ── deer rating "x.x/5" label ── */
// score_5 is rounded by the backend, which derives the whole-number `rating` (icons, color)
// from that same value — never recompute it here or the two can disagree.
function ratingScore5(rating) {
  return rating.score_5 != null ? rating.score_5.toFixed(1) : rating.rating;
}

/* the color a deer rating reads in, from its whole-number rating (green 4-5, amber 3, red 1-2) */
function ratingTone(rating) {
  const r = rating.rating;
  return r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
}

export { localDate, corridorLengthFt, formatRelTime, formatDateTime, dateGroupLabel, morningStartIdx, ratingScore5, ratingTone };
