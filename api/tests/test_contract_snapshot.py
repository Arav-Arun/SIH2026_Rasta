"""The committed OpenAPI snapshot is what the API actually serves."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

SNAPSHOT = next(
    parent / "contracts" / "openapi.json"
    for parent in Path(__file__).resolve().parents
    if (parent / "contracts" / "openapi.json").is_file()
)


def test_the_committed_openapi_snapshot_is_current() -> None:
    app = create_app(
        Settings(
            app_name="RASTA API",
            app_version="0.1.0",
            app_mode="local_demo",
            api_prefix="/v1",
            allowed_origins=("http://localhost:3000",),
            request_id_header="X-Request-Id",
        )
    )
    served = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    assert SNAPSHOT.read_text(encoding="utf-8") == served, (
        "contracts/openapi.json is out of date. Regenerate it, and "
        "contracts/src/openapi.d.ts with it, after changing an endpoint or schema."
    )
