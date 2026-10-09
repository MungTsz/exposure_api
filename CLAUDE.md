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

Five modules, deliberately kept synchronous and decoupled from FastAPI so the pipeline logic is easy to reason about and reuse:

- **`config.py`** — single home for every tunable threshold that shapes the ETL pipeline's *output*: `etl.py`'s spike/dwell/trail-simplification thresholds. Sweeping parameter combinations while eyeballing results in `testing/testing.ipynb` against `testing/data/*.geojson` means editing this one file. `etl.py`'s `HK_OFFSET` (a fixed timezone fact) and `_API_IO_LABEL` (a fixed third-party API vocabulary mapping), plus `praise_client.py`'s API-integration constants (`MAX_ROWS_PER_CALL`, etc.), deliberately stay local to their own files — they aren't output-shaping and aren't worth sweeping.
- **`main.py`** — FastAPI app with three endpoints. `POST /ingest` persists the raw batch and returns `202 {"status": "queued", "batch_id"}` immediately; the actual pipeline (parse → clean → classify → split by hour → call `expo_calx` → collapse dense same-io runs → upsert into SQLite) runs afterward as a `BackgroundTasks` job (`_process_batch`), since a dense day's batch can need dozens of sequential `expo_calx` calls and running that inline would hold the HTTP request open for minutes. `GET /ingest/{batch_id}` polls that job's status (`queued`/`processing`/`done`/`error`). `GET /exposure/hourly/{pid}` reads back finished rows for the chart. Any batch still `queued`/`processing` from a previous run (e.g. a crash mid-batch) is auto-resumed on startup. `etl.py`, `praise_client.py`, and `db.py` are unchanged by this — only `main.py`'s wiring and `db.py`'s batch-tracking table.

  Scored rows are stored as-is (one per hourly segment). An earlier post-scoring `_merge_dense_rows` step was removed: it only kicked in at 100-row clusters and collapsed them to one dot, which broke the map track. Row thinning now happens upstream in `etl.simplify_outdoor_trails`, which also cuts `expo_calx` calls.
