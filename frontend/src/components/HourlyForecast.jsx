import { useEffect, useRef } from "react";
import { ArrowUp } from "lucide-react";
import { WX_ICONS } from "../utils/wxIcons.js";
import { sitAt } from "./HuntFlags.jsx";

const GUST_SHOWN_MPH = 8;   // gusts at least this far above the wind get their own line
const TREND_ARROW = { rising: "↗", falling: "↘", steady: "→" };

/*
  The map shelf's hourly forecast: one card per hour of the selected day (from /hours, each hour's `wx`),
  swiped sideways. The slider's hour is highlighted and kept in view; tapping a card moves the slider
  there (`onPick(position in the day)`). Hours outside the morning and evening sits are dimmed.
*/
function HourlyForecast({ day, selectedIndex, onPick }) {
  const stripRef = useRef(null);
  const pos = day ? day.hours.findIndex((hr) => hr.index === selectedIndex) : -1;
  useEffect(() => {
    const strip = stripRef.current;
    const card = strip?.children[pos];
    if (!card || strip.scrollWidth <= strip.clientWidth) return;
    // scrolls the strip only (scrollIntoView could also move the page or the sheet)
    strip.scrollTo({ left: Math.max(0, card.offsetLeft - (strip.clientWidth - card.offsetWidth) / 2), behavior: "smooth" });
  }, [pos, day?.day]);
  if (!day) return null;
  return (
    <div className="hourly-strip" ref={stripRef} role="listbox" aria-label="Hourly forecast">
      {day.hours.map((hr, i) => {
        const wx = hr.wx || {};
        const Icon = WX_ICONS[wx.icon];
        const gust = wx.gust_mph != null && wx.wind_mph != null && wx.gust_mph - wx.wind_mph >= GUST_SHOWN_MPH;
        const cls = "hourly-card" + (i === pos ? " on" : "") + (sitAt(day, hr.hour) ? "" : " off-sit");
        return (
          <button key={hr.index} type="button" role="option" aria-selected={i === pos} className={cls} onClick={() => onPick(i)}>
            <span className="hourly-card-time">{hr.label}</span>
            <span className="hourly-card-wx">{Icon && <Icon size={18} />}</span>
            <span className="hourly-card-temp">{wx.temp_f != null ? `${wx.temp_f}°` : "–"}</span>
            <span className="hourly-card-wind" title={wx.wind_dir != null ? `Wind from ${wx.wind_dir}°` : undefined}>
              {/* the arrow points where the wind blows to (wind_dir is where it comes from) */}
              {wx.wind_dir != null && <ArrowUp size={12} style={{ transform: `rotate(${wx.wind_dir + 180}deg)` }} />}
              {wx.wind_mph != null ? `${wx.wind_mph} mph` : "–"}
            </span>
            <span className="hourly-card-gust">{gust ? `G ${wx.gust_mph}` : " "}</span>
            <span className="hourly-card-rain">{wx.rain_in ? `${wx.rain_in.toFixed(2)}″` : "–"}</span>
            <span className="hourly-card-pres" title={wx.pressure_change_inhg != null ? `${wx.pressure_change_inhg > 0 ? "+" : ""}${wx.pressure_change_inhg} inHg over 3 hr` : undefined}>
              {wx.pressure_inhg != null ? `${wx.pressure_inhg.toFixed(2)} ${TREND_ARROW[wx.pressure_trend] || ""}` : "–"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default HourlyForecast;
