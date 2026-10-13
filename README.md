# AmbushIQ

**Know which stand to hunt before you leave the truck.**

AmbushIQ is a self-hosted hunting planner. It ranks your stands for every sit based on the wind, the thermals on your own terrain, and where the deer actually are. It reads the real elevation of your property to work out where cold air drains at dawn and where thermals rise by mid-morning. It combines that with the hour-by-hour forecast to show where your scent will go. Then it adds what your trail cameras are seeing and what you've scouted on the ground. The result is a clear answer to the question every hunter asks: *where should I sit today?*

It runs on your own server, keeps your spots private, and installs on your phone like a native app, with maps and forecasts that keep working where there's no signal.

---

## Features

### Stand ranking that understands your land
- **Every sit, ranked.** Each morning, midday and evening window gets a ranked list of your stands, with the reasons behind every score.
- **Scent cones from wind plus thermals.** The direction your scent travels blends the forecast wind with the thermal airflow on your terrain, and stands that blow scent onto deer approaches are flagged.
- **Terrain-driven thermals.** USGS 3DEP elevation is analyzed across the whole property: slope, aspect, cold-air drainage, and the morning and evening switch between downslope and upslope flow.
- **Lee-side eddies.** The app finds where wind curls behind ridges and swirls your scent, and explains why.
- **Proximity scoring.** Stands earn credit for being near bedding, food, corridors and fresh sign. Corridor widths, cover around each stand, and season weighting are all adjustable.

### Deer movement forecast
- **A daily 1–5 movement rating** for the next two weeks, built on published deer research: the rut, barometric pressure, wind, rain and cold fronts.
- **Your rut, your region.** Set your local breeding peak and the rating follows it.
- **Factor breakdowns** show what is driving each day's score and how it changed from yesterday.
- **Moon phase is left out on purpose.** The research doesn't support it.

### A hunting map built for the woods
- **Topo, imagery and dark base maps** from USGS, Esri and MapTiler, zoomable to 20, with hillshade, contours, and woods and water overlays.
- **Live wind and thermal arrows** at every stand, in 15-minute steps, on a drag-up time slider.
- **Map layers** for:
  - Public land, from PAD-US and official Arkansas WMA boundaries
  - Off-road trails from USFS MVUM
  - Roads
  - Recreation sites
  - Drainage
  - Lee eddies
- **Draw your hunting picture:** stands with custom icons and colors, bedding and food zones, travel corridors, scrapes and rubs. You can place any of them at your current GPS location.
- **Scouting suggestions** propose terrain funnels and habitat edges worth a look, which you can color, annotate and keep.
- **Multi-select and bulk edit** for any mix of items on the map.
- **Blue-dot location,** plus an edge arrow that points the way when you're off the visible map.

### Trail cameras that do the sorting for you
- **Automatic sync** with SpyPoint and Tactacam Reveal, with the other major brands ready to connect.
- **AI photo sorting.** MegaDetector finds the animals and a species classifier names them, so a raccoon never counts as a deer.
- **Buck identification.** Tag a few bucks and AmbushIQ trains a model on your own photos to spot bucks automatically. You set the minimum accuracy before it's used.
- **Daily Activity graphs.** See sightings by hour of day or day by day, with an optional weather overlay (temperature, pressure, wind or rain) to see what gets your deer moving.
- **A filterable gallery** by camera, species, date and time, with a swipeable full-screen viewer, deer outlines, and favorites that are never auto-deleted.
- **Camera-backed rankings.** Recent daylight deer sightings boost a stand's score.

### Works the way you hunt
- **Installable phone app** (PWA) with dark mode.
- **Offline ready.** Download map areas and keep the full forecast on the phone for places with no signal.
- **Multiple properties,** each with its own map, cameras, timezone and weather.
- **Your choice of weather source:**
  - Open-Meteo and the National Weather Service, which are free
  - OpenWeatherMap, WeatherAPI.com, Visual Crossing and Tomorrow.io, using your own key
  - Automatic backfill of anything your provider doesn't report
- **Stored weather history.** Past weather is saved per property, so your camera activity can be compared against real conditions.
- **Imperial units** throughout.
- **Background jobs** for camera sync, photo cleanup and weather history, with run-now buttons and live status in Settings.

---

## Getting started

AmbushIQ runs as two Docker containers:
- **The app:** a FastAPI backend that also serves the React interface.
- **The database:** PostgreSQL 17.

Your existing reverse proxy, such as nginx, handles HTTPS and your domain.

### 1. Prerequisites
- **A Linux server** (amd64) with Docker Engine and the Compose plugin:
  ```sh
  sudo apt update && sudo apt install -y docker.io docker-compose-v2
  sudo systemctl enable --now docker
  ```
- **A reverse proxy** for TLS. A starting point is in `nginx.example.conf`.

### 2. Configure
```sh
cp .env.example .env
openssl rand -base64 24   # use for POSTGRES_PASSWORD
openssl rand -base64 24   # use for APP_TOKEN (or leave it "unused", see below)
nano .env
```

