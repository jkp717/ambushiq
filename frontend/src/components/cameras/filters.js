/* Shared filter model for the Daily Activity and Gallery tabs.
   Empty lists / empty dates / null hours all mean "no limit". */
const UNCLASSIFIED = "__none__";   // matches the backend's stand-in for sightings with no species

const EMPTY_FILTERS = { brands: [], cameraIds: [], species: [], dateFrom: "", dateTo: "", hourFrom: null, hourTo: null };

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// "Last N days" includes today, so it starts N-1 days back; the end stays open (the server uses today).
function lastDaysFrom(n) {
  const d = new Date();
  d.setDate(d.getDate() - (n - 1));
  return isoDate(d);
}

function activeFilterCount(f, { time = false } = {}) {
  return (f.brands.length ? 1 : 0) + (f.cameraIds.length ? 1 : 0) + (f.species.length ? 1 : 0)
    + (f.dateFrom || f.dateTo ? 1 : 0) + (time && f.hourFrom != null && f.hourTo != null ? 1 : 0);
}

function filterParams(f, { time = false } = {}) {
  const p = new URLSearchParams();
  f.brands.forEach((b) => p.append("brand", b));
  f.cameraIds.forEach((id) => p.append("camera_id", id));
  f.species.forEach((s) => p.append("species", s));
  if (f.dateFrom) p.set("date_from", f.dateFrom);
  if (f.dateTo) p.set("date_to", f.dateTo);
  if (time && f.hourFrom != null && f.hourTo != null) { p.set("hour_from", f.hourFrom); p.set("hour_to", f.hourTo); }
  return p;
}

function hourLabel(h) {
  if (h === 0 || h === 24) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

export { UNCLASSIFIED, EMPTY_FILTERS, isoDate, lastDaysFrom, activeFilterCount, filterParams, hourLabel };
