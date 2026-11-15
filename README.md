# PLEIADES: burning activity calendar (NASA Space Apps 2026)

Challenge: *Harmonization of MODIS and VIIRS Hot Spots.* PLEIADES merges the two
active-fire records into one comparable **burning activity calendar** for any area
of interest, with typical seasons, critical periods and unusual weeks.

## Run it

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_data        # SYNTHETIC demo data, clearly labelled in the UI
python manage.py createsuperuser       # optional, for /admin/
python manage.py runserver
python manage.py test                  # run the test suite
```

Open http://127.0.0.1:8000/ then **Fire Calendar**.

Requires Python 3.12+ (Django 6.1). The map needs internet access for Leaflet (cdnjs) and the basemap tiles (Esri, with an automatic OpenStreetMap fallback). See `MAP_FIX_REPORT.md` for the Fire Calendar map fix.

## Use real NASA data

Download MODIS and VIIRS active-fire CSVs from https://firms.modaps.eosdis.nasa.gov/download/
and import them (the sensor is detected from the columns):

```bash
python manage.py import_firms --purge-demo MODIS_archive.csv VIIRS_SNPP_archive.csv
```

The banner on the calendar disappears once only real data remains.

## Accounts

- **Guest**: "Continue as guest" on the login and register pages. Full access to the public calendar, no saved areas.
- **Member**: free account. Profile (name, role in the field, organization), saved areas, password change.
- **Operator**: set by an administrator in `/admin/` (never self-assigned).

Profile pages live under `/accounts/profile/` with a side menu: Overview, Edit profile, Saved areas, Password.

## Method (short)

1. Snap detections to a 0.05 degree grid (about 5 km), count *active cell-days* instead of pixels.
2. Cross-calibrate: factor = MODIS cell-days / VIIRS cell-days over the overlap years, per area.
3. Harmonized daily index = rescaled VIIRS where it exists, MODIS before.
4. Weekly bins, leave-one-year-out climatology, z-scores for unusual weeks, merged critical periods.

Code: `satellite/harmonize.py` (pure Python, unit-tested). API: `/api/fire/summary/`, `/api/fire/cells/`.

## Limits

Values are MODIS-equivalent active cell-days, not hectares burned. Hotspots are detected heat, not confirmed damage.
Cloud cover and overpass times can hide fires. Areas crossing the antimeridian are not supported.
For production set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0` and `DJANGO_ALLOWED_HOSTS`.


## Satellite module: NASA FIRMS, MODIS + VIIRS harmonization

Setup
```
pip install -r requirements.txt            # adds `requests`
cp .env.example .env                        # then put your free key in FIRMS_MAP_KEY
python manage.py migrate
python manage.py import_firms --check-key
python manage.py import_firms --api --family MODIS --family VIIRS_SNPP --family VIIRS_NOAA20 \
        --bbox 20.84 -17.06 35.74 -1.54 --start 2024-06-01 --end 2024-09-30
python manage.py harmonize_hotspots --scope real
python manage.py runserver                  # http://127.0.0.1:8000/satellite/
```
No key? `python manage.py seed_demo_data` loads SYNTHETIC data, labelled everywhere and never mixed with NASA data unless `scope=all` is chosen.

Pages: `/satellite/` (overview), `/satellite/map/`, `/satellite/harmonization/`, `/public-dashboard/` (burning calendar), `/satellite/history/`, `/satellite/data/` (import and processing, operators only). JSON API: `/api/fire/...`.

Method in one paragraph: FIRMS Area API (`/api/area/csv/{key}/{SOURCE}/{west,south,east,north}/{1-5 days}/{date}`) -> per-row validation and normalization (UTC, one schema, sensor-specific brightness kept apart, raw confidence kept) -> duplicate-safe storage with import metadata (`FetchJob`) -> `harmonize_hotspots` flags exact duplicates across products (SP kept over NRT) and MODIS/VIIRS overlap groups (footprint overlap within 180 min) without deleting or merging any measurement -> calendar statuses (`no_data` = never queried, `zero` = queried and empty, `detections`, `high` = z >= 3 vs previous 28 observed days) -> ST-DBSCAN clusters (2 km, 24 h, 3 points) and anomaly reports that state metric, baseline, threshold, sources and limits. A hotspot is a satellite thermal anomaly, not a confirmed wildfire.

