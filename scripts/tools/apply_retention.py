#!/usr/bin/env python3
"""Apply the pilot's data-retention policy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "retention_run.json"


def database_url() -> str:
    """DATABASE_URL from the environment, else from the API's own settings."""

    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    sys.path.insert(0, str(REPOSITORY_ROOT / "api"))
    from app.config import Settings  # noqa: PLC0415

    return Settings().database_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="count what would go; delete nothing"
    )
    parser.add_argument(
        "--telemetry-days",
        type=int,
        default=int(os.environ.get("TELEMETRY_RETENTION_DAYS", "30")),
    )
    parser.add_argument(
        "--push-attempt-days",
        type=int,
        default=int(os.environ.get("PUSH_ATTEMPT_RETENTION_DAYS", "90")),
    )
    args = parser.parse_args(argv)

    url = database_url()
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    import psycopg  # noqa: PLC0415 - the API's own driver

    with psycopg.connect(url) as connection, connection.transaction():
        rows = connection.execute(
            "select category, rows_affected from public.apply_retention(%s, %s, %s)",
            (args.telemetry_days, args.push_attempt_days, args.dry_run),
        ).fetchall()

    counts = {category: int(count) for category, count in rows}
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": args.dry_run,
        "telemetry_retention_days": args.telemetry_days,
        "push_attempt_retention_days": args.push_attempt_days,
        "counts": counts,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    verb = "would remove" if args.dry_run else "removed"
    for category, count in counts.items():
        print(f"{verb} {count:>7} {category}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
