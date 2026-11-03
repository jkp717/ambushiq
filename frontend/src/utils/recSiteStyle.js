// Recreation-site map pins (trailheads, campgrounds, picnic sites, day-use areas): a teardrop like the
// stand pins, in the kind's color, with the kind's icon in the head. Shared by the map and its legend.
import { pinFg } from "./standStyle.js";

export const RECSITE_KINDS = [
  { key: "trailhead", label: "Trailhead", color: "#8D6E00" },
  { key: "campground", label: "Campground", color: "#2E7D32" },
  { key: "picnic", label: "Picnic site", color: "#EF6C00" },
  { key: "day_use", label: "Day-use area", color: "#1565C0" },
];
const BY_KEY = Object.fromEntries(RECSITE_KINDS.map((k) => [k.key, k]));
export const recSiteKind = (key) => BY_KEY[key] || BY_KEY.trailhead;

// SVG markup for a kind's icon in a 24×24 box, stroked in `fg`.
export function recSiteGlyph(kind, fg) {
  const g = (body) => `<g fill="none" stroke="${fg}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${body}</g>`;
  if (kind === "campground") {
    // tent with its door flap
    return g(`<path d="M12 4 3 20h18L12 4Z"/><path d="M12 12l-3.5 8M12 12l3.5 8"/>`);
  }
  if (kind === "picnic") {
    // picnic table: top, bench, splayed legs
    return g(`<path d="M4 8h16M3 14h18M8 8l-3 12M16 8l3 12"/>`);
  }
  if (kind === "day_use") {
    // sun
    return g(`<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>`);
  }
  // trailhead: signpost with two arrow boards
  return g(`<path d="M12 3v18M9 21h6"/><path d="M12 5h7l2 2.5-2 2.5h-7M12 12H5l-2 2.5L5 17h7"/>`);
}

const R = 10, HEAD = 17;   // head radius, tip-to-head-center distance (stand pins: 14, 24)
export const RECSITE_PIN_W = R * 2 + 2;
export const RECSITE_PIN_H = HEAD + R + 2;

// The pin as a standalone <svg>, tip at bottom-center; `height` scales it (defaults to actual size).
export function recSitePinSvg(key, height = RECSITE_PIN_H) {
  const { color } = recSiteKind(key);
  const fg = pinFg(color);
  const c = RECSITE_PIN_W / 2, tipY = RECSITE_PIN_H - 1, hy = tipY - HEAD;
  // straight sides from the tip to their tangent points on the head circle, then round the top
  const phi = Math.acos(R / HEAD);
  const tx = R * Math.sin(phi), ty = hy + R * Math.cos(phi);
  const width = (height * RECSITE_PIN_W) / RECSITE_PIN_H;
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${RECSITE_PIN_W} ${RECSITE_PIN_H}" style="overflow:visible;display:block">
    <path d="M ${c} ${tipY} L ${c - tx} ${ty} A ${R} ${R} 0 1 1 ${c + tx} ${ty} Z"
      fill="${color}" stroke="rgba(0,0,0,.55)" stroke-width="1.1" stroke-linejoin="round"
      style="filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))"/>
    <g transform="translate(${c - 6.6} ${hy - 6.6}) scale(0.55)">${recSiteGlyph(key, fg)}</g>
  </svg>`;
}
