#!/usr/bin/env python3
"""Draft the app's language catalogues from English with Sarvam Translate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPOSITORY_ROOT / "apps" / "client" / "i18n"
MACHINE_DIR = I18N_DIR / "machine"
REGISTRY_PATH = I18N_DIR / "languages.json"
INDEX_PATH = I18N_DIR / "index.ts"
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "i18n_translation_run.json"

DEFAULT_BASE_URL = "https://api.sarvam.ai"
DEFAULT_MODEL = "sarvam-translate:v1"
SOURCE_LANGUAGE = "en-IN"
#: Sarvam Translate accepts up to 2,000 characters per request; our longest
#: string is a few hundred, so this is a guard, not a limit anyone should meet.
MAX_INPUT_CHARS = 1900

#: Never translated: the product's name is a name.
DO_NOT_TRANSLATE = {"app.name"}

#: A catalogue with more of its strings still in English than this is kept on
#: disk, so the next run resumes it, but is not offered in the app.
MAX_FALLBACK_SHARE = 0.05

# ---------------------------------------------------------------------------
# Message structure
# ---------------------------------------------------------------------------


@dataclass
class Text:
    value: str


@dataclass
class Placeholder:
    name: str


@dataclass
class Pound:
    """`#` inside a plural branch: the count, formatted by the client."""


@dataclass
class Plural:
    name: str
    kind: str  # plural | select | selectordinal
    branches: list[tuple[str, list[Node]]] = field(default_factory=list)


Node = Text | Placeholder | Pound | Plural


class MessageSyntaxError(ValueError):
    pass


def _matching_brace(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise MessageSyntaxError(f"unbalanced braces in {text!r}")


def _split_top_level(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "," and depth == 0 and len(parts) < limit:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return parts


def parse_message(text: str, *, in_plural: bool = False) -> list[Node]:
    """Parse the ICU subset the client formatter understands."""

    nodes: list[Node] = []
    buffer = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            if buffer:
                nodes.append(Text(buffer))
                buffer = ""
            close = _matching_brace(text, index)
            inner = text[index + 1 : close]
            parts = _split_top_level(inner, 2)
            name = parts[0].strip()
            if len(parts) >= 3 and parts[1].strip() in (
                "plural",
                "select",
                "selectordinal",
            ):
                nodes.append(_parse_branches(name, parts[1].strip(), parts[2]))
            elif len(parts) == 1 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                nodes.append(Placeholder(name))
            else:
                raise MessageSyntaxError(f"unsupported argument {{{inner}}}")
            index = close + 1
            continue
        if char == "}":
            raise MessageSyntaxError(f"stray closing brace in {text!r}")
        if char == "#" and in_plural:
            if buffer:
                nodes.append(Text(buffer))
                buffer = ""
            nodes.append(Pound())
            index += 1
            continue
        buffer += char
        index += 1
    if buffer:
        nodes.append(Text(buffer))
    return nodes


def _parse_branches(name: str, kind: str, body: str) -> Plural:
    plural = Plural(name=name, kind=kind)
    index = 0
    while index < len(body):
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            break
        start = index
        while index < len(body) and body[index] != "{" and not body[index].isspace():
            index += 1
        selector = body[start:index]
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body) or body[index] != "{" or not selector:
            raise MessageSyntaxError(f"malformed branch list {body!r}")
        close = _matching_brace(body, index)
        plural.branches.append(
            (
                selector,
                parse_message(body[index + 1 : close], in_plural=kind != "select"),
            )
        )
        index = close + 1
    if not plural.branches:
        raise MessageSyntaxError(f"no branches in {body!r}")
    return plural


def render_message(nodes: list[Node]) -> str:
    out = ""
    for node in nodes:
        if isinstance(node, Text):
            out += node.value
        elif isinstance(node, Placeholder):
            out += "{" + node.name + "}"
        elif isinstance(node, Pound):
            out += "#"
        else:
            branches = " ".join(
                f"{selector} {{{render_message(branch)}}}"
                for selector, branch in node.branches
            )
            out += f"{{{node.name}, {node.kind}, {branches}}}"
    return out


def signature(nodes: list[Node]) -> list[Any]:
    """The structure a translation must keep: every argument and branch, in any order."""

    items: list[Any] = []
    for node in nodes:
        if isinstance(node, Placeholder):
            items.append(("ph", node.name))
        elif isinstance(node, Pound):
            items.append(("#",))
        elif isinstance(node, Plural):
            items.append(
                (
                    "plural",
                    node.name,
                    node.kind,
                    tuple(
                        (selector, tuple(sorted(map(repr, signature(branch)))))
                        for selector, branch in node.branches
                    ),
                )
            )
    return sorted(items, key=repr)


# ---------------------------------------------------------------------------
# Units: the pieces of text actually sent to the translator
# ---------------------------------------------------------------------------

TOKEN_STYLES: tuple[tuple[str, str], ...] = (("{", "}"), ("<x", "/>"))


@dataclass
class Unit:
    """One run of text with its arguments replaced by numbered tokens."""

    text: str
    tokens: list[Node]  # what each numbered token stands for

    def masked(self, style: int = 0) -> str:
        left, right = TOKEN_STYLES[style]
        out = self.text
        for number in range(len(self.tokens)):
            out = out.replace(f"\u0000{number}\u0000", f"{left}{number}{right}")
        return out

    def is_translatable(self) -> bool:
        visible = re.sub(r"\u0000\d+\u0000", "", self.text)
        return bool(re.search(r"[A-Za-z]", visible))


def build_unit(nodes: list[Node], nested: list[Plural]) -> Unit:
    text = ""
    tokens: list[Node] = []
    for node in nodes:
        if isinstance(node, Text):
            text += node.value
            continue
        tokens.append(node)
        if isinstance(node, Plural):
            nested.append(node)
        text += f"\u0000{len(tokens) - 1}\u0000"
    return Unit(text=text, tokens=tokens)


def latin_digits(text: str) -> str:
    """Every decimal digit as ASCII: the app writes numbers in Latin digits in
    every language, and a token like `{०}` must still be recognised as `{0}`."""

    return "".join(
        str(unicodedata.decimal(char))
        if char.isdecimal() and not char.isascii()
        else char
        for char in text
    )


def restore_unit(unit: Unit, translated: str, style: int = 0) -> list[Node] | None:
    """Put the arguments back, or None if the translation did not keep them all."""

    translated = latin_digits(translated)
    left, right = (re.escape(part) for part in TOKEN_STYLES[style])
    pattern = re.compile(left + r"\s*(\d+)\s*" + right)
    found = [int(match.group(1)) for match in pattern.finditer(translated)]
    if sorted(found) != list(range(len(unit.tokens))):
        return None
    remainder = pattern.sub("", translated)
    # Braces or a '#' the translator introduced would change the message's
    # meaning: the client reads '#' inside a plural branch as the count.
    if "{" in remainder or "}" in remainder:
        return None
    if "#" in remainder and "#" not in unit.text:
        return None
    nodes: list[Node] = []
    position = 0
    for match in pattern.finditer(translated):
        if match.start() > position:
            nodes.append(Text(translated[position : match.start()]))
        nodes.append(unit.tokens[int(match.group(1))])
        position = match.end()
    if position < len(translated):
        nodes.append(Text(translated[position:]))
    return nodes


Translator = Callable[[str], str]


class TranslationRejected(Exception):
    """The translator's answer could not be used safely."""


