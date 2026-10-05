import { useState, useEffect, useCallback, useRef } from "react";
import { Mountain, Camera, CloudSun, Trash2, Play, Loader2, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { api } from "../services/api.js";
import { formatDateTime } from "../utils/formatters.js";
import Banner from "./ui/Banner.jsx";

const ICONS = { terrain: Mountain, camera: Camera, weather: CloudSun, cleanup: Trash2 };
const POLL_MS = 2000;   // while any job is running

// Settings → Jobs: every manual/scheduled job with a Run now button and its live / last status.
function JobsPanel({ onTerrainDone }) {
  const [jobs, setJobs] = useState(null);
  const [err, setErr] = useState(null);
  const terrainWasRunning = useRef(false);

  const load = useCallback(() => api("/jobs")
    .then((j) => { setJobs(j); setErr(null); })
    .catch(() => setErr("Couldn't load jobs.")), []);

  useEffect(() => { load(); }, [load]);

  const anyRunning = !!jobs?.some((j) => j.running);
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [anyRunning, load]);

  // New terrain lands on the stands, so the map needs a fresh stand list once the job ends.
  useEffect(() => {
    const running = !!jobs?.find((j) => j.id === "terrain_reanalyze")?.running;
    if (terrainWasRunning.current && !running) onTerrainDone?.();
    terrainWasRunning.current = running;
  }, [jobs, onTerrainDone]);

  async function run(id) {
    try { await api(`/jobs/${id}/run`, { method: "POST" }); }
    catch (e) { if (e.code !== 409) setErr(`Couldn't start the job: ${e.message}`); }
    load();
  }

  if (!jobs) return err ? <Banner>{err}</Banner> : <p className="settings-desc">Loading jobs…</p>;
  return (
    <>
      {err && <Banner>{err}</Banner>}
      {jobs.map((j) => <JobRow key={j.id} job={j} onRun={() => run(j.id)} />)}
    </>
  );
}

function JobRow({ job, onRun }) {
  const Icon = ICONS[job.icon] || Play;
  const sched = job.schedule === "Manual" ? "Manual"
    : `${job.schedule}${job.next_run_at ? ` · next ${formatDateTime(job.next_run_at)}` : ""}`;
  return (
    <div className="settings-section job-row">
      <div className="job-hd">
        <Icon size={15} color="var(--navy)" />
        <strong>{job.label}</strong>
        <button className="btn job-run" onClick={onRun} disabled={job.running}>
          <Play size={13} /> Run now
        </button>
      </div>
      <p className="settings-desc job-desc">{job.description}</p>
      <div className="job-sched">{sched}</div>
      <JobStatus run={job.last_run} running={job.running} />
    </div>
  );
}

function JobStatus({ run, running }) {
  const [open, setOpen] = useState(false);
  if (running) {
    const total = run?.total, processed = (run?.done || 0) + (run?.failed || 0);
    const pct = total ? Math.round((processed / total) * 100) : null;
    return (
      <div className="job-status job-running">
        <div className="job-line">
          <Loader2 size={14} className="spin" />
          Running{total ? ` — ${Math.min(processed + 1, total)} of ${total}` : "…"}
          {run?.current ? ` · ${run.current}` : ""}
        </div>
        {pct != null && <div className="job-bar"><span style={{ width: `${pct}%` }} /></div>}
      </div>
    );
  }
  if (!run) return <div className="job-status job-never">Not run yet</div>;

  const when = formatDateTime(run.finished_at || run.started_at);
  const counts = run.total != null ? `${run.done} of ${run.total}` : null;
  const view = {
    success: { cls: "job-ok", Icon: CheckCircle2, label: "Succeeded" },
    partial: { cls: "job-warn", Icon: AlertTriangle, label: "Partial" },
    failed: { cls: "job-fail", Icon: XCircle, label: "Failed" },
  }[run.status] || { cls: "job-never", Icon: AlertTriangle, label: run.status };
  const parts = [view.label, counts, run.failed ? `${run.failed} failed` : null, run.message, when].filter(Boolean);
  return (
    <div className={"job-status " + view.cls}>
      <div className="job-line">
        <view.Icon size={14} />
        <span>{parts.join(" · ")}</span>
        {run.errors.length > 0 && (
          <button className="job-details" onClick={() => setOpen((o) => !o)}>{open ? "hide ▲" : "details ▼"}</button>
        )}
      </div>
      {open && (
        <ul className="job-errors">
          {run.errors.map((e, i) => <li key={i}><b>{e.item}</b> — {e.error}</li>)}
        </ul>
      )}
    </div>
  );
}

export default JobsPanel;
