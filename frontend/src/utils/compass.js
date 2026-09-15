/* ───────── compass helpers ───────── */
const DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
const degToCompass = (d) => DIRS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
const compassToDeg = (c) => DIRS.indexOf(c) * 22.5;
export { DIRS, degToCompass, compassToDeg };
