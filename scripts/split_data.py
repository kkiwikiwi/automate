#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def in_united_states(coords):
    lon, lat = coords
    return (
        (-125 <= lon <= -66 and 24 <= lat <= 50)
        or (-171 <= lon <= -129 and 50 <= lat <= 72)
        or (-161 <= lon <= -154 and 18 <= lat <= 23)
        or (-68.5 <= lon <= -64 and 17 <= lat <= 19.5)
    )


def collection(features, metadata):
    return {"type": "FeatureCollection", "metadata": metadata, "features": features}


def write_json(path, value, compact=True):
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 2),
        encoding="utf-8",
    )


def main():
    source = json.loads((DATA / "pins.geojson").read_text(encoding="utf-8"))
    features = source.get("features") or []
    us = []
    world = []
    for feature in features:
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        (us if in_united_states(coordinates) else world).append(feature)

    base_meta = dict(source.get("metadata") or {})
    world_meta = {**base_meta, "region": "outside-us", "pinCount": len(world)}
    us_meta = {**base_meta, "region": "us", "pinCount": len(us)}
    manifest = {
        "updatedAt": base_meta.get("updatedAt"),
        "total": len(features),
        "international": len(world),
        "us": len(us),
        "chunks": [
            {"id": "international", "url": "../data/pins-world.geojson", "count": len(world), "priority": 1},
            {"id": "us", "url": "../data/pins-us.geojson", "count": len(us), "priority": 2},
        ],
    }
    write_json(DATA / "pins-world.geojson", collection(world, world_meta))
    write_json(DATA / "pins-us.geojson", collection(us, us_meta))
    write_json(DATA / "manifest.json", manifest, compact=False)
    print(f"Wrote {len(world):,} international and {len(us):,} U.S. pins")


if __name__ == "__main__":
    main()
