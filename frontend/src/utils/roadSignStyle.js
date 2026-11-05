// Tiny road-sign icons for the map's hover label on roads and trails (HuntMap.jsx): the shape says the type the way
// a US road sign does, tinted with the line's own map color (passed in, so the style tables in HuntMap stay the one
// source of color). Each draws in a 24×24 box and reads on the label's dark background.

const svg = (size, body) =>
  `<svg class="road-sign" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;

// kind: USGS roads (highway, secondary, connector, local) or MVUM lines (road = forest road, trail = OHV trail).
export function roadSignSvg(kind, color, size = 16) {
  if (kind === "highway") {
    // interstate shield: navy body, top band in the highway color, white rim
    const shield = "M4 3h16c.7 2.4 1.6 4.5 1.6 7.5 0 5.4-4.6 8.9-9.6 11-5-2.1-9.6-5.6-9.6-11C2.4 7.5 3.3 5.4 4 3Z";
    return svg(size, `<clipPath id="rs-i"><path d="${shield}"/></clipPath>
      <path d="${shield}" fill="#1F3F8F"/><rect x="0" y="0" width="24" height="8" fill="${color}" clip-path="url(#rs-i)"/>
      <path d="${shield}" fill="none" stroke="#fff" stroke-width="1.6"/>`);
  }
  if (kind === "secondary") {
    // US-route shield: white with a rim in the line color
    return svg(size, `<path d="M12 2.5c2 1.3 4.5 1.5 7 1 .8 4 .6 8-1.5 11.5-1.4 2.4-3.4 4-5.5 5.5-2.1-1.5-4.1-3.1-5.5-5.5C4.4 11.5 4.2 7.5 5 3.5c2.5.5 5 .3 7-1Z" fill="#fff" stroke="${color}" stroke-width="2.4" stroke-linejoin="round"/>`);
  }
  if (kind === "road") {
    // Forest Service route marker: brown, wider at the top, white rim and center stripe
    return svg(size, `<path d="M3 3.5h18l-2.6 17H5.6Z" fill="${color}" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>
      <path d="M12 7v10" stroke="#fff" stroke-width="2" stroke-linecap="round"/>`);
  }
  if (kind === "trail") {
    // OHV trail: ATV on a rounded sign in the trail color
    return svg(size, `<rect x="1.5" y="1.5" width="21" height="21" rx="4" fill="${color}" stroke="#fff" stroke-width="1.4"/>
      <g fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="7" cy="16" r="2.6"/><circle cx="17" cy="16" r="2.6"/>
        <path d="M4.5 13 7 10h10l2.5 3M10 10l1.2-3.2H14"/></g>`);
  }
  // connector / local: a road receding into the distance (edges + dashed center) on a white sign
  return svg(size, `<rect x="1.5" y="1.5" width="21" height="21" rx="4" fill="#fff"/>
    <g stroke="${color}" stroke-width="2" stroke-linecap="round" fill="none">
      <path d="M9.5 4.5 5 19.5M14.5 4.5 19 19.5"/><path d="M12 5v2.5M12 10.5v3M12 16.5v3"/></g>`);
}
