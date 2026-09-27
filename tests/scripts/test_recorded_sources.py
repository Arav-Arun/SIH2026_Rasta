"""Re-dated copies of the recorded sources: fresh times, same intervals, same values."""

import json
import re
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.pipeline.recorded_sources import (
    ISSUED_BEFORE_NOW,
    RECORDED,
    redate_recorded_sources,
)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def cap_time(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>([^<]+)</{tag}>", text)
    assert match is not None
    return match.group(1)


class RedatedRecordedSources(unittest.TestCase):
    NOW = datetime(2027, 2, 14, 9, 0, tzinfo=UTC)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "sources"
        self.originals = {
            p.name: p.read_bytes() for p in RECORDED.iterdir() if p.is_file()
        }
        self.result = redate_recorded_sources(self.target, now=self.NOW)
        self.rainfall = json.loads(
            (self.target / "imd_rainfall.json").read_text(encoding="utf-8")
        )
        self.cap = (self.target / "sachet_cap.xml").read_text(encoding="utf-8")
        self.recorded_rainfall = json.loads(
            (RECORDED / "imd_rainfall.json").read_text(encoding="utf-8")
        )
        self.recorded_cap = (RECORDED / "sachet_cap.xml").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_newest_document_was_issued_just_before_now(self) -> None:
        newest = max(
            parse(self.rainfall["issued_at"]), parse(cap_time(self.cap, "sent"))
        )
        self.assertEqual(newest, self.NOW - ISSUED_BEFORE_NOW)

    def test_every_interval_of_the_recording_is_kept(self) -> None:
        def span(document: dict, a: str, b: str) -> timedelta:
            return parse(document[b]) - parse(document[a])

        self.assertEqual(
            span(self.rainfall, "issued_at", "valid_until"),
            span(self.recorded_rainfall, "issued_at", "valid_until"),
        )
        self.assertEqual(
            parse(cap_time(self.cap, "expires")) - parse(cap_time(self.cap, "sent")),
            parse(cap_time(self.recorded_cap, "expires"))
            - parse(cap_time(self.recorded_cap, "sent")),
        )
        self.assertEqual(
            parse(self.rainfall["issued_at"]) - parse(cap_time(self.cap, "sent")),
            parse(self.recorded_rainfall["issued_at"])
            - parse(cap_time(self.recorded_cap, "sent")),
        )

    def test_values_are_untouched_and_the_copy_says_it_was_redated(self) -> None:
        self.assertEqual(
            self.rainfall["districts"], self.recorded_rainfall["districts"]
        )
        self.assertIn("RECORDED FIXTURE, NOT LIVE DATA", self.rainfall["_note"])
        self.assertIn("Re-dated", self.rainfall["_note"])
        self.assertIn("Re-dated", self.cap)
        # The CAP keeps its offset style: IST in, IST out.
        self.assertTrue(cap_time(self.cap, "sent").endswith("+05:30"))

        def without_times_or_comments(text: str) -> str:
            text = re.sub(r"<(sent|effective|onset|expires)>[^<]+</\1>", r"<\1/>", text)
            return re.sub(r"<!--.*?-->", "", text, flags=re.S)

        self.assertEqual(
            without_times_or_comments(self.cap),
            without_times_or_comments(self.recorded_cap),
        )

    def test_the_recorded_originals_are_never_modified(self) -> None:
        for path in RECORDED.iterdir():
            if path.is_file():
                with self.subTest(file=path.name):
                    self.assertEqual(path.read_bytes(), self.originals[path.name])
        self.assertTrue((self.target / "malformed_cap.xml").exists())


if __name__ == "__main__":
    unittest.main()
