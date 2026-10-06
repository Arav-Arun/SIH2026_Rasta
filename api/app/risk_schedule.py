"""Re-score every district's roads on a timer, so the score does not wait for a person.

Off unless ``RISK_RECOMPUTE_MINUTES`` is set. Every worker process runs the timer;
a transaction-scoped advisory lock lets one of them score a round while the others
skip it. A transaction lock rather than a session lock, because behind a
transaction-mode connection pooler a session lock can outlive its owner.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import connect
from app.recorded_samples import RECORDED, redate_recorded_sources
from app.risk_model import shadow_model
from app.risk_pipeline import run_pipeline

# Under rasta.api, so round summaries reach the service log with its own lines.
logger = logging.getLogger("rasta.api.risk_schedule")

#: Names the scoring round among Postgres advisory locks.
ROUND_LOCK_KEY = 726_150_301_026

#: The first round runs this soon after start, not a whole interval later.
FIRST_ROUND_DELAY_SECONDS = 60.0

DEMO_MODES = ("local_demo", "hosted_demo")


def redatable_samples(settings: Settings) -> Path | None:
    """The recorded-sample directory a demo round may re-date, or None.

    A demo replays its recorded samples as if current, and they stay labelled
    recorded. Only a directory of their own is touched: never the committed
    originals, never anything inside a git checkout.
    """

    root = settings.resolved_source_fixture_root
    if settings.app_mode not in DEMO_MODES or root is None or not root.is_dir():
        return None
    resolved = root.resolve()
    if resolved == RECORDED.resolve():
        return None
    if any((folder / ".git").exists() for folder in (resolved, *resolved.parents)):
        return None
    return resolved


def run_round(
    settings: Settings,
    *,
    min_gap: timedelta = timedelta(0),
    now: datetime | None = None,
) -> list[dict[str, Any]] | None:
    """Score every district with roads once; None when another process has the round.

    A district scored less than ``min_gap`` ago is left alone, whoever scored it,
    so two workers whose timers drift apart do not score it twice.
    """

    if not settings.database_url:
        return None
    moment = now or datetime.now(tz=UTC)
    with connect(settings.database_url) as connection, connection.transaction():
        held = connection.execute(
            "select pg_try_advisory_xact_lock(%s) as held", (ROUND_LOCK_KEY,)
        ).fetchone()["held"]
        if not held:
            return None

        samples = redatable_samples(settings)
        if samples is not None:
            redate_recorded_sources(samples, now=moment)

        districts = connection.execute(
            """
            select s.organization_id::text as org, s.district_id::text as district,
                   max(scs.risk_computed_at) as last_scored
            from public.road_segments as s
            left join public.segment_current_state as scs
              on scs.segment_id = s.id and scs.organization_id = s.organization_id
            group by s.organization_id, s.district_id
            order by 1, 2
            """
        ).fetchall()

        shadow = shadow_model(settings)
        results: list[dict[str, Any]] = []
        for row in districts:
            if row["last_scored"] is not None and moment - row["last_scored"] < min_gap:
                continue
            try:
                with connection.transaction():
                    result = run_pipeline(
                        connection,
                        organization_id=row["org"],
                        district_id=row["district"],
                        imd_base_url=settings.imd_api_base_url,
                        cap_base_url=settings.sachet_cap_base_url,
                        fixture_root=samples or settings.resolved_source_fixture_root,
                        terrain_file=settings.resolved_terrain_file,
                        now=now,
                        shadow=shadow,
                    )
            except Exception:
                # One district's failure must not stop the others being scored.
                logger.exception(
                    "scheduled risk recompute failed for district %s", row["district"]
                )
                continue
            results.append(result.as_dict())
        return results


class RecomputeSchedule:
    """Calls ``run`` in a worker thread every ``interval`` seconds until stopped."""

    def __init__(
        self, run: Callable[[], Any], *, interval: float, first_delay: float
    ) -> None:
        self._run = run
        self._interval = interval
        self._first_delay = first_delay
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="risk-recompute")

    async def stop(self) -> None:
        """Stop the timer; a round already running is allowed to finish."""

        self._stop.set()
        if self._task is not None:
            await self._task

    async def _loop(self) -> None:
        delay = self._first_delay
        while not await self._stopped_within(delay):
            delay = self._interval
            try:
                outcome = await asyncio.to_thread(self._run)
            except Exception:
                logger.exception("scheduled risk recompute failed")
                continue
            if outcome is None:
                logger.info("scheduled risk recompute: another worker has this round")
            else:
                logger.info(
                    "scheduled risk recompute: %d district(s) scored", len(outcome)
                )

    async def _stopped_within(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True


def schedule_for(settings: Settings) -> RecomputeSchedule | None:
    """The timer this deployment asked for, or None when it asked for none."""

    if settings.risk_recompute_minutes <= 0 or not settings.database_url:
        return None
    interval = settings.risk_recompute_minutes * 60.0
    return RecomputeSchedule(
        lambda: run_round(settings, min_gap=timedelta(seconds=interval / 2)),
        interval=interval,
        first_delay=min(interval, FIRST_ROUND_DELAY_SECONDS),
    )


__all__ = [
    "ROUND_LOCK_KEY",
    "RecomputeSchedule",
    "redatable_samples",
    "run_round",
    "schedule_for",
]
