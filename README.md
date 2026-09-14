# AmbushIQ — self-hosted

Ranks your hunting stands by wind, terrain-driven thermals, and scent direction.
Each stand pulls a real USGS elevation grid and maps cold-air drainage channels
(D8 flow accumulation) so dawn/dusk thermal calls are based on your actual terrain.

Runs as a Docker stack on your Debian server: a FastAPI app + PostgreSQL. The app
is published on a local host port for your **existing nginx reverse proxy** to
handle HTTPS and public traffic. All elevation and forecast calls happen
server-side, so there's no browser sandbox to fight — your server reaches USGS and
Open-Meteo directly.

## What's in the box

- **app** — FastAPI backend (terrain analysis, scoring engine, REST API) that also
  serves the built React frontend as static files. Published on a host port.
- **db** — PostgreSQL 17. Stores your stands and caches each stand's terrain grid,
  so re-ranking is instant and terrain is only fetched once per location.

HTTPS, certificates, and public routing are handled by your nginx proxy, not here.

## Prerequisites

1. A Debian server (amd64) with Docker Engine + the Compose plugin:
   ```sh
   sudo apt update && sudo apt install -y docker.io docker-compose-v2
   sudo systemctl enable --now docker
   ```
2. Your existing nginx reverse proxy (for TLS + domain).

## Setup

```sh
cp .env.example .env
nano .env            # set APP_TOKEN, POSTGRES_PASSWORD; adjust APP_BIND/APP_PORT if needed
```

Generate a strong DB password (and optional access key):
```sh
openssl rand -base64 24   # use for POSTGRES_PASSWORD
openssl rand -base64 24   # use for APP_TOKEN (or leave blank if using external auth)
```

> **Authentication note**: `APP_TOKEN` can be left blank or set to `unused` if authentication will be handled by another process (such as Authentik, Authelia, or Cloudflare Access forward auth). When unset or `unused`, the app automatically disables its internal login prompt. If `APP_TOKEN` is set to a secret key, the app requires this access key on all API calls and prompts for it in the web UI.

Build and start:
```sh
docker compose up -d --build
```

The app is now listening on `127.0.0.1:8000` (default). Confirm it's up:
```sh
curl -s http://127.0.0.1:8000/api/health
# {"ok":true,"auth_required":true,"version":"2.21.0"}  (or "auth_required":false if token is blank/unused)
```

## Day-to-day

| Action | Command |
|---|---|
| View logs | `docker compose logs -f app` |
| Restart | `docker compose restart` |
| Update after code changes | `docker compose up -d --build` |
| Stop | `docker compose down` |
| Stop and wipe the database | `docker compose down -v` *(deletes your stands)* |

## How it works

- **Add a stand**: enter a name + lat/lon, then **Analyze terrain**. The backend
  samples a 40×40 elevation grid (~800 m box) from USGS 3DEP (falls back to
  Open-Meteo), computes slope/aspect and the cold-air drainage direction, and
  caches the result in Postgres.
- **Choose a sit**: the app pulls a 3-day forecast for your land and offers each
  morning/evening window. Pick one and your stands are ranked.
- **Ranking** weighs scent safety (does your combined wind+thermal scent cone blow
  away from where deer approach), thermal phase (sinking down the drainage at
  dawn/dusk, rising mid-day), and wind steadiness.
- **Manual mode**: punch in wind direction/speed/gust and time of day to rank
  without a forecast — useful as a fallback or for "what if" checks.

## Backups

Your data lives in the `dbdata` Docker volume. To back it up:
```sh
docker compose exec db pg_dump -U ambush ambushiq > ambushiq-backup.sql
```
Restore into a fresh stack:
```sh
cat ambushiq-backup.sql | docker compose exec -T db psql -U ambush ambushiq
```

## Security notes

- When `APP_TOKEN` is set, all API endpoints (except `/api/health`) require the `APP_TOKEN`
  bearer key, preventing unauthorized visitors from reading or editing your stands.
- If authentication is handled upstream (e.g. Authentik forward auth), leave `APP_TOKEN` blank
  or set to `unused`. The app will trust upstream requests without prompting for a key.
- Postgres is only reachable inside the Docker network — not published to the host.
- With the default `APP_BIND=127.0.0.1`, the app port isn't exposed beyond
  loopback; only your nginx proxy can reach it.
