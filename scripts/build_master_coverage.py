#!/usr/bin/env python3
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def normalize(value):
    value = (value or "").lower().replace("&", " and ")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\b(?:statewide|state|country|nationwide|warning|outdoor|civil defence|civil defense|official|the|map|sirens?|unfinished|various|multiple countries)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def names(region):
    return [region.get("name", ""), *(region.get("aliases") or [])]


def source_match(source, region):
    source_name = normalize(source.get("name", ""))
    if not source_name:
        return False
    for value in names(region):
        expected = normalize(value)
        if expected and (expected == source_name or expected in source_name or source_name in expected):
            return True
    return False


def main():
    regions = read(DATA / "master-regions.json", [])
    registry = read(DATA / "source-registry.json", [])
    status = read(DATA / "status.json", {})
    successful = status.get("successfulSources") or []
    failed = status.get("failedSources") or []
    nationwide_us = next((item for item in successful if item.get("name") == "Official U.S.A. Siren Map"), None)
    nationwide_canada = next((item for item in successful if item.get("name") == "Great Canadian Siren Map"), None)

    entries = []
    for region in regions:
        exact_success = [item for item in successful if source_match(item, region)]
        exact_failed = [item for item in failed if source_match(item, region)]
        listed = [item for item in registry if source_match(item, region)]
        state = "unresolved"
        aggregate = None
        if exact_success:
            state = "imported"
        elif exact_failed:
            state = "blocked_or_deleted"
        if region.get("category") == "us" and nationwide_us:
            aggregate = {"name": nationwide_us.get("name"), "mapId": nationwide_us.get("mapId")}
        elif region.get("category") == "canada" and region.get("id") != "canada" and nationwide_canada:
            aggregate = {"name": nationwide_canada.get("name"), "mapId": nationwide_canada.get("mapId")}
        entries.append({
            "id": region.get("id"),
            "name": region.get("name"),
            "category": region.get("category"),
            "status": state,
            "importedSources": exact_success,
            "failedSources": exact_failed,
            "listedSources": listed,
            "aggregateCoverage": aggregate,
        })

    counts = {
        "totalListedRegions": len(entries),
        "imported": sum(entry["status"] == "imported" for entry in entries),
        "blockedOrDeleted": sum(entry["status"] == "blocked_or_deleted" for entry in entries),
        "unresolved": sum(entry["status"] == "unresolved" for entry in entries),
        "aggregateCoveredButSourceMissing": sum(entry["status"] != "imported" and bool(entry["aggregateCoverage"]) for entry in entries),
    }
    document = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **counts,
        "entries": entries,
    }
    (DATA / "master-coverage.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
