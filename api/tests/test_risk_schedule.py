"""The re-scoring timer and its settings, without a database."""

from __future__ import annotations

import asyncio

import pytest
from app.config import Settings
from app.recorded_samples import RECORDED
from app.risk_schedule import RecomputeSchedule, redatable_samples, schedule_for
from pydantic import ValidationError


def settings(**values) -> Settings:
    return Settings(allowed_origins=("http://localhost:3000",), **values)


@pytest.mark.parametrize("minutes", [0, 5, 60, 1440])
def test_the_interval_is_off_or_between_five_minutes_and_a_day(minutes) -> None:
    assert settings(risk_recompute_minutes=minutes).risk_recompute_minutes == minutes


@pytest.mark.parametrize("minutes", [1, 4, 1441, -5])
def test_an_interval_that_would_hammer_or_never_run_is_refused(minutes) -> None:
    with pytest.raises(ValidationError, match="RISK_RECOMPUTE_MINUTES"):
        settings(risk_recompute_minutes=minutes)


def test_no_timer_without_an_interval_or_a_database() -> None:
    database = "postgresql://localhost/rasta"
    assert (
        schedule_for(settings(risk_recompute_minutes=0, database_url=database)) is None
    )
    assert schedule_for(settings(risk_recompute_minutes=60, database_url="")) is None
    assert (
        schedule_for(settings(risk_recompute_minutes=60, database_url=database))
        is not None
    )


def test_only_a_demos_own_copy_of_the_samples_is_ever_redated(tmp_path) -> None:
    own = tmp_path / "samples"
    own.mkdir()
    assert (
        redatable_samples(
            settings(app_mode="hosted_demo", source_fixture_root=str(own))
        )
        == own.resolve()
    )

    # The committed originals, a directory inside a checkout, a real deployment
    # and a directory that does not exist are all left alone.
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "sources").mkdir()
    for refused in (
        settings(app_mode="local_demo", source_fixture_root=str(RECORDED)),
        settings(app_mode="local_demo", source_fixture_root=str(checkout / "sources")),
        settings(app_mode="production", source_fixture_root=str(own)),
        settings(app_mode="local_demo", source_fixture_root=str(tmp_path / "none")),
    ):
        assert redatable_samples(refused) is None


def test_the_timer_keeps_going_after_a_failed_round_and_stops_cleanly() -> None:
    calls: list[int] = []

    def round_() -> list:
        calls.append(len(calls))
        if len(calls) == 2:
            raise RuntimeError("one bad round")
        return []

    async def scenario() -> int:
        schedule = RecomputeSchedule(round_, interval=0.01, first_delay=0.0)
        schedule.start()
        await asyncio.sleep(0.2)
        await schedule.stop()
        stopped_at = len(calls)
        await asyncio.sleep(0.05)
        return stopped_at

    stopped_at = asyncio.run(scenario())
    assert len(calls) >= 3, "a failed round must not end the timer"
    assert len(calls) == stopped_at, "nothing runs after stop"


def test_stopping_before_the_first_round_runs_nothing() -> None:
    calls: list[int] = []

    async def scenario() -> None:
        schedule = RecomputeSchedule(
            lambda: calls.append(1), interval=60, first_delay=60
        )
        schedule.start()
        await asyncio.sleep(0)
        await schedule.stop()

    asyncio.run(scenario())
    assert calls == []
