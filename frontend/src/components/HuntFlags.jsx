import { CloudRain, Wind } from "lucide-react";

/* Hunt conditions for a day's morning and evening sit (backend forecast/hunt.py, on each /hours day as
   `hunt`): swirling winds (the wind swinging back and forth, or a lee eddy at a stand), heavy rain and
   high wind. Shown where a day is picked (the day chips, the selected day, the map's time panel), not as
   bell alerts: they're facts about that day's forecast. */

const SIT_LABEL = { morning: "Morning", evening: "Evening" };
const SIT_ICON = { morning: "☀", evening: "☾" };

const swirls = (c) => !!(c?.swirl || c?.eddy_stands?.length);

export function swirlText(c) {
  const parts = [];
  if (c.swirl) parts.push(`wind swings ${c.swirl.swings}× (${c.swirl.dirs.join(" → ")})`);
  if (c.eddy_stands?.length) parts.push(`lee eddy at ${c.eddy_stands.map((s) => s.name).join(", ")}`);
  return parts.join("; ");
}
export const rainText = (c) => `${c.rain.in_per_h} in/h at ${c.rain.at}`;
export const windText = (c) => `wind ${c.wind.mph} mph, gusts to ${c.wind.gust_mph} mph (${c.wind.at})`;

/** The ⟲ / rain / wind icons for a whole day (any sit), for the day chips. Nothing when the day is clear. */
export function HuntIcons({ hunt, size = 12 }) {
  const sits = Object.values(hunt || {});
  const swirl = sits.some(swirls), rain = sits.some((c) => c.rain), wind = sits.some((c) => c.wind);
  if (!swirl && !rain && !wind) return null;
  const said = [swirl && "swirling winds", rain && "heavy rain", wind && "high wind"].filter(Boolean).join(", ");
  return (
    <span className="hunt-icons" role="img" aria-label={`Hunt conditions: ${said}`} title={`Hunt conditions: ${said}`}>
      {swirl && <span className="hunt-swirl" style={{ fontSize: size }}>⟲</span>}
      {rain && <CloudRain size={size} />}
      {wind && <Wind size={size} />}
    </span>
  );
}

/** Which sit ("morning" / "evening") an hour of a /hours day falls in, or null. Same windows as the
 *  backend's scoring.period_windows: morning sunrise-1 to sunrise+3, evening sunset-3 to sunset (whole hours). */
export function sitAt(day, hour) {
  if (!day || hour == null) return null;
  const sr = day.sunrise_h ?? 6.5, ss = day.sunset_h ?? 19;
  if (hour >= Math.trunc(sr - 1) && hour <= Math.trunc(sr + 3)) return "morning";
  if (hour >= Math.trunc(ss - 3) && hour <= Math.trunc(ss)) return "evening";
  return null;
}

/** One sit's conditions as short coloured parts for the map's time panel chips:
 *  [{kind: "swirl" | "wind" | "rain", text}]. */
export function sitParts(c) {
  if (!c) return [];
  return [swirls(c) && { kind: "swirl", text: "⟲ Swirling" },
          c.wind && { kind: "wind", text: `💨 Gusts ${c.wind.gust_mph} mph` },
          c.rain && { kind: "rain", text: `🌧 ${c.rain.in_per_h} in/h rain` }].filter(Boolean);
}

/** The same sit in full sentences, for a chip's tooltip. */
export function sitDetail(c) {
  if (!c) return "";
  return [swirls(c) && `Swirling winds: ${swirlText(c)}`, c.wind && `High ${windText(c)}`, c.rain && `Rain ${rainText(c)}`]
    .filter(Boolean).join(". ");
}

/** The selected day's conditions, one line per flagged sit (Outlook day detail). Nothing when clear. */
export function HuntConditions({ hunt }) {
  const sits = ["morning", "evening"].filter((s) => hunt?.[s]);
  if (!sits.length) return null;
  return (
    <div className="hunt-conditions">
      <div className="hunt-conditions-hd">Hunt conditions</div>
      {sits.map((s) => {
        const c = hunt[s];
        return (
          <div key={s} className="hunt-sit">
            <span className="hunt-sit-name">{SIT_ICON[s]} {SIT_LABEL[s]}</span>
            <span className="hunt-sit-items">
              {swirls(c) && <span><b className="hunt-swirl">⟲</b> Swirling winds: {swirlText(c)}</span>}
              {c.wind && <span><Wind size={13} /> High {windText(c)}</span>}
              {c.rain && <span><CloudRain size={13} /> Rain {rainText(c)}</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}
