"""External source adapters, conditional fetching and run provenance."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

RunStatus = Literal["success", "unchanged", "partial", "failed", "disabled"]

#: How long a source's content stays usable before it is called stale.
DEFAULT_FRESHNESS = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """One attempt at one source."""

    status: RunStatus
    etag: str | None = None
    checksum: str | None = None
    body: bytes | None = None
    error_code: str | None = None
    #: Never a raw exception string: those carry URLs, tokens and host paths.
    detail: str | None = None
    record_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A normalised observation an adapter produced."""

    subject_type: str
    subject_ref: str
    kind: str
    value: dict[str, Any]
    observed_at: datetime
    valid_until: datetime | None = None


def checksum_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def redact(error: BaseException) -> str:
    """A diagnostic a dashboard may show."""

    return type(error).__name__


class SourceAdapter(Protocol):
    name: str
    freshness: timedelta

    def configured(self) -> bool: ...

    def fetch(self, *, etag: str | None) -> FetchResult: ...

    def parse(self, body: bytes) -> list[SourceRecord]: ...


def conditional_get(
    url: str,
    *,
    etag: str | None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> FetchResult:
    """HTTP GET that asks the server not to resend what we already hold."""

    request = urllib.request.Request(url, headers={"Accept": "*/*", **(headers or {})})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return FetchResult(
                status="success",
                etag=response.headers.get("ETag"),
                checksum=checksum_of(body),
                body=body,
                metadata={"http_status": response.status, "bytes": len(body)},
            )
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return FetchResult(
                status="unchanged", etag=etag, metadata={"http_status": 304}
            )
        return FetchResult(
            status="failed",
            error_code=f"http_{error.code}",
            detail=f"The source answered {error.code}.",
            metadata={"http_status": error.code},
        )
    except Exception as error:  # timeouts, DNS, TLS, connection refused
        return FetchResult(
            status="failed",
            error_code="unreachable",
            detail=f"The source could not be reached ({redact(error)}).",
        )


# --- adapters ---------------------------------------------------------------


class CapWarningAdapter:
    """Common Alerting Protocol warnings, as SACHET publishes them."""

    name = "sachet_cap"
    freshness = timedelta(hours=1)

    #: CAP severities, most serious first, mapped to what this system means.
    SEVERITY_ORDER = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")

    def __init__(self, base_url: str | None) -> None:
        self._base_url = (base_url or "").strip()

    def configured(self) -> bool:
        return bool(self._base_url)

    def fetch(self, *, etag: str | None) -> FetchResult:
        if not self.configured():
            return FetchResult(
                status="disabled",
                error_code="not_configured",
                detail="No CAP feed URL is configured for this deployment.",
            )
        return conditional_get(self._base_url, etag=etag)

    def parse(self, body: bytes) -> list[SourceRecord]:
        """Read CAP alert blocks. A document that is not CAP raises."""

        root = ElementTree.fromstring(body.decode("utf-8"))
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag[: root.tag.index("}") + 1]

        records: list[SourceRecord] = []
        alerts = (
            [root]
            if root.tag == f"{namespace}alert"
            else root.findall(f".//{namespace}alert")
        )
        for alert in alerts:
            identifier = (alert.findtext(f"{namespace}identifier") or "").strip()
            sent = (alert.findtext(f"{namespace}sent") or "").strip()
            info = alert.find(f"{namespace}info")
            if info is None or not identifier:
                continue
            severity = (info.findtext(f"{namespace}severity") or "Unknown").strip()
            event = (info.findtext(f"{namespace}event") or "").strip()
            headline = (info.findtext(f"{namespace}headline") or "").strip()
            expires = (info.findtext(f"{namespace}expires") or "").strip()
            areas = [
                (area.findtext(f"{namespace}areaDesc") or "").strip()
                for area in info.findall(f"{namespace}area")
            ]
            records.append(
                SourceRecord(
                    subject_type="area",
                    subject_ref=areas[0] if areas else "unspecified",
                    kind="official_warning",
                    value={
                        "identifier": identifier,
                        "event": event,
                        "headline": headline,
                        "severity": severity,
                        "areas": [area for area in areas if area],
                        # Normalised 0..1 so the risk engine has one scale, with
                        # the original severity kept beside it.
                        "severity_normalised": self.normalise(severity),
                    },
                    observed_at=_parse_time(sent),
                    valid_until=_parse_time(expires) if expires else None,
                )
            )
        if not records:
            raise ValueError("No CAP alert blocks found in the document.")
        return records

    @classmethod
    def normalise(cls, severity: str) -> float:
        scale = {"Extreme": 1.0, "Severe": 0.75, "Moderate": 0.5, "Minor": 0.25}
        return scale.get(severity.strip().title(), 0.0)


class RainfallForecastAdapter:
    """District rainfall forecast, as IMD publishes it over JSON."""

    name = "imd_rainfall"
    freshness = timedelta(hours=6)

    #: Millimetres in 24 hours that maps to a normalised 1.0. IMD's own
    #: "heavy rainfall" threshold for the region, so the scale means something.
    HEAVY_RAINFALL_MM = 115.0

    def __init__(self, base_url: str | None) -> None:
        self._base_url = (base_url or "").strip()

    def configured(self) -> bool:
        return bool(self._base_url)

    def fetch(self, *, etag: str | None) -> FetchResult:
        if not self.configured():
            return FetchResult(
                status="disabled",
                error_code="not_configured",
                detail="No rainfall forecast URL is configured for this deployment.",
            )
        return conditional_get(self._base_url, etag=etag)

    def parse(self, body: bytes) -> list[SourceRecord]:
        payload = json.loads(body.decode("utf-8"))
        entries = payload.get("districts")
        if not isinstance(entries, list) or not entries:
            raise ValueError("No district entries in the forecast document.")

        records: list[SourceRecord] = []
        for entry in entries:
            name = str(entry.get("district", "")).strip()
            millimetres = entry.get("rainfall_mm_48h")
            if not name or millimetres is None:
                continue
            records.append(
                SourceRecord(
                    subject_type="district",
                    subject_ref=name,
                    kind="forecast_rainfall",
                    value={
                        "rainfall_mm_48h": float(millimetres),
                        "normalised": self.normalise(float(millimetres)),
                        "issued_by": str(payload.get("issued_by", "")).strip() or None,
                    },
                    observed_at=_parse_time(str(payload.get("issued_at", ""))),
                    valid_until=_parse_time(str(payload.get("valid_until", "")))
                    if payload.get("valid_until")
                    else None,
                )
            )
        if not records:
            raise ValueError("No usable district rainfall entries in the document.")
        return records

    @classmethod
    def normalise(cls, millimetres: float) -> float:
        if millimetres <= 0:
            return 0.0
        return min(1.0, round(millimetres / cls.HEAVY_RAINFALL_MM, 4))


class FixtureAdapter:
    """An adapter that reads a recorded document from disk."""

    def __init__(
        self,
        name: str,
        path: Path,
        parser: SourceAdapter,
        freshness: timedelta | None = None,
    ) -> None:
        self.name = name
        self.freshness = freshness or parser.freshness
        self._path = path
        self._parser = parser

    def configured(self) -> bool:
        return self._path.exists()

    def fetch(self, *, etag: str | None) -> FetchResult:
        if not self._path.exists():
            return FetchResult(
                status="failed",
                error_code="fixture_missing",
                detail="The recorded document for this source is not present.",
            )
        body = self._path.read_bytes()
        digest = checksum_of(body)
        if etag == digest:
            # The same rule a 304 expresses, applied to a file: unchanged
            # content is not re-parsed and not re-alerted.
            return FetchResult(status="unchanged", etag=digest, checksum=digest)
        return FetchResult(
            status="success",
            etag=digest,
            checksum=digest,
            body=body,
            metadata={"bytes": len(body), "recorded_fixture": self._path.name},
        )

    def parse(self, body: bytes) -> list[SourceRecord]:
        return self._parser.parse(body)


def _parse_time(value: str) -> datetime:
    text = (value or "").strip()
    if not text:
        return datetime.now(tz=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- the run pipeline -------------------------------------------------------


@dataclass
class RunOutcome:
    source: str
    status: RunStatus
    record_count: int
    error_code: str | None
    detail: str | None
    reused_cached_content: bool
    run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "record_count": self.record_count,
            "error_code": self.error_code,
            "detail": self.detail,
            "reused_cached_content": self.reused_cached_content,
            "run_id": self.run_id,
        }


def last_successful_etag(
    connection: psycopg.Connection, *, organization_id: str, source: str
) -> str | None:
    row = connection.execute(
        """
        select etag
        from public.source_runs
        where organization_id = %(org)s::uuid and source = %(source)s
          and status in ('success', 'unchanged') and etag is not null
        order by started_at desc
        limit 1
        """,
        {"org": organization_id, "source": source},
    ).fetchone()
    return row["etag"] if row else None


def run_source(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    adapter: SourceAdapter,
    source_mode: str = "live",
    district_id: str | None = None,
) -> tuple[RunOutcome, list[SourceRecord]]:
    """Fetch, parse and record one source run. Never raises for a bad source."""

    started = datetime.now(tz=UTC)
    etag = last_successful_etag(
        connection, organization_id=organization_id, source=adapter.name
    )
    result = adapter.fetch(etag=etag)

    records: list[SourceRecord] = []
    status = result.status
    error_code = result.error_code
    detail = result.detail

    if result.status == "success" and result.body is not None:
        try:
            records = adapter.parse(result.body)
        except Exception as error:
            # Malformed content is a failure of this run, not of the data we
            # already hold. Nothing stored is touched.
            status = "failed"
            error_code = "malformed_payload"
            detail = f"The source's response could not be read ({redact(error)})."
            records = []

    row = connection.execute(
        """
        insert into public.source_runs (
          organization_id, district_id, source, source_mode, started_at,
          finished_at, status, etag, raw_checksum, record_count, error_code, metadata
        )
        values (
          %(org)s::uuid, %(district)s::uuid, %(source)s, %(mode)s::public.data_mode,
          %(started)s, %(finished)s, %(status)s::public.source_run_status, %(etag)s,
          %(checksum)s, %(count)s, %(error)s, %(meta)s
        )
        returning id::text as id
        """,
        {
            "org": organization_id,
            "district": district_id,
            "source": adapter.name,
            "mode": source_mode,
            "started": started,
            # Both ends of the measurement come from one clock.
            "finished": datetime.now(tz=UTC),
            "status": status,
            "etag": result.etag or etag,
            "checksum": result.checksum,
            "count": len(records),
            "error": error_code,
            # Redacted by construction: counts, byte sizes and HTTP status only.
            "meta": Jsonb(
                {**result.metadata, "detail": detail} if detail else result.metadata
            ),
        },
    ).fetchone()

    return (
        RunOutcome(
            source=adapter.name,
            status=status,
            record_count=len(records),
            error_code=error_code,
            detail=detail,
            reused_cached_content=status == "unchanged",
            run_id=row["id"],
        ),
        records,
    )


def store_records(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    source: str,
    source_mode: str,
    run_id: str,
    records: list[SourceRecord],
) -> int:
    """Keep what this run read, so a later unchanged or failed run loses nothing."""

    if not records:
        return 0
    for record in records:
        connection.execute(
            """
            insert into public.source_records (
              organization_id, source, subject_type, subject_ref, kind,
              observed_at, valid_until, value, source_mode, run_id
            )
            values (
              %(org)s::uuid, %(source)s, %(subject_type)s, %(subject_ref)s, %(kind)s,
              %(observed)s, %(until)s, %(value)s, %(mode)s::public.data_mode, %(run)s::uuid
            )
            on conflict (organization_id, source, subject_type, subject_ref, kind, observed_at)
            do update set
              value = excluded.value,
              valid_until = excluded.valid_until,
              source_mode = excluded.source_mode,
              run_id = excluded.run_id,
              recorded_at = now()
            """,
            {
                "org": organization_id,
                "source": source,
                "subject_type": record.subject_type,
                "subject_ref": record.subject_ref,
                "kind": record.kind,
                "observed": record.observed_at,
                "until": record.valid_until,
                "value": Jsonb(record.value),
                "mode": source_mode,
                "run": run_id,
            },
        )
    return len(records)


def latest_records(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    kinds: tuple[str, ...],
) -> list[tuple[SourceRecord, str, str]]:
    """The most recent reading of each kind per subject, with its source and mode."""

    if not kinds:
        return []
    rows = connection.execute(
        """
        select distinct on (kind, subject_ref)
          source, subject_type, subject_ref, kind, observed_at, valid_until,
          value, source_mode::text as source_mode
        from public.source_records
        where organization_id = %(org)s::uuid and kind = any(%(kinds)s::text[])
        order by kind, subject_ref, observed_at desc
        """,
        {"org": organization_id, "kinds": list(kinds)},
    ).fetchall()
    return [
        (
            SourceRecord(
                subject_type=row["subject_type"],
                subject_ref=row["subject_ref"],
                kind=row["kind"],
                value=row["value"],
                observed_at=row["observed_at"],
                valid_until=row["valid_until"],
            ),
            row["source"],
            row["source_mode"],
        )
        for row in rows
    ]


def build_adapters(
    *,
    imd_base_url: str | None,
    cap_base_url: str | None,
    fixture_root: Path | None = None,
) -> list[tuple[SourceAdapter, str]]:
    """Every adapter this deployment will attempt, with the mode it reports as."""

    adapters: list[tuple[SourceAdapter, str]] = []
    rainfall = RainfallForecastAdapter(imd_base_url)
    warnings = CapWarningAdapter(cap_base_url)

    for adapter, fixture_name in (
        (rainfall, "imd_rainfall.json"),
        (warnings, "sachet_cap.xml"),
    ):
        if adapter.configured():
            adapters.append((adapter, "live"))
            continue
        if fixture_root is not None and (fixture_root / fixture_name).exists():
            adapters.append(
                (
                    FixtureAdapter(
                        f"{adapter.name}_recorded", fixture_root / fixture_name, adapter
                    ),
                    "recorded",
                )
            )
        else:
            adapters.append((adapter, "live"))
    return adapters


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


__all__ = [
    "CapWarningAdapter",
    "FetchResult",
    "FixtureAdapter",
    "RainfallForecastAdapter",
    "RunOutcome",
    "SourceAdapter",
    "SourceRecord",
    "build_adapters",
    "checksum_of",
    "conditional_get",
    "connect",
    "last_successful_etag",
    "latest_records",
    "redact",
    "run_source",
    "store_records",
]
