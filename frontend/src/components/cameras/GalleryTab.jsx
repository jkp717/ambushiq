import { Fragment, useState, useEffect, useLayoutEffect, useCallback, useRef } from "react";
import { SlidersHorizontal, Camera, X, ChevronLeft, ChevronRight, Heart, Scan } from "lucide-react";
import { api, withToken } from "../../services/api.js";
import { formatDateTime, dateGroupLabel } from "../../utils/formatters.js";
import Banner from "../ui/Banner.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import StandLinks from "../StandLinks.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

const PAGE = 48;
// Lightbox drag: movement before a touch counts as a drag (and picks its direction), the share of the
// width or flick speed that turns a drag into a swipe, and how much a drag toward no photo is damped.
const DRAG_START_PX = 10, SWIPE_FRACTION = 0.2, FLICK_PX_PER_MS = 0.4, EDGE_RESIST = 3;
const SLIDE_MS = 250;
// Pinch-to-zoom on the open photo: the most it zooms, where a double-tap zooms to, and what counts as a
// double-tap (two taps on the photo this close together in time and place).
const MAX_ZOOM = 4, TAP_ZOOM = 2.5, DOUBLE_TAP_MS = 300, DOUBLE_TAP_PX = 30;
const OUTLINES_KEY = "ambushiq.gallery.outlines";
const circled = (n) => String.fromCharCode(0x2460 + n);   // ① ② ③ …

