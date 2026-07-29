#!/usr/bin/env python3
import html
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
MASTER = [
    "https://www.airraidsirens.net/forums/viewtopic.php?t=27945",
    "https://r.jina.ai/https://www.airraidsirens.net/forums/viewtopic.php?t=27945",
    "https://r.jina.ai/http://www.airraidsirens.net/forums/viewtopic.php?t=27945",
]
USA = "1AA5lVxum02jYm0T3jDepjuNctwVPf51j"
MID = re.compile(r"(?:[?&](?:mid|id)=|/earth/d/|/d/)([\w-]{15,})", re.I)
TAG = re.compile(r"<[^>]+>")
A = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
MD = re.compile(r"\[([^]]*)\]\((https?://[^)]+)\)")
URL = re.compile(r"https?://[^\s<>\"']+")
TEST_PATTERNS = [
    re.compile(r"\b(?:tested|testing|test)\b[^.;\n]{0,170}", re.I),
    re.compile(r"\b(?:first|second|third|fourth|last)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b[^.;\n]{0,120}", re.I),
    re.compile(r"\b(?:every|each)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|month|week)\b[^.;\n]{0,120}", re.I),
    re.compile(r"\b(?:noon|monthly|weekly|quarterly|annual(?:ly)?)\b[^.;\n]{0,120}\b(?:test|testing|tested)\b", re.I),
]
MODEL_WORDS = re.compile(
    r"\b(?:Federal Signal|Federal Sign(?: and Signal)?|American Signal(?: Corporation)?|ASC|Whelen|ACA|Sentry|Sterling|Darley|ATI|Hörmann|Horman|Telegrafia|Siemens|Elektror|Klaxon|Carter|Castle Castings|Chrysler|CLM|Decot|Erick|Loudoun|TOA|Yamaha|EOWS|Thunderbolt|Modulator|Alertronic|Allertor|Cyclone|P-?50|P-?10|T-?128|3T22|2T22|2001(?:-\d+)?|Model\s+[A-Z0-9-]+|WPS-?\d+|Vortex|E-Class|OM-?120|ECN-?\d+|DSA-?\d+|HPSS-?\d+|STH-?10[A-Z]?|STL-?10|SD-?10|Siro-?Drone|SiraTone|10V2T|20V2T|3V8(?:-H)?|Model\s+M)\b[^,;|]{0,60}",
    re.I,
)
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]
OVERPASS_BOXES = [
    (0, -180, 85, -20),
    (-60, -180, 0, -20),
    (0, -20, 85, 60),
    (-60, -20, 0, 60),
    (0, 60, 85, 180),
    (-60, 60, 0, 180),
]


def get(url, limit=300_000_000, tries=4, data=None, content_type=None):
    last = None
    for n in range(tries):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; SirenFinder/3.0; +https://github.com/kkiwikiwi/automate)",
                "Accept": "*/*",
            }
            if content_type:
                headers["Content-Type"] = content_type
            req = Request(url, headers=headers, data=data)
            with urlopen(req, timeout=180) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise RuntimeError("file too large")
                return body
        except Exception as exc:
            last = exc
            if n == tries - 1:
                raise
            time.sleep(2**n)
    raise RuntimeError(last or "download failed")


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", value or ""))).strip()


def map_id(url):
    match = MID.search(html.unescape(url or ""))
    return match.group(1) if match else None


def master_section(text):
    starts = [text.find(x) for x in ("United States-", "United States -", "United States") if text.find(x) >= 0]
    if not starts:
        return text
    start = min(starts)
    ends = [
        text.find(x, start + 500)
        for x in ("Last edited by", "### Re:", "Re: Master list", "Post Reply")
        if text.find(x, start + 500) > start
    ]
    return text[start : min(ends) if ends else None]


def discover_maps():
    errors = []
    found = []
    for source_url in MASTER:
        try:
            text = master_section(get(source_url, 35_000_000).decode("utf-8", "replace"))
            candidates = []
            for regex in (A, MD):
                for match in regex.finditer(text):
                    url = html.unescape(match.group(1) if match.group(1).startswith("http") else match.group(2))
                    label = match.group(2) if match.group(1).startswith("http") else match.group(1)
                    if map_id(url):
                        candidates.append((match.start(), url, label))
            for match in URL.finditer(text):
                url = html.unescape(match.group(0).rstrip(".,);]"))
                if map_id(url):
                    candidates.append((match.start(), url, ""))
            seen = set()
            output = []
            for position, url, label in sorted(candidates):
                mid = map_id(url)
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                line = clean(text[text.rfind("\n", 0, position) + 1 : position])
                name = (line.rsplit(":", 1)[0] if ":" in line else clean(label)) or f"Map {len(output) + 1}"
                output.append(
                    {
                        "name": name.strip(" -*#>:|")[:100],
                        "mapId": mid,
                        "url": url,
                        "directory": True,
                    }
                )
            if len(output) >= 40:
                found = output
                break
            errors.append(f"{source_url}: only {len(output)} maps")
        except Exception as exc:
            errors.append(f"{source_url}: {exc}")

    registry = DATA / "source-registry.json"
    if registry.exists():
        try:
            found += json.loads(registry.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"source-registry.json: {exc}")
    found.append(
        {
            "name": "Official U.S.A. Siren Map",
            "mapId": USA,
            "url": f"https://www.google.com/maps/d/viewer?mid={USA}",
            "directory": False,
        }
    )
    unique = {}
    for source in found:
        if source.get("mapId"):
            unique[source["mapId"]] = source
    return sorted(unique.values(), key=lambda x: (not x.get("directory", True), x["name"].lower())), errors


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def extract_testing(text):
    text = clean(text)
    candidates = []
    for pattern in TEST_PATTERNS:
        candidates.extend(match.group(0).strip(" ,:-") for match in pattern.finditer(text))
    candidates = list(dict.fromkeys(x for x in candidates if 5 <= len(x) <= 190))
    return candidates[0] if candidates else ""


