#!/usr/bin/env python3
"""Resolve missing master-list Google My Maps by web search plus KML geography validation.

This script never replaces a working source with an unvalidated search result.  It queries
several public search surfaces, extracts complete Google My Maps IDs from returned HTML or
redirect URLs, downloads candidate KML, and accepts a candidate only when its pin geography
matches the expected master-list region.
"""

import base64
import html
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from import_registry_maps import DATA, import_google_map, read_json

ROOT = Path(__file__).resolve().parents[1]
REGIONS_PATH = DATA / "master-regions.json"
REGISTRY_PATH = DATA / "source-registry.json"
REPORT_PATH = DATA / "search-resolution.json"

MAP_PATTERNS = [
    re.compile(r"(?:[?&](?:mid|id)=)([A-Za-z0-9_-]{15,})", re.I),
    re.compile(r"(?:[?&](?:mid|id)%3D)([A-Za-z0-9_-]{15,})", re.I),
    re.compile(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]{15,})", re.I),
    re.compile(r"google\.[^/\s]+/maps/d/(?:u/\d+/)?(?:viewer|view|edit|embed)[^\s<>\"']*?[?&]mid=([A-Za-z0-9_-]{15,})", re.I),
]
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
BING_U_RE = re.compile(r"[?&]u=a1([A-Za-z0-9_-]+)", re.I)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

ENGINES = [
    ("bing-rss", "https://www.bing.com/search?format=rss&count=30&q={query}"),
    ("bing-html", "https://www.bing.com/search?count=30&q={query}"),
    ("duckduckgo", "https://html.duckduckgo.com/html/?q={query}"),
    ("yahoo", "https://search.yahoo.com/search?n=20&p={query}"),
]


def normalize(value):
    value = (value or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def region_names(region):
    values = [region.get("name", ""), *(region.get("aliases") or [])]
    name = region.get("name", "")
    values.extend([f"{name} Siren Map", f"{name} Statewide Siren Map"])
    return [normalize(value) for value in values if value]


def source_matches_region(source, region):
    source_name = normalize(source.get("name", ""))
    if not source_name:
        return False
    for expected in region_names(region):
        if not expected:
            continue
        if expected in source_name or source_name in expected:
            return True
    return False


def get_text(url, tries=2, timeout=30):
    last = None
    for attempt in range(tries):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read(8_000_000).decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "search request failed")


def decoded_variants(text):
    variants = []
    current = text or ""
    for _ in range(4):
        current = html.unescape(current).replace("\\/", "/").replace("\\u0026", "&")
        current = unquote(current)
        if current not in variants:
            variants.append(current)
    return variants


def decode_bing_redirect(url):
    match = BING_U_RE.search(url or "")
    if not match:
        return None
    payload = match.group(1)
    try:
        payload += "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    except Exception:
        return None


def extract_ids(document):
    found = []
    seen = set()
    values = decoded_variants(document)
    for value in list(values):
        for href in HREF_RE.findall(value):
            decoded = decode_bing_redirect(html.unescape(href))
            if decoded:
                values.extend(decoded_variants(decoded))
            values.extend(decoded_variants(href))
    for value in values:
        for pattern in MAP_PATTERNS:
            for match in pattern.finditer(value):
                map_id = match.group(1)
                if map_id not in seen:
                    seen.add(map_id)
                    found.append(map_id)
    return found


def search_queries(region):
    name = region["name"]
    aliases = region.get("aliases") or []
    preferred = aliases[0] if aliases else name
    if region["category"] == "us":
        return [
            f'"{name} Statewide Siren Map" "google.com/maps/d"',
            f'"{name} Siren Map" "mid="',
        ]
    if region["category"] == "canada":
        return [
            f'"{preferred} Siren Map" Canada "google.com/maps/d"',
            f'"{preferred}" siren map "mid="',
        ]
    return [
        f'"{preferred} Siren Map" "google.com/maps/d"',
        f'"{preferred}" warning siren map "mid="',
    ]


def search_region(region):
    candidates = []
    errors = []
    seen = set()
    for query in search_queries(region):
        encoded = quote_plus(query)
        for engine, template in ENGINES:
            try:
                document = get_text(template.format(query=encoded))
                ids = extract_ids(document)
                for map_id in ids:
                    if map_id not in seen:
                        seen.add(map_id)
                        candidates.append({"mapId": map_id, "engine": engine, "query": query})
                if len(candidates) >= 12:
                    break
            except Exception as exc:
                errors.append(f"{engine}: {exc}")
        if len(candidates) >= 12:
            break
    return candidates[:12], errors


def haversine(lat1, lon1, lat2, lon2):
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def allowed_radius(region):
    name = region["name"]
    if region["category"] == "us":
        if name == "Alaska":
            return 2600
        if name == "Hawaii":
            return 700
        if name in {"Texas", "California", "Montana"}:
            return 1200
        return 750
    if region["category"] == "canada":
        if name == "Canada":
            return 4200
        if name in {"British Columbia", "Ontario", "Quebec"}:
            return 1900
        return 1400
    if region["category"] == "regional":
        if "South America" in name:
            return 5200
        if "Europe" in name:
            return 3500
        if "Portugal" in name:
            return 1600
        return 900
    if name == "Australia":
        return 3500
    if name in {"Chile", "Greenland"}:
        return 3000
    if name in {"Sweden", "Finland", "Türkiye"}:
        return 1900
    return 1400


