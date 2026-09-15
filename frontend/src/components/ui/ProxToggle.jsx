import { Eye, EyeOff } from "lucide-react";

function ProxToggle({ on, color, label, icon: Icon, onClick }) {
  return (
    <button onClick={onClick} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 5, opacity: on ? 1 : 0.45, borderColor: on ? color : "var(--bord)" }}>
      <Icon size={13} color={color} />{label}{on ? <Eye size={12} /> : <EyeOff size={12} />}
    </button>
  );
}

export default ProxToggle;
