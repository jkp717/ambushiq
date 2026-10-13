import { useState, useEffect } from "react";
import { SlidersHorizontal, RefreshCw, Camera } from "lucide-react";
import { api } from "../../services/api.js";
import Banner from "../ui/Banner.jsx";
import ActivityChart from "./ActivityChart.jsx";
import DateActivityChart from "./DateActivityChart.jsx";
import { WEATHER_OPTIONS, WeatherLegend } from "./WeatherOverlay.jsx";
import CameraFilterModal from "./CameraFilterModal.jsx";
import { activeFilterCount, filterParams } from "./filters.js";

function fmtDay(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const GRAPHS = [{ key: "hour", label: "By hour" }, { key: "date", label: "By date" }];

/* `graph` picks the chart: "hour" (average per hour of the day, the default) or "date" (sightings on each date).
   `weather` ("" = off) adds a weather measure on a second axis: temp, pressure, wind or rain. */
function DailyActivityTab({ cameras, speciesOptions, filters, setFilters, graph, setGraph, weather, setWeather,
                            onGoSetup, onViewPhotos, onViewDatePhotos }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [filtering, setFiltering] = useState(false);
  const params = filterParams(filters);
  if (graph === "date") params.set("by", "date");
  if (weather) params.set("weather", weather);
  const query = params.toString();
  const count = activeFilterCount(filters);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    const shape = graph;     // tag the data with its graph, so a chart never gets the other graph's data mid-switch
    api(`/cameras/activity?${query}`)
      .then((j) => { if (!cancel) { setData({ ...j, graph: shape }); setErr(null); } })
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
        <div className="act-mode" role="group" aria-label="Graph">
          {GRAPHS.map(({ key, label }) => (
            <button key={key} type="button" className={graph === key ? "on" : undefined} aria-pressed={graph === key}
              onClick={() => setGraph(key)}>{label}</button>
          ))}
        </div>
        <label className="act-weather">
          <span>Secondary axis</span>
          <select value={weather} onChange={(e) => setWeather(e.target.value)}>
            {WEATHER_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </label>
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
              {data.graph === "date"
                ? <DateActivityChart dates={data.dates} weather={data.weather} onViewPhotos={onViewDatePhotos} />
                : <ActivityChart hours={data.hours} weather={data.weather} onViewPhotos={onViewPhotos} />}
              {data.weather && (data.weather.points.some((p) => p.avg != null)
                ? <WeatherLegend weather={data.weather} />
                : <div className="act-legend">No weather history for these dates yet — try again in a minute.</div>)}
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
