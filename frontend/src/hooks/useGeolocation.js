import { useEffect, useState } from "react";

// Force the GPS chip and never accept a cached fix; a long timeout so a slow first fix (cold GPS,
// under tree cover) keeps trying instead of erroring out.
const OPTIONS = { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 };
const COARSE_ACCURACY_M = 1000;

/*
  Live device position while `enabled`. Returns { position, status }:
    position  { lat, lon, accuracy (m), gpsHeading (deg | null), speed (m/s | null), timestamp } | null
    status    "off" | "locating" | "active" | "denied" | "unavailable" | "insecure"
  Watching starts when `enabled` turns true and stops (clearWatch) when it turns false or the
  component unmounts, so the phone's location indicator goes away with it.
*/
export default function useGeolocation(enabled) {
  const [state, setState] = useState({ position: null, status: "off" });

  useEffect(() => {
    if (!enabled) { setState({ position: null, status: "off" }); return undefined; }
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setState({ position: null, status: "unavailable" });
      return undefined;
    }
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      setState({ position: null, status: "insecure" });   // browsers only allow geolocation over HTTPS
      return undefined;
    }

    setState({ position: null, status: "locating" });
    let lastAccuracy = null;
    const id = navigator.geolocation.watchPosition(
      (p) => {
        const c = p.coords;
        // once a good fix exists, ignore a coarse (cell/wifi-tower) fix so the dot doesn't jump back
        if (lastAccuracy != null && lastAccuracy <= COARSE_ACCURACY_M && c.accuracy > COARSE_ACCURACY_M) return;
        lastAccuracy = c.accuracy;
        setState({
          status: "active",
          position: {
            lat: c.latitude, lon: c.longitude, accuracy: c.accuracy,
            gpsHeading: c.heading, speed: c.speed, timestamp: p.timestamp,
          },
        });
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) setState({ position: null, status: "denied" });
        // timeout / temporarily unavailable: keep watching and keep the last dot
        else setState((s) => ({ ...s, status: s.position ? "active" : "locating" }));
      },
      OPTIONS,
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [enabled]);

  return state;
}
