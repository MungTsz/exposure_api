# Exposure API

HTTP API for the RN app: upload raw GPS pings, wait for the background job,
then read per-hour exposure rows for the chart and the map.

Base URL when running locally: `http://localhost:8000`. The server also
serves interactive docs generated from the code:
[Swagger UI](http://localhost:8000/docs) and
[ReDoc](http://localhost:8000/redoc) (both need the server running).

## Contents

- [How it works](#how-it-works)
- [Limits](#limits)
- [Points outside Hong Kong](#points-outside-hong-kong)
- [Try it yourself](#try-it-yourself)
  - [1. Two pings, same spot, 10 minutes apart: one row](#1-two-pings-same-spot-10-minutes-apart-one-row)
  - [2. Two separate spots, same hour: two rows](#2-two-separate-spots-same-hour-two-rows)
  - [3. Staying 25 minutes in one spot: Home](#3-staying-25-minutes-in-one-spot-home)
  - [4. Same stay at noon: Other Indoor](#4-same-stay-at-noon-other-indoor)
  - [5. Outside Hong Kong: returned, but no exposure value](#5-outside-hong-kong-returned-but-no-exposure-value)
  - [6. Crossing the border: one row in HK, one outside](#6-crossing-the-border-one-row-in-hk-one-outside)
  - [7. Body too large: 413, nothing stored](#7-body-too-large-413-nothing-stored)
- [Endpoint reference](#endpoint-reference)
  - [Upload pings](#upload-pings)
  - [Poll a batch](#poll-a-batch)
  - [Read hourly exposure](#read-hourly-exposure)
- [Errors](#errors)
- [Configuration](#configuration)

## How it works

1. **Upload**: `POST /ingest` with a `pid` and the raw ping records. The
   server checks the body size and that it contains at least one valid
   ping, stores it, and answers at once with `202` and a `batch_id`.
2. **Wait**: a background job cleans the pings, groups them into stays,
   cuts them at HK clock hours, and sends the rows **inside Hong Kong** to
   the PRAISE-HK `expo_calx` API. Poll `GET /ingest/{batch_id}` until
   `status` is `done` or `error`.
3. **Read**: `GET /exposure/hourly/{pid}` returns every row, inside and
   outside Hong Kong. Each row has `in_hk`; rows outside HK have
   `exposure_value: null`.

```
RN app                       this server                          expo_calx
  | POST /ingest ------------> size check (413), ping check (400)
  | <------------ 202 batch_id store batch
  |                            background job: pipeline -> in_hk?
  |                              in HK rows -----------------------> scored (<=10 calls at once)
  |                              outside HK rows: stored, not sent
  | GET /ingest/{batch_id} --> status: pending/processing/done/error
  | GET /exposure/hourly/{pid} -> rows with in_hk + exposure_value
```

## Limits

| limit | value | what happens past it |
|---|---|---|
| request body size | **25 MB** (26,214,400 bytes) | `413`, the upload is not stored or processed |
| rows per `expo_calx` call | 40 | rows are split into several calls |
| `expo_calx` calls running at once | **10**, shared by all batches on one server process | further calls wait until one finishes |
| region scored | Hong Kong only | rows outside get `in_hk: false`, `exposure_value: null` |

What 25 MB fits: a full day at 1 ping per second (~86,400 GeoJSON
Features) is about 21 MB; `data/samples/20260830.geojson` (8,139 pings) is
about 2 MB. Upload one day per request.

How long a batch takes: each `expo_calx` call takes about 20-30 s no matter
its size. A batch's calls run in parallel, up to 10 at a time, so a batch of
up to 10 calls (400 rows) finishes in about one call's time. The densest
sample day, 20260830, becomes 270 rows = 7 calls, all sent at once. If
several batches are processed at the same time, they share the same 10
slots. The cap is per server process: running `uvicorn` with N workers
allows up to 10 x N calls at once.

## Points outside Hong Kong

`expo_calx` only calculates exposure for Hong Kong. Each hourly row is
checked against a Hong Kong outline (land and waters) by its `lng`/`lat`:

- **inside** (`in_hk: true`): sent to `expo_calx`; `exposure_value` is a
  number (or `null` if `expo_calx` failed for that single row).
- **outside** (`in_hk: false`): not sent; `exposure_value` is always
  `null`. The row is still returned with its time, `io`, duration and
  position, so the app can draw it, e.g. in a different color, and leave
  it out of exposure totals.

A batch with no points in Hong Kong makes no `expo_calx` call at all and
still finishes as `done`.

The outline is simplified: near the Shenzhen border (Deep Bay, Shenzhen
River, Sha Tau Kok) it is accurate to roughly 1 km, so a point right on the
border may land on the wrong side. It lives in `HK_BOUNDARY` in
[app/config/constants.py](../app/config/constants.py).

## Try it yourself

Start the server first (see [Setup in the README](../README.md#setup)).

Every command below is **Command Prompt (`cmd.exe`)** syntax: the whole
JSON body is wrapped in double quotes with the inner quotes escaped
(`\"`). In bash, git-bash or PowerShell, single-quote the body instead
(`-d '{"pid": ...}'`) and drop the backslashes.

Each scenario uses its own `pid` and fixed, made-up timestamps and
coordinates, so the result is exactly reproducible - **except**
`exposure_value` for rows inside Hong Kong, which comes from the live
`expo_calx` API (it depends on the real air quality at that time and
place). Everything else - number of rows, `io`, `delta_t_hours`,
`lng`/`lat`, `in_hk` - should match exactly.

After each upload, use the `batch_id` from its response to poll the batch,
then read the rows:

```bat
curl http://localhost:8000/ingest/<batch_id>
curl http://localhost:8000/exposure/hourly/<pid>
```

#### 1. Two pings, same spot, 10 minutes apart: one row

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-merge\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\",\"speed\":0.05},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\",\"speed\":0.3},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}}]}"
```

Why one row: both pings are at the same coordinates, and the second one's
`speed` (0.3 m/s) is under the 1.0 m/s threshold, so they form one stay.
10 minutes is under the 20-minute indoor cutoff, so it's `"Outdoor"`, and
both times fall in the same HK-local hour, so nothing is split. The point
is in Clear Water Bay, Hong Kong. Batch: `"status": "done",
"hours_processed": 1, "rows": 1`. `/exposure/hourly/demo-merge`:

```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.16666666666666666, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "in_hk": true}]
```

#### 2. Two separate spots, same hour: two rows

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-split\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\",\"speed\":0.1},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:05:00.000Z\",\"speed\":0.2},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\",\"speed\":0.3},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.265,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:15:00.000Z\",\"speed\":0.1},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.265,22.3399579]}}]}"
```

Why two rows: the first 2 pings are at one spot (`lng 114.2632457`), the
last 2 at another about 180m east (`lng 114.265`) - well outside the ~20m
stay box, so ping 3 starts a new stay. A stay lasts until the next stay
starts, so the first covers 16:00-16:10 (10 min) and the second 16:10-16:15
(5 min, ending at the last ping). Both are under 20 minutes, so both are
`"Outdoor"`, and both land in the same hour - two rows sharing one
`hour_start_hk`, told apart by `start_time_hk`. `/exposure/hourly/demo-split`:

```json
[
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.16666666666666666, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "in_hk": true},
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830001000", "io": "Outdoor", "delta_t_hours": 0.08333333333333333, "exposure_value": <live value>, "lng": 114.265, "lat": 22.3399579, "in_hk": true}
]
```

#### 3. Staying 25 minutes in one spot: Home

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-indoor\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\",\"speed\":0.1},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2633,22.339965]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:25:00.000Z\",\"speed\":0.05},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26322,22.33995]}}]}"
```

Why Home, and why that exact `lng`/`lat`: the 3 pings drift a few meters
but stay inside the ~20m box of the first ping and are slow, so they form
one stay from 16:00 to 16:25 UTC - 25 minutes, past the 20-minute indoor
cutoff, so it's indoor. It starts at 00:00 HK time (16:00 UTC is 00:00
the next day in HK), inside the 00:00-06:00 Home window, so it's `"Home"`.
Home is a `stationary` io type, so the row uses the first ping's exact
coordinates instead of the average. `/exposure/hourly/demo-indoor`:

```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Home", "delta_t_hours": 0.4166666666666667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "in_hk": true}]
```

The request to `expo_calx` used `{"IO": "Home"}` - Home's `api_label`.

#### 4. Same stay at noon: Other Indoor

The same 3 pings as example 3, 12 hours later (04:00 UTC = 12:00 HK):

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-other-indoor\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:10:00.000Z\",\"speed\":0.1},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2633,22.339965]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:25:00.000Z\",\"speed\":0.05},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26322,22.33995]}}]}"
```

Still one 25-minute indoor stay, but it starts at 12:00 HK, outside the
00:00-06:00 Home window, so it's `"Other Indoor"`. Other Indoor is also
`stationary`, so the position is the first ping again.
`/exposure/hourly/demo-other-indoor`:

```json
[{"hour_start_hk": "20260830120000", "start_time_hk": "20260830120000", "io": "Other Indoor", "delta_t_hours": 0.4166666666666667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "in_hk": true}]
```

The request to `expo_calx` used `{"IO": "Other Indoor"}`.

Only the stay's **start** time decides Home vs Other Indoor: an indoor stay
from 05:50 to 08:00 HK is Home as a whole, even after the hour split.

#### 5. Outside Hong Kong: returned, but no exposure value

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-macau\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[113.5439,22.1987]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[113.5439,22.1987]}}]}"
```

Why: both pings are in Macau, so the stay is outside Hong Kong. It is
stored and returned like any other row, but `expo_calx` is never called
(this batch makes no API call) and `exposure_value` is `null`. This
result is fully reproducible - there is no live value. Batch: `"status":
"done", "hours_processed": 1, "rows": 1`. `/exposure/hourly/demo-macau`:

```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.16666666666666666, "exposure_value": null, "lng": 113.5439, "lat": 22.1987, "in_hk": false}]
```

#### 6. Crossing the border: one row in HK, one outside

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-border\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.128,22.501]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.117,22.545]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:20:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.117,22.545]}}]}"
```

Why: the first ping is in Sheung Shui (Hong Kong), the next two are in
Luohu (Shenzhen), about 5 km apart, so they form two stays of 10 minutes
each. Only the Sheung Shui row is sent to `expo_calx`; the Luohu row comes
back with `in_hk: false` and no value. Batch: `"status": "done",
"hours_processed": 1, "rows": 2` (`rows` counts both).
`/exposure/hourly/demo-border`:

```json
[
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.16666666666666666, "exposure_value": <live value>, "lng": 114.128, "lat": 22.501, "in_hk": true},
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830001000", "io": "Outdoor", "delta_t_hours": 0.16666666666666666, "exposure_value": null, "lng": 114.117, "lat": 22.545, "in_hk": false}
]
```

#### 7. Body too large: 413, nothing stored

Making a real 25 MB body by hand is impractical, so lower the limit for
this test. Stop the server, then restart it in `cmd.exe` with a 200-byte
limit:

```bat
set MAX_INGEST_BODY_BYTES=200
uvicorn app.main:app
```

Now send any upload from the scenarios above (they are all over 200
bytes), e.g. scenario 1. The response is:

```
HTTP 413
{"detail": "request body too large: limit is 200 bytes"}
```

No batch is created. Afterwards, close that window or run
`set MAX_INGEST_BODY_BYTES=` before restarting, to go back to the 25 MB
default.

## Endpoint reference

### Upload pings

`POST /ingest` - store a batch of raw pings and queue it for background
processing.

Request body (`Content-Type: application/json`, at most 25 MB):

| field | type | required | notes |
|---|---|---|---|
| `pid` | string | yes | person/device id; every result row is stored under it |
| `features` | array of objects | yes | raw ping records; currently GeoJSON Features (below) |

GeoJSON `Feature` record:

| field | required | used? |
|---|---|---|
| `properties.time` (ISO 8601, UTC) | yes | yes - ping time |
| `geometry.coordinates` (`[lng, lat]`) | yes | yes - position |
| `properties.speed` (m/s) | no | yes, if present - stay speed check. A negative value means "unknown" |
| `.provider`, `.time_long`, `.accuracy`, `.altitude`, `.bearing` | no | no - accepted, ignored |

Records in any other shape are skipped (and logged).

Responses:

| status | body | when |
|---|---|---|
| `202` | `{"status": "queued", "batch_id": 12}` | stored; processing has started in the background |
| `400` | `{"detail": "no valid pings in request"}` | `features` contains no valid ping |
| `413` | `{"detail": "request body too large: limit is 26214400 bytes"}` | body over the size limit; nothing stored |
| `422` | `{"detail": [{"type": "missing", "loc": ["body", "pid"], "msg": "Field required", ...}]}` | `pid` or `features` missing or the wrong type |

Uploading a whole GeoJSON export (e.g. a file in `data/samples/`) works the
same way - put its `features` array in the request body. That needs `jq`
and `$(...)` substitution (git-bash; `cmd.exe` has neither):

```bash
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"local-device\", \"features\": $(jq '.features' data/samples/20260830.geojson)}"
```

### Poll a batch

`GET /ingest/{batch_id}` - the background job's progress. A batch still
`pending` or `processing` when the server restarts is picked up again on
startup.

`200` response:

```json
{"id": 12, "pid": "demo-border", "status": "done", "error_detail": null, "hours_processed": 1, "rows": 2, "created_at": "2026-10-09T05:01:27.912Z", "updated_at": "2026-10-09T05:01:27.958Z"}
```

| field | type | notes |
|---|---|---|
| `id` | int | the `batch_id` |
| `pid` | string | |
| `status` | string | `pending` \| `processing` \| `done` \| `error` |
| `error_detail` | string or `null` | only set when `status` is `error`, e.g. `"expo_calx call timed out: ..."` |
| `hours_processed` | int or `null` | distinct HK clock hours in the batch; set once `done` |
| `rows` | int or `null` | rows stored, inside and outside Hong Kong; set once `done` |
| `created_at` / `updated_at` | string, ISO 8601 UTC | |

`404 {"detail": "unknown batch_id"}` if there is no such batch.

If any `expo_calx` call fails, the whole batch becomes `error` and none of
its rows are stored; upload it again to retry.

### Read hourly exposure

`GET /exposure/hourly/{pid}` - every stored row for one `pid`, ordered by
`start_time_hk`. An unknown `pid` returns `[]`.

Each row is the part of one stay that falls in one HK-local clock hour.
More than one row can share an `hour_start_hk` + `io` (scenario 2).

- **Stacked bar chart**: group by `hour_start_hk` + `io` and sum
  `delta_t_hours` / `exposure_value`. Rows with `in_hk: false` have no
  value - leave them out of the exposure totals or show them separately.
- **Map**: plot each row's `lng`/`lat` in `start_time_hk` order, and
  color rows with `in_hk: false` differently.

| field | type | notes |
|---|---|---|
| `hour_start_hk` | string `YYYYMMDDHH0000`, HK local | hour bucket - group by this (+ `io`) for the chart |
| `start_time_hk` | string `YYYYMMDDHHMMSS`, HK local | this row's own start time; unique per `pid` + `io` - sort by this for the route |
| `io` | string | io type: `"Home"` \| `"Other Indoor"` \| `"Outdoor"` today |
| `delta_t_hours` | number | duration of this row, in hours |
| `exposure_value` | number or `null` | `null` if `in_hk` is `false`, or if `expo_calx` returned `-9` (failed) for this row |
| `lng` / `lat` | number | `moving` io types (Outdoor): average of the stay's pings. `stationary` (Home, Other Indoor): the stay's first ping |
| `in_hk` | boolean | `true` inside Hong Kong (scored); `false` outside (not scored) |

The full `expo_calx` result item for each scored row is also kept in the
DB column `api_result_json`, so new fields the API adds aren't lost.

## Errors

Every error body is JSON with a `detail` field.

| status | endpoint | meaning | what the app should do |
|---|---|---|---|
| `400` | `POST /ingest` | no valid pings in `features` | check the record format |
| `413` | `POST /ingest` | body over 25 MB; nothing stored | split the upload (e.g. per day or half day) |
| `422` | `POST /ingest` | `pid`/`features` missing or wrong type | fix the request body |
| `404` | `GET /ingest/{batch_id}` | unknown `batch_id` | |
| `status: "error"` | `GET /ingest/{batch_id}` | the background job failed; see `error_detail` | upload the batch again later |

## Configuration

Set as environment variables or in `.env` (see
[.env.example](../.env.example) and
[app/config/settings.py](../app/config/settings.py)):

| variable | default | meaning |
|---|---|---|
| `MAX_INGEST_BODY_BYTES` | `26214400` (25 MB) | largest accepted `POST /ingest` body |
| `PRAISE_MAX_CONCURRENT_CALLS` | `10` | `expo_calx` calls running at once, per server process |
| `PRAISE_MAX_ROWS_PER_CALL` | `40` | rows per `expo_calx` call |
| `PRAISE_REQUEST_TIMEOUT_SECONDS` | `60` | timeout for one `expo_calx` call |
