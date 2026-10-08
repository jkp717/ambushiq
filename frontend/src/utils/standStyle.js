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

// SVG markup for a stand type's icon in a 24×24 box, drawn in `fg`. Cut-outs (the blind's windows)
// are even-odd holes, so whatever is behind — the pin color, or a chip — shows through.
export function standGlyph(type, fg) {
  if (type === "blind") {
    // hub blind: wide body with three shooting windows
    return `<path fill="${fg}" fill-rule="evenodd" clip-rule="evenodd" d="M19.066 11.493c0 .915-.847 1.613-1.782 1.47l-3.349-.515a.454.454 0 0 1-.265-.774l4.582-4.511c.298-.294.814-.09.814.32v4.01Zm-8.198-.403L6.702 7.055c-.264-.257-.076-.695.3-.695H17.01c.375 0 .563.438.298.695l-4.165 4.035a1.648 1.648 0 0 1-2.276 0Zm-.803 1.358-3.348.514c-.936.144-1.783-.554-1.783-1.47V7.484c0-.41.516-.614.814-.32l4.582 4.511a.454.454 0 0 1-.265.774Zm10.616 7.916a.504.504 0 0 1-.123-.544C21.083 18.45 22 15.669 22 12.966c0-2.904-1.036-6.059-1.518-7.37a1.424 1.424 0 0 0-.881-.851C18.093 4.228 14.273 3 12 3 9.726 3 5.906 4.228 4.398 4.745a1.424 1.424 0 0 0-.88.85C3.035 6.908 2 10.063 2 12.966c0 2.704.917 5.485 1.442 6.855a.503.503 0 0 1-.123.544l-.245.237c-.153.147-.045.399.17.399h17.511c.216 0 .324-.252.172-.4l-.246-.236Z"/>`;
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
