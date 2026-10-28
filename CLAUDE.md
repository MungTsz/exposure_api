# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A production FastAPI backend that takes raw GPS pings from an RN watch/phone app (only time, lng, lat are read), works out the io type of every moment of the day (Home, Office, School, Other Indoor, Outdoor, MTR, Vehicle) with a rule-based decision tree, turns the day into points (one per stay, a thinned line per route, with heading and road name), splits their time by HK clock hour, calls the third-party PRAISE-HK `expo_calx` exposure API for the rows inside Hong Kong, and stores per-hour rows (with an `in_hk` flag; outside-HK rows have no value) in SQLite for the app's stacked bar chart and map. API documentation for app developers: `docs/API.md`. The logic was developed and checked against the person's own account of sample days in `testing/io_classification_experiment.ipynb` (untracked experiment notebook).

## Running

```bash
pip install -r requirements.txt
cp .env.example .env                  # fill in PRAISE_API_KEY and PRAISE_MYID
python -m app.services.road_service   # download the road centrelines (~38 MB) to data/road_centrelines/
uvicorn app.main:app --reload         # from the repo root
pip install -r requirements-dev.txt && pytest
```

All settings come from `app/config/settings.py` (pydantic-settings): env vars first, then the repo-root `.env`. `PRAISE_API_KEY`/`PRAISE_MYID` have no default, so `create_app()` fails fast without them; the lifespan loads the road centrelines (`road_service.get_road_index`) and refuses to start without the file. Tests set dummy values, a temp DB and an empty road file in `tests/conftest.py`, build their own roads (`conftest.road_index`) and mock `expo_calx`, so they need no key, network or road file; `tests/test_regression.py` uses the real road file and samples when present and skips otherwise.

## Rules for this codebase

- **No hard-coded values.** Every parameter goes in `app/config/`: deployment values in `settings.py`, fixed facts (timezone, formats, API names, statuses, `Reason`, `PointKind`, `HK_BOUNDARY`, road data URL/projection) in `constants.py`, pipeline thresholds (one section per step) in `pipeline.py`, io type names / expo_calx labels / decision tree and place label thresholds in `io_types.py`.
- **Plain terms**: ping, spike, track, interval, motion, run, stay, route, point, io type, reason, place label, hourly slice (see the README glossary). Distances between pings are `deg` = `|dlat| + |dlng|` (`geo.deg_between`); real sizes (label radii, route shape, roads) are metres. Don't reintroduce "dwell", "trail" or "anchor".
- **`io`** is the field name everywhere (code, DB column, API response, expo_calx `{"IO": ...}`).
- **Pipeline output must not change by accident**: `tests/test_regression.py` compares against `tests/fixtures/golden/` (GeoJSON sample days) and checks 1011/1012 against what the person did. Regenerate goldens with `python -m tests.fixtures.update_golden` only for an intended change.
- Repo files are English only.

## Architecture

```
app/main.py           create_app(); lifespan loads the road centrelines, inits DB, resumes pending/processing batches in a thread
app/api/              schemas.py + body_limit.py (413 middleware) + routes/ingest.py (POST /ingest, POST /ingest/gpx, GET /ingest/{id}) + routes/exposure.py (GET /exposure/hourly/{pid}, DELETE /exposure/hourly/{pid}?date=) + routes/place_labels.py (PUT/DELETE /place-labels/{label_id})
app/services/         ingest_service.process_batch: place_label_repository.get_all + road_service.get_road_index -> run_pipeline -> to_request_rows (in-HK slices only) -> praise_client -> exposure_repository (all slices); road_service loads/downloads the road file; label_service.reprocess_near re-runs stored batches after a label change; day_service.delete_day deletes one HK day of a pid (rows + that day's records in its stored batches, splitting a batch that spans the day, under label_service.rerun_lock)
app/pipeline/         pure functions, no I/O; run.run_pipeline(records, roads, place_labels) defines the step order; models.py has Ping, Track, Motion, Run, ClassifiedRun, Point, HourlySlice
app/clients/          praise_client: expo_calx calls, batching, parallel up to a process-wide cap, ExposureResult(t, pid, value, raw)
app/db/               connection (schema, init_db with in-place column adds) + exposure_repository + batch_repository + place_label_repository
```

