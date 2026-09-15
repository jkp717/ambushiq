function Field({ label, children }) {
  return <label style={{ display: "block" }}><span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>{label}</span>{children}</label>;
}

export default Field;
