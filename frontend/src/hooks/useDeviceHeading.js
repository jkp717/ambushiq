import { useEffect, useState } from "react";

const SMOOTHING = 0.25;       // 0..1, higher = follows the compass faster but jitters more
const PUBLISH_DELTA_DEG = 2;  // re-render only when the heading has moved this much

/*
  iOS only exposes the compass after the user grants permission, and the request must be made
  directly inside a tap handler. Call this synchronously from the button's onClick; a denial just
  means no compass beam. Returns true when compass events may be used.
*/
export async function requestOrientationPermission() {
  const D = typeof window !== "undefined" ? window.DeviceOrientationEvent : undefined;
  if (D && typeof D.requestPermission === "function") {
    try { return (await D.requestPermission()) === "granted"; } catch { return false; }
  }
  return true;
}

const norm = (deg) => ((deg % 360) + 360) % 360;
const diff = (a, b) => ((a - b + 540) % 360) - 180;   // signed shortest angle a→b, -180..180

/*
  Compass heading of the device in degrees clockwise from north, or null when unavailable.
  iOS: DeviceOrientationEvent.webkitCompassHeading. Android: the absolute orientation event, where
  the compass heading is 360 - alpha. Adjusted for screen rotation, smoothed with a circular
  low-pass filter, and throttled so it doesn't re-render on every sensor tick.
*/
export default function useDeviceHeading(enabled) {
  const [heading, setHeading] = useState(null);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") { setHeading(null); return undefined; }
    let smoothed = null, published = null;

    const onOrientation = (e) => {
      let h = null;
      if (typeof e.webkitCompassHeading === "number") h = e.webkitCompassHeading;
      else if (e.absolute && typeof e.alpha === "number") h = 360 - e.alpha;
      if (h == null || Number.isNaN(h)) return;
      const screenAngle = (window.screen && window.screen.orientation && window.screen.orientation.angle) || 0;
      h = norm(h + screenAngle);

      smoothed = smoothed == null ? h : norm(smoothed + diff(h, smoothed) * SMOOTHING);
      if (published == null || Math.abs(diff(smoothed, published)) >= PUBLISH_DELTA_DEG) {
        published = smoothed;
        setHeading(Math.round(smoothed));
      }
    };

    window.addEventListener("deviceorientationabsolute", onOrientation, true);
    window.addEventListener("deviceorientation", onOrientation, true);
    return () => {
      window.removeEventListener("deviceorientationabsolute", onOrientation, true);
      window.removeEventListener("deviceorientation", onOrientation, true);
      setHeading(null);
    };
  }, [enabled]);

  return heading;
}