- **`POST /ingest`** rejects bodies over `max_ingest_body_bytes` with 413 before reading them (`api/body_limit.py`, also counts chunked bodies), then stores the raw batch and returns `202 {"status": "queued", "batch_id"}`; processing runs as a `BackgroundTasks` job because a dense day needs several ~20-30s `expo_calx` calls. `process_batch` never raises: any failure is recorded as batch status `error` (secrets redacted).
- **Pipeline steps** (`app/pipeline/`, thresholds in `config/pipeline.py`):
  - input `parsers.parse_pings` - one module per input format in a `PARSERS` list: `geojson.py` and `gpx.py`; both read only time + position (`common.parse_utc`, `common.parse_lng_lat`) and drop a record without them. `POST /ingest/gpx` turns the uploaded file into one `{"type": "gpx_trkpt", lat, lon, time}` record per `<trkpt>` (`gpx.read_gpx`, defusedxml); those records are what the batch stores, so resume works unchanged.
  0. `spikes.remove_spikes` - drops B of A,B,C when both legs are >= `SPIKE_MIN_DEG` and C returns near A, or B needs > `SPIKE_MAX_V` to reach/leave while A->C does not; exact repeats of B's position within `SPIKE_MAX_S` count as B. Then `Track.from_pings` (interval i = ping i-1 -> ping i; `dt_s`, `dd_deg`).
  1. `motion.interval_motions` - `jump` (dd >= `JUMP_DEG`), `still` (dt >= `GAP_S` or `v_net` < `STILL_MAX_V`), `walk` (< `WALK_MAX_V`), else `vehicle`. `v_net` = net displacement over a `V_NET_WINDOW_S` window minus `NOISE_DEG`, so indoor jitter cancels.
  2. `runs.build_runs` - runs of the same motion; repeated clean-up (2a stop inside a trip, 2b shortest GPS blip joins a neighbour, 2c same-place stays merge), then 2d jump next to vehicle -> vehicle. Works on a run list (`_relabel` merges neighbours), not per interval, so a dense day stays fast.
  3. `mtr.find_mtr` - 3a not underground: a jump run with more than `MTR_MAX_JUMPS` jumps -> vehicle (Vehicle is the default when MTR and a rarely-logging phone on the road can't be told apart), one covering < `MTR_MIN_DEG` -> walk (Outdoor when in doubt); 3b rail test: vehicle run inside HK with median distance to the nearest road centreline >= `RAIL_MIN_ROAD_M` -> `rail`; 3c a jump faster than `MTR_MAX_AVG_V` takes in the still intervals before it; 3d still/walk <= `INTERCHANGE_MAX_S` between two MTR runs -> `interchange`. Road distances come from `roads.RoadIndex.nearest` (shapely STRtree on a flat projection around `ROAD_PROJECTION_ORIGIN`), computed once per track in `run.py`.
  4. `classify.classify_runs` - decision tree: jump/rail/interchange -> MTR, vehicle -> Vehicle, walk -> Outdoor; a stay: place label (circles grown by `PLACE_LABEL_DRIFT_MARGIN_M`, `place_labels.match_place_label` with ping fraction, ranking and crowd vote) > night stay Home (>= `INDOOR_MIN_S`, overlaps `HOME_HOURS`) > within `HOME_RADIUS_DEG` of a night stay (not one labelled otherwise) Home > short Outdoor > Other Indoor. Each ClassifiedRun has `io`, `io_source` (label/inferred) and `reason` (`constants.Reason`). Office/School only from labels.
  5. `points.build_points` - stay/interchange -> 1 point at the median; route -> Douglas-Peucker (`ROUTE_TOL_M`, `geo.douglas_peucker` on local metres) + a point every `ROUTE_MAX_GAP_DEG` + both ends of every GPS gap; heading/compass to the next point; Vehicle `road_name` within `ROAD_NAME_MAX_M`; each point owns [its time, next point's time); zero-length points are dropped; `in_hk` per point (`region.is_in_hk`). Then `time_split.split_by_hour` cuts each point at HK clock hours (prorated, total time exact).
  6. `request_rows.to_request_rows` - called on `PipelineResult.hk_slices` only. `[t, pid, lng, lat, {"IO": API_LABELS[io]}, delta_t]`; the io name is translated to its expo_calx label only here.
- **Place labels** (`PUT /place-labels/{label_id}`, shared crowd data, never listed back to clients): matched per stay (still run), any duration. Upsert ignores a stale `updated_at` (200), 403 for another contributor. A create/update/delete queues `label_service.reprocess_near` (BackgroundTasks) for the new and old circle: finished batches whose stored bbox (`ingest_batches.min_lng..max_lat`) overlaps and that have a ping within radius + `PLACE_LABEL_DRIFT_MARGIN_M` are re-run via `process_batches`, oldest first, under one process-wide lock; a batch fully covered (`first/last_ping_utc`) by a later batch of the same pid is skipped. Costs expo_calx calls for every nearby pid.
- **`praise_client`** (legacy `todo=expo_calx` on the `praise-ir-cal` uwsgi script): success is inferred from `exposure` being a list (`msg` only on failure); `-9` on one row means only that row failed (`value=None`); batches of `praise_max_rows_per_call` (40, own convention - no documented limit) run in parallel on one shared `ThreadPoolExecutor` of `praise_max_concurrent_calls` (10) workers, so the cap holds across concurrent batches; results keep row order; on a failure, unstarted calls are cancelled; no retry. Each result keeps the API's full item in `raw`, stored in the DB `api_result_json` column, so new API fields aren't lost.
- **DB**: a batch replaces the pid's rows whose `start_time_hk` lies between its first and last ping (`exposure_repository.replace_hourly`, one transaction), so a re-upload never leaves old rows of another io next to the new ones. `hourly_exposure` keyed on `(pid, io, start_time_hk)`: one row per hourly slice of a point, with `io_source`, `reason`, `kind` (stay/route), `heading_deg`, `compass`, `road_name` and `in_hk` (1/0; outside-HK rows have NULL `exposure_value` and `api_result_json`); `hour_start_hk` is the chart's group-by column. Batch statuses are `constants.BatchStatus` (`pending/processing/done/error`). `place_labels` keyed on `label_id` (updated_at stored as UTC ISO). Only `app/db` touches SQL, so swapping SQLite is confined there.
- The service checks `len(results) == len(hk_slices)` before pairing results with the in-HK slices by position - expo_calx has no row id, so order must be preserved end to end.

## Data flow

```
RN app → POST /ingest {pid, features[]}  (or POST /ingest/gpx?pid= <GPX file> → gpx.read_gpx → records)
  → parsers.parse_pings → spikes.remove_spikes → Track → motion.interval_motions → runs.build_runs
  → mtr.find_mtr (+ roads.RoadIndex.nearest) → classify.classify_runs (+ stored place labels)
  → points.build_points → time_split.split_by_hour → request_rows.to_request_rows (in-HK slices)
  → clients.praise_client.calculate_exposure (batches of 40, <=10 in flight)
  → db.exposure_repository.replace_hourly (all slices, replacing the batch's time span)
GET /exposure/hourly/{pid} → [{hour_start_hk, start_time_hk, io, io_source, reason, kind, delta_t_hours, exposure_value, lng, lat, heading_deg, compass, road_name, in_hk}, ...]
PUT/DELETE /place-labels/{label_id} → db.place_label_repository (read by every later batch)
  → services.label_service.reprocess_near (re-runs stored batches near the old/new circle)
```

## Other folders

- `notebooks/pipeline_walkthrough.ipynb` - runs each step on a sample day and prints it; uses its own `walkthrough.db` (recreated each run); expo_calx only with `CALL_EXPO_CALX = True`.
- `testing/io_classification_experiment.ipynb` - the experiment the pipeline was designed in (untracked).
- `data/samples/` - GeoJSON and GPX sample days. Ignored by git except the files already tracked; regression tests skip missing samples.
- `data/road_centrelines/` - the downloaded road centrelines (git-ignored).
