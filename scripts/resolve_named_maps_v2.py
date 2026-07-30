#!/usr/bin/env python3
"""Bounded, title-validated SirenFinder source-map resolver."""

import html
import io
import re
import zipfile
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import resolve_named_maps as base
from import_registry_maps import get, local_name


BASE_VALIDATE = base.validate_candidate
USER_AGENT = "Mozilla/5.0 (compatible; SirenFinder/4.0; +https://github.com/kkiwikiwi/automate)"

# Direct map-index surfaces only. Every request is bounded to five seconds.
base.ENGINES = [
    ("bing-rss", "https://www.bing.com/search?format=rss&count=50&q={query}"),
    ("bing-html", "https://www.bing.com/search?count=50&q={query}"),
    ("google-html", "https://www.google.com/search?num=30&filter=0&q={query}"),
    ("brave", "https://search.brave.com/search?source=web&q={query}"),
    ("yahoo", "https://search.yahoo.com/search?n=30&p={query}"),
]


def fast_get_text(url, tries=1, timeout=5):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=5) as response:
        return response.read(8_000_000).decode("utf-8", "replace")


base.get_text = fast_get_text


def improved_queries(region):
    name = region["name"]
    aliases = region.get("aliases") or []
    preferred = aliases[0] if aliases else name
    queries = [
        f'site:google.com/maps/d "{preferred} Siren Map"',
        f'site:www.google.com/maps/d "{preferred}" "Warning Siren Map"',
        f'"{preferred} Siren Map" "mid="',
    ]
    if region.get("category") == "us":
        queries.insert(1, f'site:google.com/maps/d "{preferred} Statewide Siren Map"')
    return list(dict.fromkeys(queries))


base.search_queries = improved_queries


def clean_title(value):
    value = html.unescape(value or "").lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def map_titles(map_id):
    last = None
    for url in (
        f"https://www.google.com/maps/d/u/0/kml?mid={quote(map_id)}&forcekml=1",
        f"https://www.google.com/maps/d/kml?mid={quote(map_id)}&forcekml=1",
    ):
        try:
            return titles_from_bytes(get(url))
        except Exception as exc:
            last = exc
    raise RuntimeError(last or "could not read candidate KML titles")


def titles_from_bytes(data):
    documents = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for filename in archive.namelist():
                if filename.lower().endswith(".kml"):
                    documents.extend(titles_from_bytes(archive.read(filename)))
        return list(dict.fromkeys(documents))
    root = ET.fromstring(data)
    for element in root.iter():
        if local_name(element.tag) not in {"document", "folder"}:
            continue
        for child in list(element):
            if local_name(child.tag) == "name" and (child.text or "").strip():
                documents.append((child.text or "").strip()[:250])
                break
    return list(dict.fromkeys(documents))


def title_matches(region, titles):
    normalized_titles = [clean_title(value) for value in titles if value]
    expected = [clean_title(region.get("name", "")), *[clean_title(value) for value in (region.get("aliases") or [])]]
    code = clean_title(region.get("code", ""))
    for title in normalized_titles:
        if not title:
            continue
        has_siren = "siren" in title or "warning" in title or "civil defense" in title or "civil defence" in title
        for name in expected:
            if name and has_siren and (name in title or title in name):
                return True
        if region.get("category") == "us" and code and has_siren and re.search(rf"(?:^| ){re.escape(code)}(?: |$)", title):
            return True
    return False


def strict_validate(region, candidate):
    result = BASE_VALIDATE(region, candidate)
    titles = map_titles(candidate["mapId"])
    matched = title_matches(region, titles)
    result["kmlTitles"] = titles[:20]
    result["titleMatched"] = matched
    result["accepted"] = bool(result.get("accepted") and matched)
    return result


base.validate_candidate = strict_validate


if __name__ == "__main__":
    raise SystemExit(base.main())
