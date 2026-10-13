"""
Scored hourly exposure rows.

One row per (pid, io, stay start time) - a clock hour can hold more than
one row of the same io if separate stays land in it (e.g. two distinct
outdoor trips within the same hour), so callers sum delta_t_hours/
exposure_value across rows sharing an hour_start_hk+io for the chart, and
use each row's own lng/lat (ordered by start_time_hk) to draw a route.
Rows outside Hong Kong have in_hk = 0 and no exposure_value.
"""

from typing import Any, Dict, List

from app.db.connection import get_conn


def upsert_hourly(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO hourly_exposure
                (pid, hour_start_hk, start_time_hk, io, io_source, delta_t_hours, exposure_value, lng, lat, in_hk,
                 api_result_json)
            VALUES
                (:pid, :hour_start_hk, :start_time_hk, :io, :io_source, :delta_t_hours, :exposure_value, :lng, :lat,
                 :in_hk, :api_result_json)
            ON CONFLICT(pid, io, start_time_hk) DO UPDATE SET
                hour_start_hk = excluded.hour_start_hk,
                io_source = excluded.io_source,
                delta_t_hours = excluded.delta_t_hours,
                exposure_value = excluded.exposure_value,
                lng = excluded.lng,
                lat = excluded.lat,
                in_hk = excluded.in_hk,
                api_result_json = excluded.api_result_json
            """,
            rows,
        )


def get_hourly(pid: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT hour_start_hk, start_time_hk, io, io_source, delta_t_hours, exposure_value, lng, lat, in_hk
            FROM hourly_exposure
            WHERE pid = ?
            ORDER BY start_time_hk
            """,
            (pid,),
        )
        return [dict(r) for r in cur.fetchall()]
