import { useEffect, useState } from "react";

/** A counter that goes up when the app comes back to the foreground (phone unlocked, app switched back
 * to) or the device regains a connection — at most once per `minIntervalMs`. Put it in an effect's
 * dependencies to refresh data "whenever the app is opened", not only when the page first mounts. */
export default function useForegroundRefresh(minIntervalMs = 10 * 60 * 1000) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let last = Date.now();
    const bump = (force) => {
      if (!force && Date.now() - last < minIntervalMs) return;
      last = Date.now();
      setTick((t) => t + 1);
    };
    const onVisible = () => { if (document.visibilityState === "visible") bump(false); };
    const onOnline = () => bump(true);   // signal is back: worth trying right away
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
    };
  }, [minIntervalMs]);
  return tick;
}
