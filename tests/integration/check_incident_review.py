#!/usr/bin/env python3
"""Incident review check against the local Supabase/PostGIS stack."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import uuid
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

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

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "incident_review_check.json"


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


def create_identity(
    env: dict[str, str], *, role: str, district_id: str, label: str
) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    email = f"test-{role}-{suffix}@example.test"
    password = f"TEST-{uuid.uuid4().hex}"
    service_role_key = env["SERVICE_ROLE_KEY"]
    publishable_key = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]
    created = httpx.post(
        f"{env['API_URL']}/auth/v1/admin/users",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
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
                (str(uuid.uuid4()), user_id, DEFAULT_ORGANIZATION_ID, label),
            )
            profile_id = cursor.fetchone()[0]
            cursor.execute(
                """
                insert into public.role_assignments (id, organization_id, profile_id, role, district_id)
                values (%s::uuid, %s::uuid, %s::uuid, %s::public.app_role, %s::uuid)
                """,
                (
                    str(uuid.uuid4()),
                    DEFAULT_ORGANIZATION_ID,
                    profile_id,
                    role,
                    district_id,
                ),
            )
        connection.commit()
    token = httpx.post(
        f"{env['API_URL']}/auth/v1/token?grant_type=password",
        headers={"apikey": publishable_key},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    token.raise_for_status()
    return profile_id, token.json()["access_token"]


def test_png(seed: int) -> bytes:
    """A tiny valid PNG (16×16, generated pattern) used only as test evidence."""

    width = height = 16
    rows = b""
    for y in range(height):
        rows += b"\x00" + bytes(
            ((x * 17 + y * 29 + seed) % 256) for x in range(width * 3)
        )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def storage_upload(
    env: dict[str, str], *, token: str, path: str, content: bytes, mime: str
) -> int:
    publishable_key = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]
    response = httpx.post(
        f"{env['API_URL']}/storage/v1/object/evidence/{path}",
        headers={
            "apikey": publishable_key,
            "Authorization": f"Bearer {token}",
            "Content-Type": mime,
            "x-upsert": "false",
        },
        content=content,
        timeout=30.0,
    )
    return response.status_code


def main() -> int:
    env = supabase_env()
    database_url = env["DB_URL"]
    checks: list[CheckResult] = []
    report: dict[str, Any] = {}

    with psycopg.connect(database_url) as connection:
        import_summary = import_network(
            connection,
            organization_id=DEFAULT_ORGANIZATION_ID,
            graph=load_json(GRAPH_PATH),
            facilities=load_json(FACILITIES_PATH),
        )
        connection.commit()
    district_id = pilot_district_id()
    graph = load_json(GRAPH_PATH)
    # A real coordinate on the pilot network (first vertex of the first edge).
    first_edge = graph["edges"][0]
    lon, lat = first_edge["geometry"][0]

    officer_profile, officer_token = create_identity(
        env,
        role="field_officer",
        district_id=district_id,
        label="Incident check field officer",
    )
    dispatcher_profile, dispatcher_token = create_identity(
        env,
        role="district_dispatcher",
        district_id=district_id,
        label="Incident check dispatcher",
    )

    os.environ["DATABASE_URL"] = database_url
    os.environ["SUPABASE_URL"] = env["API_URL"]
    os.environ["SUPABASE_SECRET_KEY"] = env["SERVICE_ROLE_KEY"]
    from app.config import Settings  # noqa: E402
    from app.main import create_app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    app = create_app(
        Settings(
            database_url=database_url,
            supabase_url=env["API_URL"],
            supabase_secret_key=env["SERVICE_ROLE_KEY"],
        )
    )
    officer = {"Authorization": f"Bearer {officer_token}"}
    dispatcher = {"Authorization": f"Bearer {dispatcher_token}"}

    def key() -> dict[str, str]:
        return {"Idempotency-Key": str(uuid.uuid4())}

    image = test_png(1)
    image_sha = hashlib.sha256(image).hexdigest()
    captured_at = datetime.now(UTC) - timedelta(minutes=20)

    with TestClient(app) as client:
        # 1. field officer submits a report --------------------------------
        create_key = key()
        body = {
            "type": "landslide_debris",
            "captured_at": captured_at.isoformat(),
            "location": {"longitude": lon, "latitude": lat},
            "location_source": "device_gps",
            "accuracy_m": 9.5,
            "note": "Incident check run: generated test image, not a real observation.",
            "attachments": [
                {
                    "local_id": "att-1",
                    "mime_type": "image/png",
                    "bytes": len(image),
                    "sha256": image_sha,
                }
            ],
        }
        created = client.post(
            "/v1/incidents", json=body, headers={**officer, **create_key}
        )
        created_body = created.json()
        incident = created_body.get("incident", {})
        suggested = created_body.get("suggested_segments", [])
        checks.append(
            CheckResult(
                "officer_submits_report_with_suggested_segments",
                created.status_code == 201
                and incident.get("status") == "submitted"
                and incident.get("district_id") == district_id
                and incident.get("reporter_profile_id") == officer_profile
                and len(created_body.get("upload_instructions", [])) == 1
                and len(suggested) >= 1
                and suggested[0]["distance_m"] < 5,
                f"status={created.status_code} suggested={len(suggested)} nearest={suggested[0]['distance_m'] if suggested else None}m",
            )
        )
        incident_id = incident.get("id")
        instruction = created_body["upload_instructions"][0]
        report["incident_create"] = created_body

        # 2. idempotent replay / key reuse --------------------------------
        replay = client.post(
            "/v1/incidents", json=body, headers={**officer, **create_key}
        )
        reused = client.post(
            "/v1/incidents",
            json={**body, "note": "different"},
            headers={**officer, **create_key},
        )
        checks.append(
            CheckResult(
                "replay_returns_same_incident_and_reuse_is_refused",
                replay.status_code == 201
                and replay.json()["replayed"] is True
                and replay.json()["incident"]["id"] == incident_id
                and reused.status_code == 409
                and reused.json()["error"]["code"] == "idempotency_key_reused",
                f"replay={replay.status_code}/{replay.json().get('replayed')} reuse={reused.status_code}",
            )
        )

        # 3. evidence upload with the officer's own session -----------------
        wrong_path = instruction["path"].rsplit("/", 1)[0] + "/other.png"
        denied = storage_upload(
            env, token=officer_token, path=wrong_path, content=image, mime="image/png"
        )
        allowed = storage_upload(
            env,
            token=officer_token,
            path=instruction["path"],
            content=image,
            mime="image/png",
        )
        dispatcher_denied = storage_upload(
            env,
            token=dispatcher_token,
            path=instruction["path"] + ".x",
            content=image,
            mime="image/png",
        )
        checks.append(
            CheckResult(
                "storage_accepts_only_the_authorised_path_for_the_reporter",
                allowed == 200 and denied >= 400 and dispatcher_denied >= 400,
                f"authorised={allowed} wrong_path={denied} other_user={dispatcher_denied}",
            )
        )

        # 4. server-side verification ---------------------------------------
        complete = client.post(
            f"/v1/incidents/{incident_id}/attachments/{instruction['attachment_id']}/complete",
            json={"sha256": image_sha, "size_bytes": len(image)},
            headers={**officer, **key()},
        )
        checks.append(
            CheckResult(
                "completion_verifies_checksum_and_length",
                complete.status_code == 200
                and complete.json()["verification"]["status"] == "verified"
                and complete.json()["attachment"]["upload_status"] == "verified",
                f"status={complete.status_code} verification={complete.json().get('verification')}",
            )
        )

        # tampered second attachment: declared hash differs from uploaded bytes
        other = test_png(2)
        add = client.post(
            f"/v1/incidents/{incident_id}/attachments",
            json={
                "local_id": "att-2",
                "mime_type": "image/png",
                "bytes": len(other),
                "sha256": image_sha,
            },
            headers={**officer, **key()},
        )
        add_body = add.json()
        storage_upload(
            env,
            token=officer_token,
            path=add_body["upload_instruction"]["path"],
            content=other,
            mime="image/png",
        )
        tampered = client.post(
            f"/v1/incidents/{incident_id}/attachments/{add_body['attachment']['id']}/complete",
            json={"sha256": image_sha, "size_bytes": len(other)},
            headers={**officer, **key()},
        )
        checks.append(
            CheckResult(
                "checksum_mismatch_is_rejected",
                add.status_code == 201
                and tampered.status_code == 200
                and tampered.json()["verification"]["status"] == "rejected",
                f"add={add.status_code} verification={tampered.json().get('verification')}",
            )
        )

        # 5. queues are scoped --------------------------------------------------
        queue = client.get(
            "/v1/incidents", params={"status": "submitted"}, headers=dispatcher
        )
        own = client.get("/v1/incidents", headers=officer)
        checks.append(
            CheckResult(
                "dispatcher_sees_queue_officer_sees_own_reports",
                queue.status_code == 200
                and queue.json()["scope"] == "district"
                and any(i["id"] == incident_id for i in queue.json()["incidents"])
                and own.status_code == 200
                and own.json()["scope"] == "own_reports"
                and all(
                    i["reporter_profile_id"] == officer_profile
                    for i in own.json()["incidents"]
                ),
                f"queue={queue.status_code}/{queue.json().get('total')} own={own.status_code}/{own.json().get('total')}",
            )
        )

        # 6. officer cannot review ---------------------------------------------
        segment_id = suggested[0]["segment_id"]
        review_body = {
            "decision": "confirm_closure",
            "affected_segment_ids": [segment_id],
            "reason": "Photo shows debris blocking both lanes.",
            "expected_version": incident["version"],
        }
        forbidden = client.post(
            f"/v1/incidents/{incident_id}/review",
            json=review_body,
            headers={**officer, **key()},
        )
        checks.append(
            CheckResult(
                "field_officer_cannot_review",
                forbidden.status_code == 403,
                f"status={forbidden.status_code}",
            )
        )

        # 7. dispatcher confirms closure --------------------------------------
        before = client.get(
            f"/v1/network/segments/{segment_id}", headers=dispatcher
        ).json()
        summary_before = client.get(
            "/v1/connectivity/summary",
            params={"district_id": district_id},
            headers=dispatcher,
        ).json()
        reviewed = client.post(
            f"/v1/incidents/{incident_id}/review",
            json=review_body,
            headers={**dispatcher, **key()},
        )
        reviewed_body = reviewed.json()
        after = client.get(
            f"/v1/network/segments/{segment_id}", headers=dispatcher
        ).json()
        checks.append(
            CheckResult(
                "closure_confirmed_in_one_transaction",
                reviewed.status_code == 200
                and reviewed_body["incident"]["status"] == "confirmed"
                and reviewed_body["incident"]["version"] == incident["version"] + 1
                and reviewed_body["segment_changes"][0]["changed"] is True
                and reviewed_body["segment_changes"][0]["previous_passability"]
                == before["segment"]["properties"]["passability"]
                and reviewed_body["segment_changes"][0]["passability"] == "closed"
                and reviewed_body["network_version"]
                not in (None, before["network_version"])
                and len(reviewed_body["audit_event_ids"]) == 2
                and len(reviewed_body["outbox_event_ids"]) == 2,
                f"status={reviewed.status_code} changes={reviewed_body.get('segment_changes')} version={reviewed_body.get('network_version')}",
            )
        )
        report["review"] = reviewed_body
        checks.append(
            CheckResult(
                "network_endpoints_reflect_the_decision",
                after["segment"]["properties"]["passability"] == "closed"
                and after["segment"]["properties"]["network_version"]
                == reviewed_body["network_version"]
                and "confirmed by dispatcher"
                in after["segment"]["properties"]["source_summary"].get(
                    "passability_basis", ""
                )
                and len(after["observations"]) == len(before["observations"]) + 1
                and after["observations"][0]["kind"] == "field_report_reviewed"
                and after["observations"][0]["passability"] == "closed",
                f"passability={after['segment']['properties']['passability']} observations={len(after['observations'])}",
            )
        )
        closed_list = client.get(
            "/v1/network/segments",
            params={"bbox": "91.875,25.56,91.9,25.585", "passability": "closed"},
            headers=dispatcher,
        ).json()
        summary = client.get(
            "/v1/connectivity/summary",
            params={"district_id": district_id},
            headers=dispatcher,
        ).json()
        previous = before["segment"]["properties"]["passability"]
        checks.append(
            CheckResult(
                "map_filter_and_connectivity_count_the_closure",
                any(f["id"] == segment_id for f in closed_list["features"])
                and closed_list["total_in_bbox"]
                == summary_before["segments"]["closed"] + 1
                and summary["segments"]["closed"]
                == summary_before["segments"]["closed"] + 1
                and summary["segments"][previous]
                == summary_before["segments"][previous] - 1
                and sum(
                    summary["segments"][k]
                    for k in ("open", "restricted", "closed", "unknown")
                )
                == summary["segments"]["total"],
                f"closed_in_bbox={closed_list['total_in_bbox']} summary_closed={summary_before['segments']['closed']}->{summary['segments']['closed']} previous={previous}",
            )
        )

        # 8. stale and repeated decisions are refused -----------------------------
        stale = client.post(
            f"/v1/incidents/{incident_id}/review",
            json=review_body,
            headers={**dispatcher, **key()},
        )
        decided = client.post(
            f"/v1/incidents/{incident_id}/review",
            json={**review_body, "expected_version": incident["version"] + 1},
            headers={**dispatcher, **key()},
        )
        checks.append(
            CheckResult(
                "stale_version_and_repeat_decision_refused",
                stale.status_code == 409
                and stale.json()["error"]["code"] == "version_conflict"
                and decided.status_code == 409
                and decided.json()["error"]["code"] == "incident_already_decided",
                f"stale={stale.status_code}/{stale.json()['error']['code']} decided={decided.status_code}/{decided.json()['error']['code']}",
            )
        )

        # 9. reopen: an older observation cannot clear the closure ---------------
        def reopen_report(captured: datetime) -> dict[str, Any]:
            response = client.post(
                "/v1/incidents",
                json={
                    "type": "road_reopened",
                    "captured_at": captured.isoformat(),
                    "location": {"longitude": lon, "latitude": lat},
                    "accuracy_m": 8,
                    "note": "Incident check run: reopen observation.",
                    "proposed_segment_ids": [segment_id],
                },
                headers={**officer, **key()},
            )
            return response.json()["incident"]

        old_reopen = reopen_report(captured_at - timedelta(hours=2))
        stale_reopen = client.post(
            f"/v1/incidents/{old_reopen['id']}/review",
            json={
                "decision": "reopen",
                "affected_segment_ids": [segment_id],
                "reason": "Old photo",
                "expected_version": 1,
            },
            headers={**dispatcher, **key()},
        ).json()
        still_closed = client.get(
            f"/v1/network/segments/{segment_id}", headers=dispatcher
        ).json()
        checks.append(
            CheckResult(
                "stale_reopen_does_not_clear_later_closure",
                stale_reopen["segment_changes"][0]["outcome"]
                == "superseded_by_later_closure"
                and still_closed["segment"]["properties"]["passability"] == "closed",
                f"outcome={stale_reopen['segment_changes'][0]['outcome']} passability={still_closed['segment']['properties']['passability']}",
            )
        )
        fresh_reopen = reopen_report(datetime.now(UTC))
        reopened = client.post(
            f"/v1/incidents/{fresh_reopen['id']}/review",
            json={
                "decision": "reopen",
                "affected_segment_ids": [segment_id],
                "reason": "Debris cleared, verified on site",
                "expected_version": 1,
            },
            headers={**dispatcher, **key()},
        ).json()
        now_open = client.get(
            f"/v1/network/segments/{segment_id}", headers=dispatcher
        ).json()
        checks.append(
            CheckResult(
                "fresh_reopen_reopens_the_road_with_new_version",
                reopened["segment_changes"][0]["changed"] is True
                and now_open["segment"]["properties"]["passability"] == "open"
                and now_open["segment"]["properties"]["network_version"]
                not in (reviewed_body["network_version"], before["network_version"]),
                f"passability={now_open['segment']['properties']['passability']} version={now_open['segment']['properties']['network_version']}",
            )
        )

        # 10. inspections -------------------------------------------------------
        bad_assignee = client.post(
            "/v1/inspections",
            json={
                "target_type": "incident",
                "target_id": incident_id,
                "assignee_profile_id": dispatcher_profile,
            },
            headers={**dispatcher, **key()},
        )
        assigned = client.post(
            "/v1/inspections",
            json={
                "target_type": "segment",
                "target_id": segment_id,
                "assignee_profile_id": officer_profile,
                "due_at": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
                "instructions": "Confirm the road is clear for a 10 t vehicle.",
            },
            headers={**dispatcher, **key()},
        )
        inspection = assigned.json().get("inspection", {})
        inspection_id = inspection.get("id")
        officer_list = client.get("/v1/inspections", headers=officer).json()
        accepted = client.post(
            f"/v1/inspections/{inspection_id}/accept", headers={**officer, **key()}
        )
        completed = client.post(
            f"/v1/inspections/{inspection_id}/complete",
            json={"result_incident_id": fresh_reopen["id"], "note": "Cleared."},
            headers={**officer, **key()},
        )
        cancel_late = client.post(
            f"/v1/inspections/{inspection_id}/cancel", headers={**dispatcher, **key()}
        )
        checks.append(
            CheckResult(
                "inspection_state_machine_enforced",
                bad_assignee.status_code == 422
                and bad_assignee.json()["error"]["code"] == "assignee_not_eligible"
                and assigned.status_code == 201
                and inspection.get("status") == "assigned"
                and officer_list["scope"] == "assigned_to_me"
                and any(i["id"] == inspection_id for i in officer_list["inspections"])
                and accepted.status_code == 200
                and accepted.json()["inspection"]["status"] == "accepted"
                and completed.status_code == 200
                and completed.json()["inspection"]["status"] == "submitted"
                and completed.json()["inspection"]["result_incident_id"]
                == fresh_reopen["id"]
                and cancel_late.status_code == 409
                and cancel_late.json()["error"]["code"] == "invalid_transition",
                f"bad_assignee={bad_assignee.status_code} assigned={assigned.status_code} accept={accepted.status_code} complete={completed.status_code} cancel_late={cancel_late.status_code}",
            )
        )
        report["inspection"] = (
            completed.json() if completed.status_code == 200 else assigned.json()
        )

    # 11. audit + outbox rows exist ------------------------------------------------
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        audit = connection.execute(
            """
            select action, count(*)::int as n
            from public.audit_events
            where organization_id = %s::uuid
              and (entity_id = %s::uuid or metadata ->> 'incident_id' = %s)
            group by action order by action
            """,
            (DEFAULT_ORGANIZATION_ID, incident_id, incident_id),
        ).fetchall()
        outbox = connection.execute(
            """
            select event_type, count(*)::int as n
            from public.event_outbox
            where organization_id = %s::uuid and processed_at is null
              and (aggregate_id = %s::uuid or payload ->> 'incident_id' = %s)
            group by event_type order by event_type
            """,
            (DEFAULT_ORGANIZATION_ID, incident_id, incident_id),
        ).fetchall()
    audit_map = {row["action"]: row["n"] for row in audit}
    outbox_map = {row["event_type"]: row["n"] for row in outbox}
    checks.append(
        CheckResult(
            "audit_and_outbox_recorded",
            audit_map.get("incident.created") == 1
            and audit_map.get("incident.reviewed") == 1
            and audit_map.get("segment.state_changed", 0) >= 1
            and audit_map.get("attachment.completed") == 2
            and outbox_map.get("incident.submitted") == 1
            and outbox_map.get("incident.reviewed") == 1
            and outbox_map.get("segment.state_changed", 0) >= 1,
            json.dumps({"audit": audit_map, "outbox": outbox_map}),
        )
    )

    passed = all(check.passed for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "check": "check_incident_review",
                "passed": passed,
                "run_at": datetime.now(UTC).isoformat(),
                "graph_version": import_summary["graph_version"],
                "district_id": district_id,
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
