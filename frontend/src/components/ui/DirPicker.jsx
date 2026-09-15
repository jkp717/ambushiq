import { DIRS, degToCompass, compassToDeg } from "../../utils/compass.js";

function DirPicker({ label, value, onChange, allowNull }) {
  return (
    <div>
      <div style={{ fontSize: 13, color: "var(--sub)", marginBottom: 6 }}>
        {label}{value != null && <strong style={{ color: "var(--txt)" }}> · {degToCompass(value)}</strong>}
      </div>
      <div className="grid-dir">
        {allowNull && <button className={"chip"+(value==null?" on":"")} onClick={() => onChange(null)}>none</button>}
        {DIRS.map((d) => <button key={d} className={"chip"+(value===compassToDeg(d)?" on":"")} onClick={() => onChange(Math.round(compassToDeg(d)))}>{d}</button>)}
      </div>
    </div>
  );
}

export default DirPicker;
