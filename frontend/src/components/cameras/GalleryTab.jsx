import { useState, useEffect, useCallback, useRef } from "react";
import { SlidersHorizontal, Camera, X, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../../services/api.js";
import { formatDateTime } from "../../utils/formatters.js";
import Banner from "../ui/Banner.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

const PAGE = 48;
const SWIPE_PX = 50;   // horizontal travel that counts as a swipe rather than a tap

function GalleryTab({ cameras, speciesOptions, filters, setFilters, onGoSetup }) {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  // Index into `items` of the photo open in the lightbox. Prev/next walk `items` itself, so they
  // follow exactly what the gallery shows under the current filters.
  const [viewIdx, setViewIdx] = useState(null);
  const [pendingNext, setPendingNext] = useState(false);   // stepping past the loaded page
  const touch = useRef(null);
  const [filtering, setFiltering] = useState(false);
  const query = filterParams(filters, { time: true }).toString();
  const count = activeFilterCount(filters, { time: true });

  const fetchPage = useCallback(async (cursor) => {
    const p = new URLSearchParams(query);
    p.set("limit", PAGE);
    if (cursor) { p.set("before_ts", cursor.ts); p.set("before_id", cursor.id); }
    return api(`/cameras/gallery?${p}`);
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

  function onTouchStart(e) {
    const t = e.touches[0];
    touch.current = { x: t.clientX, y: t.clientY };
  }
  function onTouchEnd(e) {
    if (!touch.current) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touch.current.x, dy = t.clientY - touch.current.y;
    touch.current = null;
    if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
  }

  if (!cameras.length) {
    return (
      <div className="cameras-empty">
        <Camera size={44} color="var(--bord2)" />
        <p>No cameras yet. Connect a camera in Setup to sync photos.</p>
        <button className="btn btn-primary" onClick={onGoSetup}>Go to Setup</button>
      </div>
    );
  }

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
        {items.map((s) => {
          const conf = s.confidence_score;
          const confCls = conf >= 0.7 ? "conf-high" : conf >= 0.4 ? "conf-med" : "conf-low";
          return (
            <div key={s.id} className="sighting-card" onClick={() => s.image_url && setViewIdx(items.indexOf(s))}>
              {!s.is_animal
                ? <span className="sighting-species sighting-empty">No animal detected</span>
                : s.species && <span className="sighting-species">{s.species}</span>}
              {s.image_url
                ? <img src={s.image_url} alt="sighting" className="sighting-thumb" loading="lazy" />
                : <div className="sighting-nophoto"><Camera size={22} color="var(--bord2)" /></div>}
              <div className="sighting-cam" title={s.camera_name}>{s.camera_name}</div>
              <div className="sighting-meta">
                <span className="sighting-ts">{formatDateTime(s.timestamp)}</span>
                {s.is_animal && <span className={"conf-badge " + confCls}>{Math.round(conf * 100)}%</span>}
              </div>
            </div>
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
        <div className="lightbox" onClick={() => setViewIdx(null)} onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
          <img src={items[viewIdx].image_url} alt="full-size sighting" className="lightbox-img" onClick={(e) => e.stopPropagation()} />
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
