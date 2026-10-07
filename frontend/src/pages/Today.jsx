import React, { useState, useEffect, useRef } from "react";
import { Target, Plus, RefreshCw, AlertTriangle, Footprints, Wheat, Trees } from "lucide-react";
import { apiSaved, fromDevice } from "../services/api.js";
import { savedAtLabel } from "../utils/offlineData.js";
import useForegroundRefresh from "../hooks/useForegroundRefresh.js";
import { localDate, ratingScore5 } from "../utils/formatters.js";
import { fmtRain } from "../utils/units.js";
import { PERIOD_COLORS } from "../utils/periods.js";
import Banner from "../components/ui/Banner.jsx";
import PeriodKey from "../components/ui/PeriodKey.jsx";
import ProxToggle from "../components/ui/ProxToggle.jsx";
import DeerRating from "../components/DeerRating.jsx";
import DayRankCard from "../components/DayRankCard.jsx";

// Where a day's ranking is kept on the phone: one copy per day and proximity-toggle combination.
const dayKey = (day, p) => `/day/ranked/${day}/${+p.corridor}${+p.food}${+p.bedding}`;
const ALL_ON = { corridor: true, food: true, bedding: true };

function TodayPage({ stands, onGoDraw }) {
  const [days, setDays] = useState([]);
  const [deerRatings, setDeerRatings] = useState(null);
  // "Yesterday" (from actual observed weather) -- only used for the delta on
  // today's card, since today has no same-array predecessor in `deerRatings`
  // (the forward forecast window never looks backward).
  const [previousDay, setPreviousDay] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null);
  const [dayRanked, setDayRanked] = useState(null);
  const [useProx, setUseProx] = useState({ corridor: true, food: true, bedding: true });
  const [utcOffset, setUtcOffset] = useState(0);
  const [err, setErr] = useState(null);
  const [staleAt, setStaleAt] = useState(null);   // epoch seconds of the cached forecast being shown, or null when live
  const [deviceAt, setDeviceAt] = useState(null);  // when the on-phone copy being shown was saved (ms), while offline
  const foregroundTick = useForegroundRefresh();

  // Each open (and return to the foreground) refreshes the ratings, hours and every day's ranking, and
  // keeps them on the phone; with no connection the saved copies are shown instead.
  useEffect(() => {
    if (!stands.length) return;
    apiSaved("/deer-ratings").then((j) => {
      const savedAt = fromDevice(j);
      setDeviceAt(savedAt);
      setDeerRatings(j.ratings);
      setStaleAt(!savedAt && j.stale ? j.fetched_at ?? null : null);
      setPreviousDay(j.previous_day ?? null);
      // Use the property's UTC offset so "today" matches the local calendar date
      // on the property rather than the UTC date in the browser or on the server.
      const ofs = j.utc_offset_seconds ?? 0;
      setUtcOffset(ofs);
      const today = localDate(ofs);
      const hit = j.ratings.find((r) => r.day === today);
      setSelectedDay(hit ? today : j.ratings[0]?.day ?? null);
    }).catch(() => setErr("Couldn't load deer ratings."));
  }, [stands.length, foregroundTick]);

  useEffect(() => {
    if (!stands.length) return;
    let cancel = false;
    apiSaved("/hours").then((j) => {
      if (cancel) return;
      setDays(j.days || []);
      if (fromDevice(j)) return;
      // Online: save every day's ranking (default toggles) in the background, two requests at a time,
      // so any day can be opened later with no signal — not just the ones looked at now.
      const queue = (j.days || []).map((d) => d.day);
      const next = () => {
        const day = queue.shift();
        if (!day || cancel) return Promise.resolve();
        return apiSaved("/day/ranked", { key: dayKey(day, ALL_ON), method: "POST",
                                         body: JSON.stringify({ day, use_corridor: true, use_food: true, use_bedding: true }) })
          .catch(() => {}).then(next);
      };
      next(); next();
    }).catch(() => {});
    return () => { cancel = true; };
  }, [stands.length, foregroundTick]);

  const curDay = days.find((d) => d.day === selectedDay);

  useEffect(() => {
    if (!curDay) return;
    let cancel = false;
    apiSaved("/day/ranked", { key: dayKey(curDay.day, useProx), method: "POST",
                              body: JSON.stringify({ day: curDay.day, use_corridor: useProx.corridor, use_food: useProx.food, use_bedding: useProx.bedding }) })
      .then((j) => {
        if (cancel) return;
        setDayRanked(j);
        const savedAt = fromDevice(j);
        if (savedAt) setDeviceAt(savedAt);
      })
      .catch(() => { if (!cancel) setDayRanked(null); });
    return () => { cancel = true; };
  }, [curDay?.day, useProx.corridor, useProx.food, useProx.bedding]);

  if (!stands.length) {
    return (
      <div className="today-empty">
        <Target size={52} color="var(--bord2)" />
        <h2>No stands yet</h2>
        <p>Add your first stand to see your daily hunt outlook and rankings.</p>
        <button className="btn btn-primary" onClick={() => onGoDraw("stand")}><Plus size={15} /> Add first stand</button>
      </div>
    );
  }

  const selectedRating = deerRatings?.find((r) => r.day === selectedDay);
  const loadableDays = days.map((d) => d.day);
  // Prior-day comparison for the detail card's delta indicators: any day but
  // the first uses the previous entry in the same 14-day array; the first
  // (today) has no such predecessor, so it falls back to `previousDay`
  // (yesterday, rated from actual observed weather -- see the effect above).
  const selectedIndex = deerRatings?.findIndex((r) => r.day === selectedDay) ?? -1;
  const prevRating = selectedIndex > 0 ? deerRatings[selectedIndex - 1]
    : selectedIndex === 0 ? previousDay : null;

  return (
    <div className="today-page">
      {err && <Banner>{err}</Banner>}
      {deviceAt && <Banner>No connection — showing the forecast saved on this phone at {savedAtLabel(deviceAt)}.</Banner>}
      {staleAt && <Banner>Showing the cached forecast from {new Date(staleAt * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} while the weather service catches up. Reload in a minute for the latest.</Banner>}

      {/* Hero */}
      {selectedRating
        ? <HeroCard rating={selectedRating} utcOffset={utcOffset} />
        : <div className="hero-skeleton"><RefreshCw className="spin" size={20} /></div>}

      {/* 14-day strip */}
      {deerRatings && (
        <section>
          <div className="section-label">14-Day Outlook</div>
          <OutlookStrip ratings={deerRatings} selectedDay={selectedDay}
            loadableDays={loadableDays} onPick={setSelectedDay} utcOffset={utcOffset} />
        </section>
      )}

      {/* Expanded day detail */}
      {selectedRating && curDay && (
        <DayDetailPanel rating={selectedRating} prevRating={prevRating} day={curDay}
          dayRanked={dayRanked} useProx={useProx} setUseProx={setUseProx} />
      )}

      {selectedRating && !curDay && selectedRating.confidence === "low" && (
        <div className="day-detail">
          <Banner><AlertTriangle size={14} /> This day is beyond the detailed forecast window — rut phase is reliable but stand rankings aren't available.</Banner>
          <DeerRating rating={selectedRating} prevRating={prevRating} />
        </div>
      )}
    </div>
  );
}

