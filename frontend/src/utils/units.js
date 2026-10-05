/* ───────── imperial display units ─────────
   Everything is stored and computed in metres / mm (API fields end in _m / _mm); these
   convert only at the edge — what's shown, and what's typed into an input. Ground distances
   are yards, heights are feet, long distances miles, rain inches. */

const M_PER_YD = 0.9144;
const M_PER_FT = 0.3048;
const M_PER_MI = 1609.344;
const MM_PER_IN = 25.4;
const MILES_FROM_M = M_PER_MI / 2;   // fmtDist switches to miles at half a mile

const mToYd = (m) => m / M_PER_YD;
const ydToM = (yd) => yd * M_PER_YD;
const mToFt = (m) => m / M_PER_FT;
const mToMi = (m) => m / M_PER_MI;
const mmToIn = (mm) => mm / MM_PER_IN;

const fmtYd = (m) => `${Math.round(mToYd(m)).toLocaleString()} yd`;
const fmtFt = (m) => `${Math.round(mToFt(m)).toLocaleString()} ft`;
const fmtDist = (m) => (m >= MILES_FROM_M ? `${mToMi(m).toFixed(1)} mi` : fmtYd(m));
const fmtRain = (mm) => `${mmToIn(mm).toFixed(2)} in`;

// Value for a yards <input> bound to a metres field ("" stays "").
const ydInput = (m) => (m === "" || m == null ? "" : Math.round(mToYd(+m)));
// Metres to store from a yards <input> value ("" → null).
const ydFromInput = (v) => (v === "" || v == null ? null : ydToM(+v));

export { mToYd, ydToM, mToFt, mToMi, mmToIn, fmtYd, fmtFt, fmtDist, fmtRain, ydInput, ydFromInput };
