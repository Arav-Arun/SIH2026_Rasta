#!/usr/bin/env python3
"""Pilot network import check against the local Supabase/PostGIS stack."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "api"))
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.pipeline.import_pilot_network import (  # noqa: E402
    DEFAULT_ORGANIZATION_ID,
    FACILITIES_PATH,
    GRAPH_PATH,
    import_network,
    load_json,
    pilot_district_id,
)

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "network_import_check.json"
SYNTHETIC_DISTRICT_ID = "a2600002-0000-4000-8000-000000000002"
PILOT_BBOX = "91.875,25.56,91.9,25.585"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def supabase_env() -> dict[str, str]:
    output = subprocess.check_output(
        ["supabase", "status", "-o", "env"], cwd=REPOSITORY_ROOT, text=True
    )
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def create_dispatcher(env: dict[str, str], district_id: str) -> tuple[str, str]:
    """Create an Auth user + profile + district grant; return (user_id, token)."""

    suffix = uuid.uuid4().hex[:8]
    email = f"test-dispatcher-{suffix}@example.test"
    password = f"TEST-{uuid.uuid4().hex}"
    service_role_key = env["SERVICE_ROLE_KEY"]
    publishable_key = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]

    created = httpx.post(
        f"{env['API_URL']}/auth/v1/admin/users",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    created.raise_for_status()
    user_id = created.json()["id"]

    with psycopg.connect(env["DB_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.profiles (id, user_id, organization_id, display_name, locale, active)
                values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', true)
                returning id::text
                """,
                (
                    str(uuid.uuid4()),
                    user_id,
                    DEFAULT_ORGANIZATION_ID,
                    "Network import check dispatcher",
                ),
            )
            profile_id = cursor.fetchone()[0]
            cursor.execute(
                """
                insert into public.role_assignments (id, organization_id, profile_id, role, district_id)
                values (%s::uuid, %s::uuid, %s::uuid, 'district_dispatcher', %s::uuid)
                """,
                (str(uuid.uuid4()), DEFAULT_ORGANIZATION_ID, profile_id, district_id),
            )
        connection.commit()

    token = httpx.post(
        f"{env['API_URL']}/auth/v1/token?grant_type=password",
        headers={"apikey": publishable_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    token.raise_for_status()
    return user_id, token.json()["access_token"]


def main() -> int:
    env = supabase_env()
    database_url = env["DB_URL"]
    checks: list[CheckResult] = []

    with psycopg.connect(database_url) as connection:
        import_summary = import_network(
            connection,
            organization_id=DEFAULT_ORGANIZATION_ID,
            graph=load_json(GRAPH_PATH),
            facilities=load_json(FACILITIES_PATH),
        )
        connection.commit()
    district_id = pilot_district_id()
    checks.append(
        CheckResult(
            "pilot_network_imported",
            import_summary["segments"] == 2860
            and import_summary["bridges"] == 23
            and import_summary["facilities"] == 30,
            json.dumps(
                {
                    k: import_summary[k]
                    for k in ("graph_version", "segments", "bridges", "facilities")
                }
            ),
        )
    )

    _user_id, token = create_dispatcher(env, district_id)

    os.environ["DATABASE_URL"] = database_url
    os.environ["SUPABASE_URL"] = env["API_URL"]
    from app.config import Settings  # noqa: E402
    from app.main import create_app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    app = create_app(Settings(database_url=database_url, supabase_url=env["API_URL"]))
    auth = {"Authorization": f"Bearer {token}"}
    report: dict[str, Any] = {
        "district_id": district_id,
        "graph_version": import_summary["graph_version"],
    }

    with TestClient(app) as client:
        me = client.get("/v1/me", headers=auth)
        checks.append(
            CheckResult(
                "real_jwt_accepted",
                me.status_code == 200
                and "network:read" in me.json().get("capabilities", []),
                f"status={me.status_code}",
            )
        )

        full = client.get(
            "/v1/network/segments", params={"bbox": PILOT_BBOX}, headers=auth
        )
        body = full.json()
        features = body.get("features", [])
        checks.append(
            CheckResult(
                "pilot_bbox_returns_every_segment",
                full.status_code == 200
                and len(features) == 2860
                and body["total_in_bbox"] == 2860
                and body["coverage"]["truncated"] is False
                and body["network_version"] == import_summary["graph_version"]
                and body["mode"] == "recorded",
                f"status={full.status_code} returned={len(features)} total={body.get('total_in_bbox')} mode={body.get('mode')}",
            )
        )
        unknown_only = all(
            f["properties"]["passability"] == "unknown" for f in features
        )
        bridges = [f for f in features if f["properties"].get("bridge")]
        checks.append(
            CheckResult(
                "baseline_state_is_unknown_with_bridge_limits_null",
                unknown_only
                and len(bridges) == 23
                and all(
                    b["properties"]["bridge"]["max_weight_t"] is None for b in bridges
                ),
                f"unknown_only={unknown_only} bridges={len(bridges)}",
            )
        )
        report["coverage"] = body.get("coverage")
        report["sample_feature"] = features[0] if features else None

        limited = client.get(
            "/v1/network/segments",
            params={"bbox": PILOT_BBOX, "limit": 100},
            headers=auth,
        )
        checks.append(
            CheckResult(
                "limit_truncates_and_reports_it",
                limited.status_code == 200
                and len(limited.json()["features"]) == 100
                and limited.json()["coverage"]["truncated"] is True
                and limited.json()["total_in_bbox"] == 2860,
                f"status={limited.status_code}",
            )
        )

        quarter = client.get(
            "/v1/network/segments",
            params={"bbox": "91.875,25.56,91.8875,25.5725"},
            headers=auth,
        )
        quarter_count = len(quarter.json().get("features", []))
        checks.append(
            CheckResult(
                "smaller_bbox_returns_subset",
                quarter.status_code == 200 and 0 < quarter_count < 2860,
                f"returned={quarter_count}",
            )
        )

        closed = client.get(
            "/v1/network/segments",
            params={"bbox": PILOT_BBOX, "passability": "closed"},
            headers=auth,
        )
        checks.append(
            CheckResult(
                "passability_filter_honours_current_state",
                closed.status_code == 200 and closed.json()["total_in_bbox"] == 0,
                f"closed_total={closed.json().get('total_in_bbox')}",
            )
        )

        simplified = client.get(
            "/v1/network/segments",
            params={"bbox": PILOT_BBOX, "simplify_m": 20, "limit": 50},
            headers=auth,
        )
        checks.append(
            CheckResult(
                "simplify_keeps_valid_linestrings",
                simplified.status_code == 200
                and all(
                    f["geometry"]["type"] == "LineString"
                    and len(f["geometry"]["coordinates"]) >= 2
                    for f in simplified.json()["features"]
                ),
                f"status={simplified.status_code}",
            )
        )

        huge = client.get(
            "/v1/network/segments", params={"bbox": "90,24,93,27"}, headers=auth
        )
        checks.append(
            CheckResult(
                "oversized_bbox_rejected",
                huge.status_code == 422
                and huge.json()["error"]["code"] == "bbox_too_large",
                f"status={huge.status_code} code={huge.json().get('error', {}).get('code')}",
            )
        )

        foreign = client.get(
            "/v1/network/segments",
            params={"bbox": PILOT_BBOX, "district_id": SYNTHETIC_DISTRICT_ID},
            headers=auth,
        )
        checks.append(
            CheckResult(
                "foreign_district_denied",
                foreign.status_code == 403,
                f"status={foreign.status_code}",
            )
        )

        anonymous = client.get("/v1/network/segments", params={"bbox": PILOT_BBOX})
        checks.append(
            CheckResult(
                "anonymous_denied",
                anonymous.status_code == 401,
                f"status={anonymous.status_code}",
            )
        )

        bridge_id = bridges[0]["id"] if bridges else features[0]["id"]
        detail = client.get(f"/v1/network/segments/{bridge_id}", headers=auth)
        detail_body = detail.json()
        checks.append(
            CheckResult(
                "segment_detail_reports_bridge_state_and_actions",
                detail.status_code == 200
                and detail_body["segment"]["properties"]["bridge"]["status"]
                == "unknown"
                and detail_body["risk"]["available"] is False
                and detail_body["allowed_actions"]
                == ["assign_inspection", "report_observation", "plan_route"],
                f"status={detail.status_code}",
            )
        )
        report["sample_detail"] = detail_body if detail.status_code == 200 else None

        missing = client.get(f"/v1/network/segments/{uuid.uuid4()}", headers=auth)
        checks.append(
            CheckResult(
                "unknown_segment_404",
                missing.status_code == 404,
                f"status={missing.status_code}",
            )
        )

        summary = client.get(
            "/v1/connectivity/summary",
            params={"district_id": district_id},
            headers=auth,
        )
        summary_body = summary.json()
        checks.append(
            CheckResult(
                "connectivity_summary_from_real_graph",
                summary.status_code == 200
                and summary_body["facilities"]["monitored"] == 30
                and summary_body["segments"]["total"] == 2860
                and summary_body["segments"]["unknown"] == 2860
                and len(summary_body["supply_hubs"]) == 1
                and "passability_unknown_segments" in summary_body["warnings"],
                json.dumps(
                    {
                        "status": summary.status_code,
                        "coverage_state": summary_body.get("coverage_state"),
                        "facilities": summary_body.get("facilities"),
                        "warnings": summary_body.get("warnings"),
                    }
                ),
            )
        )
        report["connectivity"] = summary_body if summary.status_code == 200 else None

        foreign_summary = client.get(
            "/v1/connectivity/summary",
            params={"district_id": SYNTHETIC_DISTRICT_ID},
            headers=auth,
        )
        checks.append(
            CheckResult(
                "connectivity_foreign_district_denied",
                foreign_summary.status_code == 403,
                f"status={foreign_summary.status_code}",
            )
        )

    passed = all(check.passed for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "check": "check_network_import",
                "passed": passed,
                "run_at": datetime.now(UTC).isoformat(),
                "supabase_api_url": env["API_URL"],
                "checks": [asdict(check) for check in checks],
                **report,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    for check in checks:
        print(("PASS " if check.passed else "FAIL ") + check.name + ": " + check.detail)
    print(f"\n{'ALL CHECKS PASSED' if passed else 'CHECKS FAILED'} → {REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
