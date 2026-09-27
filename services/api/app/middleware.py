"""Transport middleware shared by every HTTP endpoint."""

from __future__ import annotations

import logging
import re
import sys
import time
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.errors import ApiError, error_response

_LOGGER = logging.getLogger("rasta.api")

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach one safe correlation ID to success and error responses, and log it."""

    def __init__(self, app: ASGIApp, *, header_name: str = "X-Request-Id") -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        supplied = request.headers.get(self.header_name)
        request_id = (
            supplied
            if supplied is not None and _SAFE_REQUEST_ID.fullmatch(supplied)
            else f"req_{uuid4().hex}"
        )
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Last-resort guard.
            _LOGGER.exception("Unhandled error for request_id=%s", request_id)
            response = error_response(
                request,
                ApiError(
                    status_code=500,
                    code="internal_error",
                    message="The server could not complete the request.",
                ),
            )
        response.headers[self.header_name] = request_id
        _LOGGER.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%d",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            round((time.perf_counter() - started) * 1000),
        )
        return response


class SecurityHeadersMiddleware:
    """Response headers a JSON API should always send."""

    #: The interactive API docs load their own scripts and styles; they keep
    #: the other headers but not the lock-everything-down CSP.
    DOC_PATHS = ("/docs", "/redoc", "/openapi.json")

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_with_headers(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}

                def add(name: str, value: str) -> None:
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))

                add("x-content-type-options", "nosniff")
                add("referrer-policy", "no-referrer")
                add("x-frame-options", "DENY")
                if not path.startswith(self.DOC_PATHS):
                    add(
                        "content-security-policy",
                        "default-src 'none'; frame-ancestors 'none'",
                    )
                if path.startswith("/v1/"):
                    # Scoped, authenticated answers: never kept by a shared cache
                    # or by the browser for the next person on a shared device.
                    add("cache-control", "no-store")
                if self.hsts:
                    add(
                        "strict-transport-security",
                        "max-age=31536000; includeSubDomains",
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuses request bodies over a fixed size before they are read."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            # The server holds a client to its declared length, so checking the
            # declaration is enough.
            if declared.isdigit() and int(declared) > self.max_bytes:
                await self._refuse(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        # No declared length (chunked): read it here, counting, and hand the app a body
        # already known to fit.
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await self._refuse(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():  # type: ignore[no-untyped-def]
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _refuse(self, scope, receive, send):  # type: ignore[no-untyped-def]
        request = Request(scope, receive)
        request.state.request_id = f"req_{uuid4().hex}"
        response = error_response(
            request,
            ApiError(413, "payload_too_large", "The request payload is too large."),
        )
        await response(scope, receive, send)


class RedactQueryStringFilter(logging.Filter):
    """Keeps query strings out of the server's access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query:
                record.args = (*args[:2], f"{path}?[redacted]", *args[3:])
        return True


def install_access_log_redaction() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(existing, RedactQueryStringFilter) for existing in access.filters
    ):
        access.addFilter(RedactQueryStringFilter())


def install_request_logging() -> None:
    """Gives the service's own log lines somewhere to go."""

    if not _LOGGER.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(levelname)s:     %(name)s %(message)s")
        )
        _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
