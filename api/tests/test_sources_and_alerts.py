"""Unit proof of source parsing, conditional reuse and alert deduplication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.alerts import dedupe_key, roles_with_capability
from app.push import MAX_PAYLOAD_BYTES, PushMessage, UnconfiguredSender, fingerprint
from app.sources import (
    CapWarningAdapter,
    FixtureAdapter,
    RainfallForecastAdapter,
    checksum_of,
    redact,
)

FIXTURES = next(
    parent / "data" / "fixtures" / "sources"
    for parent in Path(__file__).resolve().parents
    if (parent / "data" / "fixtures" / "sources").is_dir()
)
NOW = datetime(2026, 9, 26, 10, 30, tzinfo=UTC)


# --- CAP --------------------------------------------------------------------


def test_a_cap_document_yields_a_normalised_warning() -> None:
    # The recorded sample is an Exercise message, so only the recorded parser reads it.
    adapter = CapWarningAdapter(None, accept_exercise=True)
    records = adapter.parse((FIXTURES / "sachet_cap.xml").read_bytes())
    assert len(records) == 1
    record = records[0]
    assert record.kind == "official_warning"
    assert record.subject_ref == "FIXTURE-CAP-0001"
    assert record.value["areas"] == ["East Khasi Hills"]
    assert record.value["severity"] == "Severe"
    assert record.value["severity_normalised"] == 0.75


def test_a_live_feed_never_treats_the_exercise_sample_as_a_warning() -> None:
    assert (
        CapWarningAdapter(None).parse((FIXTURES / "sachet_cap.xml").read_bytes()) == []
    )


def test_a_document_that_is_not_cap_raises_rather_than_returning_nothing() -> None:
    """Returning an empty list would look like "no warnings in force"."""

    not_cap = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<weather><summary>no alert blocks here</summary></weather>"
    )
    with pytest.raises(ValueError):
        CapWarningAdapter(None).parse(not_cap)


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("Extreme", 1.0),
        ("Severe", 0.75),
        ("Moderate", 0.5),
        ("Minor", 0.25),
        ("Unknown", 0.0),
    ],
)
def test_cap_severities_map_onto_one_scale(severity: str, expected: float) -> None:
    assert CapWarningAdapter.normalise(severity) == expected


def test_an_unrecognised_severity_is_zero_not_guessed() -> None:
    assert CapWarningAdapter.normalise("Catastrophic") == 0.0


# --- rainfall ---------------------------------------------------------------


def test_a_rainfall_document_yields_one_record_per_district() -> None:
    records = RainfallForecastAdapter(None).parse(
        (FIXTURES / "imd_rainfall.json").read_bytes()
    )
    assert {record.subject_ref for record in records} == {
        "East Khasi Hills",
        "Ri Bhoi",
        "West Jaintia Hills",
    }
    east = next(r for r in records if r.subject_ref == "East Khasi Hills")
    assert east.value["rainfall_mm_48h"] == 86.4
    assert 0 < east.value["normalised"] < 1


def test_rainfall_above_the_heavy_threshold_saturates_at_one() -> None:
    assert RainfallForecastAdapter.normalise(500.0) == 1.0
    assert RainfallForecastAdapter.normalise(0.0) == 0.0
    assert RainfallForecastAdapter.normalise(-5.0) == 0.0


def test_an_empty_forecast_document_raises() -> None:
    with pytest.raises(ValueError):
        RainfallForecastAdapter(None).parse(b'{"districts": []}')


# --- configuration and conditional reuse ------------------------------------


def test_an_unconfigured_adapter_reports_disabled_rather_than_failing() -> None:
    result = RainfallForecastAdapter("").fetch(etag=None)
    assert result.status == "disabled"
    assert result.error_code == "not_configured"
    assert result.body is None


def test_a_fixture_with_a_matching_checksum_is_not_re_read() -> None:
    """The same rule a 304 expresses, applied to a recorded document."""

    path = FIXTURES / "imd_rainfall.json"
    adapter = FixtureAdapter("imd_recorded", path, RainfallForecastAdapter(None))
    first = adapter.fetch(etag=None)
    assert first.status == "success"
    again = adapter.fetch(etag=first.etag)
    assert again.status == "unchanged"
    assert again.body is None


def test_a_missing_fixture_is_a_failure_with_a_reason_code() -> None:
    adapter = FixtureAdapter(
        "gone", FIXTURES / "does-not-exist.json", RainfallForecastAdapter(None)
    )
    result = adapter.fetch(etag=None)
    assert result.status == "failed"
    assert result.error_code == "fixture_missing"


def test_a_checksum_identifies_content() -> None:
    assert checksum_of(b"a") != checksum_of(b"b")
    assert len(checksum_of(b"a")) == 64


def test_a_redacted_diagnostic_carries_no_url_or_key() -> None:
    error = ValueError("https://api.example.test/forecast?key=SECRET123")
    assert redact(error) == "ValueError"
    assert "SECRET" not in redact(error)


# --- alert dedupe -----------------------------------------------------------


def test_the_same_subject_in_one_window_collapses_to_one_key() -> None:
    a = dedupe_key(
        alert_type="road_closed",
        subject_type="segment",
        subject_id="s1",
        valid_from=NOW,
    )
    b = dedupe_key(
        alert_type="road_closed",
        subject_type="segment",
        subject_id="s1",
        valid_from=NOW + timedelta(minutes=20),
    )
    assert a == b


def test_a_later_window_is_a_new_alert() -> None:
    a = dedupe_key(
        alert_type="road_closed",
        subject_type="segment",
        subject_id="s1",
        valid_from=NOW,
    )
    b = dedupe_key(
        alert_type="road_closed",
        subject_type="segment",
        subject_id="s1",
        valid_from=NOW + timedelta(hours=2),
    )
    assert a != b


def test_different_subjects_never_share_a_key() -> None:
    base = {"alert_type": "road_closed", "subject_type": "segment", "valid_from": NOW}
    assert dedupe_key(subject_id="s1", **base) != dedupe_key(subject_id="s2", **base)


def test_recipients_are_chosen_by_capability_not_by_a_hardcoded_role_list() -> None:
    planners = roles_with_capability(("route:plan",))
    assert "district_dispatcher" in planners
    assert "driver" not in planners
    assert "driver" in roles_with_capability(("alert:read",))


# --- push -------------------------------------------------------------------


def test_an_unconfigured_push_sender_skips_and_says_why() -> None:
    status, reason, http = UnconfiguredSender().send(
        endpoint="https://push.example.test/x",
        keys={},
        message=PushMessage(
            alert_id="a1",
            title_key="alert.road_closed",
            severity="critical",
            deep_link="/alerts",
        ),
    )
    assert (status, reason, http) == ("skipped", "not_configured", None)


def test_a_push_payload_carries_a_link_and_no_operational_detail() -> None:
    payload = PushMessage(
        alert_id="a1",
        title_key="alert.road_closed",
        severity="critical",
        deep_link="/alerts",
    ).as_payload()
    assert len(payload) < MAX_PAYLOAD_BYTES
    assert b"/alerts" in payload
    assert b"segment" not in payload


def test_an_endpoint_is_stored_by_fingerprint_not_in_the_clear() -> None:
    endpoint = "https://push.example.test/very-secret-token"
    digest = fingerprint(endpoint)
    assert len(digest) == 64
    assert endpoint not in digest


# --- what a quiet source must not do ----------------------------------------
#
# Both defects below were found by the T031/T032 runtime run, not by this file,
# and both produced a confident wrong answer rather than an error.


class _FakeConnection:
    """Captures one insert, so the row that would be written can be inspected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, sql: str, params: dict[str, object] | None = None):  # noqa: ANN202
        self.calls.append((sql, dict(params or {})))
        connection = self

        class _Cursor:
            def fetchone(self):  # noqa: ANN202
                if "insert into public.source_runs" in sql:
                    return {"id": "11111111-1111-4111-8111-111111111111"}
                return None

            def fetchall(self):  # noqa: ANN202
                _ = connection
                return []

        return _Cursor()


