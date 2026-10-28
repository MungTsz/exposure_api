# Exposure ETL backend

Takes raw watch/phone GPS pings from the RN app, works out what the person
was doing all day - at home, in an office, school or other indoor place,
outdoors, on the MTR or in a vehicle - calls the third-party `expo_calx`
exposure API, and stores per-hour results for the app's bar chart and map.
Only each ping's time and position are used. `expo_calx` only covers Hong
Kong, so points outside HK are returned with `in_hk: false` and no exposure
value.

API documentation: [docs/API.md](docs/API.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env                  # fill in PRAISE_API_KEY and PRAISE_MYID
python -m app.services.road_service   # download the road centrelines (~38 MB, once)
uvicorn app.main:app --reload
```

Run everything from the repo root. Settings are read from environment
variables, falling back to `.env` (see `app/config/settings.py`); the app
refuses to start if `PRAISE_API_KEY` or `PRAISE_MYID` is missing, or if the
road centrelines are not at `ROAD_CENTRELINES_PATH`.

### Road data

The rail test (is this vehicle run a train?) and the road names of Vehicle
points use the Lands Department
[Road Centreline](https://portal.csdi.gov.hk/csdi-webpage/dataset/landsd_rcd_1637310758814_80061)
dataset from the CSDI portal (GeoJSON, ~40,000 lines, updated monthly).
`python -m app.services.road_service [PATH]` downloads the current version
(default `data/road_centrelines/RoadCentreLine.geojson`, git-ignored); run it
again now and then to pick up new roads. The file is loaded once at
startup (~3 s).

Tests (no API key, network or road file needed - `expo_calx` is mocked,
each test builds its own roads):

```bash
pip install -r requirements-dev.txt
pytest
```

Docker (the image downloads the road centrelines at build time):

```bash
docker build -t exposure-api .
docker run -p 8000:8000 -e PRAISE_API_KEY=... -e PRAISE_MYID=... -e EXPOSURE_DB_PATH=/data/exposure.db -v exposure-data:/data exposure-api
```

## Project layout

```
app/
  main.py                 FastAPI app: startup (road data, DB init, resume unfinished batches), routers
  config/                 every parameter lives here
    settings.py             deployment values from env/.env (secrets, URL, DB and road file paths, timeouts)
    constants.py            fixed facts (HK timezone, time formats, HK outline, API names, statuses, reasons)
    pipeline.py             pipeline thresholds, one section per step
    io_types.py             io type names, expo_calx labels, decision tree and place label thresholds
  api/                    HTTP layer
    schemas.py              request/response models
    body_limit.py           413 for request bodies over MAX_INGEST_BODY_BYTES
    routes/ingest.py        POST /ingest, POST /ingest/gpx, GET /ingest/{batch_id}
    routes/exposure.py      GET / DELETE /exposure/hourly/{pid}
    routes/place_labels.py  PUT/DELETE /place-labels/{label_id}
  services/
    ingest_service.py       background job: place labels + roads + pipeline -> expo_calx -> DB
    road_service.py         loads (and downloads) the road centrelines
    label_service.py        re-runs stored batches near a place label that changed
    day_service.py          deletes one day of a pid
  pipeline/               the pipeline: pure functions, no I/O
    run.py                  run_pipeline: the step order, in one place
    parsers/                input: raw records (GeoJSON, GPX) -> pings (time, lng, lat)
    spikes.py               step 0: remove GPS spikes
    motion.py               step 1: jump / still / walk / vehicle per interval
    runs.py                 step 2: runs of the same motion, cleaned up
    mtr.py                  step 3: rail test, MTR time extension, interchanges
    classify.py             step 4: the decision tree (io type of each run)
    place_labels.py         match a stay against the users' place labels
    points.py               step 5a: stay points and thinned route points
    time_split.py           step 5b: cut each point's time at HK clock hours
    roads.py                nearest road centreline of each ping
    region.py               inside / outside Hong Kong
    request_rows.py         build expo_calx rows
    geo.py                  shared distance / direction / shape helpers
    models.py               Ping, Track, Motion, Run, ClassifiedRun, Point, HourlySlice
  clients/praise_client.py  expo_calx HTTP client (parallel calls, capped)
  db/                     SQLite: connection/schema + one repository per table
docs/API.md               API documentation for app developers
tests/                    pytest suite: one file per layer, plus sample-day regression
notebooks/                step-by-step pipeline walkthrough
data/samples/             sample GPS exports (GeoJSON and GPX)
```

## How the pipeline works

Terms used throughout the code:

| term | meaning |
|---|---|
| **ping** | one GPS point: time, lng, lat |
| **spike** | a single bad ping (or a fix repeated for a few minutes) where GPS jumps far away and straight back, or that only an impossible speed could reach |
| **track** | the day's pings (spikes removed) with the time and distance between consecutive pings |
| **interval** | the time from one ping to the next |
| **motion** | what an interval (or run) looks like: `jump` (moved 300 m+ while GPS was dark), `still`, `walk`, `vehicle`; step 3 adds `rail` and `interchange` |
| **deg** | the distance between two pings, `\|dlat\| + \|dlng\|` in degrees (1 m ~ 0.00001 deg in Hong Kong) |
| **v_net** | a ping's net speed: displacement over a 2-minute window minus the GPS jitter floor - indoor jitter cancels out |
| **run** | consecutive intervals with the same motion |
| **stay** | a still run: the person stayed at one place |
| **route** | a moving run (walk, vehicle, MTR) |
| **io type** | `Home`, `Office`, `School`, `Other Indoor`, `Outdoor`, `MTR`, `Vehicle` |
| **reason** | which branch of the decision tree gave the io type |
| **place label** | a circle (centre + radius) a user put on the map to say "this place is a Home / Office / School / Other Indoor". Shared by everyone: it describes the place, not the person |
| **point** | what the app draws: one per stay, the thinned pings of a route; it owns the time until the next point |
| **hourly slice** | the part of a point's time inside one HK-local clock hour; one DB row each, and one expo_calx row if inside Hong Kong |

Steps (`app/pipeline/run.py`; thresholds in `app/config/pipeline.py`):

0. **Remove spikes**: drop ping B of A, B, C when A->B and B->C are both
   ~100 m+ and either C is back near A, or reaching / leaving B needs more
   than ~50 m/s while A->C does not (a stale or overshooting fix). Pings
   repeating B's exact position for up to 5 minutes count as B (watch
   data falls back to a fixed position, e.g. a cell tower's).
1. **Motion of each interval**: `jump` if it moved >= 0.003 deg (~300 m);
   `still` if it lasted >= 120 s (no pings in between) or its `v_net` is
   below ~0.5 m/s; `walk` below ~3 m/s; else `vehicle`. A ping's own speed
   is useless indoors (10-20 m of jitter per ping looks like 15 m/s at 1 s
   intervals), hence `v_net`.
2. **Runs**: consecutive intervals with the same motion. Clean-up, repeated
   until nothing changes: a still/walk run of <= 3 min between two
   vehicle/jump runs is part of the trip (traffic light, bus stop); a
   vehicle run < 2 min or walk run < 1 min is a GPS blip and joins a
   neighbour; two stays ~80 m apart with a walk of <= 5 min between them
   are one stay. Then a jump next to a vehicle run is GPS loss in that trip.
3. **MTR**: first, a jump run with more than 5 jumps is a phone that logs
   rarely while moving (watch data: 7-20 jumps per ride; an underground
   ride has 1-4), so it becomes a vehicle run - Vehicle is the default
   when the two can't be told apart. A jump run covering less than
   0.005 deg (~500 m, under one MTR stop) is a walk the GPS caught up with
   late, so it becomes a walk run - Outdoor when in doubt. Then the **rail test** - a vehicle run
   inside Hong Kong whose pings are at a median of >= 15 m from the nearest
   road centreline is above-ground MTR (a car drives on the centreline: 2-6 m on the sample
   days; the East Rail line runs beside the roads: 24 m). A jump faster than
   an MTR ride can be (~15 m/s door to door) started earlier - the pings
   before it were stale - so the still time right before it joins the ride.
   A still/walk run of <= 10 min between two MTR runs is an interchange.
4. **Decision tree** (`app/pipeline/classify.py`):
   ```
   run
   ├─ jump ............................................. MTR      (underground)
   ├─ rail (vehicle far from roads) .................... MTR      (above ground)
   ├─ interchange ...................................... MTR      (changing lines)
   ├─ vehicle .......................................... Vehicle  (taxi, bus, minibus, car, ...)
   ├─ walk ............................................. Outdoor
   └─ still (a stay)
       ├─ inside a place label ......................... the label's category
       ├─ >= 20 min and overlaps 00:00-06:00 HK ........ Home     (night stay)
       ├─ within ~100 m of a night stay ................ Home
       ├─ < 20 min ..................................... Outdoor  (short stop, e.g. waiting)
       └─ otherwise .................................... Other Indoor
   ```
   **Place labels come first**, for a stay of any length and even at night:
   a Home circle makes a 14:00 stay `Home`, an Office circle makes a 02:00
   stay `Office`. Office and School are never guessed - without a label
   they are `Other Indoor`. A circle is grown by 30 m for indoor GPS drift
   and matches a stay when it holds at least half of the stay's pings; if
   several match, most pings inside wins, then the smaller radius; circles
   that are mostly the same spot (overlap / union >= 0.5) vote, one vote
   per contributor (`app/pipeline/place_labels.py`). Saving, moving or
   deleting a label re-runs the stored batches near it in the background,
   so stored rows follow the labels (`app/services/label_service.py`).
5. **Output** (`app/pipeline/points.py`, `time_split.py`): a stay (or
   interchange) becomes **one point** at the median of its pings; a route
   keeps its pings thinned by **Douglas-Peucker** (the line stays within
   15 m of the GPS path, so every turn is kept) plus at least one point
   every ~300 m and both ends of every GPS gap. Route points carry their
   direction (`heading_deg`, `compass`) and Vehicle points their road
   (`road_name`). Each point owns the time until the next point; that time
   is cut at HK clock hours, because expo_calx scores each row against that
   hour's AQHI. Total time per io is kept exactly.
6. **Score** (`app/services/ingest_service.py`): only the rows inside Hong
   Kong (`HK_BOUNDARY` in `app/config/constants.py`) go to expo_calx, 40
   rows per call, up to 10 calls at once (shared by all batches); every
   row is stored. Rows outside HK have `in_hk = 0` and no exposure value.

On the sample days this keeps ~1 point per 10-15 pings: 20260830 (8,139
pings) becomes 520 points and 544 rows (14 calls). It was checked against
the person's own account of 1011 and 1012 (`tests/test_regression.py`):
every taxi, bus, shuttle bus and MTR ride (including the above-ground East
Rail line and three interchanges) comes out right, and so does the Kwun
Tong line ride of 1009.

## Adding an input format

GeoJSON Features (`POST /ingest`) and GPX files (`POST /ingest/gpx`, split
into one record per `<trkpt>` by `app/pipeline/parsers/gpx.py:read_gpx`)
are accepted today. Only time and position are read from any format. To
accept another format (e.g. live device tracking), add a module in
`app/pipeline/parsers/` with `can_parse(record)` and `parse(record)`
(return `None` for a record without a valid time or position), and append
it to `PARSERS` in `app/pipeline/parsers/__init__.py`. Records no parser
recognizes are skipped and logged.

## Adding an io type

1. `app/config/io_types.py`: add its name and its expo_calx label to
   `API_LABELS`.
2. `app/config/constants.py`: add a `Reason` for the new branch.
3. `app/pipeline/classify.py`: add the branch to the decision tree (and,
   if it needs a new kind of movement, a `Motion` in `models.py` set by an
   earlier step).
4. Add a test in `tests/test_pipeline.py`, and check the sample days
   (`tests/test_regression.py`, regenerate the golden files if intended).

## Endpoints

Full API documentation - request/response fields, limits, errors and
copy-paste examples - is in **[docs/API.md](docs/API.md)**.

| Method & path | Purpose |
|---|---|
| `POST /ingest` | upload a batch of raw GPS pings (max 5 MB) for background processing |
| `POST /ingest/gpx?pid=...` | same, but the body is a whole GPX file |
| `GET /ingest/{batch_id}` | poll a batch's processing status |
| `GET /exposure/hourly/{pid}` | read back the hourly rows: io, reason, position, direction, road, exposure; rows outside Hong Kong have no `exposure_value` |
| `DELETE /exposure/hourly/{pid}?date=YYYY-MM-DD` | delete one HK-local day of a pid's rows (and its pings in stored uploads), e.g. before re-importing it |
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

- `pipeline.py` - one section per step: spikes (`SPIKE_*`), interval motion
  (`NOISE_DEG`, `V_NET_WINDOW_S`, `JUMP_DEG`, `GAP_S`, `STILL_MAX_V`,
  `WALK_MAX_V`), runs (`STOP_MAX_S`, `MIN_*_RUN_S`, `SAME_PLACE_*`), MTR
  (`RAIL_MIN_ROAD_M`, `MTR_MAX_AVG_V`, `INTERCHANGE_MAX_S`) and output
  points (`ROUTE_TOL_M`, `ROUTE_MAX_GAP_DEG`, `ROAD_NAME_MAX_M`). Higher
  `ROUTE_TOL_M` / `ROUTE_MAX_GAP_DEG` = fewer points, rows and `expo_calx`
  calls.
- `io_types.py` - io type names and expo_calx labels (`API_LABELS`), the
  stay rules (`INDOOR_MIN_S`, `HOME_HOURS`, `HOME_RADIUS_DEG`) and place
  label matching (`PLACE_LABEL_*`).
- `settings.py` - deployment values (`PRAISE_MAX_ROWS_PER_CALL`,
  `PRAISE_MAX_CONCURRENT_CALLS`, `MAX_INGEST_BODY_BYTES`, timeouts, DB and
  road file paths), overridable by env vars.
- `constants.py` - `HK_BOUNDARY`, the Hong Kong outline used for `in_hk`
  and the rail test.

To compare settings, edit them and re-run
`notebooks/pipeline_walkthrough.ipynb`, which prints every step. `pytest`
fails `tests/test_regression.py` when the output on the sample days
changes - if the change is intended, regenerate the golden files with
`python -m tests.fixtures.update_golden`.

## Known limits

- **Sparse logging vs underground MTR**: a phone that logs only about once
  a minute while moving (e.g. a watch on a bus) moves 300 m+ between pings,
  like an underground ride that gets a fix at each station
  (`data/samples/1009`, Kwun Tong line: 4 jumps). They are told apart only
  by the number of jumps in a row (more than 5 = Vehicle, the default when
  in doubt): a long MTR ride with a fix at every station would come out as
  Vehicle, a short sparse bus ride as MTR.
- **Rail test**: the 15 m threshold comes from two East Rail rides; other
  lines are untested. A ferry is far from every road, so it would come out
  as `MTR`.
- **Short stays**: a stay under 20 minutes without a label (a quick shop
  visit) is `Outdoor`, and so is walking through a station.
- **Unlabelled schools and offices** (e.g. a university classroom) are
  `Other Indoor`.
- The Hong Kong outline (`HK_BOUNDARY`) is a ~20-point polygon, accurate to
  roughly 1 km along the Shenzhen border; a point right on the border can
  get the wrong `in_hk`. Swap in an official boundary if that matters.
- Every stored place label is loaded for each batch. Add a bounding-box
  query in `place_label_repository` if the table grows large.
- No retry on network failure, matching the documented expo_calx behavior.
- The 10-call cap on `expo_calx` is per server process; N `uvicorn` workers
  allow 10 x N calls at once.
