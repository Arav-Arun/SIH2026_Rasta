#!/usr/bin/env python3
"""Look for credentials in the repository before anything is shared."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{24,}\b")),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Supabase secret key", re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{10,}\b")),
    # Public by design, but key values stay out of source: they belong
    # in an environment file, and a committed one pins the app to one project.
    ("Supabase publishable key", re.compile(r"\bsb_publishable_[A-Za-z0-9_\-]{10,}\b")),
    (
        "JSON web token",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
        ),
    ),
    (
        "hosted Supabase project URL",
        re.compile(r"https://[a-z0-9]{20}\.supabase\.co\b"),
    ),
    (
        # A quoted value given to anything named like a credential: an
        # assignment, a JSON key, or the fallback in `process.env.X_API_KEY ||
        # '...'` (which is how a Carto key reached history unnoticed).
        "credential assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|access[_-]?token|auth[_-]?token|password|passwd)"
            r"[A-Za-z0-9_]*['\"]?\s*(?:[:=]|\|\||\?\?)\s*['\"]([^'\"\s]{16,})['\"]"
        ),
    ),
)

#: Values that look like credentials but are placeholders or public test data.
ALLOWED_VALUE = re.compile(
    r"(?i)(example|placeholder|changeme|dummy|not[-_]?real|not[-_]?configured|fake|test[-_]|your[-_]|<|\$\{|^env\(|x{6,}|\*{4,})"
)

#: Patterns specific enough that any match is reported.
GENERIC_KINDS = {"credential assignment"}


def looks_random(value: str) -> bool:
    """True for a value with the character mix and entropy of a real key."""

    classes = sum(
        bool(re.search(pattern, value)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]")
    )
    counts = Counter(value)
    entropy = -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())
    return classes >= 2 and entropy >= 3.5


SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp4",
    ".pbf",
    ".mbtiles",
    ".joblib",
    ".pkl",
}
SKIP_NAMES = {"package-lock.json"}
#: This file names the patterns, and its tests exercise them.
SKIP_PATHS = {"scripts/tools/scan_secrets.py"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    preview: str


def redact(value: str) -> str:
    return value[:4] + "…" + f"({len(value)} chars)"


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1) if match.groups() else match.group(0)
                if ALLOWED_VALUE.search(value):
                    continue
                if kind in GENERIC_KINDS and not looks_random(value):
                    continue
                findings.append(Finding(path, number, kind, redact(value)))
    return findings


def candidate_files() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPOSITORY_ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    return sorted({name.decode() for name in tracked + untracked if name})


def committed_env_files(files: list[str]) -> list[Finding]:
    """An environment file other than an example must never be committed."""

    return [
        Finding(path, 0, "environment file", "whole file")
        for path in files
        if re.search(r"(^|/)\.env(\.[^/]*)?$", path) and not path.endswith(".example")
    ]


def scan_working_tree() -> list[Finding]:
    files = candidate_files()
    findings = committed_env_files(files)
    for path in files:
        full = REPOSITORY_ROOT / path
        if (
            path in SKIP_PATHS
            or full.name in SKIP_NAMES
            or full.suffix.lower() in SKIP_SUFFIXES
            or not full.is_file()
        ):
            continue
        data = full.read_bytes()
        if b"\0" in data[:4096]:
            continue
        findings.extend(scan_text(path, data.decode("utf-8", "replace")))
    return findings


def scan_history() -> list[Finding]:
    log = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color", "--format=commit %h"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8", "replace")
    findings: list[Finding] = []
    commit = "?"
    current = "?"
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if current in SKIP_PATHS or Path(current).name in SKIP_NAMES:
                continue
            for finding in scan_text(current, line[1:]):
                findings.append(
                    Finding(f"{commit}:{current}", 0, finding.kind, finding.preview)
                )
    # One line per distinct secret, not per commit that carried it forward.
    unique: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        unique.setdefault((finding.kind, finding.preview), finding)
    return list(unique.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--history", action="store_true", help="also report secrets in git history"
    )
    args = parser.parse_args(argv)

    findings = scan_working_tree()
    for finding in findings:
        print(f"FOUND {finding.kind}: {finding.path}:{finding.line} {finding.preview}")
    print(f"Working tree: {len(findings)} finding(s).")

    if args.history:
        past = scan_history()
        for finding in past:
            print(f"HISTORY {finding.kind}: {finding.path} {finding.preview}")
        print(
            f"History: {len(past)} distinct value(s). Anything real here must be rotated "
            "at its provider; removing it from history does not make it secret again."
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