- Keep `.env` out of version control (`.gitignore` already excludes it).

## Notes & limits

- USGS 3DEP is US-only; outside the US it uses the Open-Meteo global grid (coarser).
- This is **terrain analysis driving smarter rules**, not airflow simulation. It
  nails where drainages run and where cold air pools, but it won't model swirl off
  a specific field edge or thicket — that last bit still comes from your wind
  checker in the stand.

## Property map (v2.1)

The main page now shows a topographic map of your whole property:

- **Basemap**: USGS topo tiles (contours + shaded relief), with an Imagery+Topo
  layer toggle in the map's top-right corner. Tiles load client-side from USGS.
- **Per-stand indicators**: a solid navy arrow for **wind** direction, a dashed
  blue arrow for **thermal** drift, and an amber arrow for the stored **deer
  approach**. The best-ranked stand for the selected hour gets a red ring.
- **Day picker + hourly slider**: pick a day, then scrub hour by hour. The wind
  and thermal arrows rotate live, and the selected hour also drives the ranked
  "Best stand" list below — they stay in sync.
- **Draw tools**: add **bedding** and **food** zones (click to drop) and trace
  **deer corridors** (click points along the path, then Finish). These persist
  in the database and can be deleted from the chips under the map.
- **Layer toggles**: show/hide wind, thermal, deer, corridors, and zones to keep
  the view readable.

Leaflet (the map library) loads from a CDN in `index.html`; no extra npm
dependency. New API endpoints: `/api/zones`, `/api/corridors`, `/api/hours`,
`/api/map/conditions`. The database gains `zones` and `corridors` tables, created
automatically on startup — your existing stands are untouched.

## Deer movement day rating (v2.7)

Each forecast day gets a 1–5 deer rating (shown on the Map page under the date
picker) estimating **daytime** movement — when you can catch deer moving in legal
light. Tap it to expand the factor breakdown.

The model is grounded in peer-reviewed findings and Arkansas reproduction data:

- **Rut / season** is the dominant driver (partial-migration and fractal-path
  movement studies). Modeled as a calendar curve for central Arkansas — peak
  breeding ~Dec 5 (Wilson & Sealander / AGFC), with the best daylight cruising in
  the seeking/chasing weeks beforehand. Acts as a multiplier on weather.
- **Barometric pressure** — high and/or rapidly-changing pressure correlates with
  daylight activity spikes (EKU Taylor Fork study). Sweet spot ~30.0–30.4 inHg.
- **Wind** — moderate wind *increases* daytime buck movement (scenting); calm and
  very high wind are mildly suppressive. Modeled as a curve peaking ~9 mph.
- **Rain** — heavy rain strongly suppresses movement; partially blunted by high wind.
- **Temperature** — treated as a daytime *shift* factor, not a volume factor: a day
  cooler than the recent baseline (a front) pushes movement into daylight; a warm
  spell shifts it to night.
- **Moon phase — deliberately excluded.** MSU "Lunar Legends" found no statistically
  significant effect on buck activity; including it would add noise.

Weather inputs are daytime (sunrise–sunset) aggregates from Open-Meteo. The rut peak
date is currently fixed for central Arkansas; it can be made configurable later.
The factor weights are a reasoned synthesis of the cited research, not coefficients
lifted verbatim from any single paper — tune against what you observe on your land.

## v2.15 — Trail cameras, background sync, configurable rut

- **Trail cameras**: connect cameras in Settings → Trail Cameras via a 3-step wizard
  (brand → credentials → pair to a stand). SpyPoint has a working integration; the
  other brands (Reveal, Moultrie, Stealth Cam, Browning, Spartan) are scaffolded but
  not yet wired to real endpoints — they save but won't sync until implemented.
  Credentials are encrypted at rest with a key derived from POSTGRES_PASSWORD.
- **Background scheduler** (APScheduler): syncs cameras every N minutes (configurable),
  runs photos through MegaDetector to keep only real animal detections, and records
  daylight sightings. A nightly 3 AM job deletes photos older than the retention window
  while keeping the sighting records for model tuning.
- **Camera boost**: stands with recent (72h) daylight camera sightings matching the
  current hunt period get a positive-only ranking boost, capped by max_camera_boost_pct.
  Never penalizes stands without photos.
