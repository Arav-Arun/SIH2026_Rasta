"""The repository secret scan: what it must catch, and what it must let pass."""

import unittest

from scripts.tools.scan_secrets import committed_env_files, redact, scan_text

# Every credential-shaped value here is assembled at run time, so no string in
# this file is itself a plausible secret for another scanner to trip over.
RANDOM_TAIL = "q8Zr2LkP0vXw9TnB4sHyM7cD1fGj5KaE3uWx"


def kinds(text: str) -> list[str]:
    return [finding.kind for finding in scan_text("some/file.ts", text)]


class ReportsCredentials(unittest.TestCase):
    CASES = [
        ("-----BEGIN " + "PRIVATE KEY-----", "private key"),
        ("aws = '" + "AKIA" + "Q" * 16 + "'", "AWS access key"),
        ("token: " + "gh" + "p_" + "A1b2" * 9, "GitHub token"),
        ("key = " + "sb_" + "secret_" + RANDOM_TAIL[:20], "Supabase secret key"),
        (
            "key = " + "sb_" + "publishable_" + RANDOM_TAIL[:20],
            "Supabase publishable key",
        ),
        (
            "url = 'https://" + "abcdefghij" * 2 + ".supabase.co'",
            "hosted Supabase project URL",
        ),
        ("OPENAI = " + "sk-" + "proj-" + RANDOM_TAIL, "OpenAI key"),
        (
            "jwt = "
            + "eyJ"
            + "hbGciOiJIUzI1NiJ9"
            + ".eyJ"
            + "zdWIiOiIxMjM0NTY3OCJ9"
            + "."
            + RANDOM_TAIL,
            "JSON web token",
        ),
        ("api_key = '" + RANDOM_TAIL[:24] + "'", "credential assignment"),
        # The shape that let a map-tile key into history unnoticed.
        (
            "  process.env.EXPO_PUBLIC_TILES_API_KEY || '" + RANDOM_TAIL[:30] + "';",
            "credential assignment",
        ),
        ('{"apiKey": "' + RANDOM_TAIL[:28] + '"}', "credential assignment"),
    ]

    def test_it_reports_what_looks_like_a_real_credential(self) -> None:
        for text, kind in self.CASES:
            with self.subTest(kind=kind):
                self.assertIn(kind, kinds(text))


class LetsPlaceholdersPass(unittest.TestCase):
    CASES = [
        # Supabase config.toml's own way of pointing at an environment variable.
        'auth_token = "env(SUPABASE_AUTH_SMS_TWILIO_AUTH_TOKEN)"',
        'secret = "env(SUPABASE_AUTH_EXTERNAL_APPLE_SECRET)"',
        # A readable test fixture is not a credential.
        "accessToken: 'compact.jwt.token',",
        "password = 'correct-horse-battery-staple'",
        # Placeholders.
        "api_key = 'your-api-key-goes-here'",
        "secret = '${SUPABASE_SECRET_KEY}'",
        "createClient(url, configuredKey || 'not-configured')",
    ]

    def test_it_lets_placeholders_and_references_pass(self) -> None:
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [])


class Reporting(unittest.TestCase):
    def test_a_finding_never_repeats_the_secret(self) -> None:
        value = "sb_" + "secret_" + RANDOM_TAIL
        preview = redact(value)
        self.assertNotIn(RANDOM_TAIL[:8], preview)
        self.assertTrue(preview.startswith(value[:4]))
        self.assertIn(f"({len(value)} chars)", preview)

    def test_only_example_environment_files_may_be_committed(self) -> None:
        flagged = {
            finding.path
            for finding in committed_env_files(
                [
                    ".env",
                    "apps/mobile/.env.local",
                    "services/api/.env",
                    "apps/mobile/.env.example",
                    "README.md",
                ]
            )
        }
        self.assertEqual(
            flagged, {".env", "apps/mobile/.env.local", "services/api/.env"}
        )


if __name__ == "__main__":
    unittest.main()
