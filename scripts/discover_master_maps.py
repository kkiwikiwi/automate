#!/usr/bin/env python3
import html
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

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
MAP_ID = re.compile(
    r"(?:[?&](?:mid|id)=|/earth/d/|/d/)([A-Za-z0-9_-]{15,})",
    re.I,
)
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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


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
    for url in (MASTER_URL, MASTER_URL + "&view=print", MASTER_URL + "&view=printable"):
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
    chrome = next((shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(name)), None)
    if not chrome:
        errors.append("headless Chrome/Chromium not installed")
        return
    profile = "/tmp/sirenfinder-chrome-profile"
    for url in (MASTER_URL, MASTER_URL + "&view=print"):
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
                    "--virtual-time-budget=15000",
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


def quality(text):
    lower = (text or "").lower()
    return (
        lower.count("google.com/maps/d") * 20
        + lower.count("drive.google.com/open") * 20
        + lower.count("earth.google.com/earth/d") * 20
        + (500 if "canada-" in lower else 0)
        + (500 if "other countries-" in lower else 0)
        + min(len(text or "") // 1000, 500)
    )


def master_slice(document):
    decoded = html.unescape(document or "")
    phrase = "This thread lists exhaustive siren maps"
    start = decoded.find(phrase)
    if start < 0:
        start = decoded.find("United States-")
    if start < 0:
        return decoded
    candidates = [
        decoded.find("Last edited by", start),
        decoded.find("Re: Master list of State/Country siren maps", start + 1000),
        decoded.find("<article", start + 1000),
    ]
    ends = [value for value in candidates if value > start]
    end = min(ends) if ends else min(len(decoded), start + 600_000)
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
    if not candidate or len(candidate) > 100:
        candidate = clean(fallback)
    if candidate.lower().startswith("http") or candidate == "":
        candidate = "Unnamed master-list map"
    return candidate[:120]


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
        name = label_before(chunk, match.start(), match.group(2))
        section = section_for(match.start(), chunk)
        results.append({
            "name": name,
            "mapId": mid,
            "url": href,
            "directory": True,
            "section": section,
        })
    # Some phpBB renderings leave bare URLs outside anchors.
    for match in URL_RE.finditer(chunk):
        href = html.unescape(match.group(0).rstrip(".,);]"))
        mid = map_id(href)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        name = label_before(chunk, match.start(), "")
        section = section_for(match.start(), chunk)
        results.append({
            "name": name,
            "mapId": mid,
            "url": href,
            "directory": True,
            "section": section,
        })
    return results


def normalized_name(value):
    text = re.sub(r"\([^)]*\)", "", value or "")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return text


def main():
    errors = []
    candidates = []
    for producer in (urllib_candidates, curl_candidates, chrome_candidates):
        try:
            candidates.extend(list(producer(errors) or []))
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
        if len(parsed) >= 75:
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
    missing_expected = sorted(
        name for name in EXPECTED
        if not any(normalized_name(name) in found or found in normalized_name(name) for found in found_names if found)
    )
    report = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "masterUrl": MASTER_URL,
        "method": chosen_method,
        "candidateCount": len(candidates),
        "discoveredCount": len(discovered),
        "registryCount": len(registry),
        "missingExpectedNames": missing_expected,
        "errors": errors[-30:],
    }
    existing_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "master-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    # Require a meaningful master-list extraction; retain old registry on failure but report nonzero exit.
    return 0 if len(discovered) >= 20 else 2


if __name__ == "__main__":
    raise SystemExit(main())
