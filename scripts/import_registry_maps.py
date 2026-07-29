#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

from build_data import import_google_map, merge

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def main():
    registry = read_json(DATA / "source-registry.json", [])
    current = read_json(DATA / "pins.geojson", {"type": "FeatureCollection", "features": []})
    old_status = read_json(DATA / "status.json", {})
    existing = list(current.get("features") or [])
    imported = []
    successful = []
    failed = []

    print(f"Retaining {len(existing):,} existing pins", flush=True)
    print(f"Importing {len(registry)} explicit international maps", flush=True)
    for index, source in enumerate(registry, 1):
        try:
            pins = import_google_map(source)
            imported.extend(pins)
            successful.append({**source, "pins": len(pins)})
            print(index, source.get("name"), len(pins), flush=True)
        except Exception as exc:
            failed.append({**source, "error": str(exc)})
            print(index, source.get("name"), "FAILED", exc, flush=True)

    pins = merge(existing + imported)
    now = datetime.now(timezone.utc).isoformat()
    previous_sources = [
        item
        for item in (old_status.get("successfulSources") or [])
        if item.get("name") not in {source.get("name") for source in registry}
    ]
    all_successful = previous_sources + successful
    metadata = {
        "updatedAt": now,
        "pinCount": len(pins),
        "sourceCount": len(all_successful),
        "directorySourceCount": len(successful),
        "directorySourceTotal": len(registry),
        "failedSourceCount": len(failed),
    }
    geojson = {"type": "FeatureCollection", "metadata": metadata, "features": pins}
    status = {
        "ok": bool(pins),
        **metadata,
        "message": (
            f"Loaded {len(pins):,} unique sirens; "
            f"{len(successful)}/{len(registry)} explicit international maps imported."
        ),
        "discoveryErrors": old_status.get("discoveryErrors") or [],
        "successfulSources": all_successful,
        "failedSources": failed,
    }
    (DATA / "pins.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (DATA / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(status["message"], flush=True)
    return 0 if pins else 1


if __name__ == "__main__":
    raise SystemExit(main())
