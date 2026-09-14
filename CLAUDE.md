# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small FastAPI backend that takes raw GPS pings from an RN watch/phone app, runs them through an ETL pipeline (spike removal → indoor/outdoor dwell classification → hourly splitting), calls the third-party PRAISE-HK `expo_calx` exposure API, and stores per-hour indoor/outdoor exposure results in SQLite for the app's stacked bar chart.

## Running

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in PRAISE_API_KEY and PRAISE_MYID
uvicorn app.main:app --reload
```

`PRAISE_API_KEY` and `PRAISE_MYID` are read at import time in `praise_client.py` (`os.environ[...]`, not `.get`) — the process will fail to start without them set. `praise_client.py` and `db.py` each call `load_dotenv()` (anchored to their own file's directory via `Path(__file__).resolve().parent / ".env"`) before reading `os.environ`, so `.env` is loaded automatically regardless of the caller's working directory — no manual `export $(cat .env | xargs)` step needed. A real shell-exported env var still takes priority over `.env`, since `load_dotenv()` never overwrites an already-set variable.

Note: this directory is itself the importable package (it has `__init__.py`, and `main.py` uses `from . import db, etl, praise_client`), but it isn't named `app`. Run `uvicorn` from the *parent* directory using this directory's actual name (e.g. `uvicorn exposure_api.main:app --reload`), or rename/symlink the checkout to `app` to match the README's command literally.

There are no tests, lint config, or CI in this repo currently.

## Architecture

Four modules, deliberately kept synchronous and decoupled from FastAPI so the pipeline logic is easy to reason about and reuse:

- **`main.py`** — FastAPI app with two endpoints. `POST /ingest` runs the full pipeline *synchronously in the request*: parse → clean → classify → split by hour → call `expo_calx` → upsert into SQLite. `GET /exposure/hourly/{pid}` reads back rows for the chart. This is a known v1 simplification (see README "Known simplifications") — if request latency becomes a problem, split `/ingest` into "write raw pings, return immediately" + a background worker; `etl.py`, `praise_client.py`, and `db.py` don't need to change, only how `main.py` wires them together.
- **`etl.py`** — the actual pipeline, pure functions/dataclasses, no I/O:
  1. `parse_pings` — raw ping dicts → sorted `Ping` list. Accepts two shapes, detected per record so a batch may mix both: GeoJSON `Feature` (watch-exported test data, `properties.time`/`geometry.coordinates`) and live device-tracking location objects (`react-native-background-geolocation`-style, `timestamp`/`coords.latitude`/`coords.longitude`). Live-tracking records with `sample: true` are dropped (transient, not meant to be persisted); `mock: true` (fake-GPS) records are kept as-is.
  2. `remove_spike_outliers` — deletes any point B from a triple A,B,C where the path sharply doubles back (angle < `SPIKE_ANGLE_DEG`) within `SPIKE_TIME_WINDOW_SECONDS`; re-scans after each deletion since removing B changes the next triple.
  3. `find_dwell_runs` — over the full, continuous (unbucketed) ping stream, splits pings into maximal runs that stay within `DWELL_RADIUS_METERS` of the run's *first* point; a run lasting ≥ `DWELL_INDOOR_MINUTES` is "Indoor", else "Outdoor". Uses a fixed reference point per run, so a slowly drifting walk can split into more runs than ideal (documented known limitation). Classifying before any hour-splitting is deliberate: a dwell run is judged by its true total duration even when it straddles an HK-local clock hour, instead of being cut first and potentially having both halves fall under `DWELL_INDOOR_MINUTES` on their own.
  4. `split_by_hour` — slices each labelled run at HK-local (`HK_OFFSET = UTC+8`) clock-hour boundaries, carrying the run's `io` label into every hour it touches and prorating `delta_t_hours` per hour-slice; a run crossing N hours produces N same-labelled segments.
  5. `to_request_rows` — converts segments into `expo_calx`'s 6-field positional rows `[t, pid, lng, lat, x, delta_t]`.
  All tunable thresholds live as module-level constants at the top of this file — adjust these rather than the logic below them.
- **`praise_client.py`** — client for the legacy PRAISE-HK `expo_calx` endpoint (`todo=expo_calx` on the `praise-ir-cal` uwsgi script, not `get_exposure_list` or the general praise-service base URL). Key behaviors baked in from the reference doc: success is inferred from `exposure` being an array in the response (`msg` only appears on failure); `-9` on an individual row means just that row failed, not the whole call; no documented rate limit or server-confirmed batch size, so `MAX_ROWS_PER_CALL = 40` is this app's own conservative convention; no retry on network failure — it raises straight through, matching documented API behavior.
- **`db.py`** — minimal SQLite storage, one row per `(pid, hour_start_hk, io)` via `INSERT ... ON CONFLICT DO UPDATE`. Public functions (`init_db`, `upsert_hourly`, `get_hourly`) are written to keep swapping SQLite for Postgres/etc. later a change confined to this module. `EXPOSURE_DB_PATH` env var overrides the DB file location (default `exposure.db`).

## Data flow

```
RN app → POST /ingest {pid, features[]}
  → etl.parse_pings → etl.remove_spike_outliers → etl.find_dwell_runs
  → etl.split_by_hour → etl.to_request_rows
  → praise_client.calculate_exposure (batches of 40, sequential HTTP GETs)
  → db.upsert_hourly
GET /exposure/hourly/{pid} → db.get_hourly → [{hour_start_hk, io, delta_t_hours, exposure_value}, ...]
```

`main.py` validates that `expo_calx` returned the same number of rows as requested (`len(exposure_results) != len(segments)`) before zipping results back onto segments by position — the API has no row-level id, so ordering must be preserved end-to-end through batching.