/* ── Hero card ── */
function HeroCard({ rating, utcOffset = 0 }) {
  const r = rating.rating;
  const tone = r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const label = r >= 4 ? "Great Movement" : r === 3 ? "Moderate Movement" : r >= 2 ? "Poor Movement" : "Very Poor Movement";
  const isToday = rating.day === localDate(utcOffset);
  const inp = rating.inputs || {};
  return (
    <div className="hero-card" style={{ borderTopColor: tone }}>
      <div className="hero-date">{isToday ? "Today" : rating.label}</div>
      <div className="hero-rating">
        <span className="hero-deer">{"🦌".repeat(r)}{"·".repeat(5 - r)}</span>
        <span className="hero-score" style={{ color: tone }}>{ratingScore5(rating)}/5</span>
      </div>
      <div className="hero-label" style={{ color: tone }}>{label}</div>
      <div className="hero-rut">{rating.rut?.phase}</div>
      <div className="hero-weather">
        {inp.wind_mph     != null && <WeatherPill icon="💨" label={`${inp.wind_mph} mph wind`} />}
        {inp.pressure_inhg!= null && <WeatherPill icon="🔵" label={`${inp.pressure_inhg}″`} />}
        {inp.day_high_f   != null && <WeatherPill icon="🌡" label={`${inp.day_high_f}°F`} />}
        {inp.rain_mm      != null && inp.rain_mm > 0 && <WeatherPill icon="🌧" label={fmtRain(inp.rain_mm)} />}
      </div>
    </div>
  );
}
function WeatherPill({ icon, label }) {
  return <span className="weather-pill">{icon} {label}</span>;
}

