export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

// Great-circle distance in meters between two lat/lon points.
export function distanceM(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2
    + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

// Fallback values only, used for the brief window before /api/settings has
// loaded — once settings load, the live scout_radius_default_m/min_m/max_m
// values from Settings drive the actual UI.
export const SCOUT_RADIUS_DEFAULT_M = 800;
export const SCOUT_RADIUS_MIN_M = 60;
export const SCOUT_RADIUS_MAX_M = 2400;
