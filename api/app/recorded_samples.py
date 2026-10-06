"""Copies of the recorded IMD and SACHET documents, re-dated to the moment of a run.

Until live feeds are configured, the API reads these recorded samples. They are
re-dated at every start so the risk engine never treats them as stale:

    PYTHONPATH=api python -m app.recorded_samples /tmp/rasta-sources
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECORDED = REPOSITORY_ROOT / "data" / "fixtures" / "sources"

#: How long before `now` the newest re-dated document says it was issued.
ISSUED_BEFORE_NOW = timedelta(minutes=30)

_CAP_TIME = re.compile(r"<(sent|effective|onset|expires)>([^<]+)</\1>")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _format_like(original: str, moment: datetime) -> str:
    """The shifted time, written in the original's own offset and style."""

    parsed = _parse(original)
    local = moment.astimezone(parsed.tzinfo)
    text = local.replace(microsecond=0).isoformat()
    return text.replace("+00:00", "Z") if original.strip().endswith("Z") else text


def redate_recorded_sources(
    target: Path, *, now: datetime | None = None, source: Path = RECORDED
) -> dict[str, Any]:
    """Write re-dated copies of every recorded document into `target`."""

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    rainfall_path = source / "imd_rainfall.json"
    cap_path = source / "sachet_cap.xml"
    rainfall = json.loads(rainfall_path.read_text(encoding="utf-8"))
    cap = cap_path.read_text(encoding="utf-8")
    cap_sent = re.search(r"<sent>([^<]+)</sent>", cap)
    if cap_sent is None:
        raise ValueError(f"{cap_path} has no <sent> time")

    issued = {
        "imd_rainfall.json": _parse(rainfall["issued_at"]),
        "sachet_cap.xml": _parse(cap_sent.group(1)),
    }
    shift = (moment - ISSUED_BEFORE_NOW) - max(issued.values())
    stamp = moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    label = (
        f"Re-dated at {stamp}: every time shifted by the same amount, values unchanged."
    )

    target.mkdir(parents=True, exist_ok=True)
    for key in ("issued_at", "valid_until"):
        if rainfall.get(key):
            rainfall[key] = _format_like(rainfall[key], _parse(rainfall[key]) + shift)
    rainfall["_note"] = f"{rainfall.get('_note', '').strip()} {label}".strip()
    (target / "imd_rainfall.json").write_text(
        json.dumps(rainfall, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    shifted_cap = _CAP_TIME.sub(
        lambda m: (
            f"<{m.group(1)}>{_format_like(m.group(2), _parse(m.group(2)) + shift)}</{m.group(1)}>"
        ),
        cap,
    )
    shifted_cap = shifted_cap.replace(
        "RECORDED FIXTURE, NOT LIVE DATA.",
        f"RECORDED FIXTURE, NOT LIVE DATA. {label}",
        1,
    )
    (target / "sachet_cap.xml").write_text(shifted_cap, encoding="utf-8")

    # Anything else (the README) is copied as it is.
    for path in source.iterdir():
        if path.is_file() and path.name not in issued:
            shutil.copy2(path, target / path.name)

    return {
        "directory": str(target),
        "redated_at": stamp,
        "shift_hours": round(shift.total_seconds() / 3600, 2),
        "issued": {
            name: {
                "recorded": when.isoformat(),
                "redated": (when + shift).astimezone(UTC).isoformat(),
            }
            for name, when in issued.items()
        },
    }


def main(argv: list[str]) -> int:
    """Write the re-dated copies into the directory named by the one argument."""

    if len(argv) != 1 or not argv[0].strip():
        print("usage: python -m app.recorded_samples <empty or new directory>")
        return 2
    target = Path(argv[0]).expanduser().resolve()
    # The copies include a README.md, so writing them into a working tree would
    # overwrite that tree's own README. Only a directory of their own will do.
    if (
        target == Path.cwd().resolve()
        or (target / ".git").exists()
        or target == RECORDED
    ):
        print(f"refusing to write recorded samples into {target}")
        return 2
    print(redate_recorded_sources(target)["redated_at"])
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
