# Exposure ETL backend

Takes raw watch/phone GPS pings from the RN app, cleans and classifies them,
calls the third-party `expo_calx` exposure API, and stores per-hour
indoor/outdoor results for the app's bar chart.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in PRAISE_API_KEY and PRAISE_MYID
uvicorn app.main:app --reload
```

`.env` is loaded automatically (via `python-dotenv`) when `praise_client.py`/`db.py` are imported - no manual `export` step needed.

## Endpoints

**POST /ingest** - the RN app sends a batch of raw pings here (e.g. once an
hour, or whenever it has connectivity). `features` accepts either GeoJSON
`Feature` objects (watch-exported test data) or live device-tracking
location objects (`react-native-background-geolocation`-style: `timestamp`,
`coords.latitude`/`coords.longitude`) - the two shapes can even be mixed in
the same batch. Live-tracking records with `sample: true` (a transient
reading not meant to be persisted) are dropped; `mock: true` (fake-GPS)
records are kept as-is:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
        "pid": "local-device",
        "features": [
          {"type":"Feature","properties":{"time":"2026-08-29T16:00:03.048Z"},"geometry":{"type":"Point","coordinates":[114.2632457,22.3399579]}},
          {"timestamp":"2026-08-29T16:05:12.048Z","coords":{"latitude":22.3399579,"longitude":114.2632457}}
        ]
      }'
```

**GET /exposure/hourly/{pid}** - returns one row per (hour, indoor/outdoor),
ready to feed directly into a stacked bar chart. A dwell run that crosses an
hour boundary (e.g. indoor from 16:45-17:15) is classified once over its true
duration and then split into same-labelled rows per hour touched, each with
its own prorated `delta_t_hours` (here, two Indoor rows: 0.25h in hour 16 and
0.25h in hour 17) - not reclassified per hour, which would otherwise
mislabel both partial pieces as Outdoor:

```bash
curl http://localhost:8000/exposure/hourly/local-device
```

```json
[
  {"hour_start_hk": "20260830000000", "io": "Outdoor", "delta_t_hours": 0.04, "exposure_value": 0.02},
  {"hour_start_hk": "20260830000000", "io": "Indoor",  "delta_t_hours": 0.9,  "exposure_value": 0.31}
]
```

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

All thresholds live at the top of `app/etl.py`:

- `SPIKE_ANGLE_DEG` / `SPIKE_TIME_WINDOW_SECONDS` - GPS spike rejection.
- `DWELL_INDOOR_MINUTES` / `DWELL_RADIUS_METERS` - indoor/outdoor dwell test.

## Known simplifications (fine for a v1, revisit if needed)

- `/ingest` calls `expo_calx` synchronously in the request. If a user's
  daily batch is large enough to make this slow, split it into "write raw
  pings, return immediately" plus a background worker that does the ETL +
  API call - `etl.py`, `praise_client.py`, and `db.py` don't need to change.
- No retry on network failure, matching the documented API behavior.
- Dwell-run detection uses a fixed reference point (the run's first ping);
  a slowly drifting walk could split into more runs than ideal.
