#!/usr/bin/env python3
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

US_STATES_AND_AREAS = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "district of columbia", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "puerto rico", "american samoa", "guam",
    "northern mariana islands", "u.s. virgin islands", "us virgin islands"
}


def value_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except Exception:
            pass
        return [value]
    return [str(value)]


def normalized(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_us_source(name):
    name = normalized(name)
    if not name:
        return False
    if name in {"official u s a siren map", "official usa siren map", "official us siren map"}:
        return True
    return any(
        name == area
        or name.startswith(area + " ")
        or f" {area} " in f" {name} "
        for area in US_STATES_AND_AREAS
    )


def source_classification(feature):
    properties = feature.get("properties") or {}
    sources = value_list(properties.get("sources")) or value_list(properties.get("source"))
    informative = [name for name in sources if normalized(name) != "openstreetmap worldwide sirens"]
    if not informative:
        return None
    # Any clearly international/community-country source keeps the pin outside the U.S.
    # A pin is U.S. only when every informative source is the nationwide U.S. map or a named U.S. state/territory map.
    return all(is_us_source(name) for name in informative)


def coordinate_fallback(coords):
    lon, lat = coords
    # Used only for source-neutral records such as OpenStreetMap. Source-backed country maps are classified above.
    contiguous = -125 <= lon <= -66 and 24 <= lat <= 50
    hawaii = -161 <= lon <= -154 and 18 <= lat <= 23
    puerto_rico_usvi = -68.5 <= lon <= -64 and 17 <= lat <= 19.5
    # A tighter practical Alaska envelope avoids swallowing most of western Canada.
    alaska = -170 <= lon <= -130 and 51 <= lat <= 72 and not (-141 <= lon <= -130 and 54 <= lat <= 70)
    return contiguous or alaska or hawaii or puerto_rico_usvi


def in_united_states(feature):
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return False
    source_result = source_classification(feature)
    if source_result is not None:
        return source_result
    return coordinate_fallback(coordinates)


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
        (us if in_united_states(feature) else world).append(feature)

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
