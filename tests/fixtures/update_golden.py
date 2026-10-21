"""
Regenerate tests/fixtures/golden/*.json from data/samples/*.geojson.

Only run this after an intended change to pipeline output (e.g. new
thresholds in app/config, or a newer road centreline file);
tests/test_regression.py compares against these files. Needs the road
centrelines (settings.road_centrelines_path). Run from the repo root:

    python -m tests.fixtures.update_golden
"""

import json
from pathlib import Path
from typing import Any, Dict

from app.config.constants import HK_TIME_FORMAT
from app.pipeline.request_rows import to_request_rows
from app.pipeline.run import PipelineResult, run_pipeline
from app.pipeline.time_split import to_hk
from app.services.road_service import get_road_index

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
GOLDEN_PID = "golden"


def golden_of(result: PipelineResult) -> Dict[str, Any]:
    """What a golden file holds for one pipeline result (JSON-ready)."""
    points = [
        {
            "start_time_hk": to_hk(p.start_utc).strftime(HK_TIME_FORMAT),
            "end_time_hk": to_hk(p.end_utc).strftime(HK_TIME_FORMAT),
            "io": p.io, "io_source": p.io_source.value, "reason": p.reason.value, "kind": p.kind.value,
            "lng": p.lng, "lat": p.lat, "heading_deg": p.heading_deg, "compass": p.compass,
            "road_name": p.road_name, "in_hk": p.in_hk,
        }
        for p in result.points
    ]
    return json.loads(json.dumps({"points": points, "rows": to_request_rows(GOLDEN_PID, result.slices)}))


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    roads = get_road_index()
    for path in sorted(SAMPLES_DIR.glob("*.geojson")):
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        golden = {"source": path.name, **golden_of(run_pipeline(features, roads))}
        (GOLDEN_DIR / f"{path.stem}.json").write_text(json.dumps(golden, indent=1), encoding="utf-8")
        print(f"{path.name}: {len(golden['points'])} points, {len(golden['rows'])} rows")


if __name__ == "__main__":
    main()
