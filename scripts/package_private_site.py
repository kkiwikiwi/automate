#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import secrets
import shutil
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SITE = ROOT / "site"
OUT = ROOT / "_site"
PRIVATE = ROOT / "private"


def make_compact() -> dict:
    geo = json.loads((DATA / "pins.geojson").read_text(encoding="utf-8"))
    status = json.loads((DATA / "status.json").read_text(encoding="utf-8"))
    source_index: dict[str, int] = {}
    sources: list[list[str]] = []

    def source_id(name: str, url: str) -> int:
        if name not in source_index:
            source_index[name] = len(sources)
            sources.append([name, url])
        return source_index[name]

    pins: list[list] = []
    for feature in geo["features"]:
        props = feature.get("properties") or {}
        names = props.get("sources") or [props.get("source") or "Unknown source"]
        urls = props.get("sourceUrls") or [props.get("sourceUrl") or ""]
        ids = [source_id(str(name), str(urls[i] if i < len(urls) else "")) for i, name in enumerate(names)]
        source_value: int | list[int] = ids[0] if len(ids) == 1 else ids
        lon, lat = feature["geometry"]["coordinates"]
        row: list = [lon, lat, props.get("name") or "Unnamed siren", props.get("description") or "", source_value]
        layer = props.get("layer") or ""
        if layer:
            row.append(layer)
        pins.append(row)

    failed = [
        [item.get("name") or "Unknown map", item.get("url") or "", item.get("error") or "Unavailable"]
        for item in status.get("failedSources", [])
    ]
    return {
        "v": 1,
        "meta": {
            "updatedAt": status.get("updatedAt"),
            "rawPinCount": status.get("rawPinCount"),
            "pinCount": status.get("pinCount"),
            "sourceCount": status.get("sourceCount"),
            "sourceTotal": status.get("sourceTotal"),
            "failedSourceCount": status.get("failedSourceCount"),
        },
        "sources": sources,
        "failed": failed,
        "pins": pins,
    }


def main() -> None:
    key_hex = os.environ.get("SIRENFINDER_KEY_HEX", "").strip().lower()
    if not key_hex:
        key_hex = secrets.token_hex(32)
    if len(key_hex) != 64 or any(char not in "0123456789abcdef" for char in key_hex):
        raise SystemExit("SIRENFINDER_KEY_HEX must be 64 hexadecimal characters")

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SITE, OUT)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)

    compact = json.dumps(make_compact(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(compact, compresslevel=9, mtime=0)
    nonce = secrets.token_bytes(12)
    encrypted = nonce + AESGCM(bytes.fromhex(key_hex)).encrypt(nonce, compressed, b"SirenFinder-v1")
    (OUT / "data" / "pins.sfg").write_bytes(encrypted)
    (OUT / "data" / "manifest.json").write_text(json.dumps({
        "version": 1,
        "data": "./data/pins.sfg",
        "encryptedBytes": len(encrypted),
        "access": "AES-256-GCM; key supplied only in URL fragment"
    }, indent=2), encoding="utf-8")
    (PRIVATE / "sirenfinder-access.txt").write_text(key_hex + "\n", encoding="utf-8")
    print(json.dumps({"compactBytes": len(compact), "gzipBytes": len(compressed), "encryptedBytes": len(encrypted)}, indent=2))


if __name__ == "__main__":
    main()
