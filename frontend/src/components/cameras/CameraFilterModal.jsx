import { useState } from "react";
import { X } from "lucide-react";
import Modal from "../ui/Modal.jsx";
import { BRAND_LABELS } from "../../utils/cameraBrands.js";
import { UNCLASSIFIED, EMPTY_FILTERS, lastDaysFrom, hourLabel } from "./filters.js";

const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function Chip({ on, onClick, children }) {
  return <button type="button" className={"filter-chip" + (on ? " on" : "")} aria-pressed={on} onClick={onClick}>{children}</button>;
}

/* Filter editor shared by Daily Activity and Gallery. It edits a draft copy: Apply commits it, Cancel
   throws it away. Camera names are limited to the selected brands; animal types are always the full list. */
function CameraFilterModal({ filters, cameras, speciesOptions, showTime, onApply, onCancel }) {
  const [draft, setDraft] = useState(filters);
  const brands = [...new Set(cameras.map((c) => c.brand))];
  const visibleCameras = cameras.filter((c) => !draft.brands.length || draft.brands.includes(c.brand));
  const speciesList = [...speciesOptions.species, ...(speciesOptions.has_unclassified ? [UNCLASSIFIED] : [])];
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));

  function toggleBrand(b) {
    const next = toggle(draft.brands, b);
    const inScope = new Set(cameras.filter((c) => !next.length || next.includes(c.brand)).map((c) => c.id));
    set({ brands: next, cameraIds: draft.cameraIds.filter((id) => inScope.has(id)) });
  }

  const hours = Array.from({ length: 24 }, (_, h) => h);
  const overnight = draft.hourFrom != null && draft.hourTo != null && draft.hourFrom > draft.hourTo;

  return (
    <Modal onClose={onCancel}>
      <div className="card filter-modal">
        <div className="filter-modal-hd">
          <strong>Filters</strong>
          <button className="icon-btn" onClick={onCancel} title="Cancel"><X size={16} /></button>
        </div>

        <div className="filter-modal-body">
          <section>
            <div className="filter-label">Camera brand</div>
            <div className="filter-chips">
              {brands.map((b) => <Chip key={b} on={draft.brands.includes(b)} onClick={() => toggleBrand(b)}>{BRAND_LABELS[b] || b}</Chip>)}
              {!brands.length && <span className="filter-none">No cameras yet</span>}
            </div>
          </section>

          <section>
            <div className="filter-label">Camera name</div>
            <div className="filter-chips">
              {visibleCameras.map((c) => (
                <Chip key={c.id} on={draft.cameraIds.includes(c.id)} onClick={() => set({ cameraIds: toggle(draft.cameraIds, c.id) })}>{c.name}</Chip>
              ))}
              {!visibleCameras.length && <span className="filter-none">No cameras for the selected brands</span>}
            </div>
          </section>

          <section>
            <div className="filter-label">Animal type</div>
            <div className="filter-chips">
              {speciesList.map((s) => (
                <Chip key={s} on={draft.species.includes(s)} onClick={() => set({ species: toggle(draft.species, s) })}>
                  {s === UNCLASSIFIED ? "Unclassified" : s}
                </Chip>
              ))}
              {!speciesList.length && <span className="filter-none">No animals recorded yet</span>}
            </div>
          </section>

          <section>
            <div className="filter-label">Date range</div>
            <div className="filter-chips" style={{ marginBottom: 8 }}>
              {[3, 7, 30].map((n) => (
                <Chip key={n} on={draft.dateFrom === lastDaysFrom(n) && !draft.dateTo}
                  onClick={() => set({ dateFrom: lastDaysFrom(n), dateTo: "" })}>Last {n} days</Chip>
              ))}
              <Chip on={!draft.dateFrom && !draft.dateTo} onClick={() => set({ dateFrom: "", dateTo: "" })}>All time</Chip>
            </div>
            <div className="filter-range">
              <label>From <input type="date" value={draft.dateFrom} max={draft.dateTo || undefined} onChange={(e) => set({ dateFrom: e.target.value })} /></label>
              <label>To <input type="date" value={draft.dateTo} min={draft.dateFrom || undefined} onChange={(e) => set({ dateTo: e.target.value })} /></label>
            </div>
          </section>

          {showTime && (
            <section>
              <div className="filter-label">Time of day</div>
              <div className="filter-range">
                <label>From
                  <select value={draft.hourFrom ?? ""} onChange={(e) => {
                    const v = e.target.value === "" ? null : +e.target.value;
                    set(v == null ? { hourFrom: null, hourTo: null } : { hourFrom: v, hourTo: draft.hourTo ?? 24 });
                  }}>
                    <option value="">Any</option>
                    {hours.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </label>
                <label>To
                  <select value={draft.hourTo ?? ""} disabled={draft.hourFrom == null}
                    onChange={(e) => set({ hourTo: e.target.value === "" ? null : +e.target.value })}>
                    <option value="" disabled>Any</option>
                    {hours.slice(1).concat(24).map((h) => <option key={h} value={h}>{h === 24 ? "12 AM (end of day)" : hourLabel(h)}</option>)}
                  </select>
                </label>
              </div>
              {overnight && <div className="filter-none">Runs overnight, through midnight.</div>}
            </section>
          )}
        </div>

        <div className="filter-modal-ft">
          <button className="btn" onClick={() => setDraft({ ...EMPTY_FILTERS })}>Clear all</button>
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn btn-primary" onClick={() => onApply(draft)}>Apply</button>
        </div>
      </div>
    </Modal>
  );
}

export default CameraFilterModal;
