#!/usr/bin/env python3
import html
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin
from xml.etree import ElementTree as ET

from build_data import clean, details_from_text, get, local_name

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
USA = {
    "name": "Official U.S.A. Siren Map",
    "mapId": "1AA5lVxum02jYm0T3jDepjuNctwVPf51j",
    "url": "https://www.google.com/maps/d/viewer?mid=1AA5lVxum02jYm0T3jDepjuNctwVPf51j",
    "directory": False,
}
YOUTUBE_PATTERNS = [
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})", re.I),
    re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed|shorts|live)/)([A-Za-z0-9_-]{11})", re.I),
    re.compile(r"(?:youtube\.com/watch\?[^\s<>\"']*?\bv=)([A-Za-z0-9_-]{11})", re.I),
    re.compile(r"(?:youtube\.com/.*?[?&]v=)([A-Za-z0-9_-]{11})", re.I),
]


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def value_list(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [value]
    return [value]


def extract_youtube_ids(*values):
    text = html.unescape(" ".join(str(value or "") for value in values))
    found = []
    for pattern in YOUTUBE_PATTERNS:
        for match in pattern.finditer(text):
            video_id = match.group(1)
            if video_id not in found:
                found.append(video_id)
    return found[:4]


def parse_kml(data, source):
    if data[:2] == b"PK":
        pins, links = [], []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for filename in archive.namelist():
                if filename.lower().endswith(".kml"):
                    parsed, nested = parse_kml(archive.read(filename), source)
                    pins.extend(parsed)
                    links.extend(nested)
        return pins, list(dict.fromkeys(links))

    root = ET.fromstring(data)
    pins, links = [], []
    for element in root.iter():
        if local_name(element.tag) == "networklink":
            for child in element.iter():
                if local_name(child.tag) == "href" and (child.text or "").strip().startswith("http"):
                    links.append(html.unescape(child.text.strip()))
        if local_name(element.tag) != "placemark":
            continue

        name = "Unnamed siren"
        raw_description = ""
        description = ""
        coordinates = ""
        extended = {}
        raw_extended = []
        for child in element.iter():
            raw_text = (child.text or "").strip()
            key = local_name(child.tag)
            if key == "name" and raw_text and name == "Unnamed siren":
                name = clean(raw_text)[:220]
            elif key == "description" and raw_text and not raw_description:
                raw_description = raw_text
                description = clean(raw_text)[:1200]
            elif key == "coordinates" and raw_text and not coordinates:
                coordinates = raw_text
            elif key == "data" and child.attrib.get("name"):
                raw_value = ""
                for grandchild in child.iter():
                    if local_name(grandchild.tag) == "value" and (grandchild.text or "").strip():
                        raw_value = (grandchild.text or "").strip()
                        break
                if raw_value:
                    extended[child.attrib["name"]] = clean(raw_value)
                    raw_extended.append(raw_value)

        if not coordinates:
            continue
        try:
            lon, lat = map(float, coordinates.split()[0].split(",")[:2])
        except Exception:
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        extra_text = " · ".join(f"{key}: {value}" for key, value in extended.items())
        full_description = clean(" · ".join(value for value in (description, extra_text) if value))[:1600]
        siren_type, testing = details_from_text(name, full_description)
        video_ids = extract_youtube_ids(raw_description, name, full_description, *raw_extended)
        properties = {
            "name": name,
            "description": full_description,
            "sirenType": siren_type,
            "testingSchedule": testing,
            "source": source["name"],
            "sourceUrl": source["url"],
        }
        if video_ids:
            properties["videoIds"] = video_ids
        pins.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
                "properties": properties,
            }
        )
    return pins, list(dict.fromkeys(links))


def import_google_map(source):
    last = None
    for url in (
        f'https://www.google.com/maps/d/u/0/kml?mid={quote(source["mapId"])}&forcekml=1',
        f'https://www.google.com/maps/d/kml?mid={quote(source["mapId"])}&forcekml=1',
    ):
        try:
            pins, links = parse_kml(get(url), source)
            for link in links[:75]:
                try:
                    nested, _ = parse_kml(get(urljoin(url, link), 240_000_000), source)
                    pins.extend(nested)
                except Exception as exc:
                    print(" network link failed", exc, flush=True)
            if pins:
                return pins
        except Exception as exc:
            last = exc
    raise RuntimeError(last or "no point placemarks")


def merge(features):
    output = {}
    for feature in features:
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        lon, lat = coordinates[:2]
        key = (round(float(lon), 5), round(float(lat), 5))
        props = feature.setdefault("properties", {})
        sources = value_list(props.get("sources")) or value_list(props.get("source"))
        source_urls = value_list(props.get("sourceUrls")) or value_list(props.get("sourceUrl"))
        video_ids = [item for item in value_list(props.get("videoIds")) if re.fullmatch(r"[A-Za-z0-9_-]{11}", str(item))]
        props["sources"] = list(dict.fromkeys(sources))
        props["sourceUrls"] = list(dict.fromkeys(source_urls))
        if video_ids:
            props["videoIds"] = list(dict.fromkeys(video_ids))[:4]

        if key not in output:
            output[key] = feature
            continue

        existing = output[key].setdefault("properties", {})
        existing["sources"] = list(dict.fromkeys(value_list(existing.get("sources")) + props["sources"]))
        existing["sourceUrls"] = list(dict.fromkeys(value_list(existing.get("sourceUrls")) + props["sourceUrls"]))
        merged_videos = list(dict.fromkeys(value_list(existing.get("videoIds")) + value_list(props.get("videoIds"))))
        if merged_videos:
            existing["videoIds"] = merged_videos[:4]
        for field in ("name", "description", "sirenType", "testingSchedule"):
            incoming = str(props.get(field) or "")
            current = str(existing.get(field) or "")
            if incoming and (not current or len(incoming) > len(current)):
                existing[field] = incoming
    return list(output.values())


def main():
    registry = read_json(DATA / "source-registry.json", [])
    current = read_json(DATA / "pins.geojson", {"type": "FeatureCollection", "features": []})
    old_status = read_json(DATA / "status.json", {})
    existing = list(current.get("features") or [])
    targets = registry + [USA]
    imported = []
    successful = []
    failed = []

    print(f"Retaining {len(existing):,} existing pins", flush=True)
    print(f"Refreshing {len(targets)} Google siren maps with video metadata", flush=True)
    for index, source in enumerate(targets, 1):
        try:
            pins = import_google_map(source)
            imported.extend(pins)
            video_count = sum(bool((pin.get("properties") or {}).get("videoIds")) for pin in pins)
            successful.append({**source, "pins": len(pins), "videoPins": video_count})
            print(index, source.get("name"), len(pins), "pins", video_count, "with videos", flush=True)
        except Exception as exc:
            failed.append({**source, "error": str(exc)})
            print(index, source.get("name"), "FAILED", exc, flush=True)

    pins = merge(existing + imported)
    now = datetime.now(timezone.utc).isoformat()
    target_names = {source.get("name") for source in targets}
    previous_sources = [
        item for item in (old_status.get("successfulSources") or []) if item.get("name") not in target_names
    ]
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
            f"Loaded {len(pins):,} unique sirens; {video_pin_count:,} pins include embedded-video metadata."
        ),
        "discoveryErrors": old_status.get("discoveryErrors") or [],
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
