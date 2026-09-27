"""Derive server-evaluated capability tokens from active role grants."""

from __future__ import annotations

from collections.abc import Iterable

from app.types import OperationalRole

ROLE_CAPABILITIES: dict[OperationalRole, tuple[str, ...]] = {
    "state_coordinator": (
        "network:read",
        "route:plan",
        "consignment:read",
        "incident:review",
        "alert:read",
        "data_health:read",
    ),
    "district_dispatcher": (
        "network:read",
        "route:plan",
        "consignment:manage",
        "incident:create",
        "incident:review",
        "inspection:manage",
        "fleet:read",
        "alert:read",
        "data_health:read",
    ),
    "field_officer": (
        "network:read",
        "incident:create",
        "inspection:execute",
        "alert:read",
        "sync:read",
    ),
    "driver": (
        "trip:read",
        "telemetry:submit",
        "alert:read",
        "sync:read",
    ),
    "admin": (
        "admin:settings",
        "data_health:read",
        "audit:read",
        "alert:read",
    ),
    "reviewer": (
        "network:read",
        "incident:review",
        "consignment:read",
        "alert:read",
    ),
}


def capabilities_for_roles(roles: Iterable[OperationalRole]) -> list[str]:
    """Return a stable, de-duplicated capability list for the supplied roles."""

    merged: list[str] = []
    for role in roles:
        for capability in ROLE_CAPABILITIES[role]:
            if capability not in merged:
                merged.append(capability)
    return merged
