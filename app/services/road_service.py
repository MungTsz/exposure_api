"""
Loads the road centrelines (settings.road_centrelines_path) once per
process. app.main loads them at startup, so a missing file stops the app
at once instead of failing every batch.

Download them (~38 MB, Lands Department via the CSDI portal) with:

    python -m app.services.road_service [PATH]

PATH defaults to the settings default, data/road_centrelines/RoadCentreLine.geojson.
"""

import json
import logging
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

from app.config.constants import ROAD_CENTRELINES_URL
from app.config.settings import REPO_ROOT, Settings, get_settings
from app.pipeline.roads import RoadIndex

logger = logging.getLogger(__name__)


class RoadDataMissing(RuntimeError):
    """The road centrelines file is not there."""


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def road_centrelines_path() -> Path:
    return _resolve(get_settings().road_centrelines_path)


@lru_cache(maxsize=1)
def get_road_index() -> RoadIndex:
    path = road_centrelines_path()
    if not path.is_file():
        raise RoadDataMissing(
            f"road centrelines not found at {path}. Download them with: python -m app.services.road_service {path}"
        )
    with path.open(encoding="utf-8") as f:
        index = RoadIndex.from_geojson(json.load(f))
    logger.info("loaded %d road centrelines from %s", len(index), path)
    return index


def download(path: Path) -> None:
    """Fetch the current road centrelines from the CSDI portal to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    urllib.request.urlretrieve(ROAD_CENTRELINES_URL, tmp)
    tmp.replace(path)


if __name__ == "__main__":
    # No get_settings() here: it needs the API secrets, which a build step has not got.
    target = _resolve(sys.argv[1] if len(sys.argv) > 1 else Settings.model_fields["road_centrelines_path"].default)
    print(f"downloading {ROAD_CENTRELINES_URL}\n  -> {target}")
    download(target)
    print(f"done: {target.stat().st_size / 1e6:.1f} MB")
