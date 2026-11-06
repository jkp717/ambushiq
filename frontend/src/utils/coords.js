/* ───────── typed coordinates (map search) ─────────
   parseLatLon understands the ways people paste a point:
     36.12, -93.56      36.12 -93.56            (decimal degrees, latitude first)
     36.12N 93.56W      N36.12 W93.56           (hemisphere letters, either side, either order)
     36°07'12"N 93°33'36"W                      (degrees minutes seconds)
     N36 07.2 W93 33.6  36°07.2'N, 93°33.6'W    (degrees and decimal minutes)
     36 07 12, -93 33 36                        (DMS with spaces, a comma between the two)
   Returns { lat, lon, hemispheres } or null. `hemispheres` is true when the text said N/S/E/W, so the caller
   knows a positive longitude was meant (without letters, "36.1, 93.5" is probably a forgotten minus sign). */

const HEMI = /[NSEW]/gi;

function normalize(text) {
  return String(text || "")
    .trim()
    .replace(/[′’‘`´]/g, "'")
    .replace(/[″“”]/g, '"')
    .replace(/''/g, '"')
    .replace(/[º˚]/g, "°")
    .replace(/(\d)\s*(deg|d)\b/gi, "$1°")
    .replace(/\s+/g, " ");
}

// One coordinate: "-93 33 36", "93°33.6'W", "N36 07.2" -> { value, hemi } or null
function parsePart(part) {
  const p = part.replace(/^[\s,;]+|[\s,;]+$/g, "");
  if (!p) return null;
  const letters = p.match(HEMI) || [];
  if (letters.length > 1) return null;
  const hemi = letters.length ? letters[0].toUpperCase() : null;
  const rest = p.replace(HEMI, " ").trim();
  if (!/^[-+]?\s*\d/.test(rest) || /[^\d\s.°'"+-]/.test(rest)) return null;
  const neg = /^-/.test(rest);
  const nums = rest.match(/\d+(?:\.\d+)?/g) || [];
  if (nums.length < 1 || nums.length > 3) return null;
  // only the last number may have decimals
  if (nums.slice(0, -1).some((n) => n.includes("."))) return null;
  const [d, m = 0, s = 0] = nums.map(Number);
  if (m >= 60 || s >= 60) return null;
  let value = d + m / 60 + s / 3600;
  if (neg || hemi === "S" || hemi === "W") value = -value;
  if (neg && (hemi === "N" || hemi === "E")) return null;
  return { value, hemi };
}

function splitInTwo(t) {
  const letters = [...t.matchAll(HEMI)];
  if (letters.length === 2) {
    // letters before the numbers ("N36 07.2 W93 33.6") or after them ("36°07'N 93°33'W")
    const startsWithLetter = /^[NSEW]/i.test(t);
    const cut = startsWithLetter ? letters[1].index : letters[0].index + 1;
    return [t.slice(0, cut), t.slice(cut)];
  }
  if (letters.length) return null;
  if (t.includes(",")) {
    const parts = t.split(",");
    return parts.length === 2 ? parts : null;
  }
  if (/[°'"]/.test(t)) {
    // symbols mark where each coordinate ends: split after the first one's last symbol
    const m = t.match(/^\s*[-+]?\d+(?:\.\d+)?°(?:\s*\d+(?:\.\d+)?')?(?:\s*\d+(?:\.\d+)?")?/);
    return m ? [m[0], t.slice(m[0].length)] : null;
  }
  const nums = t.split(" ");
  return nums.length === 2 ? nums : null;   // plain "lat lon"
}

export function parseLatLon(text) {
  const t = normalize(text);
  if (!t || !/\d/.test(t)) return null;
  const halves = splitInTwo(t);
  if (!halves) return null;
  const a = parsePart(halves[0]), b = parsePart(halves[1]);
  if (!a || !b) return null;
  let lat, lon;
  const isLon = (x) => x.hemi === "E" || x.hemi === "W";
  if (isLon(a) && !isLon(b)) { lat = b.value; lon = a.value; }
  else if (isLon(a) && isLon(b)) return null;
  else if ((a.hemi === "N" || a.hemi === "S") && (b.hemi === "N" || b.hemi === "S")) return null;
  else { lat = a.value; lon = b.value; }
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon, hemispheres: !!(a.hemi || b.hemi) };
}

export const fmtLatLon = (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
