import { Fragment, useState, useEffect, useLayoutEffect, useCallback, useRef } from "react";
import { SlidersHorizontal, Camera, X, ChevronLeft, ChevronRight, Heart, Scan } from "lucide-react";
import { api, withToken } from "../../services/api.js";
import { formatDateTime, dateGroupLabel } from "../../utils/formatters.js";
import Banner from "../ui/Banner.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

const PAGE = 48;
// Lightbox drag: movement before a touch counts as a drag (and picks its direction), the share of the
// width or flick speed that turns a drag into a swipe, and how much a drag toward no photo is damped.
const DRAG_START_PX = 10, SWIPE_FRACTION = 0.2, FLICK_PX_PER_MS = 0.4, EDGE_RESIST = 3;
const SLIDE_MS = 250;
const OUTLINES_KEY = "ambushiq.gallery.outlines";
const circled = (n) => String.fromCharCode(0x2460 + n);   // ① ② ③ …

function GalleryTab({ cameras, speciesOptions, filters, setFilters, onGoSetup }) {
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
  useLayoutEffect(() => { setActiveDeer(null); measurePhoto(); }, [viewIdx, measurePhoto]);
  useEffect(() => {
    window.addEventListener("resize", measurePhoto);
    return () => window.removeEventListener("resize", measurePhoto);
  }, [measurePhoto]);

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
    if (sliding.current || e.touches.length > 1) { drag.current = null; return; }
    const t = e.touches[0];
    drag.current = { x: t.clientX, y: t.clientY, t: performance.now(), dx: 0, axis: null };
    dragged.current = false;
  }
  function onTouchMove(e) {
    const d = drag.current;
    if (!d) return;
    const t = e.touches[0];
    const dx = t.clientX - d.x, dy = t.clientY - d.y;
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
  function onTouchEnd() {
    const d = drag.current;
    drag.current = null;
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
        <div className="lightbox" onClick={onBackdropClick}
          onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
          <div className="lightbox-track" ref={trackRef} onTransitionEnd={onTrackSlid}>
            {slideIdx.map((j, k) => (
              // keyed by photo so, after a swipe, the slide already showing the new photo is kept
              <div className="lightbox-slide" key={j >= 0 ? items[j].id : `none-${k}`}>
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