### 3. Launch
```sh
docker compose up -d --build
curl -s http://127.0.0.1:8000/api/health
```

### 4. Set up your property
1. Open the app and create a **region** for your property.
2. Add your **stands** on the Map page.
3. Run **Analyze terrain** so each stand gets its elevation and drainage model.
4. Add **zones, corridors and sign** as you scout.
5. Connect your **trail cameras** in Cameras → Setup.
6. The **Outlook** page now ranks your stands for every sit.

To put it on your phone, open the site in your phone's browser and use **Settings → Install app**, or your browser's "Add to Home Screen".

### Day-to-day commands

| Action | Command |
|---|---|
| View logs | `docker compose logs -f app` |
| Restart | `docker compose restart` |
| Update to a new version | `git pull && docker compose up -d --build` |
| Stop | `docker compose down` |
| Stop and **erase all data** | `docker compose down -v` |

---

## Advanced configuration

### Environment settings (`.env`)

| Setting | Default | What it does |
|---|---|---|
| `APP_TOKEN` | `unused` | Access key for the app. Leave it `unused` when another layer handles sign-in (see below). |
| `APP_BIND` / `APP_PORT` | `127.0.0.1` / `8000` | Where the app listens. Use `0.0.0.0` only if your proxy is on another machine, and firewall the port. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `ambush` / — / `ambushiq` | Database credentials. The password also protects stored camera and weather-service logins. |
| `CAMERA_IMAGE_PATH` | `./data/camera_images` | Where trail-camera photos are kept on the host. |
| `DETECTOR_MODE` | `megadetector` | `megadetector` filters photos with AI. `fallback` skips AI, which gives a much smaller image and counts every photo. |
| `DETECTOR_MODEL` | `MDV6-yolov10-c` | Which MegaDetector to load. `MDV5A` is lighter. |
| `DETECTOR_CONF` | `0.2` | Minimum detection confidence. Lower values catch more animals but also more false alarms. |
| `LOG_LEVEL` | `WARNING` | `INFO` adds camera-sync diagnostics and `DEBUG` gives a full trace. |

### Authentication
- **Built-in key:** when `APP_TOKEN` is set to a secret, every API call needs the key and the app asks for it at sign-in.
- **External sign-in:** if you already use Authentik, Authelia, Cloudflare Access or similar forward auth, set `APP_TOKEN=unused` and the app trusts the requests your proxy lets through.

### Weather providers
Choose a source in **Settings → Weather source**.
- **No key needed:** Open-Meteo, the default, and the National Weather Service (US only).
- **Your own key:** the other providers. Keys are encrypted at rest.
- **Secondary provider:** if your main source doesn't report solar radiation or runs short of days, it fills in hour by hour.

### Trail cameras and AI models
- **Photo detection:** the first build downloads PyTorch and the MegaDetector weights. These are cached in the `modelcache` volume, so rebuilds stay quick. On low-power hardware, `DETECTOR_MODE=fallback` skips detection entirely.
- **Detection settings:** sync interval, photo retention, the camera ranking boost and the confidence threshold are all in **Settings → Trail cameras**.
- **Buck model:** train it in **Settings → Model training**.
  1. Tag bucks and does in the photo viewer.
  2. Run **Find deer in my photos**.
  3. Train.

  The model switches on only when its measured accuracy meets your minimum.

### Tuning the models
These settings are in Settings, each with an explanation next to it:
- **Thermal model:** the wind speed where thermals fade, how sharply they fade, and the midday mixing discount.
- **Daily rating:** your rut peak date and the factor weights.
- **Proximity weights:** how much bedding, food, corridors and sign count, and the scent-safety gate.
- **Scouting suggestions:** how candidate spots are chosen.

### Backups
Everything you've entered lives in the `dbdata` volume, and photos live in `CAMERA_IMAGE_PATH`.
```sh
# back up
docker compose exec db pg_dump -U ambush ambushiq > ambushiq-backup.sql
# restore into a fresh stack
cat ambushiq-backup.sql | docker compose exec -T db psql -U ambush ambushiq
```

### Security
- **Database:** PostgreSQL is only reachable inside the Docker network.
- **App port:** with the default `APP_BIND=127.0.0.1`, only a proxy on the same host can reach the app.
- **Stored logins:** camera and weather-service credentials are encrypted at rest.
- **Secrets:** keep `.env` out of version control. It is already in `.gitignore`.

### Data sources and limits
- **Elevation:** USGS 3DEP covers the US. Elsewhere the app falls back to Open-Meteo's coarser global grid.
- **Trails layer:** covers National Forest land only.
- **State WMA boundaries:** currently Arkansas.
- **Thermals and scent:** AmbushIQ applies terrain-aware rules, not an airflow simulation. It knows where drainages run and where cold air pools, but the swirl off a particular field edge still comes from the wind checker in your stand.

### Development
- **Backend tests:** from `backend/`, run `python -m pytest tests -q`.
- **Frontend:** a Vite + React app in `frontend/`. Use `npm install && npm run dev` while developing; the Docker build runs `npm run build`.
- **Versions:** the current version is in `VERSION` and is reported by `/api/health`.
