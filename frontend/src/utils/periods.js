const PERIOD_COLORS = { morning: "#C28800", midday: "#1E7FB0", evening: "#7A3FA0" };
const PERIOD_LABEL  = { morning: "Morning", midday: "Midday", evening: "Evening" };

// Sit windows: the default hours either side of sunrise / sunset (the backend's sit_* settings), and the
// whole-hour, inclusive local-hour windows scoring.period_windows builds from them: [lo, hi] per period,
// where lo > hi means the period is empty. The backend sends each day's windows with /map/hours; this is
// for a saved copy from before it did, and for the Settings preview.
const SIT_DEFAULTS = { sit_morning_before_sunrise_h: 1, sit_morning_after_sunrise_h: 3,
                       sit_evening_before_sunset_h: 3, sit_evening_after_sunset_h: 0 };
function sitWindows(sunriseH, sunsetH, settings = SIT_DEFAULTS) {
  const v = (k) => settings[k] ?? SIT_DEFAULTS[k];
  const morningEnd = Math.trunc(sunriseH + v("sit_morning_after_sunrise_h"));
  const eveningStart = Math.max(Math.trunc(sunsetH - v("sit_evening_before_sunset_h")), morningEnd + 1);
  return {
    morning: [Math.trunc(sunriseH - v("sit_morning_before_sunrise_h")), morningEnd],
    midday: [morningEnd + 1, eveningStart - 1],
    evening: [eveningStart, Math.trunc(sunsetH + v("sit_evening_after_sunset_h"))],
  };
}

export { PERIOD_COLORS, PERIOD_LABEL, SIT_DEFAULTS, sitWindows };
