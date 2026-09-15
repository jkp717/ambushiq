import { AlertTriangle } from "lucide-react";

function Banner({ children }) {
  return <div style={{ background: "rgba(133,79,11,.12)", color: "var(--amber)", padding: "8px 12px", borderRadius: 8, fontSize: 13, marginBottom: 10, display: "flex", gap: 8, alignItems: "center" }}><AlertTriangle size={15} /> {children}</div>;
}

export default Banner;
