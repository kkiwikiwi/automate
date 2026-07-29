#!/usr/bin/env python3
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from import_registry_maps import DATA, USA, import_google_map, merge, read_json


def load_source(source):
    pins = import_google_map(source)
    video_count = sum(bool((pin.get("properties") or {}).get("videoIds")) for pin in pins)
    return pins, video_count


def main():
    registry = read_json(DATA / "source-registry.json", [])
    current = read_json(DATA / "pins.geojson", {"type": "FeatureCollection", "features": []})
    old_status = read_json(DATA / "status.json", {})
    existing = list(current.get("features") or [])

    unique = {}
    for source in registry + [USA]:
        map_id = source.get("mapId")
        if map_id:
            unique[map_id] = source
    targets = list(unique.values())

    imported = []
    successful = []
    failed = []
    workers = min(8, max(1, len(targets)))
    print(f"Retaining {len(existing):,} existing pins", flush=True)
    print(f"Importing {len(targets)} complete master-list maps with {workers} workers", flush=True)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="siren-map") as pool:
        futures = {pool.submit(load_source, source): source for source in targets}
        completed = 0
        for future in as_completed(futures):
            source = futures[future]
            completed += 1
            try:
                pins, video_count = future.result()
                imported.extend(pins)
                successful.append({**source, "pins": len(pins), "videoPins": video_count})
                print(
                    f"[{completed}/{len(targets)}] {source.get('name')}: "
                    f"{len(pins):,} pins, {video_count:,} with videos",
                    flush=True,
                )
            except Exception as exc:
                failed.append({**source, "error": str(exc)})
                print(f"[{completed}/{len(targets)}] {source.get('name')}: FAILED {exc}", flush=True)

    pins = merge(existing + imported)
    now = datetime.now(timezone.utc).isoformat()
    target_ids = {source.get("mapId") for source in targets}
    previous_sources = [
        item
        for item in (old_status.get("successfulSources") or [])
        if item.get("mapId") not in target_ids
    ]
    successful.sort(key=lambda item: (item.get("section", ""), item.get("name", "").lower()))
    failed.sort(key=lambda item: (item.get("section", ""), item.get("name", "").lower()))
    all_successful = previous_sources + successful
    video_pin_count = sum(bool((pin.get("properties") or {}).get("videoIds")) for pin in pins)
    directory_success = sum(bool(source.get("directory")) for source in successful)
    metadata = {
        "updatedAt": now,
        "pinCount": len(pins),
        "sourceCount": len(all_successful),
        "directorySourceCount": directory_success,
        "directorySourceTotal": len(registry),
        "failedSourceCount": len(failed),
        "videoPinCount": video_pin_count,
    }
    geojson = {"type": "FeatureCollection", "metadata": metadata, "features": pins}
    status = {
        "ok": bool(pins),
        **metadata,
        "message": (
            f"Loaded {len(pins):,} unique sirens from {len(successful)}/{len(targets)} maps; "
            f"{video_pin_count:,} pins include embedded-video metadata."
        ),
        "masterDiscovery": read_json(DATA / "master-discovery.json", {}),
        "successfulSources": all_successful,
        "failedSources": failed,
    }
    (DATA / "pins.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(status["message"], flush=True)
    return 0 if pins else 1


if __name__ == "__main__":
    raise SystemExit(main())
