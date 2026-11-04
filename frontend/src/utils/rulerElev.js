// The map ruler's elevation line: which measurement a fetched profile belongs to, and how it reads.

/** Identifies one measurement (both ends, ~1 m precision), so an answer is only shown for the points it was asked for. */
export const rulerKey = (a, b) => [a.lat, a.lon, b.lat, b.lon].map((v) => v.toFixed(5)).join(",");

/** "+40 ft (↑120 ↓80)" from /api/elevation/profile; just "+40 ft" when the line only climbs or only drops. */
export function elevText(p) {
  const n = (v) => Math.abs(v).toLocaleString();
  const net = `${p.net_ft > 0 ? "+" : p.net_ft < 0 ? "−" : "±"}${n(p.net_ft)} ft`;
  return p.gain_ft > 0 && p.loss_ft > 0 ? `${net} (↑${n(p.gain_ft)} ↓${n(p.loss_ft)})` : net;
}
