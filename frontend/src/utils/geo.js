export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

// Fallback values only, used for the brief window before /api/settings has
// loaded — once settings load, the live scout_radius_default_m/min_m/max_m
// values from Settings drive the actual UI.
export const SCOUT_RADIUS_DEFAULT_M = 800;
export const SCOUT_RADIUS_MIN_M = 60;
export const SCOUT_RADIUS_MAX_M = 2400;
