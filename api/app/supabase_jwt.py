"""Supabase JWT verification using the project's JWKS endpoint."""

from __future__ import annotations

from typing import Any

import anyio
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.auth import AuthenticatedSubject, BearerTokenVerifier
from app.errors import ApiError

#: Supabase publishes asymmetric signing keys.
ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})


def _invalid_session() -> ApiError:
    return ApiError(
        status_code=401,
        code="invalid_session",
        message="The session is invalid or could not be verified.",
        headers={"WWW-Authenticate": "Bearer"},
    )


class SupabaseJwtVerifier:
    """Verify Supabase-issued bearer tokens against the project JWKS."""

    def __init__(
        self,
        *,
        supabase_url: str,
        jwt_audience: str = "authenticated",
    ) -> None:
        normalized = supabase_url.rstrip("/")
        self._issuer = f"{normalized}/auth/v1"
        self._audience = jwt_audience
        self._jwks_client = PyJWKClient(
            f"{self._issuer}/.well-known/jwks.json",
            cache_keys=True,
        )

    async def verify(self, token: str) -> AuthenticatedSubject:
        try:
            # The key set is fetched over the network when its cache expires;
            # a thread keeps that wait off the event loop serving everyone else.
            signing_key = await anyio.to_thread.run_sync(
                self._jwks_client.get_signing_key_from_jwt, token
            )
            algorithm = getattr(signing_key, "algorithm_name", None)
            if algorithm not in ASYMMETRIC_ALGORITHMS:
                raise _invalid_session()
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
        except PyJWKClientConnectionError:
            # The signing keys could not be fetched.
            raise ApiError(
                status_code=503,
                code="auth_backend_unavailable",
                message="Sessions cannot be verified right now. Try again shortly.",
            ) from None
        except PyJWKClientError:
            # No published key matches the token's `kid`, or it has none: a
            # token this project never signed. Not a server fault, so not a 500.
            raise _invalid_session() from None
        except jwt.InvalidTokenError:
            raise _invalid_session() from None

        subject_id = str(claims.get("sub", "")).strip()
        if not subject_id:
            raise _invalid_session()

        return AuthenticatedSubject(subject_id=subject_id, claims=claims)


def build_bearer_token_verifier(
    *,
    supabase_url: str | None,
) -> BearerTokenVerifier:
    """Return a configured verifier or the safe deny-all fallback."""

    if not supabase_url:
        from app.auth import DenyAllBearerTokenVerifier

        return DenyAllBearerTokenVerifier()
    return SupabaseJwtVerifier(supabase_url=supabase_url)
