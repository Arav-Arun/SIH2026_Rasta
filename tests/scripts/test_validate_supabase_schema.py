from __future__ import annotations

import unittest
from pathlib import Path

from scripts.tools import validate_supabase_schema as validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ValidateSupabaseSchemaTests(unittest.TestCase):
    def test_checked_in_schema_satisfies_the_static_contract(self) -> None:
        report = validator.validate_schema(REPOSITORY_ROOT)

        self.assertEqual(report.status, "passed", report.errors)
        self.assertEqual(report.task, "supabase_schema")
        self.assertEqual(report.validation_kind, "static_schema_contract")
        self.assertEqual(report.table_count, len(validator.REQUIRED_TABLES))
        self.assertGreaterEqual(report.policy_count, len(validator.REQUIRED_POLICIES))
        self.assertIn("not executed", report.runtime_verification)


if __name__ == "__main__":
    unittest.main()