def extract_model(name, description):
    text = clean(f"{name} {description}")
    match = MODEL_WORDS.search(text)
    if not match:
        return ""
    value = re.sub(r"\s+", " ", match.group(0)).strip(" -,:;|")
    return value[:140]


def details_from_text(name, description):
    return extract_model(name, description), extract_testing(f"{name}. {description}")


def parse_kml(data, source):
    if data[:2] == b"PK":
        pins, links = [], []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for filename in archive.namelist():
                if filename.lower().endswith(".kml"):
                    parsed, nested = parse_kml(archive.read(filename), source)
                    pins += parsed
                    links += nested
        return pins, links

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
        description = ""
        coordinates = ""
        extended = {}
        for child in element.iter():
            text = (child.text or "").strip()
            key = local_name(child.tag)
            if key == "name" and text and name == "Unnamed siren":
                name = clean(text)[:220]
            elif key == "description" and text and not description:
                description = clean(text)[:1200]
            elif key == "coordinates" and text and not coordinates:
                coordinates = text
            elif key == "data" and child.attrib.get("name"):
                value = ""
                for grandchild in child.iter():
                    if local_name(grandchild.tag) == "value" and (grandchild.text or "").strip():
                        value = clean(grandchild.text)
                        break
                if value:
                    extended[child.attrib["name"]] = value
        if not coordinates:
            continue
        try:
            lon, lat = map(float, coordinates.split()[0].split(",")[:2])
        except Exception:
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        extra_text = " · ".join(f"{k}: {v}" for k, v in extended.items())
        full_description = clean(" · ".join(x for x in (description, extra_text) if x))[:1600]
        siren_type, testing = details_from_text(name, full_description)
        pins.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
                "properties": {
                    "name": name,
                    "description": full_description,
                    "sirenType": siren_type,
                    "testingSchedule": testing,
                    "source": source["name"],
                    "sourceUrl": source["url"],
                },
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
                    pins += nested
                except Exception as exc:
                    print(" network link failed", exc, flush=True)
            if pins:
                return pins
        except Exception as exc:
            last = exc
    raise RuntimeError(last or "no point placemarks")


def osm_value(tags, *keys):
    for key in keys:
        value = tags.get(key)
        if value:
            return clean(str(value))
    return ""


def osm_testing(tags):
    for key, value in tags.items():
        lowered = key.lower()
        if "test" in lowered or "schedule" in lowered:
            return clean(str(value))[:190]
    return extract_testing(" ".join(str(value) for value in tags.values()))


def osm_feature(element):
    tags = element.get("tags") or {}
    lat = element.get("lat")
    lon = element.get("lon")
    center = element.get("center") or {}
    if lat is None:
        lat = center.get("lat")
    if lon is None:
        lon = center.get("lon")
    if lat is None or lon is None:
        return None
    model = osm_value(tags, "siren:model", "model")
    manufacturer = osm_value(tags, "manufacturer", "brand")
    mechanism = osm_value(tags, "siren:type")
    type_parts = []
    if manufacturer:
        type_parts.append(manufacturer)
    if model and model.lower() not in manufacturer.lower():
        type_parts.append(model)
    if mechanism and mechanism.lower() not in " ".join(type_parts).lower():
        type_parts.append(mechanism.replace("_", " "))
    siren_type = " · ".join(type_parts)
    name = osm_value(tags, "name") or siren_type or "Outdoor warning siren"
    purpose = osm_value(tags, "siren:purpose").replace("_", " ")
    operator = osm_value(tags, "operator")
    status = osm_value(tags, "operational_status", "status", "disused")
    notes = osm_value(tags, "description", "note", "fixme")
    desc_parts = []
    if purpose:
        desc_parts.append(f"Purpose: {purpose}")
    if operator:
        desc_parts.append(f"Operator: {operator}")
    if status:
        desc_parts.append(f"Status: {status}")
    if notes:
        desc_parts.append(notes)
    element_type = element.get("type", "node")
    element_id = element.get("id")
    source_url = f"https://www.openstreetmap.org/{element_type}/{element_id}"
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(float(lon), 7), round(float(lat), 7)]},
        "properties": {
            "name": name[:220],
            "description": " · ".join(desc_parts)[:1600],
            "sirenType": siren_type[:140],
            "testingSchedule": osm_testing(tags),
            "source": "OpenStreetMap worldwide sirens",
            "sourceUrl": source_url,
        },
    }


