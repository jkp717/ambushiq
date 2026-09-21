import { useState, useEffect, useCallback } from "react";
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

function CamerasPage({ stands }) {
  const [tab, setTab] = useState("activity");   // opens on the activity graph
  const [cameras, setCameras] = useState([]);
  const [providers, setProviders] = useState([]);
  const [speciesOptions, setSpeciesOptions] = useState({ species: [], has_unclassified: false });
  // Each data tab keeps its own filters, held here so they survive switching tabs.
  const [activityFilters, setActivityFilters] = useState(EMPTY_FILTERS);
  const [galleryFilters, setGalleryFilters] = useState(EMPTY_FILTERS);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [cams, provData, opts] = await Promise.all([api("/cameras"), api("/camera-providers"), api("/cameras/filters")]);
      setCameras(cams);
      setProviders(provData.providers || []);
      setSpeciesOptions(opts);
      setErr(null);
    } catch { setErr("Couldn't load cameras."); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // Drop filter picks for cameras that no longer exist (deleted in Setup).
  useEffect(() => {
    const ids = new Set(cameras.map((c) => c.id));
    const prune = (f) => (f.cameraIds.every((id) => ids.has(id)) ? f : { ...f, cameraIds: f.cameraIds.filter((id) => ids.has(id)) });
    setActivityFilters(prune);
    setGalleryFilters(prune);
  }, [cameras]);

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
        <DailyActivityTab cameras={cameras} speciesOptions={speciesOptions}
          filters={activityFilters} setFilters={setActivityFilters} onGoSetup={() => setTab("setup")} />
      )}
      {tab === "gallery" && (
        <GalleryTab cameras={cameras} speciesOptions={speciesOptions}
          filters={galleryFilters} setFilters={setGalleryFilters} onGoSetup={() => setTab("setup")} />
      )}
      {tab === "setup" && (
        <CameraSetupTab cameras={cameras} providers={providers} stands={stands} reload={load} onViewPhotos={viewPhotos} />
      )}
    </div>
  );
}

export default CamerasPage;
