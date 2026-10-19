import { X, RefreshCw, CheckCircle2, BatteryLow, SignalLow, HardDrive, WifiOff, Images, RefreshCcw, Camera,
  TrendingUp, MapPinOff, Server, Clock, CloudOff, Mountain, Compass, Footprints, Brain, Settings as SettingsIcon } from "lucide-react";
import { formatRelTime } from "../utils/formatters.js";
import { savedAtLabel } from "../utils/offlineData.js";
import StandLinks from "./StandLinks.jsx";

/* The alerts bell's popup: camera and system problems worth acting on (backend app.alerts). Hunt
   conditions aren't here: they belong to a day, so the day views show them. Each alert can be dismissed
   (it comes back if its condition clears and then returns) and links straight to where it's dealt with. */

// the bell's sections, matching the backend's alert groups (alerts.service.ALERT_GROUPS) and Settings → Alerts
const GROUPS = [
  { key: "camera", label: "Camera" },
  { key: "deer",   label: "Deer activity" },
  { key: "system", label: "System" },
  { key: "setup",  label: "Setup" },
];

// icon by the alert's kind (the part of its key before the first ":")
const ICONS = {
  cam_offline: WifiOff, cam_quota: Images, cam_battery: BatteryLow, cam_signal: SignalLow, cam_sd: HardDrive,
  cam_unassigned: MapPinOff, cam_sync: RefreshCcw, buck: Camera, deer_spike: TrendingUp,
  job: Server, job_late: Clock, scheduler_down: Server, weather_provider: CloudOff,
  stand_terrain: Mountain, stand_approach: Compass, property_terrain: Mountain, stale_sign: Footprints,
  buck_model: Brain,
};

function AlertRow({ alert: a, nav, onDismiss }) {
  const Icon = ICONS[a.key.split(":")[0]] || Server;
  return (
    <div className={`alert-row sev-${a.severity}${a.quiet ? " quiet" : ""}`}>
      <span className="alert-icon"><Icon size={16} /></span>
      <div className="alert-body">
        <div className="alert-title">{a.title}</div>
        {a.detail && <div className="alert-detail">{a.detail}</div>}
        <div className="alert-foot">
          {a.at && <span className="alert-time">{formatRelTime(a.at)}</span>}
          <div className="alert-links">
            {a.camera_id != null && (
              <button type="button" className="stand-link" onClick={() => nav.toCameraPhotos([a.camera_id])}>
                <Images size={13} /> Photos
              </button>
            )}
            {a.stand_id != null && (
              <StandLinks standId={a.stand_id} standNav={nav} exclude={a.camera_id != null ? "photos" : undefined}
                className="alert-stand-links" />
            )}
            {a.link === "settings" && (
              <button type="button" className="stand-link" onClick={() => nav.toView("settings")}>
                <SettingsIcon size={13} /> Settings
              </button>
            )}
          </div>
        </div>
      </div>
      <button type="button" className="alert-dismiss" aria-label={`Dismiss: ${a.title}`} title="Dismiss"
        onClick={() => onDismiss(a.key)}><X size={14} /></button>
    </div>
  );
}

function AlertsPanel({ alerts, savedAt, loading, onRefresh, onDismiss, onClose, nav }) {
  const counted = alerts.filter((a) => !a.quiet).length;
  return (
    <>
      <div className="alerts-backdrop" onClick={onClose} />
      <div className="alerts-panel" role="dialog" aria-label="Alerts">
        <div className="alerts-hd">
          <strong>Alerts</strong>
          {counted > 0 && <span className="alerts-count">{counted}</span>}
          <span style={{ flex: 1 }} />
          <button type="button" className="icon-btn" onClick={onRefresh} title="Check again" aria-label="Check again">
            <RefreshCw size={15} className={loading ? "spin" : ""} />
          </button>
          <button type="button" className="icon-btn" onClick={onClose} title="Close" aria-label="Close alerts"><X size={16} /></button>
        </div>
        {savedAt && <div className="alerts-offline">No connection. Showing alerts saved at {savedAtLabel(savedAt)}.</div>}
        {!alerts.length ? (
          <div className="alerts-empty"><CheckCircle2 size={22} /> All clear</div>
        ) : GROUPS.map(({ key, label }) => {
          const items = alerts.filter((a) => a.category === key);
          if (!items.length) return null;
          return (
            <section key={key} className="alerts-group">
              <div className="alerts-group-hd">{label}</div>
              {items.map((a) => <AlertRow key={a.key} alert={a} nav={nav} onDismiss={onDismiss} />)}
            </section>
          );
        })}
      </div>
    </>
  );
}

export default AlertsPanel;