- **`etl.py`** — the actual pipeline, pure functions/dataclasses, no I/O:
  1. `parse_pings` — raw ping dicts → sorted `Ping` list. Accepts two shapes, detected per record so a batch may mix both: GeoJSON `Feature` (watch-exported test data, `properties.time`/`geometry.coordinates`) and live device-tracking location objects (`react-native-background-geolocation`-style, `timestamp`/`coords.latitude`/`coords.longitude`). Live-tracking records with `sample: true` are dropped (transient, not meant to be persisted); `mock: true` (fake-GPS) records are kept as-is.
  2. `remove_spike_outliers` — deletes any point B from a triple A,B,C where the path sharply doubles back (angle < `config.SPIKE_ANGLE_DEG`) within `config.SPIKE_TIME_WINDOW_SECONDS`; re-scans after each deletion since removing B changes the next triple.
  3. `find_dwell_runs` — over the full, continuous (unbucketed) ping stream, splits pings into maximal runs that stay within `config.DWELL_DEGREE_THRESHOLD` (a flat lat/lng degree box, not haversine — deliberately simple) of the run's *first* point AND report a speed below `config.SPEED_THRESHOLD_MPS` (pings with no speed reading are judged on distance alone); a run lasting ≥ `config.DWELL_INDOOR_MINUTES` is "Indoor", else "Outdoor" (a genuinely-indoor stop and an outdoor wait of the same duration — e.g. waiting for a bus — are indistinguishable by this signal alone; tuning these three `config.py` values is the only lever, not a structural fix — documented known limitation). An "Indoor" run collapses to its anchor (first) point — GPS drift while dwelling shouldn't read as distinct locations; an "Outdoor" run keeps the average of its points, as before. Uses a fixed reference point per run, so a slowly drifting walk can split into more runs than ideal (documented known limitation). Classifying before any hour-splitting is deliberate: a dwell run is judged by its true total duration even when it straddles an HK-local clock hour, instead of being cut first and potentially having both halves fall under `config.DWELL_INDOOR_MINUTES` on their own.
  4. `simplify_outdoor_trails` — while moving, 1 Hz tracking makes nearly every ping its own ~1 s Outdoor run (it leaves the dwell box immediately), flooding `expo_calx` and the app's map. Consecutive Outdoor runs are grouped into trails (broken wherever a run lasts > `config.MAX_GAP_SECONDS` — a GPS dropout — or consecutive points are > `config.MAX_GAP_METERS` apart, so both ends of e.g. a tunnel gap survive). Each trail is thinned by `_trail_dot_keep`: starting from the last kept point, following points are dropped while they stay inside a `config.TRAIL_DOT_DEGREE_THRESHOLD` lat/lng box of it (0.0027° ≈ 300 m, flat box like the dwell test) AND less than `config.TRAIL_DOT_MAX_SECONDS` (300 s) have passed; the first point breaking either condition is kept and becomes the new reference, giving roughly one dot per ~300 m (walking and faster are distance-triggered; a pause or slow stroll still gets a dot every 5 min). A trail's last point gets no special treatment — forcing it in left near-duplicate dots at every trail end. This replaced an earlier Douglas–Peucker pass, which clustered and doubled up dots around bends. Each kept point keeps its own position and absorbs the time of the dropped points after it — total time is preserved exactly. Indoor runs pass through. On 20260830: 4147 dwell runs → 238 dots, 262 rows, 7 API calls, median dot spacing ~304 m (Douglas–Peucker gave 644 rows / 17 calls; ~100 m / 90 s gave 582 rows / 15 calls).
  5. `split_by_hour` — slices each labelled run at HK-local (`HK_OFFSET = UTC+8`) clock-hour boundaries, carrying the run's `io` label into every hour it touches and prorating `delta_t_hours` per hour-slice; a run crossing N hours produces N same-labelled segments. This split has to happen *before* `to_request_rows`/`calculate_exposure`, not just before storage: `expo_calx` computes exposure from that hour's AQHI reading, so a single Outdoor run spanning e.g. 15:30–18:00 must reach the API as three separate rows (15:30–15:59, 16:00–16:59, 17:00–17:59) rather than one row covering the whole 2.5-hour span — otherwise it'd be scored against one hour's AQHI instead of each hour's own.
  6. `to_request_rows` — converts segments into `expo_calx`'s 6-field positional rows `[t, pid, lng, lat, x, delta_t]`, translating the `io` label to the API's expected value via `_API_IO_LABEL` (`"Indoor"` → `"Home"`, `"Outdoor"` → `"Outdoor"`) — `expo_calx` doesn't recognize `"Indoor"` itself; the label stays `"Indoor"` everywhere else (dwell classification, DB, chart).
  Tunable thresholds (other than `HK_OFFSET`) now live in `config.py`, which `etl.py` imports — adjust values there rather than the logic below.
- **`praise_client.py`** — client for the legacy PRAISE-HK `expo_calx` endpoint (`todo=expo_calx` on the `praise-ir-cal` uwsgi script, not `get_exposure_list` or the general praise-service base URL). Key behaviors baked in from the reference doc: success is inferred from `exposure` being an array in the response (`msg` only appears on failure); `-9` on an individual row means just that row failed, not the whole call; no documented rate limit or server-confirmed batch size, so `MAX_ROWS_PER_CALL = 40` is this app's own conservative convention; no retry on network failure — it raises straight through, matching documented API behavior.
- **`db.py`** — minimal SQLite storage, one row per `(pid, io, start_time_hk)` via `INSERT ... ON CONFLICT DO UPDATE` — keyed on each run's own precise start time (not just its hour bucket) so multiple same-labelled runs landing in the same clock hour (e.g. two separate outdoor trips) each get their own row instead of overwriting each other; `hour_start_hk` stays as a plain column callers group by for the chart. Public functions (`init_db`, `upsert_hourly`, `get_hourly`) are written to keep swapping SQLite for Postgres/etc. later a change confined to this module. `EXPOSURE_DB_PATH` env var overrides the DB file location (default `exposure.db`).

## Data flow

```
RN app → POST /ingest {pid, features[]}
  → etl.parse_pings → etl.remove_spike_outliers → etl.find_dwell_runs
  → etl.simplify_outdoor_trails → etl.split_by_hour → etl.to_request_rows
  → praise_client.calculate_exposure (batches of 40, sequential HTTP GETs)
  → db.upsert_hourly
GET /exposure/hourly/{pid} → db.get_hourly → [{hour_start_hk, start_time_hk, io, delta_t_hours, exposure_value, lng, lat}, ...]
```

`main.py` validates that `expo_calx` returned the same number of rows as requested (`len(exposure_results) != len(segments)`) before zipping results back onto segments by position — the API has no row-level id, so ordering must be preserved end-to-end through batching.
