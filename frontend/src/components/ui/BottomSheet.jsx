import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

const TAP_SLOP_PX = 5;      // movement below this is a tap, not a drag
const FLICK_PX_PER_MS = 0.4; // release speed that overrides the halfway rule

/*
  Bottom shelf: a full-width sheet with a grab handle and a one-line header (`header`) on top of its
  panel (`children`). Dragging the header down closes it: the sheet slides down until only the header
  is left showing, and it can never be dragged further than that, so the header (on the map: the chosen
  date and time) is always on screen. Dragging it up or tapping the header opens it again. Touch and
  mouse share the same pointer events; only the header drags, so swipes and sliders in the panel keep
  working.

  The sheet is absolutely positioned inside its (position: relative) parent. While it moves it
  publishes its visible height (header plus whatever of the panel shows) as the CSS variable
  --sheet-lift on that parent, so sibling controls can ride above it (bottom: calc(20px +
  var(--sheet-lift))), and toggles the class "sheet-dragging" on the parent so those controls can skip
  their transition mid-drag. It writes these straight to the DOM instead of through React state so a
  drag doesn't re-render the page on every pointer move.
*/
function BottomSheet({ open, onOpenChange, header, label = "controls", children }) {
  const sheetRef = useRef(null);
  const headerRef = useRef(null);
  const safeRef = useRef(null);
  const drag = useRef(null);

  // Where the sheet rests, as a downward offset from fully open. Closed leaves the header plus the
  // bottom safe area showing (so the header sits above a phone's home indicator). A later "expanded"
  // stop (an hourly forecast graph under the panel) belongs here too.
  const stops = useCallback(() => {
    const sheet = sheetRef.current, head = headerRef.current;
    if (!sheet || !head) return { open: 0, closed: 0, height: 0 };
    const safe = safeRef.current?.offsetHeight || 0;   // env(safe-area-inset-bottom) can't be read from JS directly
    const height = sheet.offsetHeight;
    return { open: 0, closed: Math.max(0, height - head.offsetHeight - safe), height };
  }, []);

  const setLift = useCallback((px) => {
    const parent = sheetRef.current?.parentElement;
    if (parent) parent.style.setProperty("--sheet-lift", `${Math.max(0, Math.round(px))}px`);
  }, []);

  // Resting lift (the sheet's visible height), re-measured when the sheet resizes. The header's height
  // also goes to CSS, which uses it for the closed position.
  useLayoutEffect(() => {
    const apply = () => {
      const s = stops();
      sheetRef.current?.style.setProperty("--sheet-header-h", `${headerRef.current?.offsetHeight || 0}px`);
      setLift(s.height - (open ? s.open : s.closed));
    };
    apply();
    const sheet = sheetRef.current;
    if (!sheet || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(apply);
    ro.observe(sheet);
    return () => ro.disconnect();
  }, [open, stops, setLift]);

  // Leave no stale variable/class behind on the parent when the sheet goes away.
  useEffect(() => () => {
    const parent = sheetRef.current?.parentElement;
    if (parent) { parent.style.removeProperty("--sheet-lift"); parent.classList.remove("sheet-dragging"); }
  }, []);

  const endDrag = (settleOpen) => {
    const sheet = sheetRef.current;
    sheet.style.transition = "";
    sheet.style.transform = "";
    sheet.classList.remove("dragging");
    sheet.parentElement?.classList.remove("sheet-dragging");
    if (settleOpen === open) {                        // no state change → no effect re-run
      const s = stops();
      setLift(s.height - (open ? s.open : s.closed));
    } else onOpenChange(settleOpen);
  };

  const onPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const s = stops();
    drag.current = {
      id: e.pointerId, startY: e.clientY, startT: open ? s.open : s.closed, closed: s.closed, height: s.height,
      lastY: e.clientY, lastTime: performance.now(), velocity: 0, t: open ? s.open : s.closed, moved: false,
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
      const sheet = sheetRef.current;
      sheet.style.transition = "none";
      sheet.classList.add("dragging");
      sheet.parentElement?.classList.add("sheet-dragging");
    }
    d.t = Math.max(0, Math.min(d.closed, d.startT + dy));   // never past open, never below the header
    sheetRef.current.style.transform = `translateY(${d.t}px)`;
    setLift(d.height - d.t);
    const now = performance.now();
    d.velocity = (e.clientY - d.lastY) / Math.max(1, now - d.lastTime);   // px/ms, negative = upward
    d.lastY = e.clientY;
    d.lastTime = now;
  };

  const onPointerUp = (e) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.id) return;
    drag.current = null;
    if (!d.moved) { onOpenChange(!open); return; }                     // a tap
    const settleOpen = Math.abs(d.velocity) > FLICK_PX_PER_MS ? d.velocity < 0 : d.t < d.closed / 2;
    endDrag(settleOpen);
  };

  const onPointerCancel = () => {
    const d = drag.current;
    drag.current = null;
    if (d && d.moved) endDrag(open);
  };

  // Keyboard activation only: a pointer tap is already handled in onPointerUp (click.detail is 0
  // for Enter/Space, ≥ 1 for mouse/touch clicks).
  const onClick = (e) => { if (e.detail === 0) onOpenChange(!open); };

  return (
    <div ref={sheetRef} className={"bottom-sheet " + (open ? "is-open" : "is-closed")}>
      <button ref={headerRef} type="button" className="bottom-sheet-header" aria-expanded={open}
        title={open ? `Drag down or tap to hide the ${label}` : `Drag up or tap to show the ${label}`}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel} onClick={onClick}>
        <span className="bottom-sheet-handle" aria-hidden="true" />
        <span className="bottom-sheet-title">{header}</span>
      </button>
      <div className="bottom-sheet-panel">{children}</div>
      <span ref={safeRef} className="bottom-sheet-safe-probe" aria-hidden="true" />
    </div>
  );
}

export default BottomSheet;
