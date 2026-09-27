from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import ValidationError


def test_settings_parse_explicit_comma_separated_origins() -> None:
    settings = Settings(
        allowed_origins="http://localhost:3000, https://console.example.test/"
    )

    assert settings.allowed_origins == (
        "http://localhost:3000",
        "https://console.example.test",
    )


def test_settings_read_comma_separated_origins_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,https://console.example.test"
    )

    assert Settings().allowed_origins == (
        "http://localhost:3000",
        "https://console.example.test",
    )


@pytest.mark.parametrize("origins", ["*", "", "https://example.test/path"])
def test_settings_reject_unsafe_or_ambiguous_origins(origins: str) -> None:
    with pytest.raises(ValidationError):
        Settings(allowed_origins=origins)
