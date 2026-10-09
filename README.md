# Exposure ETL backend

Takes raw watch/phone GPS pings from the RN app, cleans and classifies them,
calls the third-party `expo_calx` exposure API, and stores per-hour
indoor/outdoor results for the app's bar chart.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in PRAISE_API_KEY and PRAISE_MYID
uvicorn exposure_api_github.main:app --reload
```

This directory is itself the `exposure_api` package (see CLAUDE.md), so the
command above must be run from its *parent* directory - either check this
repo out into a folder named `exposure_api`, or `cd` up one level first.

`.env` is loaded automatically (via `python-dotenv`) when `praise_client.py`/`db.py` are imported - no manual `export` step needed.

## Endpoints

| Method & path | Purpose |
|---|---|
| `POST /ingest` | upload a batch of raw GPS pings for background processing |
| `GET /ingest/{batch_id}` | poll a batch's processing status |
| `GET /exposure/hourly/{pid}` | read back the processed hourly indoor/outdoor rows |

### Try it yourself

Every command below is **Command Prompt (`cmd.exe`)** syntax: the whole
JSON body is wrapped in double quotes with the inner quotes escaped
(`\"`). If you're in bash, git-bash, or PowerShell instead, single-quote
the body as-is - `-d '{"pid": ...}'` - and drop the backslashes; all three
of those shells treat `'...'` as a literal string, unlike `cmd.exe`.

Each scenario uses its own `pid` and fixed, made-up timestamps/coordinates,
so what you get back is exactly reproducible - **except** `exposure_value`,
which comes from the live third-party `expo_calx` API and will genuinely
differ from what's shown here (it depends on the real air quality reading
at that time/place). Everything else - the number of rows, `io`,
`delta_t_hours`, `lng`/`lat` - should match exactly.

#### 1. Two pings, same spot, 10 minutes apart → **one** row

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"demo-merge\", \"features\": [{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\",\"speed\":0.05},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"timestamp\":\"2026-08-29T16:10:00.000Z\",\"coords\":{\"latitude\":22.3399579,\"longitude\":114.2632457,\"speed\":0.3}},{\"timestamp\":\"2026-08-29T16:10:05.000Z\",\"coords\":{\"latitude\":22.3399601,\"longitude\":114.2632480},\"sample\":true}]}"
```
```bat
curl http://localhost:8000/ingest/<batch_id from the response above>
curl http://localhost:8000/exposure/hourly/demo-merge
```

Why one row: 3 pings went in, but the 3rd has `sample: true` so it's
dropped entirely - that leaves 2. Those 2 are at the identical
coordinates, 10 minutes apart, and the second one's `speed` (0.3 m/s) is
under the 1.0 m/s threshold, so it counts as "still at the same spot" -
they merge into a single run. 10 minutes is nowhere near the 20-minute
indoor cutoff, so it's labelled `"Outdoor"`. Both timestamps fall in the
same HK-local hour, so there's nothing to split either. Result:
`hours_processed: 1, rows: 1`, and the one row back from
`/exposure/hourly/demo-merge` looks like:
```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.1667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579}]
```

#### 2. Two separate stops, same hour → **two** rows

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"demo-split\", \"features\": [{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\",\"speed\":0.1},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"timestamp\":\"2026-08-29T16:05:00.000Z\",\"coords\":{\"latitude\":22.3399579,\"longitude\":114.2632457,\"speed\":0.2}},{\"timestamp\":\"2026-08-29T16:10:00.000Z\",\"coords\":{\"latitude\":22.3399579,\"longitude\":114.2650000,\"speed\":0.3}},{\"timestamp\":\"2026-08-29T16:15:00.000Z\",\"coords\":{\"latitude\":22.3399579,\"longitude\":114.2650000,\"speed\":0.1}}]}"
```
```bat
curl http://localhost:8000/ingest/<batch_id from the response above>
curl http://localhost:8000/exposure/hourly/demo-split
```

