"""
Deletes one HK-local day of a pid's data (DELETE /exposure/hourly/{pid}),
e.g. so a wrong import can be removed and that day uploaded again.

Deleting the day's rows alone isn't enough: the raw batches they came from
stay stored, and a place label change near them re-runs those batches
(label_service.reprocess_near), which would write the day back. So the
day's records are also cut out of every batch of the pid. A batch left with
nothing is deleted. One with records on both sides of the day is split in
two, because its time span would otherwise still cover the day and a
re-run (replace_hourly over that span) would wipe whatever was uploaded for
the day afterwards.

Known limitation: the split-off later part gets a new, higher batch id, so
re-runs (oldest first) process it after any batch uploaded between the
original upload and the delete.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Tuple

from app.config.constants import HK_OFFSET, HK_TIME_FORMAT, BatchStatus
from app.db import batch_repository, exposure_repository
from app.pipeline.models import Ping
from app.pipeline.parsers import parse_record
from app.services import label_service

logger = logging.getLogger(__name__)

# Statuses whose rows haven't been written yet - they would land after the delete.
_UNFINISHED = (BatchStatus.PENDING.value, BatchStatus.PROCESSING.value)

_Part = List[Tuple[Dict[str, Any], Ping]]


class DayBusyError(Exception):
    """A batch with records on the day is still waiting or being processed."""


def delete_day(pid: str, day: date) -> int:
    """Delete `day` (HK local) of `pid`: its rows and its records in stored
    batches. Returns how many rows were deleted (0 for a day with no data).
    Raises DayBusyError, changing nothing, while a batch for the day is
    unfinished."""
    start_utc = datetime.combine(day, time.min, tzinfo=timezone.utc) - HK_OFFSET
    end_utc = start_utc + timedelta(days=1)

    with label_service.rerun_lock:
        batches = [b for b in batch_repository.get_for_pid(pid) if _may_overlap(b, start_utc, end_utc)]
        if any(b["status"] in _UNFINISHED for b in batches):
            raise DayBusyError("an upload for this day is still being processed")

        for batch in batches:
            _cut_day(pid, batch, start_utc, end_utc)
        deleted = exposure_repository.delete_between(
            pid,
            datetime.combine(day, time.min).strftime(HK_TIME_FORMAT),
            datetime.combine(day, time.max).strftime(HK_TIME_FORMAT),
        )

    logger.info("pid=%s day=%s deleted %d row(s) from %d batch(es)", pid, day, deleted, len(batches))
    return deleted


def _may_overlap(batch: Dict[str, Any], start_utc: datetime, end_utc: datetime) -> bool:
    if batch["first_ping_utc"] is None:
        return True  # stored before spans were recorded: _cut_day finds out
    return batch["first_ping_utc"] < end_utc and batch["last_ping_utc"] >= start_utc


def _cut_day(pid: str, batch: Dict[str, Any], start_utc: datetime, end_utc: datetime) -> None:
    before: _Part = []
    after: _Part = []
    on_day = 0
    for record in batch["features"]:
        ping = parse_record(record)
        if ping is None:
            continue  # never produced a row; dropped if the batch is rewritten
        if ping.utc < start_utc:
            before.append((record, ping))
        elif ping.utc >= end_utc:
            after.append((record, ping))
        else:
            on_day += 1

    # Records on both sides still need splitting even with none on the day:
    # the run bridging the gap produced rows on it.
    if on_day == 0 and not (before and after):
        return

    parts = [part for part in (before, after) if part]
    if not parts:
        batch_repository.delete_batch(batch["id"])
        return
    batch_repository.replace_features(batch["id"], *_unzip(parts[0]))
    if len(parts) == 2:
        batch_repository.create_batch(pid, *_unzip(parts[1]), status=BatchStatus(batch["status"]))


def _unzip(part: _Part) -> Tuple[List[Dict[str, Any]], List[Ping]]:
    """Records in their stored order, pings time-sorted (as create_batch expects)."""
    return [record for record, _ in part], sorted((ping for _, ping in part), key=lambda p: p.utc)