Tests: `python manage.py test satellite`.


### Live data (today) from NASA FIRMS

Needs `FIRMS_MAP_KEY`. IoT stays simulated and is not touched.

* Button **"Refresh now from NASA FIRMS"** on every satellite page and on the burning calendar: pulls today and the 2 days before (UTC) for the selected area (MODIS, VIIRS SNPP, NOAA-20, NOAA-21; NRT for recent days, SP automatically for older ones). Repeating it only adds new detections (duplicate-safe), then runs harmonization on that window.
* Home globe: real hotspot counts and total FRP for the last 24 h per region (no random values). Button **"Refresh live data"** pulls the 9 regions in the background.
* Command line / cron: `python manage.py sync_firms [--days 5] [--globe] [--force] [--loop 900]`.
* API: `POST /api/fire/refresh/`, `GET /api/fire/refresh/status/`, `GET /api/fire/globe/`.
* Safety: per-area cooldown (`FIRMS_LIVE_COOLDOWN_SECONDS`, default 600) protects the 5000 requests / 10 min budget; `FIRMS_LIVE_PUBLIC=0` restricts the button to operators.
* History keeps working as before (Data management import / `import_firms`). Tests: `python manage.py test satellite.test_live`.

## PLEIADES AI assistant

The member-only assistant is available at `/assistant/`. It uses the server-side
`GEMINI_API_KEY` and a short-lived evidence snapshot built from the local PLEIADES
database: MODIS/VIIRS counts, acquisition date range, product/platform breakdown,
import-job records, quality indicators, harmonization results, temporal summaries,
and a small recent-record sample. It does **not** query NASA live for every chat
message and does not send the entire database to Gemini. Exact individual records
should be checked on the filtered satellite map or detections API.

Set `GEMINI_API_KEY` in `.env` (copy `.env.example`) or in the server environment.
Do not put the key in JavaScript, HTML, Git, or a message to anyone.

The Satellite → Map page contains three synchronized views: MODIS only, VIIRS only,
and a combined comparison. Each map uses the same area/date/confidence/category
filters and reports when the API's 5,000-feature display cap is reached.

Run the test suite after installing dependencies:
```
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```


## Windows PowerShell: installation and launch

```powershell
cd POLARIS_V2
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
pip install -r requirements.txt
Copy-Item .env.example .env             # then edit .env (see variables below)
python manage.py migrate
python manage.py createsuperuser        # optional; give the account the operator role in the admin
python manage.py import_firms --check-key
python manage.py import_firms --api --family MODIS --family VIIRS_SNPP --family VIIRS_NOAA20 `
    --bbox 20.84 -17.06 35.74 -1.54 --start 2024-06-01 --end 2024-09-30
