import { useState, useEffect, useCallback } from "react";
import { Plus, RefreshCw, Camera, AlertTriangle, CheckCircle2, ImageIcon, Edit3, Trash2, MapPin } from "lucide-react";
import { api } from "../services/api.js";
import { formatRelTime } from "../utils/formatters.js";
import { BRAND_LABELS, BRAND_COLORS } from "../utils/cameraBrands.js";
import Banner from "../components/ui/Banner.jsx";
import Modal from "../components/ui/Modal.jsx";
import CameraDiscoverWizard from "../components/CameraDiscoverWizard.jsx";
import CameraEditor from "../components/CameraEditor.jsx";
import SightingsPanel from "../components/SightingsPanel.jsx";

function CamerasPage({ stands }) {
  const [cameras, setCameras] = useState([]);
  const [providers, setProviders] = useState([]);
  const [adding, setAdding] = useState(false);
  const [editingCam, setEditingCam] = useState(null);
  const [viewingCam, setViewingCam] = useState(null);
  const [syncing, setSyncing] = useState({});
  const [backfilling, setBackfilling] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [cams, provData] = await Promise.all([api("/cameras"), api("/camera-providers")]);
      setCameras(cams);
      setProviders(provData.providers || []);
    } catch { setErr("Couldn't load cameras."); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function toggleActive(cam) {
    try { await api(`/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify({ is_active: !cam.is_active }) }); load(); } catch {}
  }
  async function deleteCamera(id) {
    if (!confirm("Delete this camera and all its sightings?")) return;
    const delImages = confirm("Also delete the downloaded photos from storage?\n\nOK = yes, delete image files\nCancel = keep image files");
    try {
      await api(`/cameras/${id}?delete_images=${delImages}`, { method: "DELETE" });
      load();
    } catch {}
  }
  async function verify(cam) {
    try {
      const r = await api(`/cameras/${cam.id}/verify`, { method: "POST" });
      if (r.implemented === false) alert("This brand isn't implemented yet — verification not available.");
      else alert(r.ok ? "✓ Credentials verified successfully!" : "✗ Verification failed. Check credentials.");
    } catch (e) { alert(`Error: ${e.message}`); }
  }
  async function syncNow(cam) {
    setSyncing((s) => ({ ...s, [cam.id]: true }));
    try {
      await api(`/cameras/${cam.id}/sync`, { method: "POST" });
      alert("Sync started — photos will appear shortly. Refresh the sightings view in a moment.");
      load();
    } catch (e) { alert(`Sync failed: ${e.message}`); }
    finally { setSyncing((s) => ({ ...s, [cam.id]: false })); }
  }
  async function backfillSpecies() {
    if (!window.confirm("Reclassify all existing photos? This re-runs species detection on every stored sighting and can take a long time depending on how many photos you have. Continue?")) return;
    setBackfilling(true);
    try {
      const r = await api("/cameras/backfill-species", { method: "POST" });
      if (!r.candidates) {
        alert("Nothing to reclassify — every sighting either already has a species, or its original photo has already been cleaned up by your image-retention policy.");
      } else {
        alert(`Reclassifying ${r.candidates} sighting(s) in the background — check server logs (grep "backfill_species") for progress, or refresh in a bit to see updated species badges.`);
      }
    } catch (e) { alert(`Reclassification failed to start: ${e.message}`); }
    finally { setBackfilling(false); }
  }

  return (
    <div className="list-page">
      {err && <Banner>{err}</Banner>}
      <div className="list-header">
        <h1>Trail Cameras</h1>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" disabled={backfilling} onClick={backfillSpecies}
            title="Reclassify species detection on existing images">
            {backfilling ? <><RefreshCw size={14} className="spin" /> Starting...</> : "Reclassify existing photos"}
          </button>
          <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={15} /> Connect cameras</button>
        </div>
      </div>
      {!cameras.length && (
        <div className="cameras-empty">
          <Camera size={44} color="var(--bord2)" />
          <p>No cameras yet. Add one to sync photos and boost stand rankings with real sighting data.</p>
        </div>
      )}
      <div className="list-grid">
        {cameras.map((cam) => {
          const stand = stands.find((s) => s.id === cam.stand_id);
          const prov  = providers.find((p) => p.brand === cam.brand);
          const bc    = BRAND_COLORS[cam.brand] || "#888";
          const bl    = BRAND_LABELS[cam.brand] || cam.brand;
          return (
            <div key={cam.id} className="list-card cam-card">
              <div className="cam-brand-bar" style={{ borderLeftColor: bc }}>
                <Camera size={13} color={bc} />
                <span style={{ fontWeight: 600, fontSize: 12, color: bc }}>{bl}</span>
                {!prov?.implemented && (
                  <span className="cam-stub-badge">coming soon</span>
                )}
                <button
                  className={"cam-active-chip " + (cam.is_active ? "active" : "")}
                  onClick={() => toggleActive(cam)}
                  title={cam.is_active ? "Click to deactivate" : "Click to activate"}
                >
                  {cam.is_active ? "● Active" : "○ Inactive"}
                </button>
              </div>
              <div className="list-card-body">
                <div className="list-card-name">{cam.name}</div>
                <div className="list-card-sub">
                  {stand ? <><MapPin size={11} style={{ verticalAlign: "text-bottom" }} /> {stand.name}</> : <span style={{ color: "var(--bord2)" }}>Unassigned</span>}
                  {cam.last_sync_at && <> · synced {formatRelTime(cam.last_sync_at)}</>}
                </div>
                {cam.health && !cam.health.healthy && (
                  <div className="cam-health-warn" title={cam.health.reason}>
                    <AlertTriangle size={12} /> {cam.health.reason}
                  </div>
                )}
              </div>
              <div className="list-card-actions" style={{ flexWrap: "wrap" }}>
                {prov?.implemented && <>
                  <button className="icon-btn" title="Verify credentials" onClick={() => verify(cam)}><CheckCircle2 size={15} /></button>
                  <button className="icon-btn" title="Sync now" disabled={!!syncing[cam.id]} onClick={() => syncNow(cam)}>
                    <RefreshCw size={15} className={syncing[cam.id] ? "spin" : ""} />
                  </button>
                </>}
                <button className="icon-btn" title="View sightings" onClick={() => setViewingCam(cam)}><ImageIcon size={15} /></button>
                <button className="icon-btn" title="Edit" onClick={() => setEditingCam(cam)}><Edit3 size={15} /></button>
                <button className="icon-btn" title="Delete" onClick={() => deleteCamera(cam.id)}><Trash2 size={15} /></button>
              </div>
            </div>
          );
        })}
      </div>

      {adding && (
        <Modal onClose={() => setAdding(false)}>
          <CameraDiscoverWizard providers={providers} onSaved={() => { setAdding(false); load(); }} onCancel={() => setAdding(false)} />
        </Modal>
      )}
      {editingCam && (
        <Modal onClose={() => setEditingCam(null)}>
          <CameraEditor cam={editingCam} providers={providers} stands={stands}
            onSaved={() => { setEditingCam(null); load(); }} onCancel={() => setEditingCam(null)} />
        </Modal>
      )}
      {viewingCam && (
        <Modal onClose={() => setViewingCam(null)}>
          <SightingsPanel cam={viewingCam} onClose={() => setViewingCam(null)} />
        </Modal>
      )}
    </div>
  );
}

export default CamerasPage;
