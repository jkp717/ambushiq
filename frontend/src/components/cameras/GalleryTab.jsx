import { useState, useEffect, useCallback } from "react";
import { SlidersHorizontal, Camera, X } from "lucide-react";
import { api } from "../../services/api.js";
import { formatDateTime } from "../../utils/formatters.js";
import Banner from "../ui/Banner.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

const PAGE = 48;

function GalleryTab({ cameras, speciesOptions, filters, setFilters, onGoSetup }) {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [viewImg, setViewImg] = useState(null);
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
            <div key={s.id} className="sighting-card" onClick={() => s.image_url && setViewImg(s.image_url)}>
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

      {viewImg && (
        <div className="lightbox" onClick={() => setViewImg(null)}>
          <img src={viewImg} alt="full-size sighting" className="lightbox-img" onClick={(e) => e.stopPropagation()} />
          <button className="lightbox-close" onClick={() => setViewImg(null)}><X size={20} /></button>
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
