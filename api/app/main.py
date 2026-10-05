"""FastAPI application factory for the bounded RASTA pilot."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.alerts import build_alert_repository
from app.audit import build_audit_repository
from app.auth import (
    AuthenticatedSubject,
    BearerTokenVerifier,
    require_authenticated_subject,
)
from app.config import Settings
from app.data_health import build_data_health_repository
from app.db import close_pools
from app.errors import ApiError, ErrorEnvelope, error_response
from app.evidence import EvidenceStore, build_evidence_store
from app.identity import (
    IdentityRepository,
    build_identity_repository,
    build_me_response,
)
from app.incidents import IncidentRepository, build_incident_repository
from app.inspections import InspectionRepository, build_inspection_repository
from app.logistics import build_logistics_repository
from app.middleware import (
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    install_access_log_redaction,
    install_request_logging,
)
from app.network import (
    OSM_ATTRIBUTION,
    NetworkRepository,
    SegmentQuery,
    bbox_area_deg2,
    build_network_repository,
    compute_connectivity,
    parse_bbox,
)
from app.push import build_push_repository, build_sender, parse_extra_hosts
from app.ratelimit import RateLimiter
from app.route_plans import build_route_plan_repository
from app.routes_alerts import build_alert_router
from app.routes_incidents import build_router as build_incident_router
from app.routes_logistics import build_logistics_router
from app.routes_operations import build_operations_router
from app.routes_route_plans import build_route_plan_router
from app.routes_telemetry import build_telemetry_router
from app.schemas import (
    ConnectivitySummaryResponse,
    HealthResponse,
    MeResponse,
    NetworkSegmentsResponse,
    SegmentDetailResponse,
)
from app.scope import WorkspaceScope, require_capability
from app.supabase_jwt import build_bearer_token_verifier
from app.telemetry import build_telemetry_repository

_LOGGER = logging.getLogger("rasta.api")


def _validation_details(error: RequestValidationError) -> dict[str, Any]:
    """Keep useful field locations/types without echoing submitted values."""

    fields: list[dict[str, str]] = []
    for issue in error.errors():
        location = issue.get("loc", ())
        fields.append(
            {
                "path": ".".join(str(part) for part in location),
                "code": str(issue.get("type", "invalid")),
            }
        )
    return {"fields": fields}


def _http_error(status_code: int) -> ApiError:
    if status_code == 400:
        return ApiError(400, "bad_request", "The request could not be processed.")
    if status_code == 401:
        return ApiError(
            401,
            "invalid_session",
            "The session is invalid or could not be verified.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if status_code == 403:
        return ApiError(403, "scope_denied", "You do not have access to this action.")
    if status_code == 404:
        return ApiError(404, "not_found", "The requested resource was not found.")
    if status_code == 405:
        return ApiError(405, "method_not_allowed", "This method is not allowed.")
    if status_code == 413:
        return ApiError(413, "payload_too_large", "The request payload is too large.")
    if status_code == 429:
        return ApiError(429, "rate_limited", "Please retry after a short delay.")
    if status_code >= 500:
        return ApiError(
            status_code,
            "server_error",
            "The server could not complete the request.",
        )
    return ApiError(status_code, "http_error", "The request could not be processed.")


#: Unique values a person can collide with by typing one that is already taken.
_TAKEN_VALUES = {
    "consignments_organization_id_reference_key": (
        "reference_in_use",
        "Another consignment already uses this reference.",
    ),
    "vehicles_organization_id_registration_ref_key": (
        "registration_in_use",
        "Another vehicle already has this registration.",
    ),
}


def _integrity_error(exc: psycopg.IntegrityError) -> ApiError:
    """Say which rule a write broke, without repeating the values involved."""

    if isinstance(exc, psycopg.errors.UniqueViolation):
        code, message = _TAKEN_VALUES.get(
            exc.diag.constraint_name or "",
            ("conflict", "This conflicts with a record that already exists."),
        )
        return ApiError(409, code, message)
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return ApiError(
            422,
            "unknown_reference",
            "The request refers to a record that does not exist.",
        )
    return ApiError(
        422, "invalid_value", "The request breaks a rule the data must follow."
    )


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Pooled connections are closed, not dropped, when the process stops.
    close_pools()


def create_app(
    settings: Settings | None = None,
    *,
    bearer_token_verifier: BearerTokenVerifier | None = None,
    identity_repository: IdentityRepository | None = None,
    network_repository: NetworkRepository | None = None,
    incident_repository: IncidentRepository | None = None,
    inspection_repository: InspectionRepository | None = None,
    evidence_store: EvidenceStore | None = None,
) -> FastAPI:
    """Build an application with an explicit config and auth composition root."""

    runtime_settings = settings or Settings()
    install_access_log_redaction()
    install_request_logging()
    app = FastAPI(
        lifespan=_lifespan,
        # Interactive docs describe every route; a production deployment does
        # not publish them. The demo and pilot keep them for reviewers.
        docs_url=None if runtime_settings.app_mode == "production" else "/docs",
        redoc_url=None if runtime_settings.app_mode == "production" else "/redoc",
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        description=(
            "Bounded RASTA pilot API. This base exposes no live "
            "operational data and does not accept unverified bearer sessions."
        ),
        openapi_tags=[
            {"name": "system", "description": "Process-level service checks."},
            {"name": "identity", "description": "Authenticated identity bootstrap."},
            {
                "name": "network",
                "description": (
                    "Scoped road-network state: bounded segment reads, segment "
                    "detail and facility reachability."
                ),
            },
            {
                "name": "incidents",
                "description": (
                    "Field reports, private evidence uploads and dispatcher review "
                    "decisions that change road state transactionally."
                ),
            },
            {
                "name": "inspections",
                "description": "Dispatcher-assigned verification tasks.",
            },
        ],
    )
    app.state.settings = runtime_settings
    app.state.bearer_token_verifier = (
        bearer_token_verifier
        or build_bearer_token_verifier(
            supabase_url=runtime_settings.supabase_url,
        )
    )
    app.state.identity_repository = (
        identity_repository
        if identity_repository is not None
        else build_identity_repository(runtime_settings.database_url)
    )
    app.state.network_repository = (
        network_repository
        if network_repository is not None
        else build_network_repository(runtime_settings.database_url)
    )
    resolved_evidence_store = evidence_store or build_evidence_store(
        supabase_url=runtime_settings.supabase_url,
        secret_key=runtime_settings.supabase_secret_key,
    )
    app.state.evidence_store = resolved_evidence_store
    push_sender = build_sender(
        vapid_private_key=runtime_settings.vapid_private_key,
        vapid_public_key=runtime_settings.vapid_public_key,
        vapid_subject=runtime_settings.vapid_subject,
        extra_hosts=parse_extra_hosts(runtime_settings.push_extra_endpoint_hosts),
    )
    app.state.push_sender = push_sender
    app.state.incident_repository = (
        incident_repository
        if incident_repository is not None
        else build_incident_repository(
            runtime_settings.database_url, resolved_evidence_store, push_sender
        )
    )
    app.state.inspection_repository = (
        inspection_repository
        if inspection_repository is not None
        else build_inspection_repository(runtime_settings.database_url)
    )

    # JSON compresses well: the map's road network for one district is several
    # megabytes as sent and a tenth of that compressed, which is most of the
    # difference between a map that loads and one that does not on a phone.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    # Request IDs wrap CORS and exception responses. CORS is intentionally
    # bearer-token friendly without cookies and never permits wildcard origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-Id",
            "X-Tracking-Grant",
        ],
        expose_headers=[runtime_settings.request_id_header],
        max_age=600,
    )
    app.add_middleware(
        RequestIdMiddleware,
        header_name=runtime_settings.request_id_header,
    )
    # Outermost: an oversized body is refused before anything reads it, and
    # every response, errors included, carries the security headers.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=runtime_settings.max_request_bytes
    )
    app.add_middleware(
        SecurityHeadersMiddleware, hsts=runtime_settings.app_mode != "local_demo"
    )
    app.state.rate_limiter = (
        RateLimiter() if runtime_settings.rate_limits_enabled else None
    )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            request,
            ApiError(
                422,
                "validation_error",
                "The request contains invalid fields.",
                details=_validation_details(exc),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return error_response(request, _http_error(exc.status_code))

    @app.exception_handler(psycopg.IntegrityError)
    async def handle_integrity_error(
        request: Request, exc: psycopg.IntegrityError
    ) -> JSONResponse:
        # A constraint caught what validation did not: the request's fault, so
        # a 409 or 422 rather than a 500. The constraint name is logged; the
        # database's detail line is not, because it quotes the values.
        _LOGGER.warning(
            "Constraint %s refused request_id=%s",
            exc.diag.constraint_name or type(exc).__name__,
            getattr(request.state, "request_id", "unknown"),
        )
        return error_response(request, _integrity_error(exc))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # The traceback is logged server-side against the request id so a 500
        # can be diagnosed; the response body never carries exception strings,
        # which may contain provider or user data.
        _LOGGER.exception(
            "Unhandled error for request_id=%s",
            getattr(request.state, "request_id", "unknown"),
            exc_info=exc,
        )
        return error_response(
            request,
            ApiError(
                500,
                "internal_error",
                "The server could not complete the request.",
            ),
        )

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check whether the API process is responding",
    )
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="rasta-api",
            version=runtime_settings.app_version,
            mode=runtime_settings.app_mode,
            as_of=datetime.now(UTC),
        )

    @app.get(
        f"{runtime_settings.api_prefix}/me",
        response_model=MeResponse,
        responses={
            401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
            503: {
                "model": ErrorEnvelope,
                "description": "Identity profile lookup is not configured.",
            },
        },
        tags=["identity"],
        summary="Return the active user's scoped identity",
    )
    async def me(
        subject: Annotated[
            AuthenticatedSubject, Depends(require_authenticated_subject)
        ],
        request: Request,
    ) -> MeResponse:
        repository = request.app.state.identity_repository
        if repository is None:
            raise ApiError(
                503,
                "identity_bootstrap_unavailable",
                "Identity bootstrap is not configured.",
            )

        record = await repository.load_identity(subject.subject_id)
        if record is None:
            raise ApiError(
                403,
                "scope_denied",
                "You do not have access to this action.",
            )
        return build_me_response(record)

    def _network_repository(request: Request) -> NetworkRepository:
        repository = request.app.state.network_repository
        if repository is None:
            raise ApiError(
                503,
                "network_unavailable",
                "The network database is not configured.",
            )
        return repository

    network_read = require_capability("network:read")
    network_responses: dict[int | str, dict[str, Any]] = {
        401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
        403: {
            "model": ErrorEnvelope,
            "description": "Capability or district scope denied.",
        },
        422: {"model": ErrorEnvelope, "description": "Invalid or oversized request."},
        503: {
            "model": ErrorEnvelope,
            "description": "Network database not configured.",
        },
    }

    @app.get(
        f"{runtime_settings.api_prefix}/network/segments",
        response_model=NetworkSegmentsResponse,
        responses=network_responses,
        tags=["network"],
        summary="Read road segments with current state inside a bounded box",
    )
    async def network_segments(
        request: Request,
        bbox: Annotated[
            str,
            Query(
                description="minLon,minLat,maxLon,maxLat in WGS84",
                examples=["91.875,25.56,91.9,25.585"],
            ),
        ],
        district_id: Annotated[str | None, Query()] = None,
        passability: Annotated[
            Literal["open", "restricted", "closed", "unknown"] | None, Query()
        ] = None,
        since: Annotated[datetime | None, Query()] = None,
        simplify_m: Annotated[float | None, Query(ge=0, le=500)] = None,
        limit: Annotated[int | None, Query(ge=1)] = None,
        scope: WorkspaceScope = Depends(network_read),
    ) -> NetworkSegmentsResponse:
        repository = _network_repository(request)
        parsed_bbox = parse_bbox(bbox)
        area = bbox_area_deg2(parsed_bbox)
        if area > runtime_settings.network_max_bbox_area_deg2:
            raise ApiError(
                422,
                "bbox_too_large",
                "Request a smaller area; the map loads only the viewport plus a margin.",
                details={
                    "requested_area_deg2": round(area, 6),
                    "max_area_deg2": runtime_settings.network_max_bbox_area_deg2,
                },
            )
        max_features = runtime_settings.network_max_features
        if limit is not None and limit > max_features:
            raise ApiError(
                422,
                "limit_too_large",
                "Reduce the feature limit.",
                details={"max_features": max_features},
            )

        scope.require_district(district_id)
        district_ids = scope.district_ids
        if district_id is not None:
            district_ids = frozenset({district_id})

        page = await repository.list_segments(
            SegmentQuery(
                organization_id=scope.organization_id,
                district_ids=district_ids,
                bbox=parsed_bbox,
                passability=passability,
                since=since,
                simplify_m=simplify_m,
                limit=limit or max_features,
            )
        )
        modes = {feature.properties.source_mode for feature in page.features}
        versions = {feature.properties.network_version for feature in page.features}
        return NetworkSegmentsResponse(
            features=page.features,
            as_of=datetime.now(UTC),
            mode=(
                next(iter(modes)) if len(modes) == 1 else "mixed" if modes else "none"
            ),
            network_version=max(versions) if versions else None,
            request_bbox=parsed_bbox,
            total_in_bbox=page.total_in_bbox,
            coverage=page.coverage,
            attribution=OSM_ATTRIBUTION,
        )

    @app.get(
        f"{runtime_settings.api_prefix}/network/segments/{{segment_id}}",
        response_model=SegmentDetailResponse,
        responses={
            **network_responses,
            404: {
                "model": ErrorEnvelope,
                "description": "Segment missing or out of scope.",
            },
        },
        tags=["network"],
        summary="Read one segment's attributes, current state, observations and actions",
    )
    async def network_segment_detail(
        request: Request,
        segment_id: str,
        scope: WorkspaceScope = Depends(network_read),
    ) -> SegmentDetailResponse:
        repository = _network_repository(request)
        detail = await repository.get_segment(
            organization_id=scope.organization_id,
            district_ids=scope.district_ids,
            segment_id=segment_id,
        )
        if detail is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        actions: list[str] = []
        if scope.has("inspection:manage"):
            actions.append("assign_inspection")
        if scope.has("incident:create"):
            actions.append("report_observation")
        if scope.has("route:plan"):
            actions.append("plan_route")
        detail.allowed_actions = actions
        return detail

    @app.get(
        f"{runtime_settings.api_prefix}/connectivity/summary",
        response_model=ConnectivitySummaryResponse,
        responses={
            **network_responses,
            404: {
                "model": ErrorEnvelope,
                "description": "District missing or out of scope.",
            },
        },
        tags=["network"],
        summary="Compute facility reachability for one district under current road state",
    )
    async def connectivity_summary(
        request: Request,
        district_id: Annotated[str, Query()],
        scope: WorkspaceScope = Depends(network_read),
    ) -> ConnectivitySummaryResponse:
        repository = _network_repository(request)
        scope.require_district(district_id)
        network = await repository.district_network(
            organization_id=scope.organization_id,
            district_id=district_id,
        )
        if network is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        return compute_connectivity(network)

    app.include_router(build_incident_router(runtime_settings.api_prefix))
    app.state.logistics_repository = build_logistics_repository(
        runtime_settings.database_url
    )
    app.include_router(build_logistics_router(runtime_settings.api_prefix))

    app.state.route_plan_repository = build_route_plan_repository(
        runtime_settings.database_url, push_sender
    )
    app.include_router(build_route_plan_router(runtime_settings.api_prefix))

    # A per-process secret keeps the local path zero-configuration.
    grant_secret = runtime_settings.telemetry_token_secret
    if not grant_secret:
        grant_secret = secrets.token_urlsafe(32)
        _LOGGER.warning(
            "TELEMETRY_TOKEN_SECRET is not set; tracking credentials are signed "
            "with a per-process key and will not survive a restart."
        )
        app.state.telemetry_token_secret_is_ephemeral = True
    else:
        app.state.telemetry_token_secret_is_ephemeral = False
    app.state.telemetry_repository = build_telemetry_repository(
        runtime_settings.database_url, grant_secret=grant_secret
    )
    app.include_router(build_telemetry_router(runtime_settings.api_prefix))

    app.state.alert_repository = build_alert_repository(runtime_settings.database_url)
    app.include_router(build_alert_router(runtime_settings.api_prefix))

    app.state.data_health_repository = build_data_health_repository(
        runtime_settings.database_url
    )
    app.state.audit_repository = build_audit_repository(runtime_settings.database_url)
    app.state.push_repository = build_push_repository(
        runtime_settings.database_url,
        sender=push_sender,
        vapid_public_key=runtime_settings.vapid_public_key,
    )
    app.include_router(build_operations_router(runtime_settings.api_prefix))

    return app


app = create_app()
