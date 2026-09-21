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
     px, py         the device's position
     width          map width; visHeight = map height minus whatever the bottom sheet covers
     reserveBottom  strip along the bottom kept free for the time-sheet tab
     obstacles      screen rects (map controls) the arrow must stay clear of
   The arrow sits where the line from the middle of the map to the device leaves the map, so it points
   the way you would pan and slides smoothly around the edge as the map moves: a device due south is at
   the bottom center, and panning east swings it toward the lower left. If a control is in the way it
   moves to the nearest free spot on the edge. `angle` is degrees clockwise from north (up), pointing
   from the arrow to the device. */
function placeEdgeArrow({ px, py, width, visHeight, obstacles = [], reserveBottom = 0, half = 18, pad = 10, step = 8 }) {
  if (px >= 0 && px <= width && py >= 0 && py <= visHeight) return null;   // device is on screen

  const inset = half + pad;
  const left = inset, top = inset;
  const right = Math.max(left, width - inset);
  const bottom = Math.max(top, visHeight - inset - reserveBottom);
  const cx = (left + right) / 2, cy = (top + bottom) / 2;

  // scale the center-to-device vector so it just reaches the edge of the usable rectangle
  const dx = px - cx, dy = py - cy;
  const tx = dx !== 0 ? (right - left) / 2 / Math.abs(dx) : Infinity;
  const ty = dy !== 0 ? (bottom - top) / 2 / Math.abs(dy) : Infinity;
  const t = Math.min(tx, ty);
  let x = Number.isFinite(t) ? cx + dx * t : cx;
  let y = Number.isFinite(t) ? cy + dy * t : cy;

  if (hitsObstacle(x, y, half, obstacles)) {
    let best = null, bestD = Infinity;
    for (const [sx, sy] of perimeter(left, top, right, bottom, step)) {
      if (hitsObstacle(sx, sy, half, obstacles)) continue;
      const d = dist2(sx, sy, x, y);
      if (d < bestD) { bestD = d; best = [sx, sy]; }
    }
    if (best) [x, y] = best;   // else nowhere is free: keep the ideal spot
  }

  const angle = (Math.atan2(px - x, -(py - y)) * 180) / Math.PI;
  return { x, y, angle };
}

export { placeEdgeArrow, hitsObstacle, perimeter };
