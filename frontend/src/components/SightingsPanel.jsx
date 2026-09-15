import { useState, useEffect, useCallback } from "react";
import { Camera, X } from "lucide-react";
import { api } from "../services/api.js";
import { formatRelTime, formatDateTime } from "../utils/formatters.js";

function SightingsPanel({ cam, onClose }) {
  const [sightings, setSightings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [viewImg, setViewImg] = useState(null);
  const PAGE = 48;

  const fetchPage = useCallback(async (since = null) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: PAGE });
      if (since) params.append("since", since);
      const rows = await api(`/cameras/${cam.id}/sightings?${params}`);
      setSightings((prev) => since ? [...prev, ...rows] : rows);
      setHasMore(rows.length === PAGE);
    } catch {}
    finally { setLoading(false); }
  }, [cam.id]);

  useEffect(() => { fetchPage(); }, [fetchPage]);
  function loadMore() {
    const oldest = sightings[sightings.length - 1]?.timestamp;
    if (oldest) fetchPage(oldest);
  }

  return (
    <div className="sightings-modal">
      <div className="sightings-modal-hd">
        <div>
          <strong style={{ fontSize: 15 }}>{cam.name} — Sightings</strong>
          <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 2 }}>
            {loading && !sightings.length ? "Loading…"
              : sightings.length ? `${sightings.length}+ sightings${hasMore ? " (scroll for more)" : ""}`
              : "No sightings yet"}
            {cam.last_sync_at && <> · synced {formatRelTime(cam.last_sync_at)}</>}
          </div>
        </div>
        <button className="icon-btn" onClick={onClose}><X size={16} /></button>
      </div>

      {!loading && !sightings.length && (
        <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--sub)" }}>
          <Camera size={40} color="var(--bord2)" style={{ marginBottom: 10 }} />
          <p style={{ margin: 0 }}>No sightings recorded yet. Sync the camera to fetch photos.</p>
        </div>
      )}

      <div className="sightings-grid">
        {sightings.map((s) => {
          const conf = s.confidence_score;
          const confCls = conf >= 0.7 ? "conf-high" : conf >= 0.4 ? "conf-med" : "conf-low";
          return (
            <div key={s.id} className="sighting-card" onClick={() => s.image_url && setViewImg(s.image_url)}>
              {s.species && <span className="sighting-species">{s.species}</span>}
              {s.image_url
                ? <img src={s.image_url} alt="sighting" className="sighting-thumb" loading="lazy" />
                : <div className="sighting-nophoto"><Camera size={22} color="var(--bord2)" /></div>
              }
              <div className="sighting-meta">
                <span className="sighting-ts">{formatDateTime(s.timestamp)}</span>
                <span className={"conf-badge " + confCls}>{Math.round(conf * 100)}%</span>
              </div>
            </div>
          );
        })}
        {loading && Array.from({ length: 6 }).map((_, i) => (
          <div key={"sk" + i} className="sighting-card sighting-skeleton" />
        ))}
      </div>

      {hasMore && !loading && (
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
    </div>
  );
}

export default SightingsPanel;
