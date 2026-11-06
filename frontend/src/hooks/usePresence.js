import { useEffect, useState } from "react";

const REDUCED_MOTION = typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Keeps something mounted for `ms` after `open` turns false, so it can play a closing animation before it goes.
// Returns true while it should be rendered; style the closing state from `open` itself (e.g. an "is-closing" class).
export default function usePresence(open, ms = 180) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) { setMounted(true); return undefined; }
    if (REDUCED_MOTION) { setMounted(false); return undefined; }
    const t = setTimeout(() => setMounted(false), ms);
    return () => clearTimeout(t);
  }, [open, ms]);
  return open || mounted;
}