function GalleryTab({ cameras, speciesOptions, filters, setFilters, onGoSetup, standNav }) {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  // Index into `items` of the photo open in the lightbox. Prev/next walk `items` itself, so they
  // follow exactly what the gallery shows under the current filters.
  const [viewIdx, setViewIdx] = useState(null);
  const [pendingNext, setPendingNext] = useState(false);   // stepping past the loaded page
  // Drag-to-swipe: the track holds prev / current / next slides and follows the finger. Its position
  // is written straight to the DOM (not React state) so dragging stays smooth on slow phones.
  const trackRef = useRef(null);
  const drag = useRef(null);         // {x, y, t, dx, axis} while a finger is down
  const dragged = useRef(false);     // the last touch was a drag, so its trailing click must not close
  const sliding = useRef(0);         // direction of a swipe animating to the next slide, else 0
  const frame = useRef(0);
  const [filtering, setFiltering] = useState(false);
  // Deer outlines on the open photo (remembered per device), and where the photo actually sits inside
  // its <img> box — object-fit: contain can leave bars, so outlines are placed on the visible photo.
  const [showOutlines, setShowOutlines] = useState(() => { try { return localStorage.getItem(OUTLINES_KEY) !== "0"; } catch { return true; } });
  const [photoRect, setPhotoRect] = useState(null);
  const [activeDeer, setActiveDeer] = useState(null);
  const curImg = useRef(null);
  // Zoom: the open photo's slide is scaled/moved with a CSS transform (origin top-left), so the deer outlines
  // inside it zoom with the photo and the outline maths above is untouched. Written straight to the DOM like
  // the swipe track. `pinch` holds the two-finger gesture's starting point while it lasts.
  const lightboxRef = useRef(null);
  const curSlide = useRef(null);
  const zoomedEl = useRef(null);
  const zoom = useRef({ s: 1, x: 0, y: 0 });
  const pinch = useRef(null);
  const lastTap = useRef(null);
  const query = filterParams(filters, { time: true }).toString();
  const count = activeFilterCount(filters, { time: true });

  const fetchPage = useCallback(async (cursor) => {
    const p = new URLSearchParams(query);
    p.set("limit", PAGE);
    if (cursor) { p.set("before_ts", cursor.ts); p.set("before_id", cursor.id); }
    const j = await api(`/cameras/gallery?${p}`);
    return { ...j, items: j.items.map((it) => ({ ...it, image_url: withToken(it.image_url) })) };
  }, [query]);

  // A filter change starts over from the newest photo.
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    setItems([]);
    setViewIdx(null);
    fetchPage(null)
      .then((j) => { if (!cancel) { setItems(j.items); setNext(j.next); setErr(null); } })
      .catch(() => { if (!cancel) setErr("Couldn't load photos."); })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [fetchPage]);

  async function loadMore() {
    if (!next) return;
    setLoading(true);
    try {
      const j = await fetchPage(next);
      setItems((prev) => [...prev, ...j.items]);
      setNext(j.next);
    } catch { setErr("Couldn't load more photos."); }
    finally { setLoading(false); }
  }

  // Favorite / un-favorite: shown at once, undone if the server refuses. The photo stays in the list
  // either way (even under "Favorites only") so an open slideshow doesn't jump.
  async function toggleFavorite(photo) {
    const want = !photo.is_favorite;
    const mark = (v) => setItems((prev) => prev.map((it) => (it.id === photo.id ? { ...it, is_favorite: v } : it)));
    mark(want);
    try {
      await api(`/camera-sightings/${photo.id}/favorite`, { method: "PUT", body: JSON.stringify({ favorite: want }) });
    } catch {
      mark(!want);
      setErr("Couldn't update the favorite. Try again when you have a connection.");
    }
  }

  // Buck toggle on one deer: shown at once, then saved; the server's answer (which also reflects
  // whether this overrides the model) replaces it, and a failure puts it back.
  async function toggleBuck(photo, deer) {
    const swap = (d) => setItems((prev) => prev.map((it) => {
      if (it.id !== photo.id) return it;
      const detections = it.detections.map((x) => (x.id === deer.id ? d : x));
      return { ...it, detections, buck_count: detections.filter((x) => x.is_buck).length };
    }));
    swap({ ...deer, is_buck: !deer.is_buck });
    try {
      swap(await api(`/deer-detections/${deer.id}/buck`, { method: "PUT", body: JSON.stringify({ buck: !deer.is_buck }) }));
    } catch {
      swap(deer);
      setErr("Couldn't save the buck mark. Try again when you have a connection.");
    }
  }

  function toggleOutlines() {
    setShowOutlines((v) => {
      try { localStorage.setItem(OUTLINES_KEY, v ? "0" : "1"); } catch { /* private mode */ }
      return !v;
    });
  }

  // Where the photo is drawn inside the current <img> (object-fit: contain keeps its shape).
  const measurePhoto = useCallback(() => {
    const img = curImg.current;
    if (!img || !img.naturalWidth || !img.clientWidth) { setPhotoRect(null); return; }
    const scale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    const width = img.naturalWidth * scale, height = img.naturalHeight * scale;
    setPhotoRect({ left: img.offsetLeft + (img.clientWidth - width) / 2, top: img.offsetTop + (img.clientHeight - height) / 2,
                   width, height });
  }, []);
  // Draw the zoom; at 1x the slide has no transform at all. `animate` for double-tap.
  const applyZoom = (animate = false) => {
    const el = curSlide.current, z = zoom.current;
    if (el) {
      el.style.transition = animate ? "transform 200ms ease-out" : "none";
      el.style.transform = z.s > 1 ? `translate(${z.x}px, ${z.y}px) scale(${z.s})` : "";
      el.style.setProperty("--zoom", String(z.s));
      zoomedEl.current = el;
    }
    lightboxRef.current?.classList.toggle("zoomed", z.s > 1);
  };
  // Keep the zoomed photo covering its slide: it can't be dragged past its own edges.
  const clampZoom = () => {
    const el = curSlide.current, z = zoom.current;
    if (!el || z.s <= 1) { zoom.current = { s: 1, x: 0, y: 0 }; return; }
    z.x = Math.min(0, Math.max(el.clientWidth * (1 - z.s), z.x));
    z.y = Math.min(0, Math.max(el.clientHeight * (1 - z.s), z.y));
  };
  // Back to 1x. Also clears the slide that was zoomed, in case the photo changed underneath it.
  const resetZoom = () => {
    zoom.current = { s: 1, x: 0, y: 0 };
    pinch.current = null;
    if (zoomedEl.current && zoomedEl.current !== curSlide.current) {
      zoomedEl.current.style.transform = "";
      zoomedEl.current.style.removeProperty("--zoom");
    }
    applyZoom();
  };
  // Double-tap: zoom in to TAP_ZOOM on the tapped point, or back out to 1x.
  const toggleZoomAt = (px, py) => {
    const el = curSlide.current;
    if (!el) return;
    if (zoom.current.s > 1) { zoom.current = { s: 1, x: 0, y: 0 }; applyZoom(true); return; }
    const r = el.getBoundingClientRect();
    zoom.current = { s: TAP_ZOOM, x: (px - r.left) * (1 - TAP_ZOOM), y: (py - r.top) * (1 - TAP_ZOOM) };
    clampZoom();
    applyZoom(true);
  };

  useLayoutEffect(() => { setActiveDeer(null); resetZoom(); measurePhoto(); }, [viewIdx, measurePhoto]); // eslint-disable-line react-hooks/exhaustive-deps
  // Re-measure whenever the photo's box changes. Rotating a phone fires `resize` before the new layout
  // (and dvh) settles, so a window listener alone reads the old size and leaves the outlines where the
  // photo used to be; observing the img and its slide catches the size and offset once layout is done.
  useEffect(() => {
    const img = curImg.current;
    if (!img) return;
    const ro = new ResizeObserver(() => { resetZoom(); measurePhoto(); });
    ro.observe(img);
    if (img.parentElement) ro.observe(img.parentElement);
    let raf = 0;
    const onWindow = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { resetZoom(); measurePhoto(); }); };
    window.addEventListener("resize", onWindow);
    window.addEventListener("orientationchange", onWindow);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onWindow);
      window.removeEventListener("orientationchange", onWindow);
    };
  }, [viewIdx, measurePhoto]);

  // A pinch zooms the photo, not the page. React's touch listeners are passive and can't prevent the
  // browser's own page zoom, so native non-passive ones on the open viewer do (iOS also sends gesture events).
  const lightboxOpen = viewIdx != null;
  useEffect(() => {
    const el = lightboxRef.current;
    if (!el) return undefined;
    const stop = (e) => { if (!e.touches || e.touches.length > 1) e.preventDefault(); };
    el.addEventListener("touchmove", stop, { passive: false });
    el.addEventListener("gesturestart", stop, { passive: false });
    el.addEventListener("gesturechange", stop, { passive: false });
    return () => {
      el.removeEventListener("touchmove", stop);
      el.removeEventListener("gesturestart", stop);
      el.removeEventListener("gesturechange", stop);
    };
  }, [lightboxOpen]);

  // Nearest index from `from` in direction `dir` (±1) that has a photo to show, or -1.
  const photoIdx = (from, dir) => {
    for (let j = from + dir; j >= 0 && j < items.length; j += dir) if (items[j].image_url) return j;
    return -1;
  };
  const hasPrev = viewIdx != null && photoIdx(viewIdx, -1) >= 0;
  const hasNext = viewIdx != null && (photoIdx(viewIdx, 1) >= 0 || !!next);

  const step = (dir) => {
    if (viewIdx == null) return;
    const j = photoIdx(viewIdx, dir);
    if (j >= 0) setViewIdx(j);
    else if (dir > 0 && next && !loading) { setPendingNext(true); loadMore(); }
  };

  // Finish a "next" that ran off the end of the loaded photos once the next page arrives
  // (keeps paging if a whole page had no photos).
  useEffect(() => {
    if (!pendingNext || loading) return;
    const j = viewIdx == null ? -1 : photoIdx(viewIdx, 1);
    if (j >= 0) { setViewIdx(j); setPendingNext(false); }
    else if (next) loadMore();
    else setPendingNext(false);
  }, [pendingNext, loading, items]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (viewIdx == null) return;
    const onKey = (e) => {
      if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "Escape") setViewIdx(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Preload the neighbours so a swipe shows the next photo immediately.
  useEffect(() => {
    if (viewIdx == null) return;
    [photoIdx(viewIdx, -1), photoIdx(viewIdx, 1)].forEach((j) => { if (j >= 0) new Image().src = items[j].image_url; });
  }, [viewIdx, items]); // eslint-disable-line react-hooks/exhaustive-deps

  // Place the track: `offset` px from centred on the current photo, or `slide` (±1) a whole photo over.
  const placeTrack = (offset, animate, slide = 0) => {
    const el = trackRef.current;
    if (!el) return;
    el.style.transition = animate ? `transform ${SLIDE_MS}ms ease-out` : "none";
    el.style.transform = slide ? `translateX(${-100 - slide * 100}%)` : `translateX(calc(-100% + ${offset}px))`;
  };
  // Whenever the photo changes (swipe, arrows, keys), re-centre before the browser paints, so the
  // slides React just reordered never show at the old position.
  useLayoutEffect(() => { placeTrack(0, false); }, [viewIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  function onTouchStart(e) {
    if (e.touches.length === 2 && !sliding.current && curSlide.current) {
      // pinch: remember where it started; the slide's untransformed origin is its box minus the current offset
      drag.current = null;
      dragged.current = true;
      const [a, b] = e.touches, z = zoom.current, r = curSlide.current.getBoundingClientRect();
      pinch.current = { dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) || 1,
                        mx: (a.clientX + b.clientX) / 2, my: (a.clientY + b.clientY) / 2,
                        s: z.s, x: z.x, y: z.y, bx: r.left - z.x, by: r.top - z.y };
      return;
    }
    if (sliding.current || e.touches.length > 1) { drag.current = null; return; }
    const t = e.touches[0];
    drag.current = { x: t.clientX, y: t.clientY, t: performance.now(), dx: 0, axis: null,
                     pan: zoom.current.s > 1, zx: zoom.current.x, zy: zoom.current.y,
                     onPhoto: !!e.target.closest?.(".lightbox-img, .deer-outline") };
    dragged.current = false;
  }
  function onTouchMove(e) {
    const p = pinch.current;
    if (p) {
      if (e.touches.length < 2) return;
      const [a, b] = e.touches;
      const s = Math.min(MAX_ZOOM, Math.max(1, p.s * Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) / p.dist));
      const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
      // the photo point that was under the fingers stays under them (which also pans as they move)
      const ux = (p.mx - p.bx - p.x) / p.s, uy = (p.my - p.by - p.y) / p.s;
      zoom.current = { s, x: mx - p.bx - ux * s, y: my - p.by - uy * s };
      clampZoom();
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => applyZoom());
      return;
    }
    const d = drag.current;
    if (!d) return;
    const t = e.touches[0];
    const dx = t.clientX - d.x, dy = t.clientY - d.y;
    if (d.pan) {
      // zoomed in: one finger moves the photo around instead of swiping to the next one
      if (!d.axis) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < DRAG_START_PX) return;
        d.axis = "pan";
        dragged.current = true;
      }
      zoom.current.x = d.zx + dx;
      zoom.current.y = d.zy + dy;
      clampZoom();
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => applyZoom());
      return;
    }
    if (!d.axis) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < DRAG_START_PX) return;
      d.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";   // a vertical drag is left to scroll the page
      dragged.current = true;
    }
    if (d.axis !== "x") return;
    d.dx = dx;
    const toward = dx < 0 ? 1 : -1;
    const shown = photoIdx(viewIdx, toward) >= 0 ? dx : dx / EDGE_RESIST;   // feel the end of the photos
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => placeTrack(shown, false));
  }
  function onTouchEnd(e) {
    if (pinch.current) {
      // the pinch is over once fewer than two fingers are down; a zoom that ended near 1x snaps back
      if (e.touches.length < 2) {
        pinch.current = null;
        if (zoom.current.s < 1.05) resetZoom();
      }
      return;
    }
    const d = drag.current;
    drag.current = null;
    if (d && !d.axis && d.onPhoto) {
      const now = performance.now(), lt = lastTap.current;
      if (lt && now - lt.t < DOUBLE_TAP_MS && Math.hypot(d.x - lt.x, d.y - lt.y) < DOUBLE_TAP_PX) {
        lastTap.current = null;
        toggleZoomAt(d.x, d.y);
        return;
      }
      lastTap.current = { t: now, x: d.x, y: d.y };
    }
    if (!d || d.axis !== "x") return;
    cancelAnimationFrame(frame.current);
    const width = trackRef.current?.clientWidth || window.innerWidth;
    const speed = Math.abs(d.dx) / Math.max(1, performance.now() - d.t);
    const dir = d.dx < 0 ? 1 : -1;
    const swipe = Math.abs(d.dx) > width * SWIPE_FRACTION || speed > FLICK_PX_PER_MS;
    if (swipe && photoIdx(viewIdx, dir) >= 0) {
      sliding.current = dir;
      placeTrack(0, true, dir);            // finish in onTrackSlid
      // backstop in case transitionend never fires (e.g. the app was backgrounded mid-slide)
      setTimeout(() => onTrackSlid({ target: trackRef.current }), SLIDE_MS + 100);
      return;
    }
    placeTrack(0, true);                   // spring back
    if (swipe && dir > 0 && next) step(1); // next photo is on a page not loaded yet: load it and move on
  }
  function onTrackSlid(e) {
    if (e.target !== trackRef.current || !sliding.current) return;
    const dir = sliding.current;
    sliding.current = 0;
    step(dir);                             // the layout effect re-centres on the new photo
  }
  function onBackdropClick() {
    if (dragged.current) { dragged.current = false; return; }
    setViewIdx(null);
  }
  const slideIdx = viewIdx == null ? [] : [photoIdx(viewIdx, -1), viewIdx, photoIdx(viewIdx, 1)];
  // the open photo's whitetail deer (other animals get no Buck toggle)
  const deer = viewIdx == null ? [] : (items[viewIdx]?.detections || []).filter((d) => d.is_deer);

  if (!cameras.length) {
    return (
      <div className="cameras-empty">
        <Camera size={44} color="var(--bord2)" />
        <p>No cameras yet. Connect a camera in Setup to sync photos.</p>
        <button className="btn btn-primary" onClick={onGoSetup}>Go to Setup</button>
      </div>
    );
  }

  const now = new Date();   // one "today" for every date heading in this render
  return (
    <div>
      <div className="cam-toolbar">
        <button className={"filter-btn" + (count ? " on" : "")} onClick={() => setFiltering(true)} title="Filters">
          <SlidersHorizontal size={16} /> Filters{count > 0 && <span className="filter-badge">{count}</span>}
        </button>
        <span className="cam-toolbar-note">
          {loading && !items.length ? "Loading..." : items.length ? `${items.length}${next ? "+" : ""} photo${items.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>

      {err && <Banner>{err}</Banner>}

      {!loading && !items.length && !err && (
        <div className="cameras-empty"><p>No photos match these filters.</p></div>
      )}

      <div className="sightings-grid gallery-grid">
        {items.map((s, i) => {
          const conf = s.confidence_score;
          const confCls = conf >= 0.7 ? "conf-high" : conf >= 0.4 ? "conf-med" : "conf-low";
          // newest first, so a heading goes wherever the date group changes
          const group = dateGroupLabel(s.timestamp, now);
          const newGroup = i === 0 || group !== dateGroupLabel(items[i - 1].timestamp, now);
          return (
            <Fragment key={s.id}>
            {newGroup && <h3 className="gallery-divider">{group}</h3>}
            <div className="sighting-card" onClick={() => s.image_url && setViewIdx(i)}>
              {!s.is_animal
                ? <span className="sighting-species sighting-empty">No animal detected</span>
                : s.species && <span className="sighting-species">{s.species}</span>}
              {s.image_url
                ? <img src={s.image_url} alt="sighting" className="sighting-thumb" loading="lazy" />
                : <div className="sighting-nophoto"><Camera size={22} color="var(--bord2)" /></div>}
              {s.is_favorite && <span className="sighting-fav" title="Favorite"><Heart size={12} fill="currentColor" /></span>}
              {s.buck_count > 0 && <span className="sighting-buck">Buck{s.buck_count > 1 ? ` ×${s.buck_count}` : ""}</span>}
              <div className="sighting-cam" title={s.camera_name}>{s.camera_name}</div>
              <div className="sighting-meta">
                <span className="sighting-ts">{formatDateTime(s.timestamp)}</span>
                {s.is_animal && <span className={"conf-badge " + confCls}>{Math.round(conf * 100)}%</span>}
              </div>
            </div>
            </Fragment>
          );
        })}
        {loading && Array.from({ length: 6 }).map((_, i) => <div key={"sk" + i} className="sighting-card sighting-skeleton" />)}
      </div>

      {next && !loading && (
        <div style={{ padding: "12px 16px", textAlign: "center" }}>
          <button className="btn" onClick={loadMore}>Load more</button>
        </div>
      )}

      {viewIdx != null && items[viewIdx] && (
        <div className="lightbox" ref={lightboxRef} onClick={onBackdropClick}
          onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
          <div className="lightbox-track" ref={trackRef} onTransitionEnd={onTrackSlid}>
            {slideIdx.map((j, k) => (
              // keyed by photo so, after a swipe, the slide already showing the new photo is kept
              <div className="lightbox-slide" key={j >= 0 ? items[j].id : `none-${k}`}
                ref={j === viewIdx ? curSlide : undefined}>
                {j >= 0 && <img src={items[j].image_url} alt={j === viewIdx ? "full-size sighting" : ""}
                  ref={j === viewIdx ? curImg : undefined} onLoad={j === viewIdx ? measurePhoto : undefined}
                  className="lightbox-img" draggable={false} onClick={(e) => e.stopPropagation()} />}
                {j === viewIdx && showOutlines && photoRect && deer.map((d, n) => {
                  const b = d.outline ?? d.box;   // the roomier outline (space for antlers), else the detector's box
                  return (
                  <span key={d.id} className={"deer-outline" + (d.is_buck ? " on" : "") + (activeDeer === d.id ? " active" : "")}
                    style={{ left: photoRect.left + b[0] * photoRect.width, top: photoRect.top + b[1] * photoRect.height,
                             width: (b[2] - b[0]) * photoRect.width, height: (b[3] - b[1]) * photoRect.height }}
                    onClick={(e) => { e.stopPropagation(); setActiveDeer(d.id); }}>
                    {deer.length > 1 && <span className="deer-num">{circled(n)}</span>}
                  </span>
                  );
                })}
              </div>
            ))}
          </div>
          {deer.length > 0 && (
            // one Buck toggle per deer; touches here never start a swipe
            <div className="lightbox-bucks" onClick={(e) => e.stopPropagation()} onTouchStart={(e) => e.stopPropagation()}>
              {deer.map((d, n) => (
                <span key={d.id} className={"lightbox-buck-row" + (activeDeer === d.id ? " active" : "")}>
                  {deer.length > 1 && <span className="deer-num">{circled(n)}</span>}
                  <button type="button" className={"lightbox-buck-btn" + (d.is_buck ? " on" : "")} aria-pressed={d.is_buck}
                    onClick={() => toggleBuck(items[viewIdx], d)}>Buck</button>
                  {!d.label && d.is_buck && d.buck_score != null && <span className="lightbox-buck-model">Model {Math.round(d.buck_score * 100)}%</span>}
                </span>
              ))}
            </div>
          )}
          <button className={"lightbox-fav" + (items[viewIdx].is_favorite ? " on" : "")} aria-pressed={!!items[viewIdx].is_favorite}
            aria-label={items[viewIdx].is_favorite ? "Remove from favorites" : "Add to favorites (never auto-deleted)"}
            title={items[viewIdx].is_favorite ? "Favorite — kept forever" : "Favorite (never auto-deleted)"}
            onClick={(e) => { e.stopPropagation(); toggleFavorite(items[viewIdx]); }}>
            <Heart size={20} fill={items[viewIdx].is_favorite ? "currentColor" : "none"} />
          </button>
          {deer.length > 0 && (
            <button className={"lightbox-outlines" + (showOutlines ? " on" : "")} aria-pressed={showOutlines}
              aria-label={showOutlines ? "Hide deer outlines" : "Show deer outlines"} title={showOutlines ? "Hide deer outlines" : "Show deer outlines"}
              onClick={(e) => { e.stopPropagation(); toggleOutlines(); }}><Scan size={20} /></button>
          )}
          {/* the stand this photo was taken at: its spot on the map and its daily score */}
          <StandLinks standId={items[viewIdx].stand_id} standNav={standNav} exclude="photos" className="lightbox-stand-links" />
          <button className="lightbox-close" onClick={() => setViewIdx(null)}><X size={20} /></button>
          {hasPrev && (
            <button className="lightbox-nav lightbox-prev" aria-label="Previous photo"
              onClick={(e) => { e.stopPropagation(); step(-1); }}><ChevronLeft size={26} /></button>
          )}
          {hasNext && (
            <button className="lightbox-nav lightbox-next" aria-label="Next photo" disabled={pendingNext}
              onClick={(e) => { e.stopPropagation(); step(1); }}><ChevronRight size={26} /></button>
          )}
        </div>
      )}

      {filtering && (
        <CameraFilterModal filters={filters} cameras={cameras} speciesOptions={speciesOptions} showTime
          onCancel={() => setFiltering(false)} onApply={(f) => { setFilters(f); setFiltering(false); }} />
      )}
    </div>
  );
}

export default GalleryTab;
