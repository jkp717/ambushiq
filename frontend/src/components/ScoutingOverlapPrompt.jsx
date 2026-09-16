import { X } from "lucide-react";
import Modal from "./ui/Modal.jsx";

function ScoutingOverlapPrompt({ count, onOverride, onMergeOnly, onCancel }) {
  return (
    <Modal onClose={onCancel}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong>Existing scouting suggestions here</strong>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
        <p style={{ fontSize: 13, color: "var(--sub)", margin: 0 }}>
          {count} existing suggestion{count === 1 ? "" : "s"} overlap this area. Replace them with a
          fresh analysis, or keep them and only add new spots?
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
          <button className="btn btn-primary" onClick={onOverride}>Override — start fresh</button>
          <button className="btn" onClick={onMergeOnly}>Only show new ones</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

export default ScoutingOverlapPrompt;
