#!/usr/bin/env python3
"""Apply a reviewed batch of recovered SirenFinder source links to the registry.

The batch file supports:
- sources: source objects to add/replace by mapId
- removeMapIds: obsolete or misattributed map IDs to remove
- removeNames: obsolete source names to remove

This script only mutates the source registry. The normal importer validates that each
public map actually exports usable KML before its data is marked successful.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REGISTRY = DATA / "source-registry.json"
BATCH = DATA / "recovered-source-batch.json"


def read(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def main():
    registry = read(REGISTRY, [])
    batch = read(BATCH, {})
    removals = set(batch.get("removeMapIds") or [])
    remove_names = set(batch.get("removeNames") or [])
    output = {
        item.get("mapId"): item
        for item in registry
        if item.get("mapId") and item.get("mapId") not in removals and item.get("name") not in remove_names
    }
    for source in batch.get("sources") or []:
        map_id = source.get("mapId")
        if not map_id:
            continue
        item = dict(source)
        item.setdefault("url", f"https://www.google.com/maps/d/viewer?mid={map_id}")
        item.setdefault("directory", True)
        output[map_id] = item
    result = sorted(output.values(), key=lambda item: (item.get("section", ""), item.get("name", "").lower()))
    REGISTRY.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Applied {len(batch.get('sources') or [])} recovered sources; registry contains {len(result)} maps")


if __name__ == "__main__":
    main()
