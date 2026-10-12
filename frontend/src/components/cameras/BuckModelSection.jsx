import { useState, useEffect } from "react";
import { api } from "../../services/api.js";
import BuckTrainer from "./BuckTrainer.jsx";

/* Settings → Model Training: how far along the buck model is, and the way into the trainer. */
function BuckModelSection() {
  const [st, setSt] = useState(null);
  const [training, setTraining] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const refresh = () => api("/bucks/model").then((r) => { setSt(r); setErr(null); }).catch(() => setErr("Couldn't load the buck model status."));
  useEffect(() => { refresh(); }, []);

  async function retrain() {
    setBusy(true);
    try { setSt(await api("/bucks/train", { method: "POST" })); }
    catch { setErr("Couldn't retrain the model."); }
    finally { setBusy(false); }
  }

  let line = "Loading…";
  if (st && !st.available) line = "Needs MegaDetector detection (DETECTOR_MODE=megadetector) on the server.";
  else if (st?.enabled) {
    line = `On · ${Math.round(st.cv_accuracy * 100)}% accurate · trained ${new Date(st.trained_at).toLocaleDateString([], { month: "short", day: "numeric" })}`
      + ` · ${st.waiting} deer waiting for review. New photos are marked automatically — just fix any it misses.`;
  } else if (st) {
    const need = Math.max(0, st.min_each - st.n_buck), needOther = Math.max(0, st.min_each - st.n_not_buck);
    line = `Not on yet · ${st.n_buck} bucks · ${st.n_not_buck} other deer reviewed`;
    if (need || needOther) line += ` — needs ${st.min_each} of each to turn on.`;
    else if (st.cv_accuracy != null) line += ` — ${Math.round(st.cv_accuracy * 100)}% accurate, needs ${Math.round(st.min_accuracy * 100)}%. Review more deer to improve it.`;
    line += ` ${st.waiting} deer waiting for review.`;
    if (st.unscanned) line += ` ${st.unscanned} older photo${st.unscanned === 1 ? "" : "s"} not checked for deer yet — Train the model can scan them.`;
  }

  return (
    <div className="settings-section">
      <p className="settings-desc" style={{ marginBottom: 8 }}>
        Teach the app to recognize bucks. You tap the bucks in a batch of deer, and every deer you
        don't tap counts as "not a buck". Once it's accurate enough it marks bucks in new photos on its own.
        It recognizes antlers, so bucks that have shed and button bucks won't be marked.
      </p>
      {err ? <div className="settings-desc" style={{ color: "var(--amber)" }}>{err}</div>
        : <div className="settings-desc" style={{ marginBottom: 10 }}>{line}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn btn-primary" disabled={!st?.available} onClick={() => setTraining(true)}>Train the model</button>
        <button className="btn" disabled={!st?.available || busy} onClick={retrain}>{busy ? "Retraining…" : "Retrain now"}</button>
      </div>
      {training && <BuckTrainer onStatus={setSt} onClose={() => { setTraining(false); refresh(); }} />}
    </div>
  );
}

export default BuckModelSection;