def translate_nodes(nodes: list[Node], translator: Translator) -> list[Node]:
    """Translate a parsed message, keeping every argument and plural branch."""

    nested: list[Plural] = []
    unit = build_unit(nodes, nested)
    for plural in nested:
        plural.branches = [
            (selector, translate_nodes(branch, translator))
            for selector, branch in plural.branches
        ]
    if not unit.is_translatable():
        return [
            unit.tokens[int(part[1:-1])]
            if re.fullmatch(r"\u0000\d+\u0000", part)
            else Text(part)
            for part in re.split(r"(\u0000\d+\u0000)", unit.text)
            if part
        ]
    for style in range(len(TOKEN_STYLES)):
        restored = restore_unit(unit, translator(unit.masked(style)).strip(), style)
        if restored is not None:
            return restored
    raise TranslationRejected("the translation lost or altered a placeholder")


def translate_message(source: str, translator: Translator) -> str:
    nodes = parse_message(source)
    translated = translate_nodes(parse_message(source), translator)
    if signature(translated) != signature(nodes):
        raise TranslationRejected("the translation changed the message structure")
    return render_message(translated)


def message_units(source: str) -> list[str]:
    """The masked strings a message would send, for sizing a run."""

    out: list[str] = []

    def walk(nodes: list[Node]) -> None:
        nested: list[Plural] = []
        unit = build_unit(nodes, nested)
        if unit.is_translatable():
            out.append(unit.masked())
        for plural in nested:
            for _, branch in plural.branches:
                walk(branch)

    walk(parse_message(source))
    return out


