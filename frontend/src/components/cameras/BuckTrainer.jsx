import { useState, useEffect, useCallback } from "react";
import { X, Maximize2, Check, Loader2 } from "lucide-react";
import { api, withToken } from "../../services/api.js";
import { formatDateTime } from "../../utils/formatters.js";

const FIND_DEER_JOB = "find_deer";
const SCAN_POLL_MS = 2000;

/* Buck model trainer: shows whitetail deer in batches, one crop each. Tap every buck, then "Next batch":
   the tapped deer are saved as bucks and every other deer shown as "not a buck" (implied — there is no
   doe button), and the model retrains. Closing without "Next batch" saves nothing for that batch. */
function BuckTrainer({ onClose, onStatus }) {
  const [items, setItems] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const [batch, setBatch] = useState(1);
  const [preview, setPreview] = useState(null);       // a deer shown on its full photo
  const [justEnabled, setJustEnabled] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [q, st] = await Promise.all([api("/bucks/queue?limit=16"), api("/bucks/model")]);
      setItems(q.items); setStatus(st); setPicked(new Set()); setErr(null);
    } catch { setErr("Couldn't load deer to review."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // The "Find deer in existing photos" job, run from here when older photos haven't been scanned yet.
  const [scan, setScan] = useState(null);             // {processed, total} while it runs
  const [scanDone, setScanDone] = useState(false);
  const pollScan = useCallback(async () => {
    try {
      const job = (await api("/jobs")).find((j) => j.id === FIND_DEER_JOB);
      if (job?.running) {
        const r = job.last_run || {};
        setScan({ processed: (r.done || 0) + (r.failed || 0), total: r.total || 0 });
        return true;
      }
    } catch { /* keep polling */ return true; }
    setScan(null);
    return false;
  }, []);
  useEffect(() => { pollScan(); }, [pollScan]);          // already running from the Jobs list?
  const scanning = !!scan;
  useEffect(() => {
    if (!scanning) return;
    const t = setInterval(async () => { if (!(await pollScan())) { setScanDone(true); load(); } }, SCAN_POLL_MS);
    return () => clearInterval(t);
  }, [scanning, pollScan, load]);

  async function findDeer() {
    setScan({ processed: 0, total: 0 });
    try { await api(`/jobs/${FIND_DEER_JOB}/run`, { method: "POST" }); }
    catch (e) { if (e.code !== 409) { setScan(null); setErr("Couldn't start looking for deer."); } }
  }

  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  async function nextBatch() {
    if (!items.length) return;
    setSaving(true);
    try {
      const wasOn = !!status?.enabled;
      const st = await api("/bucks/batch", { method: "POST",
        body: JSON.stringify({ shown: items.map((i) => i.id), bucks: [...picked] }) });
      setStatus(st); onStatus && onStatus(st);
      if (st.enabled && !wasOn) setJustEnabled(true);
      setBatch((b) => b + 1);
      await load();
    } catch { setErr("Couldn't save this batch. Check your connection and try again."); }
    finally { setSaving(false); }
  }

  function close() {
    if (picked.size && !window.confirm("Close without saving this batch? The bucks you tapped won't be kept.")) return;
    onClose();
  }

  const needBucks = status ? Math.max(0, status.min_each - status.n_buck) : null;

  return (
    <div className="buck-trainer">
      <div className="buck-trainer-hd">
        <div>
          <strong>Tap every buck</strong>
          {status && (
            <div className="buck-trainer-sub">
              Bucks {status.n_buck}{needBucks ? `/${status.min_each}` : ""} · other deer {status.n_not_buck} · batch {batch}
              {status.enabled && status.cv_accuracy != null && ` · model ${Math.round(status.cv_accuracy * 100)}% accurate`}
            </div>
          )}
        </div>
        <button className="icon-btn" onClick={close} aria-label="Close trainer"><X size={18} /></button>
      </div>

      {justEnabled && (
        <div className="buck-trainer-note">Buck model is on — new photos will be marked automatically.</div>
      )}
      {err && <div className="buck-trainer-note warn">{err}</div>}

      <div className="buck-trainer-body">
        {loading ? <div className="buck-trainer-empty">Loading…</div>
          : !items.length ? (
            <div className="buck-trainer-empty">
              {scan ? (
                <>
                  <Loader2 size={18} className="spin" /> Looking for deer in your photos…
                  <div className="buck-trainer-sub">
                    {scan.total ? `${Math.min(scan.processed + 1, scan.total)} of ${scan.total} photos` : "Starting…"} — deer show up here when it's done. You can close this and come back.
                  </div>
                </>
              ) : status?.unscanned && !scanDone ? (
                <>
                  {status.unscanned} photo{status.unscanned === 1 ? " hasn't" : "s haven't"} been checked for deer yet.
                  <div className="buck-trainer-sub">Photos from before the buck model existed need a one-time scan to outline each deer.</div>
                  <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={findDeer}>Find deer in my photos</button>
                </>
              ) : (
                <>
                  {scanDone ? "Finished looking — no deer to review." : "Nothing left to review."}
                  <div className="buck-trainer-sub">New deer appear here as your cameras sync.</div>
                </>
              )}
            </div>
          ) : (
            <div className="buck-grid">
              {items.map((it) => {
                const on = picked.has(it.id);
                return (
                  <div key={it.id} className={"buck-cell" + (on ? " on" : "")} role="button" tabIndex={0}
                    aria-pressed={on} onClick={() => toggle(it.id)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(it.id); } }}>
                    <img src={withToken(it.crop_url)} alt="deer" loading="lazy" draggable={false} />
                    {on && <span className="buck-badge"><Check size={12} /> Buck</span>}
                    <button type="button" className="buck-zoom" aria-label="Show the whole photo"
                      onClick={(e) => { e.stopPropagation(); setPreview(it); }}><Maximize2 size={13} /></button>
                  </div>
                );
              })}
            </div>
          )}
      </div>

      <div className="buck-trainer-ft">
        <span className="buck-trainer-sub">{items.length ? `${picked.size} of ${items.length} tapped as bucks — the rest count as "not a buck"` : ""}</span>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={close}>Close</button>
        <button className="btn btn-primary" onClick={nextBatch} disabled={!items.length || saving || loading}>
          {saving ? "Saving…" : "Next batch"}
        </button>
      </div>

      {preview && (
        <div className="buck-preview" onClick={() => setPreview(null)}>
          <div className="buck-preview-photo" onClick={(e) => e.stopPropagation()}>
            <img src={withToken(preview.image_url)} alt="full photo" draggable={false} />
            <span className="deer-outline on" style={{
              left: `${preview.box[0] * 100}%`, top: `${preview.box[1] * 100}%`,
              width: `${(preview.box[2] - preview.box[0]) * 100}%`, height: `${(preview.box[3] - preview.box[1]) * 100}%` }} />
          </div>
          <div className="buck-trainer-sub" style={{ color: "#fff" }}>
            {preview.camera_name} · {formatDateTime(preview.timestamp)} — tap anywhere to go back
          </div>
        </div>
      )}
    </div>
  );
}

export default BuckTrainer;
