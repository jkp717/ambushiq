import { useState, useEffect, useCallback, useRef } from "react";
import { Activity, Images, Wrench } from "lucide-react";
import { api } from "../services/api.js";
import Banner from "../components/ui/Banner.jsx";
import CameraSetupTab from "../components/cameras/CameraSetupTab.jsx";
import DailyActivityTab from "../components/cameras/DailyActivityTab.jsx";
import GalleryTab from "../components/cameras/GalleryTab.jsx";
import { EMPTY_FILTERS } from "../components/cameras/filters.js";

const TABS = [
  { key: "activity", label: "Daily Activity", icon: Activity },
  { key: "gallery",  label: "Gallery",        icon: Images },
  { key: "setup",    label: "Setup",          icon: Wrench },
];

function CamerasPage({ stands, standNav, onCamerasLoaded, photosRequest, clearPhotosRequest }) {
  // Opens on the activity graph, or on the Gallery for one stand's cameras when the user arrived from a
  // stand's "Photos" link elsewhere (photosRequest).
  const [tab, setTab] = useState(photosRequest ? "gallery" : "activity");
  const [cameras, setCameras] = useState([]);
  const [providers, setProviders] = useState([]);
  const [speciesOptions, setSpeciesOptions] = useState({ species: [], has_unclassified: false });
  // Each data tab keeps its own filters, held here so they survive switching tabs.
  const [activityFilters, setActivityFilters] = useState(EMPTY_FILTERS);
  const [galleryFilters, setGalleryFilters] = useState(() => (
    photosRequest ? { ...EMPTY_FILTERS, cameraIds: photosRequest.cameraIds } : EMPTY_FILTERS));
  const [activityGraph, setActivityGraph] = useState("hour");   // Daily Activity chart: by hour or by date
  const [activityWeather, setActivityWeather] = useState("");   // its second axis: "" (off), temp, pressure, wind, rain
  const [err, setErr] = useState(null);
  const [loaded, setLoaded] = useState(false);   // the camera list has arrived at least once

  const load = useCallback(async () => {
    try {
      const [cams, provData, opts] = await Promise.all([api("/cameras"), api("/camera-providers"), api("/cameras/filters")]);
      setCameras(cams);
      setProviders(provData.providers || []);
      setSpeciesOptions(opts);
      setLoaded(true);
      onCamerasLoaded?.(cams);   // keeps the app's stand → camera links current after edits here
      setErr(null);
    } catch { setErr("Couldn't load cameras."); }
  }, [onCamerasLoaded]);
  useEffect(() => { load(); }, [load]);

  // Drop filter picks for cameras that no longer exist (deleted in Setup). Waits for the list, so picks made
  // before it arrives (a stand's "Photos" link) aren't dropped against the empty starting list.
  useEffect(() => {
    if (!loaded) return;
    const ids = new Set(cameras.map((c) => c.id));
    const prune = (f) => (f.cameraIds.every((id) => ids.has(id)) ? f : { ...f, cameraIds: f.cameraIds.filter((id) => ids.has(id)) });
    setActivityFilters(prune);
    setGalleryFilters(prune);
  }, [cameras, loaded]);

  // A "Photos" link used while this page is already open: switch the Gallery over to that stand's cameras.
  // (On first open the state above already started there.) Either way the request is then cleared.
  const firstRequest = useRef(photosRequest);
  useEffect(() => {
    if (!photosRequest) return;
    if (photosRequest !== firstRequest.current) {
      setGalleryFilters({ ...EMPTY_FILTERS, cameraIds: photosRequest.cameraIds });
      setTab("gallery");
    }
    clearPhotosRequest();
  }, [photosRequest, clearPhotosRequest]);

  // From an activity-chart bar: the Gallery with the same filters plus that hour of the day.
  function viewHourPhotos(hour) {
    setGalleryFilters({ ...activityFilters, hourFrom: hour, hourTo: hour + 1, includeEmpty: false });
    setTab("gallery");
  }

  // From a by-date bar: the Gallery with the same filters, on just that day (any time of day).
  function viewDatePhotos(date) {
    setGalleryFilters({ ...activityFilters, dateFrom: date, dateTo: date, hourFrom: null, hourTo: null, includeEmpty: false });
    setTab("gallery");
  }

  function viewPhotos(cam) {
    setGalleryFilters({ ...EMPTY_FILTERS, cameraIds: [cam.id] });
    setTab("gallery");
  }

  return (
    <div className="list-page">
      {err && <Banner>{err}</Banner>}
      <div className="sub-tabs">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} className={"sub-tab" + (tab === key ? " active" : "")} onClick={() => setTab(key)}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {tab === "activity" && (
        <DailyActivityTab cameras={cameras} speciesOptions={speciesOptions} onViewPhotos={viewHourPhotos}
          onViewDatePhotos={viewDatePhotos} graph={activityGraph} setGraph={setActivityGraph}
          weather={activityWeather} setWeather={setActivityWeather}
          filters={activityFilters} setFilters={setActivityFilters} onGoSetup={() => setTab("setup")} />
      )}
      {tab === "gallery" && (
        <GalleryTab cameras={cameras} speciesOptions={speciesOptions}
          filters={galleryFilters} setFilters={setGalleryFilters} onGoSetup={() => setTab("setup")} standNav={standNav} />
      )}
      {tab === "setup" && (
        <CameraSetupTab cameras={cameras} providers={providers} stands={stands} reload={load} onViewPhotos={viewPhotos} standNav={standNav} />
      )}
    </div>
  );
}

export default CamerasPage;
