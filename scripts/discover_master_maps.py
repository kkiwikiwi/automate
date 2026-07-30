#!/usr/bin/env python3
import gzip
import html
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb
from warcio.archiveiterator import ArchiveIterator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
MASTER_URL = "https://www.airraidsirens.net/forums/viewtopic.php?t=27945"
USER_AGENT = "SirenFinder/10.3 (+https://github.com/kkiwikiwi/automate)"
CRAWLS = ["CC-MAIN-2026-25", "CC-MAIN-2026-21", "CC-MAIN-2026-17", "CC-MAIN-2025-51"]
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


def fetch_bytes(url, timeout=120, limit=100_000_000, headers=None):
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    with urlopen(Request(url, headers=request_headers), timeout=timeout) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError("response too large")
    return payload


def clean(value):
    value = BR_RE.sub("\n", value or "")
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n\s+", "\n", value).strip()


def map_id(url):
    match = MAP_ID.search(html.unescape(url or ""))
    return match.group(1) if match else None


def master_slice(document):
    decoded = html.unescape(document or "")
    starts = [decoded.find("This thread lists exhaustive siren maps"), decoded.find("United States-")]
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
    choices = {
        "United States": chunk.rfind("United States-", 0, position),
        "Canada": chunk.rfind("Canada-", 0, position),
        "Other Countries": chunk.rfind("Other Countries-", 0, position),
    }
    section, marker = max(choices.items(), key=lambda item: item[1])
    return section if marker >= 0 else "Other"


def label_before(chunk, position, fallback):
    boundary = max(
        chunk.rfind("<br", 0, position), chunk.rfind("</p", 0, position),
        chunk.rfind("\n", 0, position), chunk.rfind(">", 0, position - 1),
    )
    sample = clean(chunk[max(0, boundary):position])
    lines = [line.strip(" >-*#|\t") for line in sample.splitlines() if line.strip()]
    candidate = lines[-1] if lines else ""
    candidate = re.sub(r"https?://.*$", "", candidate).strip(" :-")
    if not candidate or len(candidate) > 140:
        candidate = clean(fallback)
    return (candidate if candidate and not candidate.lower().startswith("http") else "Unnamed master-list map")[:140]


def parse_registry(document):
    chunk = master_slice(document)
    output, seen = [], set()
    for regex in (ANCHOR_RE, URL_RE):
        for match in regex.finditer(chunk):
            href = html.unescape(match.group(1) if regex is ANCHOR_RE else match.group(0)).rstrip(".,);]")
            mid = map_id(href)
            if not mid or mid in seen:
                continue
            seen.add(mid)
            output.append({
                "name": label_before(chunk, match.start(), match.group(2) if regex is ANCHOR_RE else ""),
                "mapId": mid, "url": href, "directory": True,
                "section": section_for(match.start(), chunk),
            })
    return output


def normalized(value):
    value = re.sub(r"\([^)]*\)", "", value or "")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def has_name(name, names):
    wanted = normalized(name)
    return any(wanted == found or wanted in found or found in wanted for found in names if found)


def index_paths(crawl):
    listing_url = f"https://data.commoncrawl.org/crawl-data/{crawl}/cc-index-table.paths.gz"
    listing = gzip.decompress(fetch_bytes(listing_url, timeout=120, limit=10_000_000)).decode("utf-8")
    paths = [line.strip() for line in listing.splitlines() if "/subset=warc/" in line]
    if not paths:
        raise RuntimeError(f"no WARC URL-index Parquet paths for {crawl}")
    return ["https://data.commoncrawl.org/" + path for path in paths]


def sql_list(values):
    return "[" + ",".join("'" + value.replace("'", "''") + "'" for value in values) + "]"


def query_url_index(crawl):
    urls = index_paths(crawl)
    print(f"Querying {len(urls)} URL-index files for {crawl}", flush=True)
    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs")
    con.execute("SET enable_http_metadata_cache=true")
    con.execute("SET threads=6")
    con.execute("SET memory_limit='5GB'")
    query = f"""
        SELECT url, fetch_time, warc_filename, warc_record_offset, warc_record_length
        FROM read_parquet({sql_list(urls)}, hive_partitioning=true, union_by_name=true)
        WHERE url_host_registered_domain = 'airraidsirens.net'
          AND url_path = '/forums/viewtopic.php'
          AND fetch_status = 200
          AND (
               strpos(coalesce(url_query, ''), 't=27945') > 0
            OR strpos(coalesce(url_query, ''), 'p=232754') > 0
          )
        ORDER BY fetch_time DESC
        LIMIT 50
    """
    rows = con.execute(query).fetchall()
    con.close()
    return rows


def fetch_warc_record(row):
    url, fetch_time, filename, offset, length = row
    start, size = int(offset), int(length)
    payload = fetch_bytes(
        "https://data.commoncrawl.org/" + filename,
        timeout=180,
        limit=size + 4096,
        headers={"Range": f"bytes={start}-{start + size - 1}"},
    )
    for record in ArchiveIterator(io.BytesIO(payload)):
        if record.rec_type == "response":
            body = record.content_stream().read().decode("utf-8", "replace")
            return f"url-index:{fetch_time}:{url}", body, parse_registry(body)
    raise RuntimeError("WARC response record not found")


def discover():
    errors, candidates = [], []
    for crawl in CRAWLS:
        try:
            rows = query_url_index(crawl)
            print(f"{crawl}: {len(rows)} matching URL-index records", flush=True)
        except Exception as exc:
            errors.append(f"URL Index {crawl}: {exc}")
            continue
        for row in rows:
            try:
                method, document, parsed = fetch_warc_record(row)
                print(f"{method}: {len(parsed)} map links", flush=True)
                candidates.append((method, document, parsed))
                if len(parsed) >= 65 and has_name("Canada", {normalized(item.get('name')) for item in parsed}):
                    return candidates, errors
            except Exception as exc:
                errors.append(f"WARC {row[0]}: {exc}")
        if candidates:
            best = max(candidates, key=lambda item: len(item[2]))
            if len(best[2]) >= 65:
                break
    return candidates, errors


def main():
    candidates, errors = discover()
    candidates.sort(key=lambda item: len(item[2]), reverse=True)
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
    report = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "masterUrl": MASTER_URL,
        "method": method,
        "candidateCount": len(candidates),
        "discoveredCount": len(discovered),
        "registryCount": len(registry),
        "canadaFoundInDiscovery": has_name("Canada", discovered_names),
        "missingExpectedNames": sorted(name for name in EXPECTED if not has_name(name, names)),
        "errors": errors[-50:],
    }
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "master-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["discoveredCount"] >= 65 and report["canadaFoundInDiscovery"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