python manage.py harmonize_hotspots --scope real
python manage.py runserver
```
Open http://127.0.0.1:8000/ . Without a NASA key: `python manage.py seed_demo_data` then `python manage.py harmonize_hotspots --scope demo` (synthetic data, labelled everywhere).

Environment variables (set in `.env`, never commit it): `FIRMS_MAP_KEY` (NASA FIRMS, required for real data), `GEMINI_API_KEY` (optional; without it the assistant answers directly from the database), `GEMINI_MODEL` and `GEMINI_FALLBACK_MODELS` (optional; use API model codes, not dashboard display names. Defaults `gemini-3.8-flash` then `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` were checked against https://ai.google.dev/gemini-api/docs/models on 2026-10-22; display names, deprecated codes and duplicates are skipped automatically), `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`.

## PLEIADES AI assistant: how it uses the data

For each question `satellite/ai_retrieval.py` detects the intent (latest observations, counts, a given day/month/year, regional change, sensor differences, harmonization, data quality) and runs the matching queries on the live database. The results, with their query names and data category (real NASA or synthetic), are given to Gemini together with a labelled list of scientific background. Answers must tag statements `[Database]`, `[Background]` or `[Interpretation]`. If Gemini is missing, rate-limited or unreachable, the user gets a deterministic database-only answer with a notice. Nothing is cached.

## Demonstration workflow (about 5 minutes)

1. `/satellite/data/` (operator): show the key status and the import history (source, product, counts, rejected rows).
2. `/satellite/quality/`: real NASA vs synthetic, MODIS vs VIIRS, missing values, "not available" vs zero.
3. `/satellite/harmonization/`: sensor-specific statistics, matched / unmatched counts, change the time window and watch the numbers move; read "Why the sensors differ".
4. `/public-dashboard/`: pick an area and a year, switch MODIS / VIIRS / combined, show no data vs zero vs detections, open a high day, click "Open this day on the map".
5. `/satellite/map/`: three synchronized maps (MODIS, VIIRS, comparison), popups with only the fields the product provides.
6. `/assistant/`: ask "How many real NASA observations are available?", "What differs between MODIS and VIIRS?", "Which regions show increased activity?", then show the "Database queries used" line.


## Gemini quota behaviour

Models are tried in order, at most 4 per question and within a 70 s budget. A 429 (quota) moves straight to the next model with no retry and no waiting; a 5xx or network error also moves straight to the next model (one attempt per model); 401/403 stop at once; a 400/404 (bad identifier) moves on. If every model is rate-limited the user receives a database-only answer with a notice.

## Heuristic matching parameters

The 180 min window and the 6 km value are analyst-chosen, configurable heuristics, not validated thresholds (`--max-minutes`, `--max-radius-km`, or the form on the Harmonization page). 180 min also admits the roughly 3 h Terra to afternoon-satellite gap, so a pair means "activity at the same place the same day", not "the same detection event"; use 30-60 min for near-coincident passes. The real radius is the mean of the two pixel sizes (about 0.7 km at nadir, up to about 2.8 km at the MODIS scan edge); 6 km is only a safety cap.

## Validation status (read this before the demo)

| Component | Verified how | Not verified |
|---|---|---|
| FIRMS URL format, date and bounding-box order, 5-day chunking, error handling | Official FIRMS API documentation read on 2026-11; unit tests with a MOCKED HTTP session | No request has been sent to the real FIRMS service from this build: key acceptance, real CSV columns, rate limits and data availability are unconfirmed. Run `import_firms --check-key` first. |
| Gemini model identifiers | Compared with the official model list (page dated 2026-10-22) | No request has been sent to the real Gemini API: your key, quota, tier and each model's availability to you are unconfirmed. |
| Gemini 429 / 5xx / auth handling | Tests with a MOCKED `_call_gemini` | Real quota responses and Retry-After behaviour |
| Browser interface (maps, calendar, charts) | Server-side rendering and API tests; JavaScript syntax check | Not exercised in a real browser |
| Real vs synthetic separation | Automated tests with overlapping real and synthetic rows | |


## Contact form email delivery (Gmail)

The contact form always stores messages in the Django admin database. To also deliver
them to the team inbox, configure SMTP in `.env` (use a Google App Password, not
your normal Gmail password):

```dotenv
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=your-site-account@gmail.com
EMAIL_HOST_PASSWORD=your-16-character-google-app-password
DEFAULT_FROM_EMAIL=your-site-account@gmail.com
CONTACT_EMAIL=the-inbox-that-should-receive-messages@gmail.com
```

Enable 2-Step Verification on the sending Google account and create an App Password
in its Google Account security settings. Restart Django after changing `.env`.
Never commit `.env` or share the App Password.


## IoT field monitoring and verification

The IoT Command Center is available at `/iot/`, with a separate GPS/time-window comparison page at `/iot/comparison/`. It supports BME280 environmental values, PMS5003 particulate values, MQ gas raw readings, solar/battery telemetry and LoRa link metrics. See [`README_IOT_INTEGRATION.md`](README_IOT_INTEGRATION.md) for the device API contract, migration sequence, simulator, provisional screening thresholds and limitations.

## FIRMS intelligence upgrade

Scheduled synchronisation, historical import, Today & estimates page, hotspot detail panel and same-date comparison are described in
`docs/UPGRADE_FIRMS_INTELLIGENCE.md` (installation, scheduling, commands, verification status, limitations).

