# Exposure ETL backend

Takes raw watch/phone GPS pings from the RN app, cleans and classifies them,
calls the third-party `expo_calx` exposure API, and stores per-hour results
per io type (Home, Other Indoor, Outdoor, ...) for the app's bar chart and map.
`expo_calx` only covers Hong Kong, so points outside HK are returned with
`in_hk: false` and no exposure value.

API documentation: [docs/API.md](docs/API.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in PRAISE_API_KEY and PRAISE_MYID
uvicorn app.main:app --reload
```

Run `uvicorn` from the repo root. Settings are read from environment
variables, falling back to `.env` (see `app/config/settings.py`); the app
refuses to start if `PRAISE_API_KEY` or `PRAISE_MYID` is missing.

Tests (no API key or network needed - `expo_calx` is mocked):

```bash
pip install -r requirements-dev.txt
pytest
```

Docker:

```bash
docker build -t exposure-api .
docker run -p 8000:8000 -e PRAISE_API_KEY=... -e PRAISE_MYID=... -e EXPOSURE_DB_PATH=/data/exposure.db -v exposure-data:/data exposure-api
```

## Project layout

```
app/
  main.py                 FastAPI app: startup (DB init, resume unfinished batches), routers
  config/                 every parameter lives here
    settings.py             deployment values from env/.env (secrets, URL, DB path, timeouts)
    constants.py            fixed facts (HK timezone, time formats, API names, batch statuses)
    pipeline.py             ETL tuning thresholds (spikes, stays, routes, gaps)
    io_types.py             per io type: stored name, expo_calx label, rule thresholds
  api/                    HTTP layer
    schemas.py              request/response models
    body_limit.py           413 for request bodies over MAX_INGEST_BODY_BYTES
    routes/ingest.py        POST /ingest, GET /ingest/{batch_id}
    routes/exposure.py      GET /exposure/hourly/{pid}
    routes/place_labels.py  PUT/DELETE /place-labels/{label_id}
  services/
    ingest_service.py       background job: place labels + pipeline -> expo_calx -> DB
  etl/                    pure pipeline steps, no I/O
    pipeline.py             run_pipeline: the step order, in one place
    parsers/                raw records -> pings (one module per input format)
    spikes.py               remove GPS spikes
    stays.py                group pings into stays
    routes.py               thin moving routes
    time_split.py           cut stays at HK clock hours
    region.py               flag hourly slices inside/outside Hong Kong
    request_rows.py         build expo_calx rows
    geo.py                  shared distance/angle/box helpers
    models.py               Ping, Stay, HourlySlice
  io_types/               which io type a stay gets
    registry.py             the ordered list of io types + classify/label
    base.py                 IoType definition (name, api_label, kind, rule)
    place_labels.py         match a stay against the users' place labels
    rules/                  one module per io type (home.py, office.py, school.py, other_indoor.py, outdoor.py)
  clients/praise_client.py  expo_calx HTTP client (parallel calls, capped)
  db/                     SQLite: connection/schema + one repository per table
docs/API.md               API documentation for app developers
tests/                    pytest suite, incl. golden-output regression test
notebooks/                step-by-step pipeline walkthrough
data/samples/             sample GPS exports
```

## How the pipeline works

Terms used throughout the code:

| term | meaning |
|---|---|
| **ping** | one GPS point |
| **spike** | a single bad point where GPS jumps away and straight back |
| **stay** | consecutive pings that stay inside a small box and move slowly - where the person stayed, and for how long. While moving, each ping usually becomes its own very short stay |
| **io type** | what kind of place a stay is: `Home`, `Office`, `School`, `Other Indoor`, `Outdoor`, later `Bus`, ... |
| **place label** | a circle (centre + radius) a user put on the map to say "this place is a Home / Office / School / Other Indoor". Shared by everyone: it describes the place, not the person |
| **route** | consecutive moving stays of the same io type |
| **route point** | a stay kept after thinning a route |
| **hourly slice** | the part of a stay inside one HK-local clock hour; one DB row each, and one expo_calx row if inside Hong Kong |

Steps (`app/etl/pipeline.py`):

1. **Parse** raw records into time-sorted pings.
2. **Remove spikes**: drop point B of any A,B,C where the path sharply doubles back within a short time.
3. **Group into stays**: a stay continues while each ping is within a ~20m box of the stay's first ping and its speed (if reported) is below 1 m/s.
4. **Label stays** with an io type: the first io type (in `app/io_types/registry.py` order) whose rule matches wins. Today: a stay of 20+ minutes is indoor - `Home` if it starts between 00:00 and 06:00 HK time (06:00 not included), otherwise `Other Indoor` - and anything else is `Outdoor`. Only the stay's start time counts, so a stay from 05:50 to 08:00 is `Home` as a whole. Labelling happens before the hour split, so a stay crossing an hour is judged by its full duration.
   **Place labels come first**: during a visit to a labelled place the label's category wins over the rules - a Home circle makes a 14:00 stay `Home`, an Office circle makes a 02:00 stay `Office`, never `Home`. A visit is the consecutive stays a label matches - indoor GPS drift splits a night into many short stays - plus neighbouring stays drifted up to 30 m past the circle (`PLACE_LABEL_DRIFT_MARGIN_M`); they become one stay. It needs 20+ minutes in total, so walking past an office is still `Outdoor`. A label matches a stay when its circle holds at least half of the stay's pings (GPS drifts indoors, so all pings are counted, not just the first). If several match: most pings inside wins, then the smaller radius. Matching circles that are mostly the same spot (overlap / union >= 0.5) vote: the category with the most distinct contributors wins. Each row's `io_source` says `label` or `inferred` (`app/io_types/place_labels.py`).
5. **Simplify routes**: while moving, 1 Hz tracking makes one stay per ping. Each route keeps roughly one point per ~300m (or per 5 min); the dropped points' time is added to the kept point before them, so total time is unchanged. Routes break at GPS gaps (>120s or >200m).
6. **Split by hour**: cut each stay at HK-local clock hours, because expo_calx scores each row against that hour's AQHI reading.
7. **Mark Hong Kong**: flag each hourly slice `in_hk` by testing its position against a simplified HK outline (`HK_BOUNDARY` in `app/config/constants.py`): a bounding-box check, then point-in-polygon.
8. **Score**: send only the in-HK rows to expo_calx, 40 rows per call, up to 10 calls at once (shared by all batches), and store one DB row per hourly slice. Slices outside HK are stored with `in_hk = 0` and no exposure value.

Where a stay is placed depends on its io type's `kind`:
- `stationary` (Home, Office, School, Other Indoor): at its first ping, so GPS drift doesn't move it.
- `moving` (Outdoor): at the average of its pings, and its routes are thinned (step 5).

## Adding an io type (e.g. Bus)

1. `app/config/io_types.py`: add a section with `<TYPE>_NAME` (stored in the DB and shown in the app), `<TYPE>_API_LABEL` (the value expo_calx expects) and any thresholds its rule needs.
2. `app/io_types/rules/<type>.py`: define an `IoType` with its `kind` (`STATIONARY` or `MOVING`) and a `matches(stay)` function. A stay carries its `pings` (time, lng, lat, speed) and duration; use `app/etl/geo.py` for distance or box checks. `rules/other_indoor.py` is a minimal example.
3. `app/io_types/registry.py`: add it to `IO_TYPES` before any type it should win over. `OUTDOOR` matches everything and must stay last.
4. Add a test in `tests/test_etl.py`.

## Adding an input format

GeoJSON Features (`POST /ingest`) and GPX files (`POST /ingest/gpx`, split into one record per `<trkpt>` by `app/etl/parsers/gpx.py:read_gpx`) are accepted today. To accept another format (e.g. live device tracking), add a module in `app/etl/parsers/` with `can_parse(record)` and `parse(record)`, and append it to `PARSERS` in `app/etl/parsers/__init__.py`. Records no parser recognizes are skipped and logged.

## Endpoints

Full API documentation - request/response fields, limits, errors and
copy-paste examples - is in **[docs/API.md](docs/API.md)**.

| Method & path | Purpose |
|---|---|
| `POST /ingest` | upload a batch of raw GPS pings (max 25 MB) for background processing |
| `POST /ingest/gpx?pid=...` | same, but the body is a whole GPX file |
| `GET /ingest/{batch_id}` | poll a batch's processing status |
| `GET /exposure/hourly/{pid}` | read back the hourly rows; each has `in_hk`, and rows outside Hong Kong have no `exposure_value` |
| `PUT /place-labels/{label_id}` | create or update a place label (idempotent) |
| `DELETE /place-labels/{label_id}?contributor_id=...` | delete a place label |

## Local network testing (RN app on a device/emulator)

By default `uvicorn` binds to `127.0.0.1`, which only accepts connections
from the same machine - a phone or emulator on the same Wi-Fi network can't
reach that. To test the RN app against a locally-running server:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then find your machine's LAN IP (Windows: `ipconfig`, look for the `IPv4
Address` under the active adapter, e.g. `192.168.1.23`) and point the RN
app's API base URL at it, depending on where it's running:

- **Physical device**: `http://<your-LAN-IP>:8000` - use a home network or
  phone hotspot; many corporate/public Wi-Fi networks block device-to-device
  traffic and this will just look like a timeout.
- **iOS simulator**: `http://localhost:8000` works directly.
- **Android emulator**: `http://10.0.2.2:8000` (the emulator's alias for the
  host machine's `localhost`); the LAN IP works too.
- **Android cleartext HTTP**: Android 9+ blocks plaintext `http://` by
  default, so the RN app needs cleartext traffic explicitly allowed for the
  dev host (`usesCleartextTraffic` / a network security config) - this is a
  change on the RN app's side, not this repo.

On Windows, allow the server through the firewall when prompted (or add a
rule for port 8000 manually) - otherwise requests from a phone will hang
even though the IP and binding are correct.

No CORS setup is needed: CORS only applies to browser `fetch` calls, and a
native RN app isn't subject to it. Only add `CORSMiddleware` if the app is
ever tested from a browser context (e.g. Expo web).

## Tuning

All parameters live in `app/config/`:

- `pipeline.py`
  - `SPIKE_ANGLE_DEG` / `SPIKE_TIME_WINDOW_SECONDS` - GPS spike removal.
  - `STAY_DEGREE_THRESHOLD` / `SPEED_THRESHOLD_MPS` - how far and how fast a ping can be and still belong to the same stay.
  - `ROUTE_POINT_DEGREE_THRESHOLD` / `ROUTE_POINT_MAX_SECONDS` - route thinning: keep a point once the route leaves a ~300m box (0.0027 deg) of the previous kept point, or after 300s. Lower = denser map, more rows and `expo_calx` calls.
  - `MAX_GAP_DEGREES` / `MAX_GAP_SECONDS` - where a route breaks (GPS dropout or jump). The jump is `|dlat| + |dlng|` in degrees (0.0018 = ~200m north/south).
- `io_types.py` - each io type's name, expo_calx label and thresholds (e.g. `STAY_INDOOR_MINUTES`), and place label matching (`PLACE_LABEL_MIN_PING_FRACTION`, `PLACE_LABEL_SAME_SPOT_MIN_OVERLAP`).
- `settings.py` - deployment values (`PRAISE_MAX_ROWS_PER_CALL`, `PRAISE_MAX_CONCURRENT_CALLS`, `MAX_INGEST_BODY_BYTES`, timeouts, DB path), overridable by env vars.
- `constants.py` - `HK_BOUNDARY`, the Hong Kong outline used for `in_hk`.

To compare settings, edit them and re-run `notebooks/pipeline_walkthrough.ipynb`. `pytest` will fail `tests/test_regression.py` when the pipeline output changes - if the change is intended, regenerate the files in `tests/fixtures/golden/`.

## Known simplifications (fine for a v1, revisit if needed)

- No retry on network failure, matching the documented API behavior.
- A stay uses a fixed reference point (its first ping); a slowly drifting
  walk could split into more stays than ideal.
- `STAY_DEGREE_THRESHOLD` is a flat degree box applied identically to lat
  and lng, with no correction for longitude compression - simpler than
  haversine, at the cost of the east-west tolerance being slightly tighter
  in real meters than the north-south one.
- A genuinely-indoor stay and an outdoor wait of the same duration (e.g.
  waiting for a bus) are indistinguishable by duration/box/speed alone -
  tuning `STAY_INDOOR_MINUTES` etc. is a mitigation, not a structural fix.
  A dedicated io type rule (e.g. a bus-stop geofence) is the structural fix.
- The Hong Kong outline (`HK_BOUNDARY`) is a ~20-point polygon, accurate to
  roughly 1 km along the Shenzhen border; a point right on the border can
  get the wrong `in_hk`. Swap in an official boundary if that matters.
- Place labels apply only to batches processed after the label is stored;
  stored rows are not re-labelled. Re-uploading a day whose io changes adds
  a new row (the key is `pid, io, start_time_hk`) next to the old one.
- Every stored place label is loaded for each batch. Add a bounding-box
  query in `place_label_repository` if the table grows large.
- The 10-call cap on `expo_calx` is per server process; N `uvicorn` workers
  allow 10 x N calls at once.
- `tests/test_regression.py` golden files were generated on Python 3.11 (the
  Docker image). On Python 3.12+ `sum()` of floats is more precise, so a few
  averaged `lng`/`lat` values differ in the last digit and those golden
  tests fail even though nothing changed.
