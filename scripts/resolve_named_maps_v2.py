#!/usr/bin/env python3
"""Stricter SirenFinder resolver.

Extends resolve_named_maps with direct Google My Maps searches and KML document-title
validation. Geography alone is not enough: a candidate must also identify the intended
state/country in its exported document or folder title.
"""

import html
import io
import re
import zipfile
from urllib.parse import quote
from xml.etree import ElementTree as ET

import resolve_named_maps as base
from import_registry_maps import get, local_name


BASE_VALIDATE = base.validate_candidate
BASE_QUERIES = base.search_queries

# Search direct My Maps results before forum/discussion pages.
base.ENGINES = [
    ("bing-rss", "https://www.bing.com/search?format=rss&count=50&q={query}"),
    ("bing-html", "https://www.bing.com/search?count=50&q={query}"),
    ("google-html", "https://www.google.com/search?num=30&filter=0&q={query}"),
    ("brave", "https://search.brave.com/search?source=web&q={query}"),
    ("mojeek", "https://www.mojeek.com/search?q={query}"),
    ("duckduckgo", "https://html.duckduckgo.com/html/?q={query}"),
    ("yahoo", "https://search.yahoo.com/search?n=30&p={query}"),
]


def improved_queries(region):
    name = region["name"]
    aliases = region.get("aliases") or []
    names = [name, *aliases]
    queries = []
    for value in names[:2]:
        queries.extend([
            f'site:google.com/maps/d "{value} Siren Map"',
            f'site:www.google.com/maps/d "{value}" "Siren Map"',
            f'site:google.com/maps/d "{value} Warning Siren Map"',
        ])
        if region.get("category") == "us":
            queries.extend([
                f'site:google.com/maps/d "{value} Statewide Siren Map"',
                f'site:google.com/maps/d "{value} Statewide Warning Siren Map"',
            ])
    queries.extend(BASE_QUERIES(region))
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
            data = get(url)
            return titles_from_bytes(data)
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
        kind = local_name(element.tag)
        if kind not in {"document", "folder"}:
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
