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

- **`main.py`** — FastAPI app with three endpoints. `POST /ingest` persists the raw batch and returns `202 {"status": "queued", "batch_id"}` immediately; the actual pipeline (parse → clean → classify → split by hour → call `expo_calx` → collapse dense same-io runs → upsert into SQLite) runs afterward as a `BackgroundTasks` job (`_process_batch`), since a dense day's batch can need dozens of sequential `expo_calx` calls and running that inline would hold the HTTP request open for minutes. `GET /ingest/{batch_id}` polls that job's status (`queued`/`processing`/`done`/`error`). `GET /exposure/hourly/{pid}` reads back finished rows for the chart. Any batch still `queued`/`processing` from a previous run (e.g. a crash mid-batch) is auto-resumed on startup. `etl.py`, `praise_client.py`, and `db.py` are unchanged by this — only `main.py`'s wiring and `db.py`'s batch-tracking table.

  Before storing, `_merge_dense_rows` collapses every full run of `DENSE_MERGE_CHUNK_SIZE` (100) consecutive same-`io` scored rows into one summary row: `find_dwell_runs`'s box is anchored to each run's *first* point, so continuous movement (almost always `"Outdoor"`) can emit one short run per ping, and a dense batch (e.g. ~8000 pings) can turn into hundreds/thousands of rows that make the app's chart lag when rendered. Each merged row takes its `lng`/`lat`/`hour_start_hk`/`start_time_hk` from the chunk's *last* row, sums `delta_t_hours`, and sums the chunk's non-`None` `exposure_value`s (`None` only if every row in the chunk failed to score). This runs *after* `expo_calx` scores every original segment individually — merging never changes what's sent to the API, only what's persisted/returned afterward. Grouping deliberately ignores `hour_start_hk` (pure chronological same-`io` sequence), so a merged row can pull summed time from an earlier hour into whichever hour its last constituent row falls in — an accepted tradeoff for capping row count in pathological dense-movement batches. A same-`io` streak shorter than the chunk size (including the leftover tail after full chunks) is left as individual rows.
- **`etl.py`** — the actual pipeline, pure functions/dataclasses, no I/O:
  1. `parse_pings` — raw ping dicts → sorted `Ping` list. Accepts two shapes, detected per record so a batch may mix both: GeoJSON `Feature` (watch-exported test data, `properties.time`/`geometry.coordinates`) and live device-tracking location objects (`react-native-background-geolocation`-style, `timestamp`/`coords.latitude`/`coords.longitude`). Live-tracking records with `sample: true` are dropped (transient, not meant to be persisted); `mock: true` (fake-GPS) records are kept as-is.
  2. `remove_spike_outliers` — deletes any point B from a triple A,B,C where the path sharply doubles back (angle < `SPIKE_ANGLE_DEG`) within `SPIKE_TIME_WINDOW_SECONDS`; re-scans after each deletion since removing B changes the next triple.
  3. `find_dwell_runs` — over the full, continuous (unbucketed) ping stream, splits pings into maximal runs that stay within `DWELL_DEGREE_THRESHOLD` (a flat lat/lng degree box, not haversine — deliberately simple) of the run's *first* point AND report a speed below `SPEED_THRESHOLD_MPS` (pings with no speed reading are judged on distance alone); a run lasting ≥ `DWELL_INDOOR_MINUTES` is "Indoor", else "Outdoor". An "Indoor" run collapses to its anchor (first) point — GPS drift while dwelling shouldn't read as distinct locations; an "Outdoor" run keeps the average of its points, as before. Uses a fixed reference point per run, so a slowly drifting walk can split into more runs than ideal (documented known limitation). Classifying before any hour-splitting is deliberate: a dwell run is judged by its true total duration even when it straddles an HK-local clock hour, instead of being cut first and potentially having both halves fall under `DWELL_INDOOR_MINUTES` on their own.
  4. `split_by_hour` — slices each labelled run at HK-local (`HK_OFFSET = UTC+8`) clock-hour boundaries, carrying the run's `io` label into every hour it touches and prorating `delta_t_hours` per hour-slice; a run crossing N hours produces N same-labelled segments. This split has to happen *before* `to_request_rows`/`calculate_exposure`, not just before storage: `expo_calx` computes exposure from that hour's AQHI reading, so a single Outdoor run spanning e.g. 15:30–18:00 must reach the API as three separate rows (15:30–15:59, 16:00–16:59, 17:00–17:59) rather than one row covering the whole 2.5-hour span — otherwise it'd be scored against one hour's AQHI instead of each hour's own.
  5. `to_request_rows` — converts segments into `expo_calx`'s 6-field positional rows `[t, pid, lng, lat, x, delta_t]`, translating the `io` label to the API's expected value via `_API_IO_LABEL` (`"Indoor"` → `"Home"`, `"Outdoor"` → `"Outdoor"`) — `expo_calx` doesn't recognize `"Indoor"` itself; the label stays `"Indoor"` everywhere else (dwell classification, DB, chart).
  All tunable thresholds live as module-level constants at the top of this file — adjust these rather than the logic below them.
- **`praise_client.py`** — client for the legacy PRAISE-HK `expo_calx` endpoint (`todo=expo_calx` on the `praise-ir-cal` uwsgi script, not `get_exposure_list` or the general praise-service base URL). Key behaviors baked in from the reference doc: success is inferred from `exposure` being an array in the response (`msg` only appears on failure); `-9` on an individual row means just that row failed, not the whole call; no documented rate limit or server-confirmed batch size, so `MAX_ROWS_PER_CALL = 40` is this app's own conservative convention; no retry on network failure — it raises straight through, matching documented API behavior.
- **`db.py`** — minimal SQLite storage, one row per `(pid, io, start_time_hk)` via `INSERT ... ON CONFLICT DO UPDATE` — keyed on each run's own precise start time (not just its hour bucket) so multiple same-labelled runs landing in the same clock hour (e.g. two separate outdoor trips) each get their own row instead of overwriting each other; `hour_start_hk` stays as a plain column callers group by for the chart. Public functions (`init_db`, `upsert_hourly`, `get_hourly`) are written to keep swapping SQLite for Postgres/etc. later a change confined to this module. `EXPOSURE_DB_PATH` env var overrides the DB file location (default `exposure.db`).

## Data flow

```
RN app → POST /ingest {pid, features[]}
  → etl.parse_pings → etl.remove_spike_outliers → etl.find_dwell_runs
  → etl.split_by_hour → etl.to_request_rows
  → praise_client.calculate_exposure (batches of 40, sequential HTTP GETs)
  → main._merge_dense_rows (collapses dense same-io runs, 100 rows -> 1)
  → db.upsert_hourly
GET /exposure/hourly/{pid} → db.get_hourly → [{hour_start_hk, start_time_hk, io, delta_t_hours, exposure_value, lng, lat}, ...]
```

`main.py` validates that `expo_calx` returned the same number of rows as requested (`len(exposure_results) != len(segments)`) before zipping results back onto segments by position — the API has no row-level id, so ordering must be preserved end-to-end through batching.
