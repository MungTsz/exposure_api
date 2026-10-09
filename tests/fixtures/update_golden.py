"""
Regenerate tests/fixtures/golden/*.json from data/samples/*.geojson.

Only run this after an intended change to pipeline output (e.g. new
thresholds in app/config); tests/test_regression.py compares against these
files. Run from the repo root:

    python -m tests.fixtures.update_golden
"""

import json
from pathlib import Path

from app.config.constants import HK_HOUR_FORMAT, HK_TIME_FORMAT
from app.etl.pipeline import run_pipeline
from app.etl.request_rows import to_request_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
GOLDEN_PID = "golden"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(SAMPLES_DIR.glob("*.geojson")):
        if path.name.endswith(".processed.geojson"):
            continue
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        slices = run_pipeline(features).slices
        rows = to_request_rows(GOLDEN_PID, slices)
        golden = {
            "source": path.name,
            "segments": [
                {
                    "hour_start_hk": s.hour_start_hk.strftime(HK_HOUR_FORMAT),
                    "start_time_hk": s.start_time_hk.strftime(HK_TIME_FORMAT),
                    "io": s.io, "lng": s.lng, "lat": s.lat, "delta_t_hours": s.delta_t_hours,
                }
                for s in slices
            ],
            "rows": rows,
        }
        (GOLDEN_DIR / f"{path.stem}.json").write_text(json.dumps(golden, indent=1), encoding="utf-8")
        print(f"{path.name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
