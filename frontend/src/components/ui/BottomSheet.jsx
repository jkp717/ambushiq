import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

const TAP_SLOP_PX = 5;      // movement below this is a tap, not a drag
const FLICK_PX_PER_MS = 0.4; // release speed that overrides the halfway rule

/*
  Drag-up bottom sheet: a tab that is always visible at the bottom-center and a panel that slides
  up from beneath it. Touch and mouse use the same pointer events; a tap on the tab toggles it.

  The sheet is absolutely positioned inside its (position: relative) parent. While it moves it
  publishes the visible panel height as the CSS variable --sheet-lift on that parent, so sibling
  controls can ride above it (bottom: calc(20px + var(--sheet-lift))), and toggles the class
  "sheet-dragging" on the parent so those controls can skip their transition mid-drag. It writes
  these straight to the DOM instead of through React state so a drag doesn't re-render the page
  on every pointer move.
*/
function BottomSheet({ open, onOpenChange, tab, children }) {
  const sheetRef = useRef(null);
  const tabRef = useRef(null);
  const drag = useRef(null);

  // Height of the panel below the tab (including its bottom safe-area padding). This is also how
  // far the sheet slides down when closed: only the tab stays visible, flush on the bottom edge.
  const panelHeight = useCallback(() => {
    const sheet = sheetRef.current, tabEl = tabRef.current;
    return sheet && tabEl ? Math.max(0, sheet.offsetHeight - tabEl.offsetHeight) : 0;
  }, []);

  const setLift = useCallback((px) => {
    const parent = sheetRef.current?.parentElement;
    if (parent) parent.style.setProperty("--sheet-lift", `${Math.max(0, Math.round(px))}px`);
  }, []);

  // Resting lift: full panel height when open, none when closed. Re-measured when the panel resizes.
  useLayoutEffect(() => {
    const apply = () => setLift(open ? panelHeight() : 0);
    apply();
    const sheet = sheetRef.current;
    if (!sheet || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(apply);
    ro.observe(sheet);
    return () => ro.disconnect();
  }, [open, panelHeight, setLift]);

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
    if (settleOpen === open) setLift(open ? panelHeight() : 0);   // no state change → no effect re-run
    else onOpenChange(settleOpen);
  };

  const onPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const panelFull = panelHeight(), hidden = panelFull;
    drag.current = {
      id: e.pointerId, startY: e.clientY, startT: open ? 0 : hidden, hidden, panelFull,
      lastY: e.clientY, lastTime: performance.now(), velocity: 0, t: open ? 0 : hidden, moved: false,
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
    d.t = Math.max(0, Math.min(d.hidden, d.startT + dy));
    sheetRef.current.style.transform = `translateY(${d.t}px)`;
    setLift(d.hidden > 0 ? d.panelFull * (1 - d.t / d.hidden) : 0);   // full panel height open → 0 closed
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
    const settleOpen = Math.abs(d.velocity) > FLICK_PX_PER_MS ? d.velocity < 0 : d.t < d.hidden / 2;
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
      <button ref={tabRef} type="button" className="bottom-sheet-tab" aria-expanded={open}
        title={open ? "Drag down or tap to hide the time controls" : "Drag up or tap to show the time controls"}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel} onClick={onClick}>
        {tab}
      </button>
      <div className="bottom-sheet-panel">{children}</div>
    </div>
  );
}

export default BottomSheet;