/* ── 14-day horizontal strip ── */
function OutlookStrip({ ratings, selectedDay, loadableDays, onPick, utcOffset = 0 }) {
  const today = localDate(utcOffset);
  const scrollRef = useRef(null);
  const drag = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });

  function onMouseDown(e) {
    drag.current = { active: true, startX: e.clientX, scrollLeft: scrollRef.current.scrollLeft, moved: false };
    scrollRef.current.style.cursor = "grabbing";
  }
  function onMouseMove(e) {
    if (!drag.current.active) return;
    const dx = e.clientX - drag.current.startX;
    if (Math.abs(dx) > 4) drag.current.moved = true;
    scrollRef.current.scrollLeft = drag.current.scrollLeft - dx;
  }
  function endDrag() {
    drag.current.active = false;
    if (scrollRef.current) scrollRef.current.style.cursor = "grab";
  }

  return (
    <div className="outlook-scroll" ref={scrollRef}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove}
      onMouseUp={endDrag} onMouseLeave={endDrag}
      onClickCapture={(e) => { if (drag.current.moved) e.stopPropagation(); }}
      style={{ cursor: "grab", userSelect: "none" }}>
      <div className="outlook-strip">
        {ratings.map((r) => {
          const tone = r.rating >= 4 ? "var(--green)" : r.rating === 3 ? "var(--amber)" : "var(--red)";
          const sel = r.day === selectedDay;
          const loadable = loadableDays.includes(r.day);
          return (
            <button key={r.day}
              className={"outlook-day" + (sel ? " selected" : "") + (r.confidence === "low" ? " low-conf" : "")}
              onClick={() => onPick(r.day)} disabled={!loadable}
              title={`${ratingScore5(r)}/5 · ${r.rut?.phase}${r.confidence === "low" ? " · est." : ""}`}>
              <div className="od-label">{r.day === today ? "Today" : r.label}</div>
              <div className="od-deer">
                {Array.from({ length: 5 }, (_, i) => (
                  <span key={i} className={"od-deer-icon" + (i < r.rating ? "" : " empty")}>🦌</span>
                ))}
              </div>
              <div className="od-score" style={{ color: tone }}>{ratingScore5(r)}/5</div>
              {r.confidence === "low" && <div className="od-est">est.</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ── Expanded day detail ── */
function DayDetailPanel({ rating, prevRating, day, dayRanked, useProx, setUseProx }) {
  return (
    <div className="day-detail">
      <div className="day-detail-header">
        <div>
          <h2 style={{ fontSize: 16, margin: 0 }}>{day.label}</h2>
          <div className="day-detail-meta">
            <span>☀ {day.sunrise}</span><span>☾ {day.sunset}</span>
            {day.source && <span style={{ opacity: 0.6 }}>· {day.source}</span>}
            {rating.confidence === "low" && <span className="low-badge">est.</span>}
          </div>
        </div>
      </div>

      {rating.confidence === "low" && (
        <div className="day-low-note"><AlertTriangle size={13} /> 8+ days out — wind-based rankings are directional only.</div>
      )}

      <DeerRating rating={rating} prevRating={prevRating} />

      {dayRanked && dayRanked.ranked.length > 0 && (
        <div className="best-stands">
          <div className="best-stands-hd">
            <strong style={{ fontSize: 13.5 }}>Best Stands</strong>
            <div className="prox-row">
              <ProxToggle on={useProx.corridor} color="#A35A1B" label="Corridors" icon={Footprints} onClick={() => setUseProx((u) => ({ ...u, corridor: !u.corridor }))} />
              <ProxToggle on={useProx.food}     color="var(--green)" label="Food"     icon={Wheat}     onClick={() => setUseProx((u) => ({ ...u, food: !u.food }))} />
              <ProxToggle on={useProx.bedding}  color="#6B4FA0" label="Bedding"  icon={Trees}     onClick={() => setUseProx((u) => ({ ...u, bedding: !u.bedding }))} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10, fontSize: 12 }}>
            <PeriodKey color={PERIOD_COLORS.morning} label="Morning" />
            <PeriodKey color={PERIOD_COLORS.midday}  label="Midday" />
            <PeriodKey color={PERIOD_COLORS.evening} label="Evening" />
          </div>
          {dayRanked.ranked.map((row) => <DayRankCard key={row.stand.id} row={row} />)}
        </div>
      )}
    </div>
  );
}

export default TodayPage;
