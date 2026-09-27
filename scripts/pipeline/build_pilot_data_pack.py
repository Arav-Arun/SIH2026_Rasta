"""Build the bounded offline pack the web client is allowed to download."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PILOT_DIR = REPOSITORY_ROOT / "data" / "pilot"
OUTPUT_DIR = REPOSITORY_ROOT / "apps" / "client" / "public" / "packs"

#: Coordinates are rounded to this many decimals.
COORDINATE_DECIMALS = 5

#: How long a reader should treat the topology as current. Roads change; this is
#: guidance printed in the pack, not an expiry the client enforces silently.
FRESHNESS_GUIDANCE = timedelta(days=90)


def round_point(point: list[float]) -> list[float]:
    return [round(point[0], COORDINATE_DECIMALS), round(point[1], COORDINATE_DECIMALS)]


def build_pack() -> dict[str, Any]:
    graph = json.loads((PILOT_DIR / "shillong_graph.json").read_text(encoding="utf-8"))
    facilities = json.loads(
        (PILOT_DIR / "shillong_facilities.json").read_text(encoding="utf-8")
    )

    edges = [
        {
            "edge_id": edge["edge_id"],
            "from_node": edge["from_node"],
            "to_node": edge["to_node"],
            "length_m": round(float(edge["length_m"]), 1),
            "road_class": edge.get("road_class"),
            "base_speed_kph": edge.get("base_speed_kph"),
            "bridge": bool(edge.get("bridge")),
            "max_weight_t": edge.get("max_weight_t"),
            "geometry": [round_point(point) for point in edge["geometry"]],
            # Stated on every segment so no screen has to infer it. The pack is
            # topology; it does not know whether this road is open today.
            "passability": "unknown",
        }
        for edge in graph["edges"]
    ]
    nodes = [
        {
            "node_id": node["node_id"],
            "latitude": round(node["latitude"], COORDINATE_DECIMALS),
            "longitude": round(node["longitude"], COORDINATE_DECIMALS),
        }
        for node in graph["nodes"]
    ]
    places = [
        {
            "facility_id": facility["facility_id"],
            "name": facility["name"],
            "facility_type": facility["facility_type"],
            "latitude": round(facility["latitude"], COORDINATE_DECIMALS),
            "longitude": round(facility["longitude"], COORDINATE_DECIMALS),
            "routing_node_id": facility.get("routing_node_id"),
            "routing_eligible": bool(facility.get("routing_eligible")),
        }
        for facility in facilities["facilities"]
    ]

    # Dated by the data, not by the build: the OSM extract's own timestamp.
    built_at = datetime.fromisoformat(
        graph["source"]["osm_base_timestamp"].replace("Z", "+00:00")
    ).astimezone(UTC)
    return {
        "schema_version": 1,
        "pack_id": "pilot-shillong",
        # The graph version is the pack version: a pack and the graph it was cut
        # from are the same fact, and a client comparing them must not need a
        # second lookup.
        "version": graph["graph_version"],
        "created_at": built_at.isoformat(),
        "treat_as_current_until": (built_at + FRESHNESS_GUIDANCE).isoformat(),
        "mode": "recorded",
        "bbox": graph["query_bbox"],
        "counts": {
            "edges": len(edges),
            "nodes": len(nodes),
            "facilities": len(places),
        },
        "source": graph["source"],
        "notice": (
            "Baseline road topology for the pilot bounding box only, from the "
            "OpenStreetMap extract in data/pilot. Not a navigation map and not a "
            "statement about current road conditions: every segment here reads "
            "passability 'unknown'. Live state comes from the API."
        ),
        "edges": edges,
        "nodes": nodes,
        "facilities": places,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    pack = build_pack()
    # Written with sorted keys and no spaces so the same input always produces
    # the same bytes, and therefore the same checksum.
    body = json.dumps(pack, separators=(",", ":"), sort_keys=True).encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{pack['pack_id']}.{pack['version']}.json"
    (args.output_dir / filename).write_bytes(body)

    # The manifest is what the client reads first: it lists the packs on offer
    # with their sizes and checksums, so a download can be refused before it is
    # activated rather than after.
    manifest = {
        "schema_version": 1,
        "generated_at": pack["created_at"],
        "packs": [
            {
                "pack_id": pack["pack_id"],
                "version": pack["version"],
                "url": f"/packs/{filename}",
                "bytes": len(body),
                "sha256": checksum,
                "created_at": pack["created_at"],
                "treat_as_current_until": pack["treat_as_current_until"],
                "mode": pack["mode"],
                "bbox": pack["bbox"],
                "counts": pack["counts"],
                "attribution": pack["source"]["attribution"],
                "license": pack["source"]["license"],
                "notice": pack["notice"],
            }
        ],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "pack": filename,
                "bytes": len(body),
                "megabytes": round(len(body) / 1_048_576, 2),
                "sha256": checksum,
                "counts": pack["counts"],
                "version": pack["version"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
