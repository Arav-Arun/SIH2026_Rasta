"""Public API errors with a stable, non-leaking response envelope."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """A safe error payload exposed to API consumers."""

    code: str = Field(examples=["route_plan_stale"])
    message: str = Field(
        examples=["Road conditions changed; request a new route plan."]
    )
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(examples=["req_9d7fd10f6a8f4f9c98c4a260dd3fef82"])


class ErrorEnvelope(BaseModel):
    """Every API error response follows this envelope."""

    error: ErrorDetail


class ApiError(Exception):
    """Known client-safe application failure."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.headers = dict(headers or {})


def request_id_for(request: Request) -> str:
    """Return an existing request ID or create a safe local fallback."""

    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    request_id = f"req_{uuid4().hex}"
    request.state.request_id = request_id
    return request_id


def error_response(request: Request, error: ApiError) -> JSONResponse:
    """Serialize a known safe error with the current request ID."""

    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=error.code,
            message=error.message,
            details=error.details,
            request_id=request_id_for(request),
        )
    )
    response = JSONResponse(
        status_code=error.status_code,
        content=envelope.model_dump(mode="json"),
        headers=error.headers,
    )
    response.headers["X-Request-Id"] = request_id_for(request)
    return response
