import { Plus, Mountain, Eye, EyeOff, Edit3, Trash2 } from "lucide-react";
import { degToCompass } from "../utils/compass.js";
import Empty from "../components/ui/Empty.jsx";
import MiniMap from "../components/MiniMap.jsx";

function StandsPage({ stands, onAdd, onEdit, onToggle, onDelete }) {
  return (
    <div className="list-page">
      <div className="list-header">
        <h1>Stands</h1>
        <button className="btn btn-primary" onClick={onAdd}><Plus size={15} /> Add stand</button>
      </div>
      {!stands.length && <Empty>No stands yet — click "Add stand" to place one on the map.</Empty>}
      <div className="list-grid">
        {stands.map((s) => (
          <div key={s.id} className="list-card" style={!s.is_active ? { opacity: 0.55 } : undefined}>
            <div className="list-card-map">
              <MiniMap kind="stand" feature={{ lat: s.lat, lon: s.lon }} height={110} />
            </div>
            <div className="list-card-body">
              <div className="list-card-name">
                {s.name || "Unnamed stand"}
                {!s.is_active && <span className="cam-stub-badge" style={{ marginLeft: 6 }}>inactive</span>}
              </div>
              <div className="list-card-sub">
                {(+s.lat).toFixed(4)}, {(+s.lon).toFixed(4)}
                {s.terrain && <> · {s.terrain.elevation}m · drains {degToCompass(s.terrain.drainage_deg)}</>}
                {!s.terrain && s.downhill_deg != null && <> · downhill {degToCompass(s.downhill_deg)}</>}
                {s.deer_approach_deg != null && <> · deer from {degToCompass(s.deer_approach_deg)}</>}
                {s.visibility_m ? <> · visibility {Math.round(s.visibility_m)}m</> : null}
              </div>
            </div>
            <div className="list-card-actions">
              {s.terrain && <Mountain size={13} color="var(--green)" title={"terrain: " + s.terrain.source} />}
              <button className="icon-btn" title={s.is_active ? "Disable stand" : "Enable stand"}
                onClick={() => onToggle(s)}>
                {s.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
              </button>
              <button className="icon-btn" onClick={() => onEdit(s)}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={() => onDelete(s.id)}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default StandsPage;
