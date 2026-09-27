import io
import json
import os
import ssl
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scripts.tools import translate_catalogues as tc

ENGLISH_PATH = (
    Path(__file__).resolve().parents[2] / "apps" / "client" / "i18n" / "en.json"
)

SCRIPT_SAMPLE = {"hi-IN": "हिंदी", "ur-IN": "اردو", "as-IN": "অসমীয়া"}


def fake_translation(text: str, target: str) -> str:
    """A translator double that keeps tokens and writes in the target's script."""

    return f"{SCRIPT_SAMPLE.get(target, 'हिंदी')} {text}"


class FakeSarvam(BaseHTTPRequestHandler):
    calls: list[dict] = []
    fail_first_with: int | None = None
    refused_language: str | None = "xx-IN"
    lock = threading.Lock()

    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with FakeSarvam.lock:
            # Header names are case-insensitive on the wire; compare them that way.
            headers = {name.lower(): value for name, value in self.headers.items()}
            FakeSarvam.calls.append({"headers": headers, "body": body})
            failing = FakeSarvam.fail_first_with
            FakeSarvam.fail_first_with = None
        if failing:
            self.send_response(failing)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"error":"slow down"}')
            return
        if body.get("target_language_code") == FakeSarvam.refused_language:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Invalid target_language_code"}}')
            return
        payload = {
            "request_id": "req_test",
            "translated_text": fake_translation(
                body["input"], body["target_language_code"]
            ),
            "source_language_code": body["source_language_code"],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence the test output
        pass


class MessageStructureTest(unittest.TestCase):
    def test_every_english_string_parses_and_renders_back_unchanged(self):
        english = tc.flatten(json.loads(ENGLISH_PATH.read_text(encoding="utf-8")))
        self.assertGreater(len(english), 500)
        for path, text in english.items():
            with self.subTest(key=tc.dotted(path)):
                self.assertEqual(tc.render_message(tc.parse_message(text)), text)

    def test_placeholders_are_masked_and_restored(self):
        result = tc.translate_message(
            "Updated {when} by {who}", lambda text: f"T:{text}"
        )
        self.assertEqual(result, "T:Updated {when} by {who}")

    def test_plural_branches_are_translated_separately_and_reassembled(self):
        seen: list[str] = []

        def translator(text: str) -> str:
            seen.append(text)
            return text.upper()

        result = tc.translate_message(
            "{count, plural, =0 {Nothing queued} one {# item waiting} other {# items waiting}}",
            translator,
        )
        self.assertEqual(
            result,
            "{count, plural, =0 {NOTHING QUEUED} one {{0} ITEM WAITING} other {{0} ITEMS WAITING}}".replace(
                "{0}", "#"
            ),
        )
        # Branches go out one at a time, with the count as a protected token.
        self.assertIn("{0} item waiting", seen)
        self.assertNotIn("#", "".join(seen))

    def test_text_around_a_plural_is_translated_with_the_plural_as_a_token(self):
        source = "{shown, plural, one {# segment in view} other {# segments in view}} of {total} in scope"
        units = tc.message_units(source)
        self.assertEqual(units[0], "{0} of {1} in scope")
        result = tc.translate_message(source, lambda text: text.replace("of", "OF"))
        self.assertIn("OF {total}", result)
        self.assertTrue(result.startswith("{shown, plural, one {# segment in view}"))

    def test_a_lost_placeholder_retries_with_another_token_style_then_refuses(self):
        styles: list[str] = []

        def drops_braces(text: str) -> str:
            styles.append(text)
            return "no tokens here" if "{" in text else text.replace("<x0/>", "<x0/> !")

        self.assertEqual(
            tc.translate_message("Updated {when}", drops_braces), "Updated {when} !"
        )
        self.assertEqual(styles, ["Updated {0}", "Updated <x0/>"])

        with self.assertRaises(tc.TranslationRejected):
            tc.translate_message("Updated {when}", lambda text: "nothing kept")

    def test_native_digits_in_a_token_are_still_recognised(self):
        result = tc.translate_message("Updated {when}", lambda text: "अद्यतन {०}")
        self.assertEqual(result, "अद्यतन {when}")

    def test_a_hash_introduced_in_a_plural_branch_is_refused(self):
        with self.assertRaises(tc.TranslationRejected):
            tc.translate_message(
                "{count, plural, one {# day} other {# days}}",
                lambda text: text + " #",
            )

    def test_text_with_nothing_to_translate_is_not_sent(self):
        calls: list[str] = []
        self.assertEqual(
            tc.translate_message("{a} · {b}", lambda text: calls.append(text) or text),
            "{a} · {b}",
        )
        self.assertEqual(calls, [])

    def test_script_detection(self):
        self.assertEqual(tc.detect_script(["اردو متن", "English"]), "Arab")
        self.assertEqual(tc.detect_script(["हिंदी"]), "Deva")
        self.assertEqual(tc.detect_script(["ᱥᱟᱱᱛᱟᱲᱤ"]), "Olck")
        self.assertIsNone(tc.detect_script(["only latin"]))


class CatalogueRunTest(unittest.TestCase):
    """A whole run against a local stand-in for the Sarvam API."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.i18n = root / "i18n"
        self.i18n.mkdir()
        self.english = {
            "_meta": {"locale": "en", "reviewed": True},
            "app": {"name": "RASTA", "tagline": "Road accessibility"},
            "status": {"closed": "Closed", "open": "Open"},
            "offline": {
                "queued": "{count, plural, one {# item waiting} other {# items waiting}}"
            },
            "outbox": {"type": {"incident.create": "Field report"}},
        }
        (self.i18n / "en.json").write_text(json.dumps(self.english), encoding="utf-8")
        registry = {
            "languages": [
                {
                    "code": "en",
                    "sarvam": "en-IN",
                    "english": "English",
                    "native": "English",
                    "dir": "ltr",
                },
                {
                    "code": "hi",
                    "sarvam": "hi-IN",
                    "english": "Hindi",
                    "native": "हिन्दी",
                    "dir": "ltr",
                },
                {
                    "code": "ur",
                    "sarvam": "ur-IN",
                    "english": "Urdu",
                    "native": "اردو",
                    "dir": "rtl",
                },
            ]
        }
        (self.i18n / "languages.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        # Hindi already has one human string and one untranslated placeholder.
        (self.i18n / "hi.json").write_text(
            json.dumps(
                {
                    "_meta": {
                        "locale": "hi",
                        "label": "Hindi",
                        "nativeLabel": "हिन्दी",
                        "reviewed": False,
                        "reviewedBy": None,
                        "notes": "Team draft.",
                    },
                    "app": {"name": "RASTA", "tagline": "Road accessibility"},
                    "status": {"closed": "बंद", "open": "Open"},
                    "offline": {
                        "queued": "{count, plural, one {# item waiting} other {# items waiting}}"
                    },
                    "outbox": {"type": {"incident.create": "फ़ील्ड रिपोर्ट"}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.patches = [
            mock.patch.object(tc, "I18N_DIR", self.i18n),
            mock.patch.object(tc, "MACHINE_DIR", self.i18n / "machine"),
            mock.patch.object(tc, "REGISTRY_PATH", self.i18n / "languages.json"),
            mock.patch.object(tc, "INDEX_PATH", self.i18n / "index.ts"),
            mock.patch.object(tc, "REPORT_PATH", root / "report.json"),
            mock.patch.object(tc, "REPOSITORY_ROOT", root),
            mock.patch.dict(os.environ, {"SARVAM_API_KEY": "test-key-not-real"}),
        ]
        for patch in self.patches:
            patch.start()
        FakeSarvam.calls = []
        FakeSarvam.fail_first_with = None
        FakeSarvam.refused_language = "xx-IN"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSarvam)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.report = root / "report.json"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def run_main(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()):
            return tc.main(["--base-url", self.base, "--concurrency", "2", *args])

    def test_a_dry_run_sends_nothing_and_needs_no_key(self):
        with mock.patch.dict(os.environ, {"SARVAM_API_KEY": ""}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(tc.main(["--dry-run", "--base-url", self.base]), 0)
        self.assertEqual(FakeSarvam.calls, [])
        self.assertIn("Nothing was sent", out.getvalue())

    def test_a_full_run_drafts_catalogues_without_touching_human_work(self):
        FakeSarvam.fail_first_with = 429  # the first request is told to slow down
        self.assertEqual(self.run_main(), 0)

        # The API was called the way it expects, with the key only in its header.
        first = FakeSarvam.calls[0]
        self.assertEqual(
            first["headers"].get("api-subscription-key"), "test-key-not-real"
        )
        self.assertEqual(first["body"]["source_language_code"], "en-IN")
        self.assertEqual(first["body"]["model"], tc.DEFAULT_MODEL)
        self.assertEqual(first["body"]["numerals_format"], "international")

        hindi = json.loads((self.i18n / "hi.json").read_text(encoding="utf-8"))
        self.assertEqual(
            hindi["status"]["closed"], "बंद", "a person's string was overwritten"
        )
        self.assertEqual(hindi["outbox"]["type"]["incident.create"], "फ़ील्ड रिपोर्ट")
        self.assertEqual(hindi["status"]["open"], "हिंदी Open")
        self.assertEqual(hindi["app"]["name"], "RASTA")
        self.assertEqual(
            hindi["offline"]["queued"],
            "{count, plural, one {हिंदी # item waiting} other {हिंदी # items waiting}}",
        )
        self.assertFalse(hindi["_meta"]["reviewed"])
        self.assertEqual(hindi["_meta"]["notes"], "Team draft.")
        self.assertEqual(hindi["_meta"]["machine"]["provider"], "Sarvam Translate")

        urdu = json.loads((self.i18n / "ur.json").read_text(encoding="utf-8"))
        self.assertFalse(urdu["_meta"]["reviewed"])
        self.assertIsNone(urdu["_meta"]["reviewedBy"])
        self.assertEqual(urdu["_meta"]["dir"], "rtl")
        self.assertEqual(urdu["_meta"]["script"], "Arab")
        self.assertEqual(
            list(urdu.keys()),
            list(self.english.keys()),
            "key order must follow English",
        )

        machine = json.loads(
            (self.i18n / "machine" / "hi.json").read_text(encoding="utf-8")
        )
        self.assertIn("status.open", machine["strings"])
        self.assertNotIn("status.closed", machine["strings"])

        index = (self.i18n / "index.ts").read_text(encoding="utf-8")
        self.assertIn("hi: () => import('./hi.json'),", index)
        self.assertIn("ur: () => import('./ur.json'),", index)

        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertGreater(report["characters_sent"], 0)
        self.assertNotIn("test-key-not-real", self.report.read_text(encoding="utf-8"))

    def test_a_second_run_sends_only_what_changed_and_keeps_a_reviewers_edit(self):
        self.assertEqual(self.run_main("--languages", "hi"), 0)
        sent_before = len(FakeSarvam.calls)

        # A reviewer corrects a machine string; English changes for another key.
        hindi = json.loads((self.i18n / "hi.json").read_text(encoding="utf-8"))
        hindi["status"]["open"] = "खुला"
        (self.i18n / "hi.json").write_text(
            json.dumps(hindi, ensure_ascii=False), encoding="utf-8"
        )
        english = json.loads((self.i18n / "en.json").read_text(encoding="utf-8"))
        english["app"]["tagline"] = "Road accessibility and supply"
        (self.i18n / "en.json").write_text(json.dumps(english), encoding="utf-8")

        self.assertEqual(self.run_main("--languages", "hi"), 0)
        # Everything after the one-request preflight is the actual work.
        new_calls = FakeSarvam.calls[sent_before + 1 :]
        self.assertEqual(
            [call["body"]["input"] for call in new_calls],
            ["Road accessibility and supply"],
        )
        hindi = json.loads((self.i18n / "hi.json").read_text(encoding="utf-8"))
        self.assertEqual(hindi["status"]["open"], "खुला")

    def add_refused_language(self):
        registry = json.loads(
            (self.i18n / "languages.json").read_text(encoding="utf-8")
        )
        registry["languages"].append(
            {
                "code": "xx",
                "sarvam": "xx-IN",
                "english": "Test",
                "native": "Test",
                "dir": "ltr",
            }
        )
        (self.i18n / "languages.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )

    def test_a_refused_first_request_stops_the_run_before_any_work(self):
        self.add_refused_language()
        self.assertEqual(self.run_main("--languages", "xx"), 2)
        self.assertEqual(len(FakeSarvam.calls), 1)
        self.assertFalse((self.i18n / "xx.json").exists())

    def test_a_language_refused_mid_run_is_cut_short_left_in_english_and_named(self):
        self.add_refused_language()
        self.assertEqual(self.run_main("--languages", "hi,xx"), 1)
        test = json.loads((self.i18n / "xx.json").read_text(encoding="utf-8"))
        self.assertEqual(test["status"]["open"], "Open")
        self.assertIn("status.open", test["_meta"]["fallbackKeys"])
        # Kept on disk for the next run, but not offered while it is English.
        index = (self.i18n / "index.ts").read_text(encoding="utf-8")
        self.assertIn("hi: () => import('./hi.json'),", index)
        self.assertNotIn("xx:", index)
        # The breaker stopped it: nowhere near one refused request per string retried.
        refused = [
            call
            for call in FakeSarvam.calls
            if call["body"]["target_language_code"] == "xx-IN"
        ]
        self.assertLessEqual(len(refused), 5 + 2)

    def test_english_left_by_a_failed_run_is_not_mistaken_for_a_translation(self):
        self.add_refused_language()
        self.assertEqual(self.run_main("--languages", "hi,xx"), 1)

        # The English it fell back to changes before the service answers again.
        english = json.loads((self.i18n / "en.json").read_text(encoding="utf-8"))
        english["status"]["open"] = "Open now"
        (self.i18n / "en.json").write_text(json.dumps(english), encoding="utf-8")
        FakeSarvam.refused_language = None

        self.assertEqual(self.run_main("--languages", "hi,xx"), 0)
        test = json.loads((self.i18n / "xx.json").read_text(encoding="utf-8"))
        self.assertEqual(test["status"]["open"], "हिंदी Open now")
        self.assertNotIn("fallbackKeys", test["_meta"])

    def test_the_key_can_come_from_the_repository_env_file(self):
        with mock.patch.dict(os.environ, {"SARVAM_API_KEY": ""}):
            (Path(self.tmp.name) / ".env").write_text(
                "OTHER=1\nexport SARVAM_API_KEY='from-dot-env'\n", encoding="utf-8"
            )
            self.assertEqual(tc.load_api_key(), "from-dot-env")

    def test_without_a_key_nothing_is_sent(self):
        with (
            mock.patch.dict(os.environ, {"SARVAM_API_KEY": ""}),
            redirect_stdout(io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(tc.main(["--base-url", self.base]), 2)
        self.assertEqual(FakeSarvam.calls, [])


class TransportTest(unittest.TestCase):
    def test_an_untrusted_certificate_fails_at_once_instead_of_retrying(self):
        # What a python.org macOS Python without root certificates raises.
        refused = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
        )
        waits: list[float] = []
        client = tc.SarvamClient("test-key-not-real", sleep=waits.append)
        with (
            mock.patch.object(
                tc.urllib.request, "urlopen", side_effect=refused
            ) as opened,
            self.assertRaisesRegex(tc.SarvamError, "certificate could not be verified"),
        ):
            client.translate("Road closed", "bn-IN")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(waits, [])

    def test_a_dropped_connection_is_still_retried(self):
        waits: list[float] = []
        client = tc.SarvamClient(
            "test-key-not-real", sleep=waits.append, max_attempts=3
        )
        with (
            mock.patch.object(
                tc.urllib.request, "urlopen", side_effect=urllib.error.URLError("reset")
            ) as opened,
            self.assertRaisesRegex(tc.SarvamError, "network error"),
        ):
            client.translate("Road closed", "bn-IN")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(len(waits), 2)


if __name__ == "__main__":
    unittest.main()
