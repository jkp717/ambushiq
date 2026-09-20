import { useEffect, useState } from "react";

const LABELS = {
  suggestion: ["scouting", "scouting"],
  zone: ["zone", "zones"],
  corridor: ["corridor", "corridors"],
  sign: ["rub/scrape", "rubs/scrapes"],
  stand: ["stand", "stands"],
};
const ORDER = ["suggestion", "zone", "corridor", "sign", "stand"];

// "2 scouting · 1 zone · 3 stands" from per-kind counts
function summarize(counts) {
  return ORDER.filter((k) => counts[k] > 0)
    .map((k) => `${counts[k]} ${LABELS[k][counts[k] === 1 ? 0 : 1]}`).join(" · ");
}

// Bar shown while the map is in multi-select mode: what's selected, box tool, quick filters and
// the bulk actions. Scouting "dismissed" is the equivalent of "inactive", so one action set
// covers every type; labels switch to Restore/Dismiss when only scouting items are selected.
// Delete needs a second tap (no native dialog, so it works the same on a phone).
function SelectionBar({ counts, total, scoutingOnly, canActivate, canDeactivate, boxTool, busy,
                        onToggleBox, onSelectAll, onSelectKind, onSelectInactive, onSelectBelow, onClear,
                        onActivate, onDeactivate, onDelete, onDone }) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [belowScore, setBelowScore] = useState(50);
  const [confirming, setConfirming] = useState(false);

  const count = ORDER.reduce((n, k) => n + (counts[k] || 0), 0);
  const summary = summarize(counts);
  const hasScouting = counts.suggestion > 0;
  const mixed = hasScouting && !scoutingOnly;

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 4000);
    return () => clearTimeout(t);
  }, [confirming]);
  useEffect(() => { setConfirming(false); }, [count]);

  function tapDelete() {
    if (!confirming) { setConfirming(true); return; }
    setConfirming(false);
    onDelete();
  }

  return (
    <div className="map-draw-bar map-select-bar">
      <span style={{ fontWeight: 600 }}>
        {count === 0 ? `Nothing selected (${total} selectable)` : `${summary} selected`}
      </span>
      <button className={"btn" + (boxTool ? " btn-on" : "")} onClick={onToggleBox} aria-pressed={boxTool}
        title="Drag a rectangle to select everything inside it (desktop: hold Shift and drag)">
        ▭ Box{boxTool ? " on" : ""}
      </button>
      <button className="btn" onClick={() => setFiltersOpen((o) => !o)} aria-expanded={filtersOpen}>Select ▾</button>
      <span style={{ flex: 1 }} />
      <button className="btn" onClick={onActivate} disabled={busy || !canActivate}>
        {scoutingOnly ? "↺ Restore" : "✓ Make active"}
      </button>
      <button className="btn" onClick={onDeactivate} disabled={busy || !canDeactivate}>
        {scoutingOnly ? "✕ Dismiss" : "⊘ Make inactive"}
      </button>
      <button className="btn map-select-del" onClick={tapDelete} disabled={busy || count === 0}>
        {confirming ? `Confirm delete: ${summary}?` : "🗑 Delete"}
      </button>
      <button className="btn" onClick={onDone}>Done</button>

      {mixed && (
        <div className="map-select-note">Scouting items: Make inactive = dismiss, Make active = restore.</div>
      )}
      {confirming && counts.stand > 0 && (
        <div className="map-select-note map-select-warn">
          Deleting stands unassigns their cameras and discards their terrain data.
        </div>
      )}

      {filtersOpen && (
        <div className="map-select-filters">
          <button className="btn" onClick={onSelectAll}>All</button>
          {ORDER.map((k) => (
            <button key={k} className="btn" onClick={() => onSelectKind(k)}>
              {k === "suggestion" ? "Scouting" : LABELS[k][1][0].toUpperCase() + LABELS[k][1].slice(1)}
            </button>
          ))}
          <button className="btn" onClick={onSelectInactive}>Inactive</button>
          <span className="map-select-below">
            Scouting score below
            <input type="number" min={0} max={100} value={belowScore} inputMode="numeric"
              onChange={(e) => setBelowScore(Math.max(0, Math.min(100, Number(e.target.value) || 0)))} />
            <button className="btn" onClick={() => onSelectBelow(belowScore)}>Select</button>
          </span>
          <button className="btn" onClick={onClear} disabled={count === 0}>Clear</button>
        </div>
      )}
    </div>
  );
}

export default SelectionBar;
