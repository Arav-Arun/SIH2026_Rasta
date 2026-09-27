#!/usr/bin/env python3
"""Execute the routing acceptance cases and save inspectable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.app.routing import route_scenario  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graph",
        type=Path,
        default=Path("data/pilot/shillong_graph.json"),
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("data/fixtures/synthetic_scenarios.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/reports/routing_verification.json"),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    args = parse_args()
    graph = load_json(args.graph)
    fixture = load_json(args.fixtures)
    scenario_by_id = {scenario["id"]: scenario for scenario in fixture["scenarios"]}
    results = {
        scenario_id: route_scenario(graph, fixture, scenario_id)
        for scenario_id in ("S1", "S2", "S4", "S6")
    }
    errors: list[str] = []

    if results["S1"]["status"] != "feasible":
        errors.append("S1 did not return a feasible baseline path")

    s2_closed = set(scenario_by_id["S2"]["expected"]["excluded_segment_ids"])
    s2_path = set((results["S2"].get("path") or {}).get("segment_ids", []))
    if s2_closed.intersection(s2_path):
        errors.append("S2 returned a path through its closed segment")
    if not s2_closed.issubset(set(results["S2"]["exclusions"]["closed_segment_ids"])):
        errors.append("S2 did not explain its closure exclusion")

    if results["S4"]["status"] != "no_route":
        errors.append("S4 did not return no_route for the isolated facility")
    if "closed_segments_disconnect_route" not in results["S4"].get("reason_codes", []):
        errors.append("S4 no-route result lacks the closure explanation")

    s6_constraint = scenario_by_id["S6"]["overlays"]["segment_constraints"][0]
    s6_segment = s6_constraint["segment_id"]
    s6_path = set((results["S6"].get("path") or {}).get("segment_ids", []))
    if s6_segment in s6_path:
        errors.append("S6 returned a path through the incompatible bridge")
    if s6_segment not in set(
        results["S6"]["exclusions"]["vehicle_constraint_segment_ids"]
    ):
        errors.append("S6 did not explain its vehicle constraint exclusion")

    report_body = {
        "check": "verify_routing",
        "status": "passed" if not errors else "failed",
        "mode": "synthetic",
        "ui_label": "SIMULATED SCENARIO",
        "graph_version": graph["graph_version"],
        "fixture_set_id": fixture["fixture_set_id"],
        "results": results,
        "errors": errors,
    }
    report = {
        **report_body,
        "verification_checksum": canonical_sha(report_body),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        scenario_id: {
            "status": result["status"],
            "result_id": result["result_id"],
            "segments": len((result.get("path") or {}).get("segment_ids", [])),
            "cost_seconds": result.get("cost_seconds"),
            "exclusions": result["exclusions"],
        }
        for scenario_id, result in results.items()
    }
    print(
        json.dumps(
            {
                "status": report["status"],
                "errors": errors,
                "verification_checksum": report["verification_checksum"],
                "results": summary,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
