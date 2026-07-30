#!/usr/bin/env python3
import gzip
import html
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
MASTER_URL = "https://www.airraidsirens.net/forums/viewtopic.php?t=27945"
USER_AGENT = "Mozilla/5.0 (compatible; SirenFinder/10.0; +https://github.com/kkiwikiwi/automate)"
MAP_ID = re.compile(r"(?:[?&](?:mid|id)=|/earth/d/|/d/)([A-Za-z0-9_-]{15,})", re.I)
ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<(?:br\s*/?|/p|/div|/li|/blockquote|/article)>\s*", re.I)
EXPECTED = {
    "Canada", "Alberta", "British Columbia", "Manitoba", "Ontario", "Quebec", "Saskatchewan",
    "Australia", "Croatia", "Finland", "France", "Greenland", "Hungary", "Italy", "Kuwait",
    "Netherlands", "Turkiye", "Sweden", "United Kingdom", "Europe", "South America",
    "American Samoa", "Guam & NMI", "U.S. Virgin Islands & BVI", "Country of Niue",
    "Puerto Rico", "Thailand", "Portugal and Spain", "Chile", "Malta", "Latvia",
}


def request_bytes(url, timeout=60, limit=20_000_000, headers=None):
    base = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        base.update(headers)
    with urlopen(Request(url, headers=base), timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("response too large")
    return data


def request_text(url, timeout=60, limit=20_000_000):
    return request_bytes(url, timeout, limit).decode("utf-8", "replace")


def clean(value):
    value = BR_RE.sub("\n", value or "")
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return value.strip()


def map_id(url):
    match = MAP_ID.search(html.unescape(url or ""))
    return match.group(1) if match else None


def master_slice(document):
    decoded = html.unescape(document or "")
    starts = [
        decoded.find("This thread lists exhaustive siren maps"),
        decoded.find("United States-"),
    ]
    starts = [value for value in starts if value >= 0]
    if not starts:
        return decoded
    start = min(starts)
    ends = [
        decoded.find("Last edited by", start),
        decoded.find("Re: Master list of State/Country siren maps", start + 1500),
        decoded.find("<article", start + 1500),
    ]
    ends = [value for value in ends if value > start]
    end = min(ends) if ends else min(len(decoded), start + 1_000_000)
    return decoded[max(0, start - 3000):end]


def section_for(position, chunk):
    positions = {
        "United States": chunk.rfind("United States-", 0, position),
        "Canada": chunk.rfind("Canada-", 0, position),
        "Other Countries": chunk.rfind("Other Countries-", 0, position),
    }
    section, value = max(positions.items(), key=lambda item: item[1])
    return section if value >= 0 else "Other"


def label_before(chunk, position, fallback):
    boundary = max(
        chunk.rfind("<br", 0, position),
        chunk.rfind("</p", 0, position),
        chunk.rfind("\n", 0, position),
        chunk.rfind(">", 0, position - 1),
    )
    sample = clean(chunk[max(0, boundary):position])
    lines = [line.strip(" >-*#|\t") for line in sample.splitlines() if line.strip()]
    candidate = lines[-1] if lines else ""
    candidate = re.sub(r"https?://.*$", "", candidate).strip(" :-")
    if not candidate or len(candidate) > 140:
        candidate = clean(fallback)
    if not candidate or candidate.lower().startswith("http"):
        candidate = "Unnamed master-list map"
    return candidate[:140]


def parse_registry(document):
    chunk = master_slice(document)
    output, seen = [], set()
    for match in ANCHOR_RE.finditer(chunk):
        href = html.unescape(match.group(1))
        mid = map_id(href)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        output.append({
            "name": label_before(chunk, match.start(), match.group(2)),
            "mapId": mid,
            "url": href,
            "directory": True,
            "section": section_for(match.start(), chunk),
        })
    for match in URL_RE.finditer(chunk):
        href = html.unescape(match.group(0).rstrip(".,);]"))
        mid = map_id(href)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        output.append({
            "name": label_before(chunk, match.start(), ""),
            "mapId": mid,
            "url": href,
            "directory": True,
            "section": section_for(match.start(), chunk),
        })
    return output


def normalized(value):
    value = re.sub(r"\([^)]*\)", "", value or "")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def has_name(name, names):
    wanted = normalized(name)
    return any(wanted == found or wanted in found or found in wanted for found in names if found)


def decode_warc(payload):
    try:
        raw = gzip.decompress(payload)
    except Exception:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
            raw = archive.read()
    return raw.decode("utf-8", "replace")


def crawl_indexes():
    rows = json.loads(request_text("https://index.commoncrawl.org/collinfo.json", timeout=45, limit=2_000_000))
    return [row["id"] for row in rows[:16] if row.get("id")]


def query_records(index_id, target):
    endpoint = (
        f"https://index.commoncrawl.org/{index_id}-index?url="
        + quote(target, safe="")
        + "&output=json&filter=status:200&filter=mime:text/html"
    )
    body = request_text(endpoint, timeout=45, limit=5_000_000)
    return [json.loads(line) for line in body.splitlines() if line.strip().startswith("{")]


def fetch_record(record):
    offset = int(record["offset"])
    length = int(record["length"])
    payload = request_bytes(
        "https://data.commoncrawl.org/" + record["filename"],
        timeout=90,
        limit=length + 1024,
        headers={"Range": f"bytes={offset}-{offset + length - 1}"},
    )
    return decode_warc(payload)


def quality(document):
    lower = (document or "").lower()
    return (
        20 * (lower.count("google.com/maps/d") + lower.count("google.ca/maps/d"))
        + 20 * lower.count("earth.google.com/earth/d")
        + (1500 if "united states-" in lower else 0)
        + (1500 if "canada-" in lower else 0)
        + (1500 if "other countries-" in lower else 0)
    )


def discover():
    targets = [
        MASTER_URL,
        "https://airraidsirens.net/forums/viewtopic.php?t=27945",
        "https://www.airraidsirens.net/forums/viewtopic.php?p=232754",
        "https://airraidsirens.net/forums/viewtopic.php?p=232754",
    ]
    candidates, errors, seen = [], [], set()
    for index_id in crawl_indexes():
        for target in targets:
            try:
                records = query_records(index_id, target)
            except Exception as exc:
                errors.append(f"{index_id} query {target}: {exc}")
                continue
            for record in reversed(records[-4:]):
                key = (record.get("filename"), record.get("offset"), record.get("length"))
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                try:
                    document = fetch_record(record)
                    parsed = parse_registry(document)
                    method = f"commoncrawl:{index_id}:{record.get('timestamp')}:{target}"
                    print(f"{method}: {len(parsed)} map links, quality {quality(document)}", flush=True)
                    candidates.append((method, document, parsed))
                    if len(parsed) >= 80:
                        return candidates, errors
                except Exception as exc:
                    errors.append(f"{index_id} fetch {key}: {exc}")
    return candidates, errors


def main():
    candidates, errors = discover()
    candidates.sort(key=lambda item: (len(item[2]), quality(item[1])), reverse=True)
    method, _, discovered = candidates[0] if candidates else (None, "", [])

    registry_path = DATA / "source-registry.json"
    try:
        existing = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        existing = []
    merged = {}
    for source in existing + discovered:
        mid = source.get("mapId")
        if not mid:
            continue
        item = dict(source)
        item.setdefault("directory", True)
        item.setdefault("section", "Supplemental")
        item["url"] = item.get("url") or f"https://www.google.com/maps/d/viewer?mid={mid}"
        merged[mid] = item
    registry = sorted(merged.values(), key=lambda item: (item.get("section", ""), item.get("name", "").lower()))
    names = {normalized(item.get("name")) for item in registry}
    discovered_names = {normalized(item.get("name")) for item in discovered}
    missing = sorted(name for name in EXPECTED if not has_name(name, names))
    canada_found = has_name("Canada", discovered_names)
    report = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "masterUrl": MASTER_URL,
        "method": method,
        "candidateCount": len(candidates),
        "discoveredCount": len(discovered),
        "registryCount": len(registry),
        "canadaFoundInDiscovery": canada_found,
        "missingExpectedNames": missing,
        "errors": errors[-50:],
    }
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "master-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if len(discovered) >= 65 and canada_found else 2


if __name__ == "__main__":
    raise SystemExit(main())
