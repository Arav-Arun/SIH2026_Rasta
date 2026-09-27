"""Authentication boundary for Supabase-issued bearer sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import ApiError

SUPABASE_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="SupabaseBearer",
    description=(
        "A Supabase-issued JWT verified against the project JWKS endpoint when "
        "SUPABASE_URL is configured."
    ),
)


@dataclass(frozen=True, slots=True)
class AuthenticatedSubject:
    """Identity emitted only after a cryptographically verified bearer token."""

    subject_id: str
    claims: dict[str, Any]


class BearerTokenVerifier(Protocol):
    """Verifies a compact bearer token and returns its authenticated subject."""

    async def verify(self, token: str) -> AuthenticatedSubject:
        """Verify issuer, signature, audience, expiry and subject."""


class DenyAllBearerTokenVerifier:
    """Safe temporary verifier used until Supabase JWT verification exists."""

    async def verify(self, token: str) -> AuthenticatedSubject:
        # Deliberately avoid logging or reflecting the token.
        del token
        raise ApiError(
            status_code=401,
            code="invalid_session",
            message="The session is invalid or could not be verified.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="A valid session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, token = authorization.partition(" ")
    if (
        scheme.casefold() != "bearer"
        or not separator
        or not token
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        raise ApiError(
            status_code=401,
            code="invalid_session",
            message="The session is invalid or could not be verified.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def require_authenticated_subject(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    documented_bearer: Annotated[
        HTTPAuthorizationCredentials | None, Security(SUPABASE_BEARER)
    ] = None,
) -> AuthenticatedSubject:
    """Require a verified bearer identity; currently all bearer tokens reject."""

    # ``HTTPBearer`` contributes the OpenAPI security scheme. Parsing the raw
    # header below lets this boundary consistently reject malformed schemes too.
    del documented_bearer
    token = _extract_bearer_token(authorization)
    verifier = request.app.state.bearer_token_verifier
    return await verifier.verify(token)
