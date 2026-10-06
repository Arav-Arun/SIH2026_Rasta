"""External source adapters, conditional fetching and run provenance."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb

from app import db

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


def _cap_text(element: ElementTree.Element, tag: str, namespace: str) -> str:
    return (element.findtext(f"{namespace}{tag}") or "").strip()


def parse_cap_references(text: str) -> list[str]:
    """Identifiers named by a CAP ``references`` element.

    The element is a space-separated list of ``sender,identifier,sent`` triples.
    """

    identifiers: list[str] = []
    for triple in text.split():
        parts = triple.split(",")
        if len(parts) >= 3 and parts[1].strip():
            identifiers.append(parts[1].strip())
    return identifiers


def parse_cap_polygon(text: str) -> list[list[float]] | None:
    """A CAP polygon as a closed ring of ``[lat, lon]`` pairs, or None if unusable."""

    points: list[list[float]] = []
    for pair in text.split():
        try:
            lat, lon = (float(part) for part in pair.split(","))
        except ValueError:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        points.append([lat, lon])
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def parse_cap_circle(text: str) -> dict[str, float] | None:
    """A CAP circle, ``lat,lon radius`` with the radius in kilometres, or None."""

    try:
        centre, radius = text.split()
        lat, lon = (float(part) for part in centre.split(","))
        radius_km = float(radius)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius_km <= 1000):
        return None
    return {"lat": lat, "lon": lon, "radius_km": radius_km}


class CapWarningAdapter:
    """Common Alerting Protocol warnings, as SACHET publishes them.

    Only messages that are actionable are turned into warnings: status ``Actual``,
    and ``Exercise`` too when ``accept_exercise`` is set, which is for the recorded
    sample that is an exercise message by design. Test, draft and system messages
    never are. A ``Cancel`` message becomes a record that withdraws the alerts it
    references, and an ``Update`` withdraws the alert it replaces.
    """

    name = "sachet_cap"
    freshness = timedelta(hours=1)

    #: CAP severities, most serious first, mapped to what this system means.
    SEVERITY_ORDER = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")

    def __init__(self, base_url: str | None, *, accept_exercise: bool = False) -> None:
        self._base_url = (base_url or "").strip()
        self._statuses = {"actual", "exercise"} if accept_exercise else {"actual"}

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
        """Read CAP alerts. A document with no ``alert`` element at all raises."""

        root = ElementTree.fromstring(body.decode("utf-8"))
        # A feed may wrap CAP alerts in its own, differently namespaced elements, so
        # alerts are found by local name and each is read in its own namespace.
        alerts = [
            element
            for element in root.iter()
            if isinstance(element.tag, str)
            and element.tag.rsplit("}", 1)[-1] == "alert"
        ]
        if not alerts:
            raise ValueError("No CAP alert blocks found in the document.")

        records: list[SourceRecord] = []
        for alert in alerts:
            namespace = (
                alert.tag[: alert.tag.index("}") + 1]
                if alert.tag.startswith("{")
                else ""
            )
            record = self._read_alert(alert, namespace)
            if record is not None:
                records.append(record)
        return records

    def _read_alert(
        self, alert: ElementTree.Element, namespace: str
    ) -> SourceRecord | None:
        identifier = _cap_text(alert, "identifier", namespace)
        status = _cap_text(alert, "status", namespace) or "Actual"
        message_type = (_cap_text(alert, "msgType", namespace) or "Alert").title()
        if not identifier or status.lower() not in self._statuses:
            return None
        if message_type in {"Ack", "Error"}:
            return None

        sent = _parse_time(_cap_text(alert, "sent", namespace))
        sender = _cap_text(alert, "sender", namespace)
        supersedes = parse_cap_references(_cap_text(alert, "references", namespace))

        if message_type == "Cancel":
            return SourceRecord(
                subject_type="alert",
                subject_ref=identifier,
                kind="official_warning_cancel",
                value={
                    "identifier": identifier,
                    "sender": sender,
                    "msg_type": "Cancel",
                    "status": status,
                    "supersedes": supersedes,
                },
                observed_at=sent,
            )

        infos = alert.findall(f"{namespace}info")
        if not infos:
            return None

        def severity_of(info: ElementTree.Element) -> float:
            return self.normalise(_cap_text(info, "severity", namespace) or "Unknown")

        # An alert may carry one info block per language or event. The most serious
        # one speaks for the alert; every block's areas still count as covered.
        lead = max(infos, key=severity_of)
        severity = _cap_text(lead, "severity", namespace) or "Unknown"
        areas: list[str] = []
        geocodes: list[dict[str, str]] = []
        polygons: list[list[list[float]]] = []
        circles: list[dict[str, float]] = []
        for info in infos:
            for area in info.findall(f"{namespace}area"):
                description = _cap_text(area, "areaDesc", namespace)
                if description and description not in areas:
                    areas.append(description)
                for geocode in area.findall(f"{namespace}geocode"):
                    code = {
                        "name": _cap_text(geocode, "valueName", namespace),
                        "value": _cap_text(geocode, "value", namespace),
                    }
                    if code["value"] and code not in geocodes:
                        geocodes.append(code)
                for text in (p.text or "" for p in area.findall(f"{namespace}polygon")):
                    polygon = parse_cap_polygon(text)
                    if polygon is not None and polygon not in polygons:
                        polygons.append(polygon)
                for text in (c.text or "" for c in area.findall(f"{namespace}circle")):
                    circle = parse_cap_circle(text)
                    if circle is not None and circle not in circles:
                        circles.append(circle)

        expires_text = _cap_text(lead, "expires", namespace)
        expires = _parse_time(expires_text) if expires_text else None
        if expires is not None and expires < sent:
            expires = None  # a validity that ends before the message was sent is wrong

        return SourceRecord(
            subject_type="alert",
            subject_ref=identifier,
            kind="official_warning",
            value={
                "identifier": identifier,
                "sender": sender,
                "msg_type": message_type,
                "status": status,
                "event": _cap_text(lead, "event", namespace),
                "headline": _cap_text(lead, "headline", namespace),
                "severity": severity,
                "areas": areas,
                "geocodes": geocodes,
                "polygons": polygons,
                "circles": circles,
                "supersedes": supersedes,
                # Normalised 0..1 so the risk engine has one scale, with the
                # original severity kept beside it.
                "severity_normalised": self.normalise(severity),
            },
            observed_at=sent,
            valid_until=expires,
        )

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


def _source_record(row: dict[str, Any]) -> tuple[SourceRecord, str, str]:
    return (
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


def latest_records(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    kinds: tuple[str, ...],
    now: datetime | None = None,
    only_in_force: bool = False,
) -> list[tuple[SourceRecord, str, str]]:
    """The most recent reading of each kind per subject, with its source and mode.

    With ``only_in_force``, a reading whose stated validity has ended by ``now`` is
    left out, so an expired forecast is absent rather than used.
    """

    if not kinds:
        return []
    rows = connection.execute(
        """
        select distinct on (kind, subject_ref)
          source, subject_type, subject_ref, kind, observed_at, valid_until,
          value, source_mode::text as source_mode
        from public.source_records
        where organization_id = %(org)s::uuid and kind = any(%(kinds)s::text[])
          and (
            not %(in_force)s or valid_until is null or valid_until > %(now)s
          )
        order by kind, subject_ref, observed_at desc
        """,
        {
            "org": organization_id,
            "kinds": list(kinds),
            "in_force": only_in_force,
            "now": now or datetime.now(tz=UTC),
        },
    ).fetchall()
    return [_source_record(row) for row in rows]


#: Warnings older than this are never read, whatever they say about their validity.
WARNING_LOOKBACK = timedelta(days=7)


def warnings_in_force(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    now: datetime,
) -> list[tuple[SourceRecord, str, str, datetime]]:
    """Every official warning that is still in force at ``now``.

    Unlike a forecast, several warnings can hold for one place at once, and a more
    serious one must not be hidden by a later, milder one. A warning is out of force
    when its validity has ended or when a later message from the same source
    withdraws it: a ``Cancel``, or an ``Update`` that names it in ``references``.

    Each comes with when the source last confirmed it: the latest run that listed
    it, or a later run that found the same content unchanged. A warning can stay in
    force for days, so its age is not what makes it doubtful; a source that has not
    been read since is.
    """

    rows = connection.execute(
        """
        select w.source, w.subject_type, w.subject_ref, w.kind, w.observed_at,
               w.valid_until, w.value, w.source_mode::text as source_mode,
               greatest(
                 coalesce(listed.finished_at, listed.started_at), reread.at
               ) as confirmed_at
        from public.source_records as w
        join public.source_runs as listed
          on listed.id = w.run_id and listed.organization_id = w.organization_id
        left join lateral (
          -- An unchanged run read the same content again, until a newer
          -- successful read replaced that content.
          select max(coalesce(u.finished_at, u.started_at)) as at
          from public.source_runs as u
          where u.organization_id = w.organization_id
            and u.source = w.source
            and u.status = 'unchanged'
            and u.started_at > listed.started_at
            and not exists (
              select 1
              from public.source_runs as newer
              where newer.organization_id = w.organization_id
                and newer.source = w.source
                and newer.status in ('success', 'partial')
                and newer.started_at > listed.started_at
                and newer.started_at < u.started_at
            )
        ) as reread on true
        where w.organization_id = %(org)s::uuid
          and w.kind = 'official_warning'
          and w.observed_at > %(since)s
          and (w.valid_until is null or w.valid_until > %(now)s)
          and not exists (
            select 1
            from public.source_records as later
            where later.organization_id = w.organization_id
              and later.source = w.source
              and later.kind in ('official_warning', 'official_warning_cancel')
              and later.observed_at >= w.observed_at
              and (later.value -> 'supersedes') ? (w.value ->> 'identifier')
          )
        order by w.observed_at desc
        """,
        {
            "org": organization_id,
            "since": now - WARNING_LOOKBACK,
            "now": now,
        },
    ).fetchall()
    return [(*_source_record(row), row["confirmed_at"]) for row in rows]


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
    # The recorded sample is an Exercise message by design, so only the parser that
    # reads it accepts that status; a live feed's exercises are never warnings.
    recorded_warnings = CapWarningAdapter(cap_base_url, accept_exercise=True)

    for adapter, fixture_name, parser in (
        (rainfall, "imd_rainfall.json", rainfall),
        (warnings, "sachet_cap.xml", recorded_warnings),
    ):
        if adapter.configured():
            adapters.append((adapter, "live"))
            continue
        if fixture_root is not None and (fixture_root / fixture_name).exists():
            adapters.append(
                (
                    FixtureAdapter(
                        f"{adapter.name}_recorded", fixture_root / fixture_name, parser
                    ),
                    "recorded",
                )
            )
        else:
            adapters.append((adapter, "live"))
    return adapters


def connect(database_url: str) -> AbstractContextManager[psycopg.Connection[Any]]:
    """A pooled connection, for use in a ``with`` block."""

    return db.connect(database_url)


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
    "parse_cap_circle",
    "parse_cap_polygon",
    "parse_cap_references",
    "redact",
    "run_source",
    "store_records",
    "warnings_in_force",
]