- **Configurable rut date**: set your regional breeding-peak month/day in Settings →
  Daily Rating (central AR ≈ Dec 5, north AR ≈ Nov 13).
- **Score breakdowns**: stand rank cards and the daily rating expand to show itemized
  factor contributions.
- **Settings** reorganized into three tabs: Best Stand, Daily Rating, Trail Cameras.

### Notes for operators
- MegaDetector pulls PyTorch + a model download — the image is much larger and the
  first build is slow. Set DETECTOR_MODE=fallback in .env to skip detection (every
  photo counts as a low-confidence sighting) if it's too heavy on your host.
- Camera JPEGs are saved to the user-configured storage directory (defaults to `/app/data/camera_images` in the `camera_images` Docker volume) organized as `[directory]/[Camera Brand]/[Camera Name]/`.

## v2.19 — Thermal coherence model

Real anabatic (upslope) airflow is physically strongest near solar noon, but in
practice wind dominates scent through the middle of the day — thermals only
reliably drive scent right after sunrise and right before sunset. The scoring
engine now models this by splitting thermal influence into two parts:

- **Potential** — the raw, solar-driven strength of upslope/downslope airflow
  (unchanged from before).
- **Coherence** — how much of that potential actually survives into the net
  scent direction, instead of being overwhelmed by wind or midday convective
  mixing. Coherence decays continuously with wind speed (rather than a blunt
  on/off cutoff), plus an extra discount during the sun-driven "rising" phase
  to reflect that thermals are least reliable mid-day even in calm wind.

The wind fade point, fade sharpness, and midday mixing discount are all
tunable in **Settings → Daily Rating → Thermal model**, with an info icon next
to each explaining what it changes. Values are stored per-deployment in the
database.

## v2.20 — Corridor width, stand visibility, camera & map polish

- **Corridor width**: a corridor can now have a width (a travel-zone buffer)
  instead of being scored as an infinitely thin line. A stand inside that
  buffer gets full proximity credit; once outside it, distance is measured
  from the buffer's edge rather than the raw center-line.
- **Stand visibility**: each stand can set its own visibility/cover radius,
  overriding the corridor's or global falloff just for that stand — a stand in
  open hardwoods can "see" a corridor from farther away than one boxed into
  thick cover.
- **Camera discovery**: re-connecting a camera brand account now shows a
  checkbox per discovered camera. Previously-removed ("skipped") cameras are
  unchecked by default but can be checked to bring them back, instead of being
  permanently excluded from every future discovery run.
- **Map UI**: the hour slider now paints its morning/midday/evening color band
  directly on the track (with the thumb rendered on top), adds hour/15-minute
  tick marks, and shows a tooltip with the selected time while hovering or
  dragging. The weather pills (cloud/temp/wind) moved off the cramped top bar
  into a floating card in the map's bottom-right corner. The layer-toggle
  button now uses a plus icon.
- **Fixes**: editing a corridor or zone from the map now switches to the
  correct Zones sub-tab instead of leaving the edit modal on a hidden tab; the
  app shell (`index.html`) is now served with no-cache headers while the
  hashed JS/CSS bundles cache aggressively, so browsers (phones especially)
  pick up new versions right after a deploy instead of caching a stale shell
  indefinitely.

## v2.21 — Deer species classification

Trail-camera sightings now go through a species classifier on top of the
existing MegaDetector animal-detection step, so the camera boost reflects
actual deer activity instead of "any animal that triggered the camera":

- Each photo's tightest animal crop (from MegaDetector) is classified by
  **DFNE** (Deepfaune–New England, via PytorchWildlife) into one of 24
  North-American species, including white-tailed deer.
- The trail-camera ranking boost now only counts sightings **confirmed as
  white-tailed deer** — a crow, raccoon, coyote, etc. no longer boosts a
  stand's score. Non-deer sightings are still recorded and shown in the camera
  log, labeled with their species.
- A **"Reclassify existing photos"** button on the Cameras page backfills
  species for sightings recorded before this feature existed, as long as the
  original photo is still saved on disk (within your `image_retention_days`
  window).
- MegaDetector and the species classifier are unloaded from memory after each
  sync batch (and only ever load when there's an actual new photo to process),
  to keep the memory footprint low on modest, GPU-less hardware.
- Buck/doe (sex) classification isn't included — no lightweight pretrained
  model does this reliably; this is species identification only.
