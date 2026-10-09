# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A production FastAPI backend that takes raw GPS pings from an RN watch/phone app, runs them through an ETL pipeline (spike removal → group into stays → label each stay with an io type → thin moving routes → split by hour), calls the third-party PRAISE-HK `expo_calx` exposure API for the slices inside Hong Kong, and stores per-hour exposure results (with an `in_hk` flag; outside-HK rows have no value) in SQLite for the app's stacked bar chart and map. API documentation for app developers: `docs/API.md`.

## Running

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in PRAISE_API_KEY and PRAISE_MYID
uvicorn app.main:app --reload    # from the repo root
pip install -r requirements-dev.txt && pytest
```

All settings come from `app/config/settings.py` (pydantic-settings): env vars first, then the repo-root `.env`. `PRAISE_API_KEY`/`PRAISE_MYID` have no default, so `create_app()` fails fast without them. Tests set dummy values and a temp DB in `tests/conftest.py` and mock `expo_calx`, so they need no key or network.

## Rules for this codebase

- **No hard-coded values.** Every parameter goes in `app/config/`: deployment values in `settings.py`, fixed facts (timezone, formats, API names, statuses, `HK_BOUNDARY`) in `constants.py`, ETL thresholds in `pipeline.py`, per-io-type names/labels/thresholds in `io_types.py`.
- **Plain terms**: ping, spike, stay, io type, route, route point, hourly slice (see the README glossary). Don't reintroduce "dwell", "trail" or "anchor".
- **`io`** is the field name everywhere (code, DB column, API response, expo_calx `{"IO": ...}`).
- **Pipeline output must not change by accident**: `tests/test_regression.py` compares against `tests/fixtures/golden/`. Regenerate with `python -m tests.fixtures.update_golden` only for an intended change.
- Repo files are English only.

## Architecture

```
app/main.py           create_app(); lifespan inits DB and resumes pending/processing batches in a thread
app/api/              schemas.py + body_limit.py (413 middleware) + routes/ingest.py (POST /ingest, GET /ingest/{id}) + routes/exposure.py (GET /exposure/hourly/{pid})
app/services/         ingest_service.process_batch: run_pipeline -> to_request_rows (in-HK slices only) -> praise_client -> exposure_repository (all slices)
app/etl/              pure functions, no I/O; pipeline.run_pipeline defines the step order
app/io_types/         registry (ordered IO_TYPES, classify, label_stays) + base.IoType + rules/<type>.py
app/clients/          praise_client: expo_calx calls, batching, parallel up to a process-wide cap, ExposureResult(t, pid, value, raw)
app/db/               connection (schema, init_db with in-place column adds) + exposure_repository + batch_repository
```

- **`POST /ingest`** rejects bodies over `max_ingest_body_bytes` (25 MB) with 413 before reading them (`api/body_limit.py`, also counts chunked bodies), then stores the raw batch and returns `202 {"status": "queued", "batch_id"}`; processing runs as a `BackgroundTasks` job because a dense day needs several ~20-30s `expo_calx` calls. `process_batch` never raises: any failure is recorded as batch status `error` (secrets redacted).
- **ETL steps** (`app/etl/`):
  1. `parsers.parse_pings` - one module per input format in a `PARSERS` list; currently only `geojson.py`. Unrecognized records are skipped and logged. Live device tracking will be added later as another parser module.
  2. `spikes.remove_spike_outliers` - drops B of A,B,C when the angle at B < `SPIKE_ANGLE_DEG` within `SPIKE_TIME_WINDOW_SECONDS`; re-scans after each deletion.
  3. `stays.group_into_stays` - a stay continues while each ping is within `STAY_DEGREE_THRESHOLD` (flat lat/lng box) of the stay's first ping and its speed (if any) < `SPEED_THRESHOLD_MPS`. A stay ends when the next stay starts. Unlabelled here.
  4. `io_types.registry.label_stays` - first `IoType` in `IO_TYPES` whose `matches(stay)` is true wins; `OUTDOOR` is the always-true fallback and must be last. Done before the hour split so a stay is judged by its full duration.
  5. `routes.simplify_routes` - consecutive `MOVING` stays with the same io form a route (broken by a stay > `MAX_GAP_SECONDS` or a jump > `MAX_GAP_METERS`); a route keeps a point once it leaves a `ROUTE_POINT_DEGREE_THRESHOLD` box of the last kept point or after `ROUTE_POINT_MAX_SECONDS`. Kept points absorb the time and pings of dropped ones, so total time is preserved. On 20260830: 262 rows, 7 API calls.
  6. `time_split.split_by_hour` - cuts at HK-local (UTC+8) clock hours, prorating `delta_t_hours`. Required before scoring: expo_calx scores each row against that hour's AQHI.
  7. `region.mark_in_hk` - sets `HourlySlice.in_hk` (bbox reject, then ray-casting point-in-polygon on `constants.HK_BOUNDARY`). Runs after the pipeline's geometry steps, so it never changes their output.
  8. `request_rows.to_request_rows` - called on `PipelineResult.hk_slices` only. `[t, pid, lng, lat, {"IO": api_label}, delta_t]`; the io name is translated to its `api_label` only here (Indoor → Home).
- **io types**: `IoKind.STATIONARY` stays are placed at their first ping; `MOVING` stays at the average of their pings and get route thinning. No pipeline code checks a specific io name - behaviour comes from `kind`. Adding one = section in `config/io_types.py` + `rules/<type>.py` + one line in `IO_TYPES` (README has the steps). Known limitation: an Indoor stay and an equally long outdoor wait are indistinguishable by duration alone.
- **`praise_client`** (legacy `todo=expo_calx` on the `praise-ir-cal` uwsgi script): success is inferred from `exposure` being a list (`msg` only on failure); `-9` on one row means only that row failed (`value=None`); batches of `praise_max_rows_per_call` (40, own convention - no documented limit) run in parallel on one shared `ThreadPoolExecutor` of `praise_max_concurrent_calls` (10) workers, so the cap holds across concurrent batches; results keep row order; on a failure, unstarted calls are cancelled; no retry. Each result keeps the API's full item in `raw`, stored in the DB `api_result_json` column, so new API fields aren't lost.
- **DB**: `hourly_exposure` keyed on `(pid, io, start_time_hk)`, with `in_hk` (1/0; outside-HK rows have NULL `exposure_value` and `api_result_json`) so several same-io stays in one hour each keep a row; `hour_start_hk` is the chart's group-by column. Batch statuses are `constants.BatchStatus` (`pending/processing/done/error`). Only `app/db` touches SQL, so swapping SQLite is confined there.
- The service checks `len(results) == len(hk_slices)` before pairing results with the in-HK slices by position - expo_calx has no row id, so order must be preserved end to end.

## Data flow

```
RN app → POST /ingest {pid, features[]}
  → etl.parsers.parse_pings → etl.spikes.remove_spike_outliers → etl.stays.group_into_stays
  → io_types.registry.label_stays → etl.routes.simplify_routes → etl.time_split.split_by_hour
  → etl.region.mark_in_hk → etl.request_rows.to_request_rows (in-HK slices)
  → clients.praise_client.calculate_exposure (batches of 40, <=10 in flight)
  → db.exposure_repository.upsert_hourly (all slices)
GET /exposure/hourly/{pid} → [{hour_start_hk, start_time_hk, io, delta_t_hours, exposure_value, lng, lat, in_hk}, ...]
```

## Other folders

- `notebooks/pipeline_walkthrough.ipynb` - runs each step on a sample day; uses its own `test_exposure.db`.
- `data/samples/` - GeoJSON sample days (+ derived CSVs). Ignored by git except the files already tracked; regression tests skip missing samples.