class _StubAdapter:
    name = "stub"

    def configured(self) -> bool:
        return True

    def fetch(self, *, etag: str | None = None):  # noqa: ANN202
        from app.sources import FetchResult

        return FetchResult(status="success", body=b"{}", etag="e1", checksum="0" * 64)

    def parse(self, body: bytes):  # noqa: ANN202
        _ = body
        return []


def test_a_run_times_itself_with_one_clock() -> None:
    """`now()` is the transaction's start time, not the wall clock.

    `run_source` timed the fetch with Python's clock and stored the end with
    Postgres's `now()`. Inside a transaction opened before the fetch that reads
    *earlier* than the start, the row violated `finished_at >= started_at`, and
    every source run failed outright.
    """

    from app.sources import run_source

    connection = _FakeConnection()
    run_source(
        connection,  # type: ignore[arg-type]
        organization_id="a2600002-0000-4000-8000-000000000001",
        adapter=_StubAdapter(),  # type: ignore[arg-type]
        source_mode="recorded",
    )
    insert = next(
        params
        for sql, params in connection.calls
        if "insert into public.source_runs" in sql
    )
    started, finished = insert["started"], insert["finished"]
    assert isinstance(started, datetime) and isinstance(finished, datetime)
    assert finished >= started, "a run cannot finish before it starts"


def _without_comments(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines)


def test_the_pipeline_reads_features_from_the_store_not_from_one_run() -> None:
    """A 304 means "use what you have", so the adapter returns no body.

    The pipeline used to build its features from the records of the run it had
    just performed. An unchanged or failed run therefore produced no features and
    every segment was rescored as `unknown` — the system silently concluded that
    no rain was forecast anywhere because the forecast had not changed. The
    runtime run measured it: 2,860 scores went to nothing on the second pass.
    """

    pipeline = _without_comments(
        (Path(__file__).resolve().parents[1] / "app" / "risk_pipeline.py").read_text()
    )
    built = pipeline[pipeline.index("rainfall: Feature | None = None") :]
    built = built[: built.index("incidents = _incident_counts(")]
    assert "latest_records(" in built, "forecasts must come from the store"
    assert "warnings_in_force(" in built, "warnings must come from the store"
    assert "for record in records" not in built, "not from one run's return value"
    assert "store_records(" in pipeline, "a successful run must refresh the store"


def test_the_store_returns_an_observation_at_its_own_age() -> None:
    """Age is preserved, so the engine's freshness rule still applies.

    Re-stamping a reused observation with the time it was reused would make a
    week-old forecast look current, which is worse than having none at all.
    """

    query = _without_comments(
        (Path(__file__).resolve().parents[1] / "app" / "sources.py").read_text()
    )
    query = query[query.index("def latest_records(") :]
    query = query[: query.index("def build_adapters(")]
    assert "order by kind, subject_ref, observed_at desc" in query
    assert "observed_at" in query and "now()" not in query


def test_the_sample_tool_refuses_an_empty_or_unsafe_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty path once meant the working directory, and a README overwritten."""

    from app import recorded_samples

    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("the project's own readme")
    assert recorded_samples.main([""]) == 2
    assert recorded_samples.main([]) == 2
    assert recorded_samples.main(["."]) == 2
    assert (tmp_path / "README.md").read_text() == "the project's own readme"
    assert recorded_samples.main([str(tmp_path / "samples")]) == 0
    assert (tmp_path / "samples" / "sachet_cap.xml").is_file()
