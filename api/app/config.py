"""Explicit, validated runtime configuration for the API service."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppMode = Literal["local_demo", "hosted_demo", "pilot", "production"]


class Settings(BaseSettings):
    """Service settings sourced from environment variables or a local ``.env``."""

    # The service's own ``.env`` is honoured whatever the working directory;
    # a ``.env`` in the current directory still takes precedence when present.
    model_config = SettingsConfigDict(
        # pydantic-settings gives LATER files precedence, so the service's own
        # .env is listed last: a repo-root .env may point at a hosted project
        # while this service is meant to run against the local stack.
        env_file=(".env", str(Path(__file__).resolve().parents[1] / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_name: str = "RASTA API"
    app_version: str = "0.1.0"
    app_mode: AppMode = "local_demo"
    api_prefix: str = "/v1"
    allowed_origins: tuple[str, ...] = ("http://localhost:3000",)
    request_id_header: str = "X-Request-Id"
    database_url: str = ""
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_jwt_audience: str = "authenticated"
    # Bounded map reads: the pilot bbox is ~0.0006 deg²; 0.25 deg² (~50 km
    # square at this latitude) is generous without allowing a whole-region pull.
    network_max_bbox_area_deg2: float = 0.25
    network_max_features: int = 5000
    # Signs the short-lived, trip-scoped tracking credential a driver's device uses to
    # file positions.
    telemetry_token_secret: str = ""
    # Official-source endpoints.
    imd_api_base_url: str = ""
    sachet_cap_base_url: str = ""
    # Recorded source documents used when no live URL is configured.
    source_fixture_root: str = "data/fixtures/sources"
    # Per-segment terrain slope built from SRTM (data/sources/build_terrain.py).
    terrain_file: str = "data/pilot/shillong_terrain.json"
    # Web Push (RFC 8292).
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = ""
    # Hosts accepted as push endpoints besides the real push services, over plain HTTP
    # too.
    push_extra_endpoint_hosts: str = ""
    # Every legitimate request body is small JSON: evidence goes straight to
    # object storage. 1 MiB leaves ample room for a 20-point telemetry batch.
    max_request_bytes: int = 1_048_576
    # Per-caller limits on costly writes (app/ratelimit.py). Off only for a
    # load test that means to exceed them.
    rate_limits_enabled: bool = True
    # Telemetry kept for completed trips; older positions are removed nightly by pg_cron.
    telemetry_retention_days: int = 30
    # Re-score every district's roads this often, in minutes (app/risk_schedule.py).
    # 0 leaves it to POST /v1/risk/recompute.
    risk_recompute_minutes: int = 0
    # A trained model exported by ml/ (model_logistic.json) and its SHA-256, both or
    # neither. It runs in shadow beside baseline-v1 (app/risk_model.py).
    risk_model_file: str = ""
    risk_model_sha256: str = ""

    @property
    def resolved_source_fixture_root(self) -> Path | None:
        """The recorded-source directory, found the same way from any cwd."""

        if not self.source_fixture_root:
            return None
        candidate = Path(self.source_fixture_root)
        if candidate.is_absolute():
            return candidate
        repository_root = Path(__file__).resolve().parents[2]
        return repository_root / candidate

    @property
    def resolved_terrain_file(self) -> Path | None:
        """The terrain-slope file, found the same way from any cwd."""

        if not self.terrain_file:
            return None
        candidate = Path(self.terrain_file)
        if candidate.is_absolute():
            return candidate
        return Path(__file__).resolve().parents[2] / candidate

    @property
    def resolved_risk_model_file(self) -> Path | None:
        """The trained-model file, found the same way from any cwd."""

        if not self.risk_model_file:
            return None
        candidate = Path(self.risk_model_file)
        if candidate.is_absolute():
            return candidate
        return Path(__file__).resolve().parents[2] / candidate

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/") or normalized == "/":
            raise ValueError("API_PREFIX must be a non-root absolute path")
        return normalized.rstrip("/")

    @field_validator("risk_recompute_minutes")
    @classmethod
    def validate_risk_recompute_minutes(cls, value: int) -> int:
        if value != 0 and not 5 <= value <= 1440:
            raise ValueError(
                "RISK_RECOMPUTE_MINUTES must be 0 (off) or between 5 and 1440"
            )
        return value

    @field_validator("request_id_header")
    @classmethod
    def validate_request_id_header(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.lower() != "x-request-id":
            raise ValueError("REQUEST_ID_HEADER must be X-Request-Id")
        return "X-Request-Id"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: str | Sequence[str]) -> tuple[str, ...]:
        candidates = value.split(",") if isinstance(value, str) else list(value)

        origins: list[str] = []
        for candidate in candidates:
            origin = str(candidate).strip().rstrip("/")
            if not origin:
                continue
            if origin == "*":
                raise ValueError("ALLOWED_ORIGINS must not contain a wildcard")
            parsed = urlparse(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.params
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "Each ALLOWED_ORIGINS value must be an http(s) origin only"
                )
            normalized = f"{parsed.scheme}://{parsed.netloc}"
            if normalized not in origins:
                origins.append(normalized)

        if not origins:
            raise ValueError("ALLOWED_ORIGINS must contain at least one origin")
        return tuple(origins)
