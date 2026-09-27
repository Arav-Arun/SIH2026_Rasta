#!/usr/bin/env python3
"""Walk the six-minute demo story in a browser, from a fresh demo."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"


def main() -> int:
    python = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
    env = {**os.environ}
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    up = subprocess.run(
        [python, "scripts/local_demo.py", "up"],
        cwd=REPOSITORY_ROOT,
        env=env,
        check=False,
    )
    if up.returncode != 0:
        print("The demo did not come up; the story was not run.", file=sys.stderr)
        subprocess.run(
            [python, "scripts/local_demo.py", "down"], cwd=REPOSITORY_ROOT, env=env
        )
        return 1
    try:
        story = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "demo-story.spec.ts",
            ],
            cwd=REPOSITORY_ROOT,
            env={
                **env,
                "E2E_BASE_URL": "http://127.0.0.1:3000",
                "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8000",
            },
            check=False,
        )
    finally:
        subprocess.run(
            [python, "scripts/local_demo.py", "down"], cwd=REPOSITORY_ROOT, env=env
        )
    print(
        "PASSED" if story.returncode == 0 else "FAILED",
        "→ artifacts/reports/demo_story.json",
    )
    return story.returncode


if __name__ == "__main__":
    raise SystemExit(main())
