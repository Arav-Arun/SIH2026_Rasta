"""Shared API type aliases used across identity and routing modules."""

from __future__ import annotations

from typing import Literal

OperationalRole = Literal[
    "state_coordinator",
    "district_dispatcher",
    "field_officer",
    "driver",
    "admin",
    "reviewer",
]

OrganizationMode = Literal["local_demo", "hosted_demo", "pilot"]
