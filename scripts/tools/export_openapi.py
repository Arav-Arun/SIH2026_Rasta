#!/usr/bin/env python3
"""Export the API OpenAPI document with deterministic local settings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPOSITORY_ROOT / "api"


def build_openapi_document() -> dict[str, Any]:
    """Return the schema from a stable, credential-free API configuration."""

    sys.path.insert(0, str(API_ROOT))

    from app.config import Settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

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
    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "contracts" / "openapi.json",
        help="Path for the deterministic OpenAPI JSON document.",
    )
    args = parser.parse_args()

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_openapi_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
