"""Published API schemas shared by routes and identity services."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.types import OperationalRole, OrganizationMode


class ProfileIdentity(BaseModel):
    """The minimal active profile a client may use for UI policy decisions."""

    id: str
    user_id: str
    display_name: str
    locale: str
    active: bool


class OrganizationIdentity(BaseModel):
    """The organization binding for the authenticated profile."""

    id: str
    name: str
    mode: OrganizationMode


class RoleGrantIdentity(BaseModel):
    """A server-evaluated role grant; no client-supplied roles are accepted."""

    id: str
    role: OperationalRole
    district_id: str | None
    valid_from: datetime
    valid_to: datetime | None


class DistrictIdentity(BaseModel):
    """An active district the caller may operate in (names for the UI)."""

    id: str
    state_code: str
    code: str
    name: str


class MeResponse(BaseModel):
    """A server-verified identity and current scoped grants."""

    profile: ProfileIdentity
    organization: OrganizationIdentity
    roles: list[RoleGrantIdentity]
    capabilities: list[str]
    districts: list[DistrictIdentity] = Field(
        default_factory=list,
        description="Active organization districts visible to the caller's grants.",
    )
    server_time: datetime


class HealthResponse(BaseModel):
    """Process-level health only; dependency health comes later."""

    status: str = Field(examples=["ok"])
    service: str = Field(examples=["rasta-api"])
    version: str = Field(examples=["0.1.0"])
    mode: str = Field(examples=["local_demo"])
    as_of: datetime


# --- Network and accessibility -------------------------------------------

Passability = Literal["open", "restricted", "closed", "unknown"]
RiskLevel = Literal["unknown", "low", "moderate", "high", "critical"]
DataMode = Literal["synthetic", "recorded", "live"]
ResponseDataMode = Literal["synthetic", "recorded", "live", "mixed", "none"]


class BBox(BaseModel):
    """WGS84 bounding box."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class BridgeInfo(BaseModel):
    id: str
    max_weight_t: float | None = Field(
        default=None,
        description="Known limit in tonnes; null means the limit is unknown, never unlimited.",
    )
    status: Passability = "unknown"
    verified_at: datetime | None = None
    source_id: str | None = None


class SegmentProperties(BaseModel):
    """Immutable attributes plus the materialized current state of one segment."""

    segment_id: str
    district_id: str
    from_node_id: str
    to_node_id: str
    name: str | None = None
    road_class: str
    length_m: float
    base_speed_kph: float | None = None
    max_weight_t: float | None = None
    bridge: BridgeInfo | None = None
    passability: Passability = "unknown"
    risk_level: RiskLevel = "unknown"
    risk_score: float | None = None
    state_as_of: datetime | None = None
    source_summary: dict[str, Any] = Field(default_factory=dict)
    graph_version: str = Field(
        description="Topology import version of the segment geometry."
    )
    network_version: str = Field(
        description="Current routable-state version; advances only when passability changes."
    )
    source_mode: DataMode


class SegmentFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: dict[str, Any] = Field(description="GeoJSON LineString in WGS84.")
    properties: SegmentProperties


class NetworkCoverage(BaseModel):
    """What the caller's scope actually contains, so an empty map is explicable."""

    bbox: BBox | None = Field(
        default=None, description="Extent of every segment in scope."
    )
    segment_count: int
    network_versions: list[str]
    district_ids: list[str]
    returned: int
    truncated: bool


class NetworkSegmentsResponse(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[SegmentFeature]
    as_of: datetime
    mode: ResponseDataMode
    network_version: str | None
    request_bbox: BBox
    total_in_bbox: int
    coverage: NetworkCoverage
    attribution: str


class SegmentRisk(BaseModel):
    available: bool
    level: RiskLevel = "unknown"
    score: float | None = None
    horizon_hours: int | None = None
    reason: str | None = Field(
        default=None,
        description="Why risk is unavailable, e.g. not_scored.",
    )
    model_version: str | None = None
    computed_at: datetime | None = None
    explanations: list[str] = Field(
        default_factory=list,
        description="What raised the score, in words, each naming its source.",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="What the score does not say, such as inputs it lacked.",
    )
    missing_inputs: list[str] = Field(default_factory=list)


class NetworkObservation(BaseModel):
    id: str
    kind: str
    passability: Passability | None = None
    risk_level: RiskLevel | None = None
    risk_score: float | None = None
    observed_at: datetime
    valid_until: datetime | None = None
    source_id: str
    source_mode: DataMode
    confidence: float | None = None


class SegmentDetailResponse(BaseModel):
    segment: SegmentFeature
    risk: SegmentRisk
    observations: list[NetworkObservation]
    affected_trips: list[dict[str, Any]]
    affected_trips_available: bool
    allowed_actions: list[str]
    as_of: datetime
    mode: ResponseDataMode
    network_version: str


class FacilityCounts(BaseModel):
    monitored: int = 0
    reachable: int = 0
    isolated: int = 0
    unknown_coverage: int = 0
    at_risk: int | None = Field(
        default=None, description="Null until a risk score has been computed."
    )


class SupplyHub(BaseModel):
    facility_id: str
    name: str


class ConnectivityFacility(BaseModel):
    facility_id: str
    name: str
    type: str
    status: Literal["reachable", "isolated", "unknown_coverage"]
    routing_node_id: str | None = None
    location: dict[str, float] | None = Field(
        default=None,
        description="{longitude, latitude} in WGS84 when the source has a point.",
    )


class ConnectivitySummaryResponse(BaseModel):
    district_id: str
    graph_version: str | None
    graph_versions: list[str]
    computed_at: datetime
    baseline_as_of: datetime | None
    coverage_state: Literal["complete", "partial", "none"]
    facilities: FacilityCounts
    supply_hubs: list[SupplyHub]
    facility_status: list[ConnectivityFacility]
    segments: dict[str, int]
    warnings: list[str]
