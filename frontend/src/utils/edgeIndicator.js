/* Placement math for the "your location is off-screen" arrow. Pure functions, all in one pixel space
   (the map body: origin top-left, y down). */

const dist2 = (ax, ay, bx, by) => (ax - bx) ** 2 + (ay - by) ** 2;

// Is a square of half-size `half` centered on (x, y) overlapping any obstacle rect {left, top, right, bottom}?
function hitsObstacle(x, y, half, obstacles) {
  return obstacles.some((o) => x + half > o.left && x - half < o.right && y + half > o.top && y - half < o.bottom);
}

// Points spaced `step` px apart around the rectangle's perimeter (corners included).
function perimeter(left, top, right, bottom, step) {
  const pts = [];
  for (let x = left; x < right; x += step) { pts.push([x, top], [x, bottom]); }
  for (let y = top; y < bottom; y += step) { pts.push([left, y], [right, y]); }
  pts.push([right, top], [right, bottom], [left, bottom]);
  return pts;
}

/* Where to draw the arrow, or null when the device is inside the visible map.
     px, py     the device's position
     width      map width; visHeight = map height minus whatever the bottom sheet covers
     obstacles  screen rects (map controls) the arrow must stay clear of
   The arrow goes on the inset rectangle's edge at the allowed point nearest the device, so a device to
   the south-east lands toward the lower right. `angle` is degrees clockwise from north (up), pointing
   from the arrow to the device. */
function placeEdgeArrow({ px, py, width, visHeight, obstacles = [], half = 18, pad = 10, step = 8 }) {
  if (px >= 0 && px <= width && py >= 0 && py <= visHeight) return null;   // device is on screen

  const inset = half + pad;
  const left = inset, top = inset;
  const right = Math.max(left, width - inset), bottom = Math.max(top, visHeight - inset);

  // the unobstructed best spot is simply the device position clamped into the inset rectangle
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  let x = clamp(px, left, right), y = clamp(py, top, bottom);

  if (hitsObstacle(x, y, half, obstacles)) {
    let best = null, bestD = Infinity;
    for (const [cx, cy] of perimeter(left, top, right, bottom, step)) {
      if (hitsObstacle(cx, cy, half, obstacles)) continue;
      const d = dist2(cx, cy, px, py);
      if (d < bestD) { bestD = d; best = [cx, cy]; }
    }
    if (best) [x, y] = best;   // else nowhere is free: fall back to the clamped spot
  }

  const angle = (Math.atan2(px - x, -(py - y)) * 180) / Math.PI;
  return { x, y, angle };
}

export { placeEdgeArrow, hitsObstacle, perimeter };
