"""Per-segment terrain slope for the risk score, from a committed, sourced file.

The file is built from SRTM elevation by ``data/sources/build_terrain.py`` and holds,
for each directed road edge, how steep the ground is around it. It describes the
terrain, not whether a slope will fail, which is why the feature is called
``terrain_slope`` and not a susceptibility.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.risk_engine import Feature

logger = logging.getLogger("rasta.terrain")

SCHEMA_VERSION = "terrain-v1"

#: Used when a file does not state its own full-scale slope.
DEFAULT_FULL_SCALE_DEG = 35.0


@dataclass(frozen=True, slots=True)
class TerrainLayer:
    """What the terrain file says about each edge, ready to turn into features."""

    observed_at: datetime
    source: str
    buffer_m: float
    full_scale_deg: float
    #: edge id -> (90th-percentile slope in degrees, mean slope, samples used)
    slopes: dict[str, tuple[float, float, int]]

    def __len__(self) -> int:
        return len(self.slopes)

    def feature_for(self, edge_id: str | None) -> Feature | None:
        """The ``terrain_slope`` input for one road edge, or None when it has none."""

        entry = self.slopes.get(edge_id) if edge_id else None
        if entry is None:
            return None
        p90, mean, samples = entry
        return Feature(
            name="terrain_slope",
            value=min(1.0, max(0.0, p90 / self.full_scale_deg)),
            observed_at=self.observed_at,
            source=(
                f"{self.source}, slope {p90:.0f} degrees at the 90th percentile "
                f"within {self.buffer_m:.0f} m"
            ),
            detail={
                "slope_p90_deg": p90,
                "slope_mean_deg": mean,
                "samples": samples,
                "full_scale_deg": self.full_scale_deg,
            },
        )


def parse_terrain(document: dict) -> TerrainLayer:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("not a terrain-v1 document")
    method = document.get("method") or {}
    slopes: dict[str, tuple[float, float, int]] = {}
    for edge_id, values in (document.get("segments") or {}).items():
        p90, mean, samples = values
        slopes[str(edge_id)] = (float(p90), float(mean), int(samples))
    if not slopes:
        raise ValueError("the terrain document holds no segments")
    observed = datetime.fromisoformat(
        str(document["generated_at"]).replace("Z", "+00:00")
    )
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return TerrainLayer(
        observed_at=observed,
        source="SRTM terrain",
        buffer_m=float(method.get("buffer_m", 45.0)),
        full_scale_deg=float(method.get("full_scale_deg", DEFAULT_FULL_SCALE_DEG)),
        slopes=slopes,
    )


def load_terrain(path: Path | None) -> TerrainLayer | None:
    """The terrain layer at ``path``, or None when there is none to use.

    A missing or unreadable file is not an error: the score then lists terrain slope
    as a missing input, which is true, instead of the whole recompute failing.
    """

    if path is None or not path.is_file():
        return None
    try:
        return parse_terrain(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("terrain file %s could not be used: %s", path, error)
        return None


__all__ = ["TerrainLayer", "load_terrain", "parse_terrain"]
