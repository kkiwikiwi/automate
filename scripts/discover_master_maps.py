#!/usr/bin/env python3
import gzip
import html
import io
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
MASTER_URL = "https://www.airraidsirens.net/forums/viewtopic.php?t=27945"
HOME_URL = "https://www.airraidsirens.net/forums/"
EXPECTED = {
    "Canada", "Alberta", "British Columbia", "Manitoba", "Ontario", "Quebec", "Saskatchewan",
    "Australia", "Croatia", "Finland", "France", "Greenland", "Hungary", "Italy", "Kuwait",
    "Netherlands", "Turkiye", "Sweden", "United Kingdom", "Europe", "South America",
    "American Samoa", "Guam & NMI", "U.S. Virgin Islands & BVI", "Country of Niue",
    "Puerto Rico", "Thailand", "Portugal and Spain", "Chile", "Malta", "Latvia",
}
MAP_ID = re.compile(r"(?:[?&](?:mid|id)=|/earth/d/|/d/)([A-Za-z0-9_-]{15,})", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<(?:br\s*/?|/p|/div|/li|/blockquote|/article)>\s*", re.I)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def clean(value):
    value = BR_RE.sub("\n", value or "")
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return value.strip()


def map_id(url):
    match = MAP_ID.search(html.unescape(url or ""))
    return match.group(1) if match else None


def browser_headers(referer=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def fetch_text(url, timeout=90, limit=50_000_000, headers=None):
    request = Request(url, headers=headers or browser_headers())
    with urlopen(request, timeout=timeout) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise RuntimeError("response exceeded size limit")
    return body.decode("utf-8", "replace")


def urllib_candidates(errors):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        opener.open(Request(HOME_URL, headers=browser_headers()), timeout=45).read(500_000)
    except Exception as exc:
        errors.append(f"homepage warmup: {exc}")
    urls = [
        MASTER_URL,
        MASTER_URL + "&view=print",
        MASTER_URL + "&view=printable",
        "https://www.airraidsirens.net/forums/feed.php?t=27945",
        "https://www.airraidsirens.net/forums/app.php/feed/topic/27945",
        "https://r.jina.ai/https://www.airraidsirens.net/forums/viewtopic.php?t=27945",
        "https://r.jina.ai/http://www.airraidsirens.net/forums/viewtopic.php?t=27945",
        "https://www-airraidsirens-net.translate.goog/forums/viewtopic.php?t=27945&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en",
    ]
    for url in urls:
        try:
            request = Request(url, headers=browser_headers(HOME_URL))
            with opener.open(request, timeout=90) as response:
                body = response.read(50_000_000).decode("utf-8", "replace")
            yield f"urllib:{url}", body
        except Exception as exc:
            errors.append(f"{url}: {exc}")


def curl_candidates(errors):
    curl = shutil.which("curl")
    if not curl:
        return
    cookie = "/tmp/sirenfinder-forum-cookies.txt"
    subprocess.run(
        [curl, "-fsSL", "-A", USER_AGENT, "-c", cookie, HOME_URL],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
    )
    urls = (
        MASTER_URL,
        MASTER_URL + "&view=print",
        MASTER_URL + "&view=printable",
        "https://www-airraidsirens-net.translate.goog/forums/viewtopic.php?t=27945&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en",
    )
    for url in urls:
        try:
            result = subprocess.run(
                [curl, "-fsSL", "--compressed", "-A", USER_AGENT, "-b", cookie, "-e", HOME_URL, url],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0:
                yield f"curl:{url}", result.stdout
            else:
                errors.append(f"curl {url}: exit {result.returncode}: {result.stderr[-300:]}")
        except Exception as exc:
            errors.append(f"curl {url}: {exc}")


def chrome_candidates(errors):
    chrome = next(
        (
            shutil.which(name)
            for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
            if shutil.which(name)
        ),
        None,
    )
    if not chrome:
        errors.append("headless Chrome/Chromium not installed")
        return
    profile = "/tmp/sirenfinder-chrome-profile"
    urls = (
        MASTER_URL,
        MASTER_URL + "&view=print",
        "https://www-airraidsirens-net.translate.goog/forums/viewtopic.php?t=27945&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en",
    )
    for url in urls:
        try:
            result = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    f"--user-agent={USER_AGENT}",
                    f"--user-data-dir={profile}",
                    "--virtual-time-budget=20000",
                    "--dump-dom",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if result.stdout:
                yield f"chrome:{url}", result.stdout
            else:
                errors.append(f"chrome {url}: exit {result.returncode}: {result.stderr[-500:]}")
        except Exception as exc:
            errors.append(f"chrome {url}: {exc}")


def wayback_candidates(errors):
    targets = [
        MASTER_URL,
        "https://airraidsirens.net/forums/viewtopic.php?t=27945",
        "https://airraidsirens.net/forums/viewtopic.php?p=232754",
    ]
    for target in targets:
        cdx = (
            "https://web.archive.org/cdx/search/cdx?url="
            + quote(target, safe="")
            + "&output=json&filter=statuscode:200&filter=mimetype:text/html"
            + "&fl=timestamp,original&collapse=digest&from=2021&to=2026&limit=50"
        )
        try:
            rows = json.loads(fetch_text(cdx, timeout=90, limit=5_000_000))
        except Exception as exc:
            errors.append(f"Wayback CDX {target}: {exc}")
            continue
        for row in reversed(rows[1:] if rows and isinstance(rows[0], list) else rows):
            try:
                timestamp, original = row[:2]
                replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
                yield f"wayback:{timestamp}:{original}", fetch_text(replay, timeout=120)
            except Exception as exc:
                errors.append(f"Wayback replay {row}: {exc}")


def commoncrawl_indexes(errors):
    try:
        indexes = json.loads(fetch_text("https://index.commoncrawl.org/collinfo.json", timeout=60, limit=2_000_000))
        return [item["id"] for item in indexes[:12] if item.get("id")]
    except Exception as exc:
        errors.append(f"Common Crawl index list: {exc}")
        return []


def decode_warc_payload(payload):
    try:
        raw = gzip.decompress(payload)
    except Exception:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
            raw = archive.read()
    # Keep the whole decoded WARC record: the registry parser safely ignores WARC/HTTP headers.
    return raw.decode("utf-8", "replace")


def commoncrawl_candidates(errors):
    targets = [
        MASTER_URL,
        "https://airraidsirens.net/forums/viewtopic.php?t=27945",
        "https://airraidsirens.net/forums/viewtopic.php?p=232754",
        "https://www.airraidsirens.net/forums/viewtopic.php?p=232754",
    ]
    seen_records = set()
    for index_id in commoncrawl_indexes(errors):
        for target in targets:
            query_url = (
                f"https://index.commoncrawl.org/{index_id}-index?url="
                + quote(target, safe="")
                + "&output=json&filter=status:200&filter=mime:text/html"
            )
            try:
                body = fetch_text(query_url, timeout=90, limit=10_000_000)
                records = [json.loads(line) for line in body.splitlines() if line.strip().startswith("{")]
            except Exception as exc:
                errors.append(f"Common Crawl query {index_id} {target}: {exc}")
                continue
            for record in reversed(records[-8:]):
                key = (record.get("filename"), record.get("offset"), record.get("length"))
                if not all(key) or key in seen_records:
                    continue
                seen_records.add(key)
                try:
                    offset = int(record["offset"])
                    length = int(record["length"])
                    request = Request(
                        "https://data.commoncrawl.org/" + record["filename"],
                        headers={**browser_headers(), "Range": f"bytes={offset}-{offset + length - 1}"},
                    )
                    with urlopen(request, timeout=180) as response:
                        payload = response.read(length + 1024)
                    document = decode_warc_payload(payload)
                    yield f"commoncrawl:{index_id}:{record.get('timestamp')}:{target}", document
                except Exception as exc:
                    errors.append(f"Common Crawl fetch {key}: {exc}")


def quality(text):
    lower = (text or "").lower()
    return (
        lower.count("google.com/maps/d") * 20
        + lower.count("google.ca/maps/d") * 20
        + lower.count("drive.google.com/open") * 20
        + lower.count("earth.google.com/earth/d") * 20
        + (1000 if "canada-" in lower else 0)
        + (1000 if "other countries-" in lower else 0)
        + (1000 if "united states-" in lower else 0)
        + min(len(text or "") // 1000, 500)
    )


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
    candidates = [
        decoded.find("Last edited by", start),
        decoded.find("Re: Master list of State/Country siren maps", start + 1000),
        decoded.find("<article", start + 1000),
    ]
    ends = [value for value in candidates if value > start]
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
    if not candidate or len(candidate) > 120:
        candidate = clean(fallback)
    if candidate.lower().startswith("http") or candidate == "":
        candidate = "Unnamed master-list map"
    return candidate[:140]


def parse_registry(document):
    chunk = master_slice(document)
    results = []
    seen = set()
    for match in ANCHOR_RE.finditer(chunk):
        href = html.unescape(match.group(1))
        mid = map_id(href)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        results.append(
            {
                "name": label_before(chunk, match.start(), match.group(2)),
                "mapId": mid,
                "url": href,
                "directory": True,
                "section": section_for(match.start(), chunk),
            }
        )
    for match in URL_RE.finditer(chunk):
        href = html.unescape(match.group(0).rstrip(".,);]"))
        mid = map_id(href)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        results.append(
            {
                "name": label_before(chunk, match.start(), ""),
                "mapId": mid,
                "url": href,
                "directory": True,
                "section": section_for(match.start(), chunk),
            }
        )
    return results


def normalized_name(value):
    text = re.sub(r"\([^)]*\)", "", value or "")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def has_expected_name(name, found_names):
    wanted = normalized_name(name)
    return any(wanted == found or wanted in found or found in wanted for found in found_names if found)


def main():
    errors = []
    candidates = []
    producers = (
        urllib_candidates,
        curl_candidates,
        chrome_candidates,
        wayback_candidates,
        commoncrawl_candidates,
    )
    for producer in producers:
        try:
            for method, document in producer(errors) or []:
                candidates.append((method, document))
                print(f"Candidate {method}: quality={quality(document)} maps={len(parse_registry(document))}", flush=True)
        except Exception as exc:
            errors.append(f"{producer.__name__}: {exc}")
    candidates.sort(key=lambda item: quality(item[1]), reverse=True)

    chosen_method = None
    discovered = []
    for method, document in candidates:
        parsed = parse_registry(document)
        if len(parsed) > len(discovered):
            discovered = parsed
            chosen_method = method
        if len(parsed) >= 80:
            break

    existing_path = DATA / "source-registry.json"
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
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
    found_names = {normalized_name(item.get("name")) for item in registry}
    missing_expected = sorted(name for name in EXPECTED if not has_expected_name(name, found_names))
    canada_found = has_expected_name("Canada", {normalized_name(item.get("name")) for item in discovered})
    report = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "masterUrl": MASTER_URL,
        "method": chosen_method,
        "candidateCount": len(candidates),
        "discoveredCount": len(discovered),
        "registryCount": len(registry),
        "canadaFoundInDiscovery": canada_found,
        "missingExpectedNames": missing_expected,
        "errors": errors[-50:],
    }
    existing_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "master-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    # The live registry is not published unless the archived master post is substantially complete.
    return 0 if len(discovered) >= 65 and canada_found else 2


if __name__ == "__main__":
    raise SystemExit(main())