# ---------------------------------------------------------------------------
# Sarvam
# ---------------------------------------------------------------------------


class SarvamError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def tls_context() -> ssl.SSLContext:
    """The system's trust store, plus certifi's bundle when it is installed."""
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context


class SarvamClient:
    """The translate endpoint, with retries for the answers that mean "try again"."""

    RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        max_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/translate"
        self.model = model
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._tls = tls_context()
        self._lock = threading.Lock()
        self.characters_sent = 0
        self.requests = 0

    def translate(self, text: str, target: str) -> str:
        if len(text) > MAX_INPUT_CHARS:
            raise SarvamError(
                f"input of {len(text)} characters is over the per-request limit"
            )
        body = json.dumps(
            {
                "input": text,
                "source_language_code": SOURCE_LANGUAGE,
                "target_language_code": target,
                "model": self.model,
                "mode": "formal",
                # Figures stay in Latin digits, as the app renders them.
                "numerals_format": "international",
                "enable_preprocessing": False,
            }
        ).encode("utf-8")
        for attempt in range(1, self._max_attempts + 1):
            request = urllib.request.Request(
                self._url,
                data=body,
                method="POST",
                headers={
                    "api-subscription-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with self._lock:
                self.requests += 1
                self.characters_sent += len(text)
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=self._tls
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                translated = payload.get("translated_text")
                if not isinstance(translated, str) or not translated.strip():
                    raise SarvamError("the response had no translated_text")
                return translated
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:300]
                if error.code in self.RETRY_STATUSES and attempt < self._max_attempts:
                    self._sleep(_backoff(attempt, error.headers.get("Retry-After")))
                    continue
                raise SarvamError(
                    f"HTTP {error.code}: {detail}", status=error.code
                ) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                # An untrusted certificate will not become trusted by waiting.
                if isinstance(
                    getattr(error, "reason", None), ssl.SSLCertVerificationError
                ):
                    raise SarvamError(
                        f"the server's TLS certificate could not be verified ({error.reason}); "
                        "install certifi or this Python's root certificates"
                    ) from None
                if attempt < self._max_attempts:
                    self._sleep(_backoff(attempt, None))
                    continue
                raise SarvamError(f"network error: {error}") from None
        raise SarvamError("gave up after retries")


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(60.0, max(0.5, float(retry_after)))
        except ValueError:
            pass
    return min(30.0, 2.0 ** (attempt - 1))


def load_api_key() -> str | None:
    value = os.environ.get("SARVAM_API_KEY", "").strip()
    if value:
        return value
    for candidate in (
        REPOSITORY_ROOT / ".env",
        REPOSITORY_ROOT / "services" / "api" / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, raw = line.partition("=")
            if name.strip().removeprefix("export ").strip() == "SARVAM_API_KEY":
                found = raw.strip().strip('"').strip("'")
                if found:
                    return found
    return None


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------

KeyPath = tuple[str, ...]


def flatten(node: dict[str, Any], prefix: KeyPath = ()) -> dict[KeyPath, str]:
    out: dict[KeyPath, str] = {}
    for key, value in node.items():
        if key == "_meta" and not prefix:
            continue
        path = (*prefix, key)
        if isinstance(value, dict):
            out.update(flatten(value, path))
        elif isinstance(value, str):
            out[path] = value
    return out


def dotted(path: KeyPath) -> str:
    """The key as the client looks it up."""

    return ".".join(path)


def nest(
    template: dict[str, Any], values: dict[KeyPath, str], prefix: KeyPath = ()
) -> dict[str, Any]:
    """Build a catalogue in the English file's shape and order."""

    out: dict[str, Any] = {}
    for key, value in template.items():
        if key == "_meta" and not prefix:
            continue
        path = (*prefix, key)
        if isinstance(value, dict):
            out[key] = nest(value, values, path)
        else:
            out[key] = values[path]
    return out


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("Arab", 0x0600, 0x06FF),
    ("Arab", 0x0750, 0x077F),
    ("Arab", 0x08A0, 0x08FF),
    ("Arab", 0xFB50, 0xFDFF),
    ("Arab", 0xFE70, 0xFEFF),
    ("Deva", 0x0900, 0x097F),
    ("Beng", 0x0980, 0x09FF),
    ("Guru", 0x0A00, 0x0A7F),
    ("Gujr", 0x0A80, 0x0AFF),
    ("Orya", 0x0B00, 0x0B7F),
    ("Taml", 0x0B80, 0x0BFF),
    ("Telu", 0x0C00, 0x0C7F),
    ("Knda", 0x0C80, 0x0CFF),
    ("Mlym", 0x0D00, 0x0D7F),
    ("Olck", 0x1C50, 0x1C7F),
    ("Mtei", 0xABC0, 0xABFF),
)


def detect_script(texts: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for text in texts:
        for char in text:
            point = ord(char)
            for name, low, high in SCRIPT_RANGES:
                if low <= point <= high:
                    counts[name] = counts.get(name, 0) + 1
                    break
    return max(counts, key=counts.__getitem__) if counts else None


@dataclass
class Plan:
    """What one language needs: strings to draft, and strings kept as they are."""

    code: str
    sarvam: str
    values: dict[KeyPath, str]
    to_translate: list[KeyPath]
    kept_human: int
    kept_machine: int
    machine: dict[str, dict[str, str]]
    existing_meta: dict[str, Any] | None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def plan_language(
    language: dict[str, str], english: dict[str, Any], *, force: bool
) -> Plan:
    code = language["code"]
    catalogue_path = I18N_DIR / f"{code}.json"
    machine_path = MACHINE_DIR / f"{code}.json"
    existing = load_json(catalogue_path) if catalogue_path.exists() else None
    machine = (
        dict(load_json(machine_path).get("strings", {}))
        if machine_path.exists()
        else {}
    )
    current = flatten(existing) if existing else {}
    # English left in place by an earlier failed run. It is not a person's
    # translation, even once the English it copied has since changed.
    fell_back = set((existing or {}).get("_meta", {}).get("fallbackKeys", []))

    values: dict[KeyPath, str] = {}
    to_translate: list[KeyPath] = []
    kept_human = kept_machine = 0
    for path, source in flatten(english).items():
        key = dotted(path)
        have = current.get(path)
        record = machine.get(key)
        if key in DO_NOT_TRANSLATE:
            values[path] = have if have is not None else source
        elif (
            have is not None
            and have != source
            and key not in fell_back
            and (
                record is None
                or record.get("output_sha256") not in (None, source_hash(have))
            )
        ):
            # A person wrote this, or edited what the machine wrote. It stays,
            # and it is no longer counted as machine output.
            values[path] = have
            kept_human += 1
            machine.pop(key, None)
        elif (
            have is not None
            and record is not None
            and record.get("source_sha256") == source_hash(source)
            and not force
        ):
            values[path] = have
            kept_machine += 1
        else:
            values[path] = source  # until a translation replaces it
            to_translate.append(path)
    return Plan(
        code=code,
        sarvam=language["sarvam"],
        values=values,
        to_translate=to_translate,
        kept_human=kept_human,
        kept_machine=kept_machine,
        machine={key: dict(record) for key, record in machine.items()},
        existing_meta=(existing or {}).get("_meta"),
    )


def build_meta(
    language: dict[str, str],
    plan: Plan,
    *,
    model: str,
    fallback: list[str],
    changed: int,
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    meta = dict(plan.existing_meta or {})
    meta.setdefault("locale", language["code"])
    meta.setdefault("label", language["english"])
    meta.setdefault("nativeLabel", language["native"])
    if not plan.existing_meta:
        meta["reviewed"] = False
        meta["reviewedBy"] = None
        meta["notes"] = (
            "Machine-drafted from the English catalogue with Sarvam Translate and not "
            "reviewed by a native speaker. Strings that could not be translated safely are "
            "left in English and listed in fallbackKeys. Do not claim support for this "
            "language until reviewed is true."
        )
    elif changed and meta.get("reviewed"):
        # New machine text inside a reviewed catalogue makes the whole claim untrue.
        meta["reviewed"] = False
        meta["notes"] = (
            f"{meta.get('notes', '')} {changed} machine-drafted string(s) were added after "
            "the last review and need one."
        ).strip()
    machine_strings = len(plan.machine)
    script = detect_script(list(plan.values.values()))
    if script:
        meta["script"] = script
        meta["dir"] = "rtl" if script == "Arab" else "ltr"
    else:
        meta.setdefault("dir", language["dir"])
    if machine_strings:
        meta["machine"] = {
            "provider": "Sarvam Translate",
            "model": model,
            "strings": machine_strings,
            "generatedAt": now,
        }
    if fallback:
        meta["fallbackKeys"] = sorted(fallback)
    else:
        meta.pop("fallbackKeys", None)
    ordered = {
        key: meta[key]
        for key in ("locale", "label", "nativeLabel", "reviewed", "reviewedBy", "notes")
        if key in meta
    }
    ordered.update({key: value for key, value in meta.items() if key not in ordered})
    return ordered


def write_catalogue(
    code: str, english: dict[str, Any], meta: dict[str, Any], values: dict[KeyPath, str]
) -> None:
    document = {"_meta": meta, **nest(english, values)}
    (I18N_DIR / f"{code}.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_machine_record(
    code: str, model: str, machine: dict[str, dict[str, str]]
) -> None:
    MACHINE_DIR.mkdir(parents=True, exist_ok=True)
    (MACHINE_DIR / f"{code}.json").write_text(
        json.dumps(
            {
                "_comment": (
                    "Which strings in ../"
                    + code
                    + ".json are machine output, and the English "
                    "each was drafted from. A string not listed here was written by a person "
                    "and is never overwritten by the generator."
                ),
                "provider": "Sarvam Translate",
                "model": model,
                "strings": dict(sorted(machine.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def is_offered(code: str) -> bool:
    """Whether a catalogue is complete enough to be offered in the app."""

    catalogue = load_json(I18N_DIR / f"{code}.json")
    total = len(flatten(catalogue))
    fell_back = len(catalogue.get("_meta", {}).get("fallbackKeys", []))
    return total > 0 and fell_back / total <= MAX_FALLBACK_SHARE


def write_index(registry: list[dict[str, str]]) -> list[str]:
    present = sorted(
        path.stem
        for path in I18N_DIR.glob("*.json")
        if path.stem not in ("en", "languages")
    )
    known = {language["code"] for language in registry}
    unknown = [code for code in present if code not in known]
    if unknown:
        raise SystemExit(f"catalogues with no registry entry: {unknown}")
    present = [code for code in present if is_offered(code)]
    lines = [
        "// Generated by scripts/tools/translate_catalogues.py. Do not edit by hand.",
        "//",
        "// The catalogues offered in this build, each loaded only when chosen, so a",
        "// visitor downloads one language rather than all of them. English is not",
        "// listed: it is the fallback and ships with the app. A catalogue still",
        "// mostly in English is left out until a run completes it.",
        "export const CATALOGUE_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {",
        *[f"  {code}: () => import('./{code}.json')," for code in present],
        "};",
        "",
    ]
    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")
    return present


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Stops a language after repeated service failures."""

    def __init__(self, threshold: int = 5) -> None:
        self._threshold = threshold
        self._consecutive = 0
        self._lock = threading.Lock()
        self.reason = ""

    @property
    def open(self) -> bool:
        return self._consecutive >= self._threshold

    def success(self) -> None:
        with self._lock:
            self._consecutive = 0

    def failure(self, error: Exception) -> None:
        with self._lock:
            self._consecutive += 1
            self.reason = (
                f"stopped after {self._consecutive} failures in a row ({error})"
            )


def translate_language(
    language: dict[str, str],
    english: dict[str, Any],
    client: SarvamClient,
    *,
    force: bool,
    limit: int | None,
    concurrency: int,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    plan = plan_language(language, english, force=force)
    work = plan.to_translate[:limit] if limit else plan.to_translate
    english_flat = flatten(english)
    fallback: list[str] = []
    errors: dict[str, str] = {}
    done = 0

    breaker = CircuitBreaker()

    def one(path: KeyPath) -> tuple[KeyPath, str | None, str | None]:
        source = english_flat[path]
        if breaker.open:
            return path, None, f"skipped: {breaker.reason}"
        try:
            translated = translate_message(
                source, lambda text: client.translate(text, plan.sarvam)
            )
            breaker.success()
            return path, translated, None
        except SarvamError as error:
            breaker.failure(error)
            return path, None, str(error)
        except (TranslationRejected, MessageSyntaxError) as error:
            return path, None, str(error)

    before = client.characters_sent
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for path, translated, error in pool.map(one, work):
            key = dotted(path)
            done += 1
            if translated is None:
                fallback.append(key)
                errors[key] = error or "unknown"
                plan.values[path] = english_flat[path]
                plan.machine.pop(key, None)
            else:
                plan.values[path] = translated
                plan.machine[key] = {
                    "source_sha256": source_hash(english_flat[path]),
                    "output_sha256": source_hash(translated),
                    "translated_at": datetime.now(UTC)
                    .replace(microsecond=0)
                    .isoformat(),
                }
            if done % 50 == 0 or done == len(work):
                progress(f"  {language['code']}: {done}/{len(work)}")

    # A key held back by --limit keeps English for now and is not claimed as machine output.
    translated_count = len(work) - len(fallback)
    meta = build_meta(
        language, plan, model=client.model, fallback=fallback, changed=translated_count
    )
    write_catalogue(language["code"], english, meta, plan.values)
    write_machine_record(language["code"], client.model, plan.machine)
    return {
        "code": language["code"],
        "translated": translated_count,
        "kept_human": plan.kept_human,
        "kept_machine": plan.kept_machine,
        "deferred_by_limit": len(plan.to_translate) - len(work),
        "fallback_keys": sorted(fallback),
        "sample_errors": dict(sorted(errors.items())[:10]),
        "characters_sent": client.characters_sent - before,
        "script": meta.get("script"),
        "dir": meta.get("dir"),
    }


def size_language(
    language: dict[str, str], english: dict[str, Any], *, force: bool
) -> dict[str, Any]:
    plan = plan_language(language, english, force=force)
    english_flat = flatten(english)
    units = [
        unit for path in plan.to_translate for unit in message_units(english_flat[path])
    ]
    return {
        "code": language["code"],
        "strings": len(plan.to_translate),
        "requests": len(units),
        "characters": sum(len(unit) for unit in units),
        "kept_human": plan.kept_human,
        "kept_machine": plan.kept_machine,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--languages", help="comma-separated codes (default: all but English)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="size the run; send nothing"
    )
    parser.add_argument(
        "--probe", action="store_true", help="one small request per language"
    )
    parser.add_argument(
        "--force", action="store_true", help="redo machine strings even if current"
    )
    parser.add_argument(
        "--limit", type=int, help="translate at most N strings per language"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--base-url", default=os.environ.get("SARVAM_API_BASE", DEFAULT_BASE_URL)
    )
    args = parser.parse_args(argv)

    registry = load_json(REGISTRY_PATH)["languages"]
    english = load_json(I18N_DIR / "en.json")
    targets = [language for language in registry if language["code"] != "en"]
    if args.languages:
        wanted = {code.strip() for code in args.languages.split(",") if code.strip()}
        unknown = wanted - {language["code"] for language in targets}
        if unknown:
            print(f"Unknown language codes: {sorted(unknown)}", file=sys.stderr)
            return 2
        targets = [language for language in targets if language["code"] in wanted]

    if args.dry_run:
        rows = [
            size_language(language, english, force=args.force) for language in targets
        ]
        print(
            f"{'lang':6} {'strings':>8} {'requests':>9} {'characters':>11}  kept(human/machine)"
        )
        for row in rows:
            print(
                f"{row['code']:6} {row['strings']:>8} {row['requests']:>9} {row['characters']:>11}"
                f"  {row['kept_human']}/{row['kept_machine']}"
            )
        total = sum(row["characters"] for row in rows)
        print(
            f"\nTotal: {sum(row['requests'] for row in rows)} requests, {total:,} characters."
        )
        print("Nothing was sent. Run without --dry-run to translate.")
        return 0

    api_key = load_api_key()
    if not api_key:
        print(
            "SARVAM_API_KEY is not set. Put it in the environment or in the repository's "
            ".env, then run again. (Use --dry-run to size a run without a key.)",
            file=sys.stderr,
        )
        return 2
    client = SarvamClient(api_key, base_url=args.base_url, model=args.model)

    if args.probe:
        failures = 0
        for language in targets:
            try:
                sample = translate_message(
                    "Road closed near {place}",
                    lambda text, code=language["sarvam"]: client.translate(text, code),
                )
                script = detect_script([sample])
                print(
                    f"OK   {language['code']:4} {language['sarvam']:7} {script or '?':5} {sample}"
                )
            except (SarvamError, TranslationRejected) as error:
                failures += 1
                print(f"FAIL {language['code']:4} {language['sarvam']:7} {error}")
        print(
            f"\n{client.requests} requests, {client.characters_sent} characters sent."
        )
        return 1 if failures else 0

    # One small request first, so a wrong key, a blocked network or a service
    # outage is reported in seconds instead of after retrying every string.
    try:
        client.translate("Road closed", targets[0]["sarvam"])
    except SarvamError as error:
        print(f"Sarvam did not accept a first request: {error}", file=sys.stderr)
        return 2

    results = []
    for language in targets:
        print(f"{language['code']} ({language['english']})")
        results.append(
            translate_language(
                language,
                english,
                client,
                force=args.force,
                limit=args.limit,
                concurrency=args.concurrency,
            )
        )
    present = write_index(registry)
    held_back = [row["code"] for row in results if row["code"] not in present]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "provider": "Sarvam Translate",
                "model": args.model,
                "requests": client.requests,
                "characters_sent": client.characters_sent,
                "catalogues_offered": present,
                "catalogues_held_back": held_back,
                "languages": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print()
    for row in results:
        print(
            f"{row['code']:4} translated {row['translated']:>4}  kept {row['kept_human']} human / "
            f"{row['kept_machine']} machine  fallback {len(row['fallback_keys']):>3}  "
            f"{row['script'] or '?'} {row['dir']}"
        )
    if held_back:
        print(f"\nNot offered until more is translated: {', '.join(held_back)}")
    print(
        f"\n{client.requests} requests, {client.characters_sent:,} characters sent → {REPORT_PATH}"
    )
    return 1 if any(row["fallback_keys"] for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
