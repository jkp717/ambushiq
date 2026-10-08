// Stand map-pin style: the type (icon in the pin head) and the pin color. Display only — neither
// affects scoring. Shared by the stand editor and the map.

export const STAND_TYPES = [
  { key: "tree", label: "Tree stand" },
  { key: "blind", label: "Ground blind" },
  { key: "spot", label: "Spot" },
];

// First entry is the default (null color on a stand) — the original stand red.
export const STAND_COLORS = ["#A32D2D", "#F2C200", "#EF6C00", "#1976D2", "#2E7D32", "#7B1FA2", "#D81B60", "#FFFFFF"];

export const standColor = (stand) => stand?.color || STAND_COLORS[0];

// Dark icon on light pins (yellow, white), white icon on everything else.
export function pinFg(hex) {
  const n = parseInt(String(hex).slice(1), 16);
  const lum = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.6 ? "#1A1A1A" : "#FFFFFF";
}

// SVG markup for a stand type's icon in a 24×24 box, drawn in `fg`; `bg` (the pin color) cuts the
// blind's window out of its tent. `bg` goes in a style attribute so a CSS var() works there too.
export function standGlyph(type, fg, bg) {
  if (type === "blind") {
    return `<path d="M2.5 20.5 L12 4 L21.5 20.5 Z" fill="${fg}" stroke="${fg}" stroke-width="1.5" stroke-linejoin="round"/>
      <rect x="8.5" y="12" width="7" height="3.2" rx="0.6" style="fill:${bg}"/>`;
  }
  if (type === "spot") {
    return `<g fill="none" stroke="${fg}" stroke-width="2" stroke-linecap="round">
        <circle cx="12" cy="12" r="6.5"/><path d="M12 2.5v4.5M12 17v4.5M2.5 12H7M17 12h4.5"/>
      </g><circle cx="12" cy="12" r="1.8" fill="${fg}"/>`;
  }
  // tree: ladder stand — platform, seat back and shooting rail on top of a ladder
  return `<g fill="none" stroke="${fg}" stroke-linecap="round">
      <path d="M8.5 22V9M14.5 22V9M8.5 13h6M8.5 17h6M8.5 21h6" stroke-width="1.8"/>
      <path d="M5.5 9h12" stroke-width="2.4"/>
      <path d="M17 9V3.5M5.5 9V5.5h7" stroke-width="1.8"/>
    </g>`;
}
