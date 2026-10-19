import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

const TAP_SLOP_PX = 5;      // movement below this is a tap, not a drag
const FLICK_PX_PER_MS = 0.4; // release speed that moves it on a stop instead of settling on the nearest
// Rubber band past either end: the first pixels follow the finger almost 1:1, then it stiffens and never
// stretches further than RUBBER_MAX_PX (the curve iOS uses). Let go and it springs back with a small bounce.
const RUBBER_MAX_PX = 60;
const rubber = (x) => RUBBER_MAX_PX * (1 - 1 / ((x * 0.55) / RUBBER_MAX_PX + 1));
const SPRING_BACK = "transform .38s cubic-bezier(.3, 1.35, .5, 1)";
const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const clamp01 = (v) => Math.max(0, Math.min(1, v));

/*
  Bottom shelf: a full-width sheet with a grab handle and a one-line header (`header`) on top of its
  panel (`children`), and optionally more below the panel (`extra`). It rests at one of three stops
  (`stop`): "open" shows the header and panel; "expanded" (only with `extra`) is pulled up further to show
  the extra section too; "closed" slides down until only the header is left showing. It can never go
  lower than that, so the header (on the map: the chosen date and time) is always on screen. Dragging
  the header settles on the nearest stop, or a flick moves it on one stop; tapping the header opens it
  from closed, closes it from open, and goes back to open from expanded. Past either end it gives a
  little with growing resistance (a rubber band) and springs back on release. Touch and mouse share the
  same pointer events; only the header drags, so swipes and sliders in the panel keep working.

  The sheet is absolutely positioned inside its (position: relative) parent. While it moves it
  publishes its visible height (header plus whatever of the panel and extra section shows) as the CSS
  variable --sheet-lift on that parent, so sibling controls can ride above it (bottom: calc(20px +
  var(--sheet-lift))), and toggles the class "sheet-dragging" on the parent so those controls can skip
  their transition mid-drag. It writes these straight to the DOM instead of through React state so a
  drag doesn't re-render the page on every pointer move.
*/
function BottomSheet({ stop, onStopChange, header, label = "controls", children, extra = null }) {
  const sheetRef = useRef(null);
  const headerRef = useRef(null);
  const panelRef = useRef(null);
  const extraRef = useRef(null);
  const safeRef = useRef(null);
  const drag = useRef(null);
  const spring = useRef(null);   // ends a spring-back still running (a new drag cancels it)

  // Where each stop rests, as a downward offset from fully expanded (the whole sheet showing): open hides
  // the extra section below the screen, closed leaves only the header, held above the bottom safe area
  // (--sheet-closed-gap: a phone's home indicator). Without an extra section, expanded and open coincide.
  const stops = useCallback(() => {
    const sheet = sheetRef.current, head = headerRef.current;
    if (!sheet || !head) return { expanded: 0, open: 0, closed: 0, height: 0 };
    const safe = safeRef.current?.offsetHeight || 0;   // the closed gap (CSS env()/max() can't be read from JS directly)
    const height = sheet.offsetHeight;
    const open = extraRef.current?.offsetHeight || 0;
    return { expanded: 0, open, closed: Math.max(open, height - head.offsetHeight - safe), height };
  }, []);

  const setLift = useCallback((px) => {
    const parent = sheetRef.current?.parentElement;
    if (parent) parent.style.setProperty("--sheet-lift", `${Math.max(0, Math.round(px))}px`);
  }, []);

  // Resting lift (the sheet's visible height), re-measured when the sheet resizes. The header's and the
  // extra section's heights also go to CSS, which uses them for the closed and open positions.
  useLayoutEffect(() => {
    const apply = () => {
      const s = stops();
      sheetRef.current?.style.setProperty("--sheet-header-h", `${headerRef.current?.offsetHeight || 0}px`);
      sheetRef.current?.style.setProperty("--sheet-extra-h", `${s.open}px`);
      setLift(s.height - s[stop]);
    };
    apply();
    const sheet = sheetRef.current;
    if (!sheet || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(apply);
    ro.observe(sheet);
    return () => ro.disconnect();
  }, [stop, stops, setLift]);

  // Leave no stale variable/class behind on the parent when the sheet goes away.
  useEffect(() => () => {
    const parent = sheetRef.current?.parentElement;
    if (parent) { parent.style.removeProperty("--sheet-lift"); parent.classList.remove("sheet-dragging"); }
  }, []);

  // The panel fades in as the sheet comes up from closed to open, and the extra section from open to
  // expanded (and out going down), instead of popping in. `t` = offset from fully expanded.
  const setFades = (t, s) => {
    if (panelRef.current) panelRef.current.style.opacity = String(s.closed > s.open ? clamp01(1 - (t - s.open) / (s.closed - s.open)) : 1);
    if (extraRef.current) extraRef.current.style.opacity = String(s.open > 0 ? clamp01(1 - t / s.open) : 1);
  };

  // `stretched`: let go while rubber-banding past an end, so it springs back with a bounce
  const endDrag = (settleStop, stretched = false) => {
    const sheet = sheetRef.current;
    if (stretched && !reducedMotion()) {
      sheet.style.transition = SPRING_BACK;
      let timer = 0;
      const clear = (e) => {
        if (e && e.type === "transitionend" && (e.target !== sheet || e.propertyName !== "transform")) return;
        clearTimeout(timer);
        sheet.removeEventListener("transitionend", clear);
        spring.current = null;
        sheet.style.transition = "";
      };
      sheet.addEventListener("transitionend", clear);
      timer = setTimeout(clear, 500);   // in case transitionend never fires
      spring.current = clear;
    } else {
      sheet.style.transition = "";
    }
    sheet.style.transform = "";
    if (panelRef.current) panelRef.current.style.opacity = "";   // back to the stop's CSS (which fades)
    if (extraRef.current) extraRef.current.style.opacity = "";
    sheet.classList.remove("dragging");
    sheet.parentElement?.classList.remove("sheet-dragging");
    if (settleStop === stop) {                        // no state change → no effect re-run
      const s = stops();
      setLift(s.height - s[stop]);
    } else onStopChange(settleStop);
  };

  const onPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const s = stops();
    drag.current = {
      id: e.pointerId, startY: e.clientY, startT: s[stop], s,
      lastY: e.clientY, lastTime: performance.now(), velocity: 0, t: s[stop], moved: false, over: false,
    };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not all browsers allow it */ }
  };

  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.id) return;
    const dy = e.clientY - d.startY;
    if (!d.moved) {
      if (Math.abs(dy) < TAP_SLOP_PX) return;
      d.moved = true;
      spring.current?.();
      const sheet = sheetRef.current;
      sheet.style.transition = "none";
      sheet.classList.add("dragging");
      sheet.parentElement?.classList.add("sheet-dragging");
    }
    const { s } = d, top = extra ? s.expanded : s.open, raw = d.startT + dy;
    d.t = Math.max(top, Math.min(s.closed, raw));   // where it counts as being: never past the top stop or below the header
    // where it's drawn: past either end it stretches a little with resistance
    const vis = raw < top ? top - rubber(top - raw) : raw > s.closed ? s.closed + rubber(raw - s.closed) : raw;
    d.over = vis !== d.t;
    sheetRef.current.style.transform = `translateY(${vis}px)`;
    setFades(d.t, s);
    setLift(s.height - d.t);
    const now = performance.now();
    d.velocity = (e.clientY - d.lastY) / Math.max(1, now - d.lastTime);   // px/ms, negative = upward
    d.lastY = e.clientY;
    d.lastTime = now;
  };

  // Where a drag lets go: a flick moves on to the next stop in its direction, otherwise the nearest stop.
  const settleFor = (d) => {
    const order = (extra ? ["expanded", "open", "closed"] : ["open", "closed"]).map((k) => [k, d.s[k]]);
    if (Math.abs(d.velocity) > FLICK_PX_PER_MS) {
      const ahead = d.velocity < 0
        ? order.filter(([, pos]) => pos < d.t).pop()      // the next stop up
        : order.find(([, pos]) => pos > d.t);             // the next stop down
      if (ahead) return ahead[0];
    }
    return order.reduce((best, cur) => (Math.abs(cur[1] - d.t) < Math.abs(best[1] - d.t) ? cur : best))[0];
  };

  const tapStop = () => (stop === "closed" ? "open" : stop === "open" ? "closed" : "open");

  const onPointerUp = (e) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.id) return;
    drag.current = null;
    if (!d.moved) { onStopChange(tapStop()); return; }                  // a tap
    endDrag(settleFor(d), d.over);
  };

  const onPointerCancel = () => {
    const d = drag.current;
    drag.current = null;
    if (d && d.moved) endDrag(stop, d.over);
  };

  // Keyboard activation only: a pointer tap is already handled in onPointerUp (click.detail is 0
  // for Enter/Space, ≥ 1 for mouse/touch clicks).
  const onClick = (e) => { if (e.detail === 0) onStopChange(tapStop()); };

  return (
    <div ref={sheetRef} className={`bottom-sheet is-${stop}`}>
      <button ref={headerRef} type="button" className="bottom-sheet-header" aria-expanded={stop !== "closed"}
        title={stop === "closed" ? `Drag up or tap to show the ${label}` : `Drag down or tap to hide the ${label}`}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel} onClick={onClick}>
        <span className="bottom-sheet-handle" aria-hidden="true" />
        <span className="bottom-sheet-title">{header}</span>
      </button>
      <div ref={panelRef} className="bottom-sheet-panel">{children}</div>
      {extra && <div ref={extraRef} className="bottom-sheet-extra">{extra}</div>}
      <span ref={safeRef} className="bottom-sheet-safe-probe" aria-hidden="true" />
    </div>
  );
}

export default BottomSheet;
