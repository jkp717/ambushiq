import { Eye, EyeOff } from "lucide-react";

function LayerChip({ on, onClick, color, label, dashed, dot }) {
  return (
    <button onClick={onClick} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: on ? 1 : 0.45 }}>
      {dot
        ? <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: color }} />
        : <span style={{ display: "inline-block", width: 16, height: 0, borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}` }} />
      }
      {label}{on ? <Eye size={12} /> : <EyeOff size={12} />}
    </button>
  );
}

export default LayerChip;
