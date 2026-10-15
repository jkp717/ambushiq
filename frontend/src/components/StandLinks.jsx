import { Map as MapIcon, BarChart3, Images } from "lucide-react";

/* One-click jumps from a stand to the rest of the app: the stand on the map, its daily score in the
   Outlook, or its camera photos. `standNav` comes from App.jsx's Shell and decides which links apply
   (Score needs an active stand, Photos needs a camera on it). `exclude` leaves out the page you're on;
   `day` (ISO date) opens the map or Outlook on that day. Every app page that shows a stand offers these. */
function StandLinks({ standId, standNav, exclude, day, className = "" }) {
  if (!standNav || standId == null) return null;
  const can = standNav.can(standId);
  const links = [
    { key: "map",    label: "Map",    icon: MapIcon,   go: () => standNav.toMap(standId, day) },
    { key: "score",  label: "Score",  icon: BarChart3, go: () => standNav.toScore(standId, day) },
    { key: "photos", label: "Photos", icon: Images,    go: () => standNav.toPhotos(standId) },
  ].filter((l) => l.key !== exclude && can[l.key]);
  if (!links.length) return null;
  return (
    <div className={"stand-links " + className} onClick={(e) => e.stopPropagation()}
      onTouchStart={(e) => e.stopPropagation()}>
      {links.map(({ key, label, icon: Icon, go }) => (
        <button key={key} type="button" className="stand-link" onClick={go}
          title={key === "map" ? "Show this stand on the map" : key === "score" ? "See this stand's daily score" : "See this stand's camera photos"}>
          <Icon size={13} /> {label}
        </button>
      ))}
    </div>
  );
}

export default StandLinks;
