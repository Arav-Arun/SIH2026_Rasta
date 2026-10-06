"""T029: which endpoints the server may send to, and what one attempt does.

The sender is exercised against a real HTTP server on loopback standing in for
a push service, so the VAPID header is verified the way a push service would
verify it: by checking the signature with the public key.
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from app.push import (
    PushMessage,
    UnconfiguredSender,
    WebPushSender,
    build_sender,
    endpoint_allowed,
    parse_extra_hosts,
    send_one,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

MESSAGE = PushMessage(
    alert_id="a1",
    title_key="alert.road_closed",
    severity="critical",
    deep_link="/alerts",
)


def test_only_real_push_services_are_accepted_as_endpoints() -> None:
    allowed = [
        "https://fcm.googleapis.com/fcm/send/abc123",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://web.push.apple.com/QGf-abc",
    ]
    refused = [
        "http://fcm.googleapis.com/fcm/send/abc",  # not HTTPS
        "https://fcm.googleapis.com:8443/fcm/send/abc",  # not the default port
        "https://fcm.googleapis.com.attacker.test/x",  # look-alike suffix
        "https://evilfcm.googleapis.com/x",  # not a subdomain of the service
        "https://user:pass@fcm.googleapis.com/x",  # credentials in the URL
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:5432/",  # something on the server itself
        "https://localhost/push",
        "not a url at all",
    ]
    for endpoint in allowed:
        assert endpoint_allowed("web", endpoint), endpoint
    for endpoint in refused:
        assert not endpoint_allowed("web", endpoint), endpoint


def test_a_configured_stub_host_is_the_only_extra_one_accepted() -> None:
    extra = parse_extra_hosts(" 127.0.0.1 , ")
    assert extra == ("127.0.0.1",)
    assert endpoint_allowed("web", "http://127.0.0.1:9999/push/1", extra)
    assert not endpoint_allowed("web", "http://127.0.0.2:9999/push/1", extra)
    assert not endpoint_allowed("web", "http://127.0.0.1:9999/push/1")


def test_an_android_registration_is_a_token_never_a_url() -> None:
    assert endpoint_allowed("android", "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]")
    assert endpoint_allowed("android", "dGhpcyBpcyBhIHRva2Vu:APA91bHabc_def-123")
    assert not endpoint_allowed("android", "http://169.254.169.254/latest/meta-data/")
    assert not endpoint_allowed("android", "short")


class _Recorder:
    def __init__(self) -> None:
        self.calls = 0

    name = "recorder"
    extra_hosts: tuple[str, ...] = ()

    def configured(self) -> bool:
        return True

    def send(self, *, endpoint, keys, message):  # noqa: ANN001
        self.calls += 1
        return "sent", None, 201


def test_a_refused_endpoint_is_never_contacted() -> None:
    sender = _Recorder()
    outcome = send_one(
        sender,
        {"platform": "web", "endpoint": "http://10.0.0.5/admin", "keys": {}},
        MESSAGE,
    )
    assert outcome == ("failed", "endpoint_not_allowed", None)
    assert sender.calls == 0


def test_an_android_registration_is_kept_but_not_sent_without_a_sender() -> None:
    sender = _Recorder()
    outcome = send_one(
        sender,
        {
            "platform": "android",
            "endpoint": "ExponentPushToken[abcdefghijklmnopqrst]",
            "keys": {},
        },
        MESSAGE,
    )
    assert outcome == ("skipped", "no_android_sender", None)
    assert sender.calls == 0


def test_without_keys_the_sender_is_the_honest_unconfigured_one() -> None:
    sender = build_sender(vapid_private_key="", vapid_public_key="", vapid_subject="")
    assert isinstance(sender, UnconfiguredSender)
    assert not sender.configured()


# --- against a stub push service -------------------------------------------


def _vapid_keys() -> tuple[str, str, ec.EllipticCurvePublicKey]:
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return pem, public, private.public_key()


class _StubPushService:
    """A push service that records what it received and answers `status`."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.requests: list[dict[str, object]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's naming
                length = int(self.headers.get("Content-Length") or 0)
                stub.requests.append(
                    {
                        "path": self.path,
                        # Header names are case-insensitive; urllib sends "Ttl".
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length),
                    }
                )
                self.send_response(stub.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _StubPushService:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_a_push_carries_a_vapid_signature_the_push_service_can_verify() -> None:
    pem, public, public_key = _vapid_keys()
    with _StubPushService(201) as service:
        sender = WebPushSender(pem, public, "mailto:ops@example.test", ("127.0.0.1",))
        outcome = send_one(
            sender,
            {"platform": "web", "endpoint": f"{service.url}/push/abc", "keys": {}},
            MESSAGE,
        )
    assert outcome == ("sent", None, 201)
    [received] = service.requests
    headers = received["headers"]
    assert received["path"] == "/push/abc"
    # Headers only: no unencrypted body crosses the push service.
    assert received["body"] == b""
    assert headers["ttl"] == "900"
    assert headers["urgency"] == "high"
    scheme, _, rest = headers["authorization"].partition(" ")
    assert scheme == "vapid"
    parts = dict(part.strip().split("=", 1) for part in rest.split(","))
    assert parts["k"] == public
    claims = jwt.decode(
        parts["t"], public_key, algorithms=["ES256"], audience=service.url
    )
    assert claims["sub"] == "mailto:ops@example.test"


def test_a_discarded_subscription_is_reported_as_gone() -> None:
    pem, public, _ = _vapid_keys()
    with _StubPushService(410) as service:
        sender = WebPushSender(pem, public, "mailto:ops@example.test", ("127.0.0.1",))
        outcome = send_one(
            sender,
            {"platform": "web", "endpoint": f"{service.url}/push/x", "keys": {}},
            MESSAGE,
        )
    assert outcome == ("failed", "subscription_gone", 410)


def test_a_push_service_error_is_a_failed_attempt_and_nothing_more() -> None:
    pem, public, _ = _vapid_keys()
    with _StubPushService(500) as service:
        sender = WebPushSender(pem, public, "mailto:ops@example.test", ("127.0.0.1",))
        outcome = send_one(
            sender,
            {"platform": "web", "endpoint": f"{service.url}/push/x", "keys": {}},
            MESSAGE,
        )
    assert outcome == ("failed", "http_500", 500)


def test_an_unreachable_push_service_is_a_failed_attempt() -> None:
    pem, public, _ = _vapid_keys()
    sender = WebPushSender(pem, public, "mailto:ops@example.test", ("127.0.0.1",))
    # Port 9 on loopback: nothing listens, so the connection is refused.
    status, code, http = send_one(
        sender,
        {"platform": "web", "endpoint": "http://127.0.0.1:9/push/x", "keys": {}},
        MESSAGE,
    )
    assert (status, http) == ("failed", None)
    assert code is not None and code.startswith("send_failed_")