Why two rows: the first 2 pings are at one spot (`lng 114.2632457`), the
last 2 are at a different spot roughly 175m away (`lng 114.2650000`) -
that's well past the ~20m "same location" box, so ping 3 starts a *new*
run instead of joining the first one. Both runs are under 20 minutes, so
both are `"Outdoor"`, and both still land in the same HK-local hour - this
is exactly the case the DB schema is keyed for: two rows sharing one
`hour_start_hk`, distinguished by `start_time_hk`:
```json
[
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.0833, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579},
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830001000", "io": "Outdoor", "delta_t_hours": 0.0833, "exposure_value": <live value>, "lng": 114.2650000, "lat": 22.3399579}
]
```

#### 3. Dwelling 25 minutes in one spot → **Indoor**, collapsed to one point

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"demo-indoor\", \"features\": [{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"timestamp\":\"2026-08-29T16:10:00.000Z\",\"coords\":{\"latitude\":22.3399650,\"longitude\":114.2633000,\"speed\":0.1}},{\"timestamp\":\"2026-08-29T16:25:00.000Z\",\"coords\":{\"latitude\":22.3399500,\"longitude\":114.2632200,\"speed\":0.05}}]}"
```
```bat
curl http://localhost:8000/ingest/<batch_id from the response above>
curl http://localhost:8000/exposure/hourly/demo-indoor
```

Why Indoor, and why that exact `lng`/`lat`: all 3 pings drift a little
(a few meters) but stay within the ~20m box of the *first* ping, and each
step is slow enough, so all 3 merge into one run spanning 16:00-16:25 -
25 minutes, past the 20-minute indoor cutoff. Because it's Indoor, the row
is represented by that first ping's exact coordinates (`114.2632457,
22.3399579`), not the average of all 3 (which would be a slightly
different point) - the whole point of collapsing an indoor dwell to its
anchor point instead of averaging:
```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Indoor", "delta_t_hours": 0.4167, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579}]
```
(Internally, this row's request to `expo_calx` used `{"IO": "Home"}` -
`expo_calx` doesn't accept `"Indoor"` itself. That translation only
happens on the outgoing request and isn't visible in any response here.)

### Request/response reference

**`POST /ingest`** body:

| field | type | required | notes |
|---|---|---|---|
| `pid` | string | yes | person/device id; keys every downstream row |
| `features` | array of objects | yes | raw ping records - two shapes accepted, detected per-record by whether it has a `geometry` key, so **they can be mixed in one batch** |

Shape A - GeoJSON `Feature` (watch-exported data):

| field | required | read by the pipeline? |
|---|---|---|
| `properties.time` (ISO 8601 UTC) | yes | yes - ping timestamp |
| `geometry.coordinates` (`[lng, lat]`) | yes | yes - position |
| `properties.speed` (m/s) | no | yes, if present - indoor/outdoor speed check |
| `.provider`, `.time_long`, `.accuracy`, `.altitude`, `.bearing` | no | no - accepted, ignored |

Shape B - live device tracking (`react-native-background-geolocation`-style):

| field | required | read by the pipeline? |
|---|---|---|
| `timestamp` (ISO 8601 UTC) | yes | yes - ping timestamp |
| `coords.latitude` / `coords.longitude` | yes | yes - position |
| `coords.speed` (m/s) | no | yes, if present. `-1` or any negative value (that library's "unknown" convention) is treated as absent |
| `sample` (boolean) | no | yes - `true` means "transient reading"; the record is **dropped entirely** |
| `mock` (boolean) | no | no - `true` (fake-GPS) records are kept as-is; the flag itself isn't read |
| other `coords.*` fields | no | no - accepted, ignored |

Response: `202 {"status": "queued", "batch_id": <int>}` immediately;
processing runs afterward in the background. Errors: `400 {"detail": "no
valid pings in request"}` if `features` parses to zero pings.

Bulk-importing a whole watch-exported GeoJSON file (e.g. one of the
fixtures in `testing/data/`) works the same way - lift its `features`
array into the request body. That needs `jq` and `$(...)` substitution
(PowerShell or git-bash; `cmd.exe` has neither):
```bash
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"local-device\", \"features\": $(jq '.features' testing/data/20260830.geojson)}"
```

**`GET /ingest/{batch_id}`** - poll for the background job's completion
(not needed for the auto-resume case: a batch still
`queued`/`processing` when the server restarts is picked back up on
startup automatically). `batch_id` comes from the `POST /ingest` response.
Errors: `404` if it doesn't exist. Response fields:

| field | type | notes |
|---|---|---|
| `id` | int | the `batch_id` |
| `pid` | string | |
| `status` | string | `queued` \| `processing` \| `done` \| `error` |
| `error_detail` | string or `null` | only set when `status` is `error` |
| `hours_processed` | int or `null` | distinct hour buckets touched; set once `done` |
| `rows` | int or `null` | rows sent to `expo_calx`; set once `done` |
| `created_at` / `updated_at` | ISO 8601 string (UTC) | |

**`GET /exposure/hourly/{pid}`** - two consumers read this array: (a) a
**stacked bar chart**, by summing `delta_t_hours`/`exposure_value` across
rows sharing an `hour_start_hk`+`io`, and (b) a **map route**, by plotting
each row's `lng`/`lat` in `start_time_hk` order. More than one row can
share an `hour_start_hk`+`io` (scenario 2 above) - each row is one dwell
run, or one hour-slice of a run that crosses an hour boundary, keyed by
`(pid, io, start_time_hk)`. Response fields:

| field | type | notes |
|---|---|---|
| `hour_start_hk` | string `'YYYYMMDDHH0000'`, HK-local | hour bucket - group by this (+ `io`) for the chart |
| `start_time_hk` | string `'YYYYMMDDHHMMSS'`, HK-local | this run's own precise start; unique per `(pid, io)` - sort by this for the route |
| `io` | string | `"Indoor"` \| `"Outdoor"` |
| `delta_t_hours` | number | duration of this row's slice, hours |
| `exposure_value` | number or `null` | `null` if `expo_calx` returned `-9` (that row failed) |
| `lng` / `lat` | number | Outdoor: average of the run's pings. Indoor: the run's anchor (its first ping) |

## Local network testing (RN app on a device/emulator)

By default `uvicorn` binds to `127.0.0.1`, which only accepts connections
from the same machine - a phone or emulator on the same Wi-Fi network can't
reach that. To test the RN app against a locally-running server:

```bash
uvicorn exposure_api.main:app --reload --host 0.0.0.0 --port 8000
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

All output-shaping thresholds live in `config.py`:

- `SPIKE_ANGLE_DEG` / `SPIKE_TIME_WINDOW_SECONDS` - GPS spike rejection.
- `DWELL_INDOOR_MINUTES` / `DWELL_DEGREE_THRESHOLD` / `SPEED_THRESHOLD_MPS` -
  indoor/outdoor dwell test.
- `TRAIL_DOT_DEGREE_THRESHOLD` / `TRAIL_DOT_MAX_SECONDS` - outdoor trail
  thinning: a moving trail keeps a dot once it leaves a ~300m lat/lng box
  (0.0027 deg) of the previous dot, or after 300s, whichever comes first.
  Lower values = denser track, more rows and `expo_calx` calls; higher =
  sparser.
- `MAX_GAP_METERS` / `MAX_GAP_SECONDS` - where a trail breaks (GPS dropout or
  jump), so simplification never smooths across a real gap.

`etl.py`'s `HK_OFFSET`/`_API_IO_LABEL` and `praise_client.py`'s API-only
constants aren't sweep tunables and stay defined in their own files.

## Known simplifications (fine for a v1, revisit if needed)

- No retry on network failure, matching the documented API behavior.
- Dwell-run detection uses a fixed reference point (the run's first ping);
  a slowly drifting walk could split into more runs than ideal.
- `DWELL_DEGREE_THRESHOLD` is a flat degree box applied identically to lat
  and lng, with no correction for longitude compression at higher
  latitudes - simpler than haversine, at the cost of the east-west tolerance
  being slightly tighter in real meters than the north-south one.
- A genuinely-indoor dwell and an outdoor stationary wait of the same
  duration (e.g. waiting for a bus) are indistinguishable by dwell
  duration/box/speed alone - tuning `DWELL_INDOOR_MINUTES` etc. is a
  mitigation, not a structural fix.
