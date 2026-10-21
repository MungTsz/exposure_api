# Exposure API

HTTP API for the RN app: upload raw GPS pings, wait for the background job,
then read per-hour exposure rows for the chart and the map. Every moment of
the day gets an io type - `Home`, `Office`, `School`, `Other Indoor`,
`Outdoor`, `MTR` or `Vehicle`. Users can also label places (Home, Office,
School, Other Indoor); those labels decide the io type of stays at that
place, for every user.

Base URL when running locally: `http://localhost:8000`. The server also
serves interactive docs generated from the code:
[Swagger UI](http://localhost:8000/docs) and
[ReDoc](http://localhost:8000/redoc) (both need the server running).

## Contents

- [How it works](#how-it-works)
- [How the io type is decided](#how-the-io-type-is-decided)
- [Limits](#limits)
- [Points outside Hong Kong](#points-outside-hong-kong)
- [Try it yourself](#try-it-yourself)
  - [1. Two pings, same spot, 10 minutes apart: one row](#1-two-pings-same-spot-10-minutes-apart-one-row)
  - [2. Moving 3 km while GPS was dark: MTR](#2-moving-3-km-while-gps-was-dark-mtr)
  - [3. Staying 25 minutes in one spot at night: Home](#3-staying-25-minutes-in-one-spot-at-night-home)
  - [4. Same stay at noon: Other Indoor](#4-same-stay-at-noon-other-indoor)
  - [5. Outside Hong Kong: returned, but no exposure value](#5-outside-hong-kong-returned-but-no-exposure-value)
  - [6. Crossing the border: one row in HK, one outside](#6-crossing-the-border-one-row-in-hk-one-outside)
  - [7. Body too large: 413, nothing stored](#7-body-too-large-413-nothing-stored)
  - [8. A place label: the noon stay becomes Office](#8-a-place-label-the-noon-stay-becomes-office)
- [Place labels](#place-labels)
- [Endpoint reference](#endpoint-reference)
  - [Upload pings](#upload-pings)
  - [Upload a GPX file](#upload-a-gpx-file)
  - [Poll a batch](#poll-a-batch)
  - [Read hourly exposure](#read-hourly-exposure)
  - [Delete one day](#delete-one-day)
  - [Create or update a place label](#create-or-update-a-place-label)
  - [Delete a place label](#delete-a-place-label)
- [Errors](#errors)
- [Configuration](#configuration)

## How it works

1. **Upload**: `POST /ingest` with a `pid` and the raw ping records (or
   `POST /ingest/gpx?pid=...` with a whole GPX file). Only each ping's
   **time and position** are read. The server checks the body size and
   that it contains at least one valid ping, stores it, and answers at
   once with `202` and a `batch_id`.
2. **Wait**: a background job splits the day into runs (staying, walking,
   in a vehicle, on the MTR), gives each run an io type, turns it into
   points (one per stay, a thinned line per route), cuts their time at HK
   clock hours, and sends the rows **inside Hong Kong** to the PRAISE-HK
   `expo_calx` API. Poll `GET /ingest/{batch_id}` until `status` is `done`
   or `error`.
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
  | GET /exposure/hourly/{pid} -> rows with io, in_hk, exposure_value
```

## How the io type is decided

The day is cut into **runs**: stretches of the same kind of movement
(still, walk, vehicle, or a jump - moving 300 m or more between two pings,
i.e. travelling while GPS was dark). Each run then gets an io type:

| run | io | `reason` |
|---|---|---|
| a jump (underground: GPS dark between two stations; at most 5 jumps in a row - more is a phone logging rarely on the road, treated as a vehicle run below) | `MTR` | `jump` |
| a vehicle run whose pings keep 15 m or more from every road centreline (a train runs beside the roads, a car on them) | `MTR` | `rail` |
| a stop of up to 10 minutes between two MTR runs | `MTR` | `interchange` |
| any other vehicle run (taxi, bus, minibus, shuttle bus, car, motorcycle) | `Vehicle` | `vehicle` |
| walking | `Outdoor` | `walk` |
| a stay inside a [place label](#place-labels) | the label's category | `place_label` |
| a stay of 20+ minutes overlapping 00:00-06:00 HK | `Home` | `night_stay` |
| a stay within ~100 m of such a night stay (same upload) | `Home` | `near_night_stay` |
| a stay shorter than 20 minutes | `Outdoor` | `short_stop` |
| any other stay | `Other Indoor` | `long_stay` |

Rules are checked top to bottom within the stays; place labels come first,
even at night. `Office` and `School` are never guessed: without a label
they are `Other Indoor`.

Then each run becomes **points**, which is what the rows are made of:

- a stay (or an MTR interchange) is **one point**, at the median of its
  pings - indoor GPS drift doesn't move it;
- a route (walk, vehicle, MTR) keeps a **thinned line** of its pings: every
  turn is kept (the line never strays more than 15 m from the GPS path),
  with at least one point every ~300 m, and both ends of every GPS gap
  (e.g. the two stations of an underground ride). Each route point has
  the direction to the next point (`heading_deg`, `compass`), and a
  Vehicle point the road it is on (`road_name`).

Each point owns the time until the next point; that time is cut at HK
clock hours (expo_calx scores each row against that hour's AQHI), giving
one row per point per hour.

## Limits

| limit | value | what happens past it |
|---|---|---|
| request body size | **5 MB** (5,242,880 bytes) | `413`, the upload is not stored or processed |
| rows per `expo_calx` call | 40 | rows are split into several calls |
| `expo_calx` calls running at once | **10**, shared by all batches on one server process | further calls wait until one finishes |
| region scored | Hong Kong only | rows outside get `in_hk: false`, `exposure_value: null` |

What 5 MB fits: about 21,000 GeoJSON Features (~250 bytes each) or about
40,000 GPX track points (~130 bytes each). A phone logs far fewer pings
while it stays put, so a whole day usually fits:
`data/samples/20260830.geojson` (8,139 pings, 24 hours) is about 2 MB. A
day logged at 1 ping per second all the time (~86,400 Features, about
21 MB) does not: split it into several uploads of a few hours each.

How long a batch takes: each `expo_calx` call takes about 20-30 s no matter
its size. A batch's calls run in parallel, up to 10 at a time, so a batch of
up to 10 calls (400 rows) finishes in about one call's time. The densest
sample day, 20260830 (8,139 pings), becomes 520 points and 544 rows = 14
calls: 10 at once, then the other 4. If several batches are processed at
the same time, they share the same 10 slots. The cap is per server
process: running `uvicorn` with N workers allows up to 10 x N calls at
once.

## Points outside Hong Kong

`expo_calx` only calculates exposure for Hong Kong. Each row is checked
against a Hong Kong outline (land and waters) by its `lng`/`lat`:

- **inside** (`in_hk: true`): sent to `expo_calx`; `exposure_value` is a
  number (or `null` if `expo_calx` failed for that single row).
- **outside** (`in_hk: false`): not sent; `exposure_value` is always
  `null`. The row is still returned with its time, `io`, duration and
  position, so the app can draw it, e.g. in a different color, and leave
  it out of exposure totals.

A batch with no points in Hong Kong makes no `expo_calx` call at all and
still finishes as `done`. The road data only covers Hong Kong, so outside
HK a vehicle run is always `Vehicle` (never `MTR` by the rail test).

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
place). Everything else - number of rows, `io`, `reason`,
`delta_t_hours`, `lng`/`lat`, `in_hk` - should match exactly. Run them
before example 8: its place label changes examples 1, 3 and 4.

After each upload, use the `batch_id` from its response to poll the batch,
then read the rows:

```bat
curl http://localhost:8000/ingest/<batch_id>
curl http://localhost:8000/exposure/hourly/<pid>
```

#### 1. Two pings, same spot, 10 minutes apart: one row

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-merge\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}}]}"
```

Why one row: the phone didn't move, so the two pings are one stay - one
point. 10 minutes is under the 20-minute indoor cutoff, so it's
`"Outdoor"` (`reason: "short_stop"`), and both times fall in the same
HK-local hour, so nothing is split. The point is in Clear Water Bay, Hong
Kong. Batch: `"status": "done", "hours_processed": 1, "rows": 1`.
`/exposure/hourly/demo-merge`:

```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "io_source": "inferred", "reason": "short_stop", "kind": "stay", "delta_t_hours": 0.16666666666666666, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true}]
```

#### 2. Moving 3 km while GPS was dark: MTR

Two pings at one station, no ping for 6 minutes, then two pings 3 km away:

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-mtr\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T01:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26,22.3074]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T01:01:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26,22.3074]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T01:07:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2366,22.292]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T01:08:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2366,22.292]}}]}"
```

Why: moving 300 m or more between two pings means travelling while GPS was
dark - an underground ride, so the 6 minutes between the stations are
`"MTR"` (`reason: "jump"`). That ride is a route: its point is at the
first station, with the direction of travel (`compass: "SW"`). The minute
at each station is a short stop (`"Outdoor"`). Batch: `"rows": 3`.
`/exposure/hourly/demo-mtr`:

```json
[
  {"hour_start_hk": "20260830090000", "start_time_hk": "20260830090000", "io": "Outdoor", "io_source": "inferred", "reason": "short_stop", "kind": "stay", "delta_t_hours": 0.016666666666666666, "exposure_value": <live value>, "lng": 114.26, "lat": 22.3074, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true},
  {"hour_start_hk": "20260830090000", "start_time_hk": "20260830090100", "io": "MTR", "io_source": "inferred", "reason": "jump", "kind": "route", "delta_t_hours": 0.1, "exposure_value": <live value>, "lng": 114.26, "lat": 22.3074, "heading_deg": 234.57358418287038, "compass": "SW", "road_name": null, "in_hk": true},
  {"hour_start_hk": "20260830090000", "start_time_hk": "20260830090700", "io": "Outdoor", "io_source": "inferred", "reason": "short_stop", "kind": "stay", "delta_t_hours": 0.016666666666666666, "exposure_value": <live value>, "lng": 114.2366, "lat": 22.292, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true}
]
```

The request to `expo_calx` used `{"IO": "MTR"}` for the middle row.

#### 3. Staying 25 minutes in one spot at night: Home

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-indoor\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2633,22.339965]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:25:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26322,22.33995]}}]}"
```

Why Home, and why that exact `lng`/`lat`: the 3 pings drift a few metres -
GPS jitter, not movement - so they form one stay from 16:00 to 16:25 UTC,
25 minutes, past the 20-minute indoor cutoff. It is at 00:00 HK time
(16:00 UTC is 00:00 the next day in HK), inside the 00:00-06:00 night
window, so it's `"Home"` (`reason: "night_stay"`). A stay's point is the
median of its pings: here the middle ping on both axes, which is the first
one. `/exposure/hourly/demo-indoor`:

```json
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Home", "io_source": "inferred", "reason": "night_stay", "kind": "stay", "delta_t_hours": 0.4166666666666667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true}]
```

The request to `expo_calx` used `{"IO": "Home"}`.

#### 4. Same stay at noon: Other Indoor

The same 3 pings as example 3, 12 hours later (04:00 UTC = 12:00 HK):

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-other-indoor\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2633,22.339965]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:25:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26322,22.33995]}}]}"
```

Still one 25-minute stay, but 12:00-12:25 HK doesn't touch the 00:00-06:00
night window, so it's `"Other Indoor"` (`reason: "long_stay"`).
`/exposure/hourly/demo-other-indoor`:

```json
[{"hour_start_hk": "20260830120000", "start_time_hk": "20260830120000", "io": "Other Indoor", "io_source": "inferred", "reason": "long_stay", "kind": "stay", "delta_t_hours": 0.4166666666666667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true}]
```

The request to `expo_calx` used `{"IO": "Other Indoor"}`.

A stay is Home if **any** part of it falls in 00:00-06:00 HK: a stay from
22:00 to 08:00 is Home as a whole, even after the hour split.

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
[{"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "Outdoor", "io_source": "inferred", "reason": "short_stop", "kind": "stay", "delta_t_hours": 0.16666666666666666, "exposure_value": null, "lng": 113.5439, "lat": 22.1987, "heading_deg": null, "compass": null, "road_name": null, "in_hk": false}]
```

#### 6. Crossing the border: one row in HK, one outside

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-border\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.128,22.501]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.117,22.545]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-29T16:20:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.117,22.545]}}]}"
```

Why: the first ping is in Sheung Shui (Hong Kong), the next two are in
Luohu (Shenzhen), about 5 km away with no ping in between - a jump, so the
first 10 minutes are `"MTR"` (heading north). The 10 minutes in Luohu are
a short stop outside Hong Kong. Only the Sheung Shui row is sent to
`expo_calx`; the Luohu row comes back with `in_hk: false` and no value.
Batch: `"status": "done", "hours_processed": 1, "rows": 2` (`rows`
counts both). `/exposure/hourly/demo-border`:

```json
[
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": "MTR", "io_source": "inferred", "reason": "jump", "kind": "route", "delta_t_hours": 0.16666666666666666, "exposure_value": <live value>, "lng": 114.128, "lat": 22.501, "heading_deg": 346.9945592676879, "compass": "N", "road_name": null, "in_hk": true},
  {"hour_start_hk": "20260830000000", "start_time_hk": "20260830001000", "io": "Outdoor", "io_source": "inferred", "reason": "short_stop", "kind": "stay", "delta_t_hours": 0.16666666666666666, "exposure_value": null, "lng": 114.117, "lat": 22.545, "heading_deg": null, "compass": null, "road_name": null, "in_hk": false}
]
```

#### 7. Body too large: 413, nothing stored

Making a real 5 MB body by hand is impractical, so lower the limit for
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
`set MAX_INGEST_BODY_BYTES=` before restarting, to go back to the default.

#### 8. A place label: the noon stay becomes Office

First label the spot from examples 3 and 4 as an Office, a 25 m circle
around the first ping:

```bat
curl -X PUT http://localhost:8000/place-labels/demo-office -H "Content-Type: application/json" -d "{\"contributor_id\":\"c-demo\",\"category\":\"Office\",\"lat\":22.3399579,\"lng\":114.2632457,\"radius_m\":25,\"updated_at\":\"2026-10-13T09:15:00+08:00\"}"
```

The response is `201` (created) with the stored label; `updated_at` comes
back in UTC:

```json
{"label_id": "demo-office", "contributor_id": "c-demo", "category": "Office", "lat": 22.3399579, "lng": 114.2632457, "radius_m": 25.0, "updated_at": "2026-10-13T01:15:00Z"}
```

Sending the same command again returns `200` with the same body and
changes nothing. Now upload the pings from example 4 again, under a new
`pid`:

```bat
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\":\"demo-label\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:00:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2632457,22.3399579]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:10:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.2633,22.339965]}},{\"type\":\"Feature\",\"properties\":{\"time\":\"2026-08-30T04:25:00.000Z\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[114.26322,22.33995]}}]}"
```

Why Office: it is still one 25-minute stay at 12:00 HK, which the rules
would call `"Other Indoor"`. But all 3 pings are within 25 m of the label's
centre (about 0, 6 and 3 m away), so the label matches and its category
wins. `io_source` and `reason` say the label decided it.
`/exposure/hourly/demo-label`:

```json
[{"hour_start_hk": "20260830120000", "start_time_hk": "20260830120000", "io": "Office", "io_source": "label", "reason": "place_label", "kind": "stay", "delta_t_hours": 0.4166666666666667, "exposure_value": <live value>, "lng": 114.2632457, "lat": 22.3399579, "heading_deg": null, "compass": null, "road_name": null, "in_hk": true}]
```

The request to `expo_calx` used `{"IO": "Office"}`.

Labels are shared, so while this one exists, examples 1, 3 and 4 at the
same spot also come out as `"Office"` - storing the label re-ran them (a
label overrides the night window too, and applies to a stay of any
length). Delete it to get the results above back:

```bat
curl -X DELETE "http://localhost:8000/place-labels/demo-office?contributor_id=c-demo"
```

`204`, no body. Running it again gives `404`, which the app can treat as
already deleted.

## Place labels

A place label is a circle - a centre and a radius of 10-200 m - that a user
puts on a place to say what it is: `Home`, `Office`, `School` or
`Other Indoor`. Labels are shared, crowd-sourced data: a label describes
the place ("this building is an office"), not the person who added it, so
**every** `pid`'s stays are checked against **every** contributor's labels.
The radius tells the server how big the place is, and lets users shrink
two nearby circles (e.g. a Home and an Office 40 m apart, 15 m each) so
they no longer overlap.

How a stay's io type is chosen:

1. A **stay** is a run of the day where the phone didn't move (GPS jitter
   aside); walking around inside one building for a few minutes doesn't
   split it. Only stays are checked against labels - walking past an
   office or driving past a home never is.
2. Indoor GPS drifts 20-50 m, so every circle is grown by **30 m**, and a
   label matches the stay when the grown circle holds **at least half of
   the stay's pings**. All pings are counted, not just the first.
3. If several labels match, the one holding the most of the stay's pings
   wins; on a tie, the smaller radius wins (it is the more specific place).
4. Labels for the same spot (circles overlapping by at least half of their
   combined area) are a vote: the category with the most distinct
   contributors wins. A small circle inside a much bigger one is not the
   same spot, so it is not outvoted by it. The result never depends on the
   order labels were added.
5. A match wins over every rule, for a stay of any length: an Office circle
   at 02:00 is `"Office"`, never `"Home"`; a Home circle at 14:00 is
   `"Home"`. No match: the rules decide (see
   [How the io type is decided](#how-the-io-type-is-decided)).

Each row in `GET /exposure/hourly/{pid}` says which way it went in
`io_source` (`"label"` or `"inferred"`) and `reason` (`"place_label"`, ...).

Stored rows follow the labels. When a label is created, moved or deleted,
the server re-runs in the background every stored batch (of every `pid`)
with a ping within the circle's radius + 30 m, at the old and the new
position; `GET /ingest/{batch_id}` shows those batches `processing` and then
`done` again. A batch that a later upload of the same `pid` fully covers is
skipped. Each re-run calls `expo_calx` again. A batch still being processed
when the label changes keeps the labels it started with; upload it again
(or touch the label) to refresh it.

Uploading the same pings again also works: a batch replaces every stored row
of that `pid` whose `start_time_hk` lies between its first and last ping.

Labels outside Hong Kong may be stored. They still set the `io` of rows
there, but those rows are never sent to `expo_calx`.

No endpoint lists labels: one user's labels (especially Home circles) are
never returned to anyone else.

## Endpoint reference

### Upload pings

`POST /ingest` - store a batch of raw pings and queue it for background
processing.

Request body (`Content-Type: application/json`, at most 5 MB):

| field | type | required | notes |
|---|---|---|---|
| `pid` | string | yes | person/device id; every result row is stored under it |
| `features` | array of objects | yes | raw ping records; currently GeoJSON Features (below) |

GeoJSON `Feature` record:

| field | required | used? |
|---|---|---|
| `properties.time` (ISO 8601; `Z` or no zone = UTC) | yes | yes - ping time |
| `geometry.coordinates` (`[lng, lat]`) | yes | yes - position |
| any other property (`speed`, `provider`, `accuracy`, `altitude`, `bearing`, ...) | no | no - accepted, ignored |

A record without a valid time and position, or in any other shape, is
skipped (and logged).

Responses:

| status | body | when |
|---|---|---|
| `202` | `{"status": "queued", "batch_id": 12}` | stored; processing has started in the background |
| `400` | `{"detail": "no valid pings in request"}` | `features` contains no valid ping |
| `413` | `{"detail": "request body too large: limit is 5242880 bytes"}` | body over the size limit; nothing stored |
| `422` | `{"detail": [{"type": "missing", "loc": ["body", "pid"], "msg": "Field required", ...}]}` | `pid` or `features` missing or the wrong type |

Uploading a whole GeoJSON export (e.g. a file in `data/samples/`) works the
same way - put its `features` array in the request body. That needs `jq`
and `$(...)` substitution (git-bash; `cmd.exe` has neither):

```bash
curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d "{\"pid\": \"local-device\", \"features\": $(jq '.features' data/samples/20260830.geojson)}"
```

### Upload a GPX file

`POST /ingest/gpx?pid=<pid>` - the same as `POST /ingest`, but the body is
a whole GPX file (GPX 1.0 or 1.1, e.g. a RouteHistory export) instead of
JSON. Every `<trkpt>` in every track becomes one ping; the batch is then
processed exactly like a JSON one.

Request:

| part | required | notes |
|---|---|---|
| query `pid` | yes | person/device id; every result row is stored under it |
| body | yes | the GPX file as-is, at most 5 MB. Send `Content-Type: application/gpx+xml` (the server does not check it) |

What is read from each `<trkpt>`:

| field | required | used? |
|---|---|---|
| `lat`, `lon` attributes | yes | yes - position. A point with a missing or out-of-range value is dropped |
| `<time>` (ISO 8601; no zone = UTC) | yes | yes - ping time. A point without it is dropped |
| `<ele>`, `<speed>`, `<extensions>`, other children | no | no - accepted, ignored |

Waypoints (`<wpt>`) and planned routes (`<rte>`) have no recorded time
sequence and are ignored.

Responses:

| status | body | when |
|---|---|---|
| `202` | `{"status": "queued", "batch_id": 13}` | stored; processing has started in the background |
| `400` | `{"detail": "invalid GPX: not a readable XML document (syntax error: line 1, column 0)"}` | body is not well-formed XML, or its root element is not `<gpx>` |
| `400` | `{"detail": "no valid pings in request"}` | the file has no `<trkpt>` with a valid time and position |
| `413` | `{"detail": "request body too large: limit is 5242880 bytes"}` | body over the size limit; nothing stored |
| `422` | `{"detail": [{"type": "missing", "loc": ["query", "pid"], "msg": "Field required", ...}]}` | `pid` missing |

Upload a sample file (works in `cmd.exe` and git-bash):

```bash
curl -X POST "http://localhost:8000/ingest/gpx?pid=local-device" -H "Content-Type: application/gpx+xml" --data-binary @data/samples/1009_route_history.gpx
```

Use `--data-binary`, not `-d`: `-d` strips the file's line breaks.
`data/samples/1009_route_history.gpx` (1,314 track points, about 9 hours)
becomes 156 points and 165 rows = 5 `expo_calx` calls.

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

Each row is the part of one point's time that falls in one HK-local clock
hour (see [How the io type is decided](#how-the-io-type-is-decided)): a
stay is one point, a route (walk, vehicle, MTR) several, so many rows can
share an `hour_start_hk` + `io`.

- **Stacked bar chart**: group by `hour_start_hk` + `io` and sum
  `delta_t_hours` / `exposure_value`. Rows with `in_hk: false` have no
  value - leave them out of the exposure totals or show them separately.
- **Map**: draw the rows' `lng`/`lat` in `start_time_hk` order - stays as
  places, consecutive route rows as a line (a point split by an hour
  boundary repeats its position, which draws nothing extra). Use
  `heading_deg` / `compass` for direction arrows and `road_name` to label
  roads. Color rows with `in_hk: false` differently.

| field | type | notes |
|---|---|---|
| `hour_start_hk` | string `YYYYMMDDHH0000`, HK local | hour bucket - group by this (+ `io`) for the chart |
| `start_time_hk` | string `YYYYMMDDHHMMSS`, HK local | this row's own start time; unique per `pid` + `io` - sort by this for the map |
| `io` | string | `"Home"` \| `"Office"` \| `"School"` \| `"Other Indoor"` \| `"Outdoor"` \| `"MTR"` \| `"Vehicle"` |
| `io_source` | string | `"label"` if a [place label](#place-labels) decided `io`, `"inferred"` if the rules did |
| `reason` | string or `null` | which rule decided `io`: `"jump"`, `"rail"`, `"interchange"`, `"vehicle"`, `"walk"`, `"place_label"`, `"night_stay"`, `"near_night_stay"`, `"short_stop"`, `"long_stay"` (see [the table](#how-the-io-type-is-decided)); `null` on rows stored before this field existed |
| `kind` | string or `null` | `"stay"` (one point for a whole stay or MTR interchange) or `"route"` (one point of a walk / vehicle / MTR route); `null` on older rows |
| `delta_t_hours` | number | duration of this row, in hours |
| `exposure_value` | number or `null` | `null` if `in_hk` is `false`, or if `expo_calx` returned `-9` (failed) for this row |
| `lng` / `lat` | number | stays: the median of the stay's pings. Route points: the kept ping's position |
| `heading_deg` | number or `null` | route points: direction to the next point, degrees, 0 = north, clockwise; `null` for stays (and if the next point is at the same place) |
| `compass` | string or `null` | `heading_deg` as `"N"`, `"NE"`, `"E"`, `"SE"`, `"S"`, `"SW"`, `"W"`, `"NW"` |
| `road_name` | string or `null` | `Vehicle` route points: the English name of the road within 30 m, e.g. `"LUNG CHEUNG ROAD"`; `null` otherwise or for unnamed roads |
| `in_hk` | boolean | `true` inside Hong Kong (scored); `false` outside (not scored) |

The full `expo_calx` result item for each scored row is also kept in the
DB column `api_result_json`, so new fields the API adds aren't lost.

### Delete one day

`DELETE /exposure/hourly/{pid}?date=YYYY-MM-DD` - delete every row of
`pid` whose `start_time_hk` is on that HK-local day, e.g. to remove a wrong
import. The day's pings are also cut out of the stored uploads, so a later
place label change can't bring the day back; the uploads' other days are
kept. To re-import the day, upload it again as usual.

Response `200`:

```json
{"pid": "p1", "date": "2026-08-30", "rows_deleted": 12}
```

`rows_deleted` is `0` for a day with no data, so the call is safe to retry.

| status | when |
|---|---|
| `200` | deleted (or nothing to delete) |
| `409` `{"detail": "an upload for this day is still being processed"}` | an upload with pings on that day is still `pending`/`processing`; nothing changes |
| `422` | `date` missing or not a valid `YYYY-MM-DD` date |

### Create or update a place label

`PUT /place-labels/{label_id}` - store a label, or update it if
`label_id` already exists. Safe to retry with the same body. See
[Place labels](#place-labels) for how labels are used, and example 8.

Path: `label_id` - string, 1-64 characters, generated by the app, unique
per label. The same id means the same label.

Request body (`Content-Type: application/json`):

| field | type | required | notes |
|---|---|---|---|
| `contributor_id` | string, 1-64 chars | yes | random per-install id; not linked to a `pid` or a person. Only this contributor can later change or delete the label |
| `category` | string | yes | exactly `"Home"`, `"Office"`, `"School"` or `"Other Indoor"` |
| `lat` / `lng` | number | yes | WGS84 centre of the circle; `lat` -90..90, `lng` -180..180 |
| `radius_m` | number | yes | 10-200 (the app's default is 25) |
| `updated_at` | string, ISO 8601 **with** a time zone | yes | when the user last edited the label. If the server already has this label with the same or a newer `updated_at`, nothing changes |

Responses (body = the label as stored, `updated_at` in UTC):

| status | when |
|---|---|
| `201` | created |
| `200` | updated, or not changed because the stored `updated_at` is the same or newer |
| `403` `{"detail": "label_id belongs to another contributor_id"}` | the label exists under a different `contributor_id`; nothing changes |
| `422` `{"detail": [{"msg": ...}, ...]}` | unknown `category`, `lat`/`lng`/`radius_m` out of range, `updated_at` without a time zone, a field missing, or an id longer than 64 |

### Delete a place label

`DELETE /place-labels/{label_id}?contributor_id=<contributor_id>`

| status | when |
|---|---|
| `204` (no body) | deleted |
| `404` `{"detail": "unknown label_id"}` | no such label - treat as already deleted |
| `403` `{"detail": "label_id belongs to another contributor_id"}` | the label belongs to someone else; nothing changes |
| `422` | `contributor_id` missing |

## Errors

Every error body is JSON with a `detail` field.

| status | endpoint | meaning | what the app should do |
|---|---|---|---|
| `400` | `POST /ingest` | no valid pings in `features` | check the record format |
| `400` | `POST /ingest/gpx` | not a readable GPX file, or no track point with a valid time and position | check the file |
| `413` | `POST /ingest`, `POST /ingest/gpx` | body over 5 MB; nothing stored | split the upload (e.g. a few hours each) |
| `422` | `POST /ingest` | `pid`/`features` missing or wrong type | fix the request body |
| `422` | `POST /ingest/gpx` | `pid` query parameter missing | add `?pid=...` |
| `404` | `GET /ingest/{batch_id}` | unknown `batch_id` | |
| `409` | `DELETE /exposure/hourly/{pid}` | an upload for that day is still processing | wait for its batch to finish, then retry |
| `422` | `DELETE /exposure/hourly/{pid}` | `date` missing or invalid | send `?date=YYYY-MM-DD` |
| `403` | `PUT`, `DELETE /place-labels/{label_id}` | the label belongs to another `contributor_id` | don't retry; use a new `label_id` for a new label |
| `404` | `DELETE /place-labels/{label_id}` | no such label | treat as already deleted |
| `422` | `PUT /place-labels/{label_id}` | invalid field (see the endpoint) | fix the request body |
| `status: "error"` | `GET /ingest/{batch_id}` | the background job failed; see `error_detail` | upload the batch again later |

## Configuration

Set as environment variables or in `.env` (see
[.env.example](../.env.example) and
[app/config/settings.py](../app/config/settings.py)):

| variable | default | meaning |
|---|---|---|
| `MAX_INGEST_BODY_BYTES` | `5242880` (5 MB) | largest accepted `POST /ingest` / `POST /ingest/gpx` body |
| `PRAISE_MAX_CONCURRENT_CALLS` | `10` | `expo_calx` calls running at once, per server process |
| `PRAISE_MAX_ROWS_PER_CALL` | `40` | rows per `expo_calx` call |
| `PRAISE_REQUEST_TIMEOUT_SECONDS` | `60` | timeout for one `expo_calx` call |
| `ROAD_CENTRELINES_PATH` | `data/road_centrelines/RoadCentreLine.geojson` | road centrelines loaded at startup (the server refuses to start without them; see the README) |
