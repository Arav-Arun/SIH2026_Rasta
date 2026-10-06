from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.auth import AuthenticatedSubject
from app.config import Settings
from app.identity import IdentityRecord, build_me_response
from app.main import create_app
from app.schemas import OrganizationIdentity, ProfileIdentity, RoleGrantIdentity
from fastapi.testclient import TestClient


class FakeBearerTokenVerifier:
    async def verify(self, token: str) -> AuthenticatedSubject:
        if token != "valid-token":
            from app.errors import ApiError

            raise ApiError(
                status_code=401,
                code="invalid_session",
                message="The session is invalid or could not be verified.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return AuthenticatedSubject(
            subject_id="11111111-1111-4111-8111-111111111111",
            claims={"sub": "11111111-1111-4111-8111-111111111111"},
        )


class FakeIdentityRepository:
    async def load_identity(self, user_id: str) -> IdentityRecord | None:
        if user_id != "11111111-1111-4111-8111-111111111111":
            return None
        return IdentityRecord(
            profile=ProfileIdentity(
                id="22222222-2222-4222-8222-222222222222",
                user_id=user_id,
                display_name="Synthetic Dispatcher",
                locale="en",
                active=True,
            ),
            organization=OrganizationIdentity(
                id="a2600002-0000-4000-8000-000000000001",
                name="RASTA synthetic local demonstration",
                mode="local_demo",
            ),
            roles=[
                RoleGrantIdentity(
                    id="33333333-3333-4333-8333-333333333333",
                    role="district_dispatcher",
                    district_id="a2600002-0000-4000-8000-000000000002",
                    valid_from=datetime(2000, 1, 1, tzinfo=UTC),
                    valid_to=None,
                )
            ],
        )


def test_build_me_response_includes_role_capabilities() -> None:
    response = build_me_response(
        IdentityRecord(
            profile=ProfileIdentity(
                id="profile",
                user_id="user",
                display_name="Test User",
                locale="en",
                active=True,
            ),
            organization=OrganizationIdentity(
                id="org",
                name="Org",
                mode="local_demo",
            ),
            roles=[
                RoleGrantIdentity(
                    id="grant",
                    role="district_dispatcher",
                    district_id="district",
                    valid_from=datetime(2000, 1, 1, tzinfo=UTC),
                    valid_to=None,
                )
            ],
        )
    )

    assert "route:plan" in response.capabilities
    assert response.profile.display_name == "Test User"


def test_me_returns_verified_identity_when_repository_is_configured() -> None:
    app = create_app(
        Settings(allowed_origins=("http://localhost:3000",)),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/me",
            headers={"Authorization": "Bearer valid-token"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["profile"]["display_name"] == "Synthetic Dispatcher"
    assert payload["roles"][0]["role"] == "district_dispatcher"
    assert "route:plan" in payload["capabilities"]


def test_me_returns_503_when_identity_repository_is_not_configured() -> None:
    # Explicitly unconfigured, regardless of any local ``.env`` on the machine.
    app = create_app(
        Settings(allowed_origins=("http://localhost:3000",), database_url=""),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=None,
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/me",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "identity_bootstrap_unavailable"


def test_unreachable_jwks_reports_service_unavailable_not_invalid_session() -> None:
    """A token we cannot check is not the same as a token we checked and rejected.

    When the JWKS endpoint is unreachable the caller's session may be entirely
    valid, so answering 401 would push them through a pointless re-login while
    the real fault is on our side.
    """

    from app.errors import ApiError
    from app.supabase_jwt import SupabaseJwtVerifier
    from jwt.exceptions import PyJWKClientConnectionError

    verifier = SupabaseJwtVerifier(supabase_url="http://127.0.0.1:54321")

    def _raise(_token: str) -> None:
        raise PyJWKClientConnectionError("network down")

    verifier._jwks_client.get_signing_key_from_jwt = _raise  # type: ignore[assignment]

    with pytest.raises(ApiError) as excinfo:
        asyncio.run(verifier.verify("any.token.value"))

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "auth_backend_unavailable"


def _verifier_with_key(signing_key: object):
    from app.supabase_jwt import SupabaseJwtVerifier

    verifier = SupabaseJwtVerifier(supabase_url="http://127.0.0.1:54321")
    verifier._jwks_client.get_signing_key_from_jwt = lambda _token: signing_key  # type: ignore[assignment]
    return verifier


def _es256_pair():
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt import PyJWK
    from jwt.algorithms import ECAlgorithm

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return private_key, PyJWK({**public_jwk, "alg": "ES256", "kid": "test-key"})


def _claims(**over: object) -> dict[str, object]:
    import time

    return {
        "sub": "7d4f2c1e-0000-4000-8000-000000000001",
        "aud": "authenticated",
        "iss": "http://127.0.0.1:54321/auth/v1",
        "exp": int(time.time()) + 300,
        **over,
    }


def test_a_token_signed_by_the_published_key_is_accepted() -> None:
    import jwt

    private_key, public = _es256_pair()
    token = jwt.encode(
        _claims(), private_key, algorithm="ES256", headers={"kid": "test-key"}
    )
    subject = asyncio.run(_verifier_with_key(public).verify(token))
    assert subject.subject_id == "7d4f2c1e-0000-4000-8000-000000000001"


def test_a_token_whose_key_id_is_not_published_is_refused_not_a_server_error() -> None:
    from app.errors import ApiError
    from app.supabase_jwt import SupabaseJwtVerifier
    from jwt.exceptions import PyJWKClientError

    verifier = SupabaseJwtVerifier(supabase_url="http://127.0.0.1:54321")

    def _raise(_token: str) -> None:
        raise PyJWKClientError("Unable to find a signing key that matches: 'forged'")

    verifier._jwks_client.get_signing_key_from_jwt = _raise  # type: ignore[assignment]
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(verifier.verify("forged.token.value"))
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "invalid_session"


def test_an_hs256_token_keyed_with_the_public_key_is_refused() -> None:
    """The algorithm-confusion forgery: HMAC over the published public key."""

    import hashlib
    import hmac
    import json
    from base64 import urlsafe_b64encode

    from app.errors import ApiError
    from cryptography.hazmat.primitives import serialization

    _private, public = _es256_pair()
    secret = public.key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(data: bytes) -> str:
        return urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = (
        b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "test-key"}).encode())
        + "."
        + b64(json.dumps(_claims()).encode())
    )
    signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(
            _verifier_with_key(public).verify(f"{signing_input}.{b64(signature)}")
        )
    assert excinfo.value.status_code == 401


def test_a_symmetric_published_key_is_never_used() -> None:
    from app.errors import ApiError
    from jwt import PyJWK

    symmetric = PyJWK(
        {"kty": "oct", "k": "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0", "alg": "HS256"}
    )
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(_verifier_with_key(symmetric).verify("a.b.c"))
    assert excinfo.value.status_code == 401
