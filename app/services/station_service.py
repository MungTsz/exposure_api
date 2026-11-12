"""
Loads the MTR station entrances (settings.mtr_stations_path) once per
process. app.main loads them at startup, so a missing file stops the app at
once instead of failing every batch. The file is kept in git; refresh it
from OpenStreetMap (data (c) OpenStreetMap contributors, ODbL) with:

    python -m app.services.station_service [PATH]

PATH defaults to the settings default, data/mtr_stations/mtr_stations.geojson.
"""

import json
import logging
import sys
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from app.config.constants import MTR_STATION_TYPES, MTR_STATIONS_QUERY, OVERPASS_URL
from app.config.settings import REPO_ROOT, Settings, get_settings
from app.pipeline.region import is_in_hk
from app.pipeline.stations import StationIndex

logger = logging.getLogger(__name__)


class StationDataMissing(RuntimeError):
    """The MTR station entrances file is not there."""


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


@lru_cache(maxsize=1)
def get_station_index() -> StationIndex:
    path = _resolve(get_settings().mtr_stations_path)
    if not path.is_file():
        raise StationDataMissing(
            f"MTR station entrances not found at {path}. Download them with: python -m app.services.station_service {path}"
        )
    with path.open(encoding="utf-8") as f:
        index = StationIndex.from_geojson(json.load(f))
    logger.info("loaded %d MTR station entrances from %s", len(index), path)
    return index


def to_geojson(overpass: Dict[str, Any]) -> Dict[str, Any]:
    """An Overpass answer to MTR_STATIONS_QUERY -> a FeatureCollection of the
    entrances and MTR heavy-rail station nodes inside Hong Kong."""
    features = []
    for node in overpass.get("elements", []):
        tags = node.get("tags", {})
        kind = tags.get("railway")
        if kind == "station" and tags.get("station") not in MTR_STATION_TYPES:
            continue
        if not is_in_hk(node["lon"], node["lat"]):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [node["lon"], node["lat"]]},
            "properties": {"osm_id": node["id"], "railway": kind, "name": tags.get("name:en") or tags.get("name")},
        })
    features.sort(key=lambda f: f["properties"]["osm_id"])
    return {"type": "FeatureCollection", "features": features}


def download(path: Path) -> int:
    """Fetch the current entrances from Overpass to `path`; returns how many."""
    body = urllib.parse.urlencode({"data": MTR_STATIONS_QUERY}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": "exposure-api"})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = to_geojson(json.load(response))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    return len(data["features"])


if __name__ == "__main__":
    # No get_settings() here: it needs the API secrets, which a build step has not got.
    target = _resolve(sys.argv[1] if len(sys.argv) > 1 else Settings.model_fields["mtr_stations_path"].default)
    print(f"downloading MTR station entrances from {OVERPASS_URL}\n  -> {target}")
    print(f"done: {download(target)} entrances and stations")
