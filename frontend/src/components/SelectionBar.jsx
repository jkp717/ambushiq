import { useEffect, useState } from "react";

// Bar shown while the map is in Select mode: selection count, box tool, quick filters and the
// bulk actions. Delete needs a second tap (no native dialog, so it works the same on a phone).
function SelectionBar({ count, total, allDismissed, boxTool, busy,
                        onToggleBox, onSelectAll, onSelectDismissed, onSelectBelow, onClear,
                        onDismissRestore, onDelete, onDone }) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [belowScore, setBelowScore] = useState(50);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 4000);
    return () => clearTimeout(t);
  }, [confirming]);
  useEffect(() => { if (count === 0) setConfirming(false); }, [count]);

  function tapDelete() {
    if (!confirming) { setConfirming(true); return; }
    setConfirming(false);
    onDelete();
  }

  return (
    <div className="map-draw-bar map-select-bar">
      <span style={{ fontWeight: 600 }}>{count} of {total} selected</span>
      <button className={"btn" + (boxTool ? " btn-on" : "")} onClick={onToggleBox} aria-pressed={boxTool}
        title="Drag a rectangle to select everything inside it (desktop: hold Shift and drag)">
        ▭ Box{boxTool ? " on" : ""}
      </button>
      <button className="btn" onClick={() => setFiltersOpen((o) => !o)} aria-expanded={filtersOpen}>Select ▾</button>
      <span style={{ flex: 1 }} />
      <button className="btn" onClick={onDismissRestore} disabled={busy || count === 0}>
        {allDismissed ? "↺ Restore" : "✕ Dismiss"}
      </button>
      <button className="btn map-select-del" onClick={tapDelete} disabled={busy || count === 0}>
        {confirming ? `Confirm delete ${count}?` : "🗑 Delete"}
      </button>
      <button className="btn" onClick={onDone}>Done</button>

      {filtersOpen && (
        <div className="map-select-filters">
          <button className="btn" onClick={onSelectAll}>All</button>
          <button className="btn" onClick={onSelectDismissed}>Dismissed</button>
          <span className="map-select-below">
            Score below
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
