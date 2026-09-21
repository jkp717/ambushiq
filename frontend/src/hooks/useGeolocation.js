import { useEffect, useState } from "react";

// Force the GPS chip and never accept a cached fix; a long timeout so a slow first fix (cold GPS,
// under tree cover) keeps trying instead of erroring out.
const OPTIONS = { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 };
// A second, network-based watch alongside it. Desktops and laptops have no GPS chip, and a GPS-only
// request there errors or times out without ever producing a fix; this one answers from wifi / IP.
const NETWORK_OPTIONS = { enableHighAccuracy: false, maximumAge: 60000, timeout: 30000 };
const COARSE_ACCURACY_M = 1000;
const GPS_PRECEDENCE_MS = 20000;   // after a GPS-grade fix, network fixes are ignored for this long

/*
  Live device position while `enabled`. Returns { position, status }:
    position  { lat, lon, accuracy (m), gpsHeading (deg | null), speed (m/s | null), timestamp } | null
    status    "off" | "locating" | "active" | "denied" | "unavailable" | "insecure" | "nofix"
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
    let lastAccuracy = null, lastGpsAt = 0, gotFix = false;
    const unavailable = { gps: false, network: false };
    const onPosition = (source) => (p) => {
      const c = p.coords;
      // once a good fix exists, ignore a coarse (cell/wifi-tower) fix so the dot doesn't jump back
      if (lastAccuracy != null && lastAccuracy <= COARSE_ACCURACY_M && c.accuracy > COARSE_ACCURACY_M) return;
      // and while the GPS is delivering, don't let the network watch drag the dot to a rougher spot
      if (source === "network" && Date.now() - lastGpsAt < GPS_PRECEDENCE_MS) return;
      if (source === "gps") lastGpsAt = Date.now();
      lastAccuracy = c.accuracy;
      gotFix = true;
      setState({
        status: "active",
        position: {
          lat: c.latitude, lon: c.longitude, accuracy: c.accuracy,
          gpsHeading: c.heading, speed: c.speed, timestamp: p.timestamp,
        },
      });
    };
    const onError = (source) => (err) => {
      if (err.code === err.PERMISSION_DENIED) { setState({ position: null, status: "denied" }); return; }
      if (err.code === err.POSITION_UNAVAILABLE) unavailable[source] = true;
      // both watches say the device can't produce a location (e.g. location services are off): say so
      // instead of waiting forever. A timeout alone is not fatal - a slow first GPS fix keeps trying.
      if (!gotFix && unavailable.gps && unavailable.network) setState({ position: null, status: "nofix" });
      else setState((s) => ({ ...s, status: s.position ? "active" : "locating" }));
    };
    const gpsId = navigator.geolocation.watchPosition(onPosition("gps"), onError("gps"), OPTIONS);
    const netId = navigator.geolocation.watchPosition(onPosition("network"), onError("network"), NETWORK_OPTIONS);
    return () => { navigator.geolocation.clearWatch(gpsId); navigator.geolocation.clearWatch(netId); };
  }, [enabled]);

  return state;
}
