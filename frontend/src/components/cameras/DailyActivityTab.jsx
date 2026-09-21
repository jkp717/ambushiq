import { useState, useEffect } from "react";
import { SlidersHorizontal, RefreshCw, Camera } from "lucide-react";
import { api } from "../../services/api.js";
import Banner from "../ui/Banner.jsx";
import ActivityChart from "./ActivityChart.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

function fmtDay(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function DailyActivityTab({ cameras, speciesOptions, filters, setFilters, onGoSetup }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [filtering, setFiltering] = useState(false);
  const query = filterParams(filters).toString();
  const count = activeFilterCount(filters);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    api(`/cameras/activity?${query}`)
      .then((j) => { if (!cancel) { setData(j); setErr(null); } })
      .catch(() => { if (!cancel) setErr("Couldn't load activity."); })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [query]);

  if (!cameras.length) {
    return (
      <div className="cameras-empty">
        <Camera size={44} color="var(--bord2)" />
        <p>No cameras yet. Connect a camera in Setup to see when animals move past your stands.</p>
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
        {loading && <RefreshCw size={14} className="spin" color="var(--sub)" />}
      </div>

      {err && <Banner>{err}</Banner>}

      {data && !err && (
        data.total === 0
          ? <div className="cameras-empty"><p>No sightings match these filters.</p></div>
          : <>
              <div className="act-summary">
                {data.date_from === data.date_to ? fmtDay(data.date_from) : `${fmtDay(data.date_from)} - ${fmtDay(data.date_to)}`}
                {" "}({data.days} day{data.days === 1 ? "" : "s"}) - {data.total} sighting{data.total === 1 ? "" : "s"}
              </div>
              <ActivityChart hours={data.hours} />
            </>
      )}

      {filtering && (
        <CameraFilterModal filters={filters} cameras={cameras} speciesOptions={speciesOptions}
          onCancel={() => setFiltering(false)} onApply={(f) => { setFilters(f); setFiltering(false); }} />
      )}
    </div>
  );
}

export default DailyActivityTab;