def overpass_request(query):
    payload = urlencode({"data": query}).encode("utf-8")
    last = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            return json.loads(
                get(endpoint, limit=120_000_000, tries=2, data=payload, content_type="application/x-www-form-urlencoded").decode(
                    "utf-8", "replace"
                )
            )
        except Exception as exc:
            last = exc
            print(" Overpass endpoint failed", endpoint, exc, flush=True)
    raise RuntimeError(last or "all Overpass endpoints failed")


def import_openstreetmap():
    pins = []
    for south, west, north, east in OVERPASS_BOXES:
        bbox = f"{south},{west},{north},{east}"
        query = (
            "[out:json][timeout:360];("
            f'nwr["emergency"="siren"]({bbox});'
            f'nwr["man_made"="siren"]({bbox});'
            ");out center tags;"
        )
        data = overpass_request(query)
        for element in data.get("elements", []):
            feature = osm_feature(element)
            if feature:
                pins.append(feature)
        print(" OSM region", bbox, "pins", len(data.get("elements", [])), flush=True)
        time.sleep(2)
    return pins


def merge(all_pins):
    output = {}
    for feature in all_pins:
        lon, lat = feature["geometry"]["coordinates"]
        key = (round(lon, 5), round(lat, 5))
        props = feature["properties"]
        if key not in output:
            props["sources"] = [props["source"]]
            props["sourceUrls"] = [props["sourceUrl"]]
            output[key] = feature
            continue
        existing = output[key]["properties"]
        if props["source"] not in existing["sources"]:
            existing["sources"].append(props["source"])
        if props["sourceUrl"] not in existing["sourceUrls"]:
            existing["sourceUrls"].append(props["sourceUrl"])
        if len(props.get("name", "")) > len(existing.get("name", "")):
            existing["name"] = props["name"]
        if len(props.get("description", "")) > len(existing.get("description", "")):
            existing["description"] = props["description"]
        if not existing.get("sirenType") and props.get("sirenType"):
            existing["sirenType"] = props["sirenType"]
        if not existing.get("testingSchedule") and props.get("testingSchedule"):
            existing["testingSchedule"] = props["testingSchedule"]
    return list(output.values())


def main():
    sources, discovery_errors = discover_maps()
    all_pins, successful, failed = [], [], []
    print("Google map sources", len(sources), flush=True)
    for index, source in enumerate(sources, 1):
        try:
            pins = import_google_map(source)
            all_pins += pins
            successful.append({**source, "pins": len(pins)})
            print(index, source["name"], len(pins), flush=True)
        except Exception as exc:
            failed.append({**source, "error": str(exc)})
            print(index, source["name"], "FAILED", exc, flush=True)

    try:
        osm_pins = import_openstreetmap()
        all_pins += osm_pins
        successful.append(
            {
                "name": "OpenStreetMap worldwide sirens",
                "url": "https://www.openstreetmap.org",
                "directory": False,
                "pins": len(osm_pins),
            }
        )
    except Exception as exc:
        failed.append(
            {
                "name": "OpenStreetMap worldwide sirens",
                "url": "https://www.openstreetmap.org",
                "directory": False,
                "error": str(exc),
            }
        )

    pins = merge(all_pins)
    now = datetime.now(timezone.utc).isoformat()
    directory_total = sum(bool(source.get("directory")) for source in sources)
    directory_success = sum(bool(source.get("directory")) for source in successful)
    metadata = {
        "updatedAt": now,
        "pinCount": len(pins),
        "sourceCount": len(successful),
        "directorySourceCount": directory_success,
        "directorySourceTotal": directory_total,
        "failedSourceCount": len(failed),
    }
    geojson = {"type": "FeatureCollection", "metadata": metadata, "features": pins}
    status = {
        "ok": bool(pins),
        **metadata,
        "message": (
            f"Loaded {len(pins):,} unique sirens from {len(successful)} datasets "
            f"({directory_success}/{directory_total} master-directory maps)."
            if pins
            else "No public siren pin data could be retrieved."
        ),
        "discoveryErrors": discovery_errors,
        "successfulSources": successful,
        "failedSources": failed,
    }
    (DATA / "pins.geojson").write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "source-registry.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    print(status["message"], flush=True)
    return 0 if pins else 1


if __name__ == "__main__":
    raise SystemExit(main())