def validate_candidate(region, candidate):
    source = {
        "name": f"{region['name']} Siren Map",
        "mapId": candidate["mapId"],
        "url": f"https://www.google.com/maps/d/viewer?mid={candidate['mapId']}",
        "directory": True,
        "section": region["category"],
    }
    pins = import_google_map(source)
    coords = []
    for feature in pins:
        point = (feature.get("geometry") or {}).get("coordinates") or []
        if len(point) >= 2:
            coords.append((float(point[1]), float(point[0])))
    if not coords:
        raise RuntimeError("candidate exported no point placemarks")
    # Deterministic sampling prevents giant maps from dominating validation time.
    if len(coords) > 5000:
        step = max(1, len(coords) // 5000)
        coords = coords[::step][:5000]
    distances = sorted(haversine(region["lat"], region["lon"], lat, lon) for lat, lon in coords)
    median = distances[len(distances) // 2]
    radius = allowed_radius(region)
    near = sum(distance <= radius * 1.5 for distance in distances) / len(distances)
    accepted = median <= radius and near >= 0.55
    return {
        "accepted": accepted,
        "pins": len(pins),
        "sampledPins": len(coords),
        "medianDistanceKm": round(median, 1),
        "nearFraction": round(near, 3),
        "radiusKm": radius,
        "source": source,
    }


def resolve_region(region, known_ids):
    candidates, search_errors = search_region(region)
    candidates = [candidate for candidate in candidates if candidate["mapId"] not in known_ids]
    validations = []
    for candidate in candidates:
        try:
            result = validate_candidate(region, candidate)
            validations.append({**candidate, **result})
            if result["accepted"]:
                return {"region": region, "chosen": result["source"], "validations": validations, "errors": search_errors}
        except Exception as exc:
            validations.append({**candidate, "accepted": False, "error": str(exc)})
    return {"region": region, "chosen": None, "validations": validations, "errors": search_errors}


def main():
    regions = read_json(REGIONS_PATH, [])
    registry = read_json(REGISTRY_PATH, [])
    status = read_json(DATA / "status.json", {})
    successful_ids = {item.get("mapId") for item in status.get("successfulSources", []) if item.get("mapId")}
    categories = {item.strip() for item in os.environ.get("RESOLVE_CATEGORIES", "canada,world,regional").split(",") if item.strip()}
    selected = [region for region in regions if region.get("category") in categories]
    unresolved = []
    for region in selected:
        working = [source for source in registry if source_matches_region(source, region) and source.get("mapId") in successful_ids]
        if not working:
            unresolved.append(region)

    print(f"Resolving {len(unresolved)} unresolved regions in categories {sorted(categories)}", flush=True)
    reports = []
    workers = min(5, max(1, len(unresolved)))
    known_ids = {source.get("mapId") for source in registry if source.get("mapId")}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="map-search") as pool:
        futures = {pool.submit(resolve_region, region, known_ids): region for region in unresolved}
        for index, future in enumerate(as_completed(futures), 1):
            region = futures[future]
            try:
                report = future.result()
            except Exception as exc:
                report = {"region": region, "chosen": None, "validations": [], "errors": [str(exc)]}
            reports.append(report)
            chosen = report.get("chosen")
            print(f"[{index}/{len(unresolved)}] {region['name']}: " + (f"resolved {chosen['mapId']}" if chosen else "unresolved"), flush=True)

    # Replace only matching failed/obsolete entries; preserve all unrelated sources.
    chosen_by_region = {report["region"]["id"]: report.get("chosen") for report in reports if report.get("chosen")}
    output = []
    replaced_ids = set()
    for source in registry:
        matched = None
        for region in unresolved:
            if source_matches_region(source, region) and chosen_by_region.get(region["id"]):
                matched = region
                break
        if matched:
            if matched["id"] not in replaced_ids:
                output.append(chosen_by_region[matched["id"]])
                replaced_ids.add(matched["id"])
        else:
            output.append(source)
    for region in unresolved:
        chosen = chosen_by_region.get(region["id"])
        if chosen and region["id"] not in replaced_ids:
            output.append(chosen)
            replaced_ids.add(region["id"])

    unique = {}
    for source in output:
        map_id = source.get("mapId")
        if map_id:
            unique[map_id] = source
    output = sorted(unique.values(), key=lambda item: (item.get("section", ""), item.get("name", "").lower()))
    REGISTRY_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    resolved = [report for report in reports if report.get("chosen")]
    report_doc = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "categories": sorted(categories),
        "selectedRegionCount": len(selected),
        "attemptedRegionCount": len(unresolved),
        "resolvedRegionCount": len(resolved),
        "remainingUnresolved": [report["region"]["name"] for report in reports if not report.get("chosen")],
        "registryCount": len(output),
        "results": reports,
    }
    REPORT_PATH.write_text(json.dumps(report_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report_doc.items() if key != "results"}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
