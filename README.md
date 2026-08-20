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
# {"ok":true,"auth_required":true,"version":"2.15.0"}  (or "auth_required":false if token is blank/unused)
```

## Wire up nginx

`nginx.example.conf` is a ready server block — copy it into your proxy
(`/etc/nginx/sites-available/`, symlink into `sites-enabled/`), then edit:

- `server_name` → your domain
- `ssl_certificate` / `ssl_certificate_key` → your cert paths
- `proxy_pass` upstream → `127.0.0.1:8000` if nginx is on this host, or the Docker
  host's LAN IP if nginx is on a different machine

```sh
sudo nginx -t && sudo systemctl reload nginx
```

Open `https://yourdomain`. If `APP_TOKEN` is set, enter your access key to unlock; if authentication is handled upstream, you will be taken directly to the app.

### Where nginx lives

- **Same host as Docker** (common): keep `APP_BIND=127.0.0.1`. The port is only on
  loopback, and nginx proxies to `127.0.0.1:8000`.
- **Different host**: set `APP_BIND=0.0.0.0` in `.env`, re-run `docker compose up -d`,
  point `proxy_pass` at the Docker host's IP, and firewall port 8000 so only the
  proxy host can reach it.

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
