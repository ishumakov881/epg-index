#!/usr/bin/env python3
"""Coverage of an M3U playlist against the built index (what the app would match on-device).

Match levels, in order:
  id     normalized tvg-id == normalized guide channel id
  name   normalized channel name == normalized guide display-name
  tokens all name tokens are contained in exactly one guide display-name (same country)

Usage: python report.py playlist.m3u [--index dist/index] [--country es]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

EXTINF = re.compile(r"#EXTINF:[^,\n]*?(?P<attrs>(?:\s+[\w-]+=\"[^\"]*\")*)\s*,(?P<name>[^\n]*)")
ATTR = re.compile(r'([\w-]+)="([^"]*)"')
BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
QUALITY = re.compile(r"\b(?:hd|fhd|uhd|sd|4k|8k|hevc|h\.?26[45]|\d{3,4}[pi])\b")
ID_COUNTRY = re.compile(r"\.([a-z]{2})$")


def id_key(raw: str | None) -> str | None:
    base = (raw or "").split("@", 1)[0].strip().lower()
    key = "".join(ch for ch in base if ch.isalnum())
    return key or None


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def name_tokens(raw: str | None) -> list[str]:
    text = BRACKETS.sub(" ", fold(raw or ""))
    text = QUALITY.sub(" ", text)
    return [t for t in re.split(r"[^0-9a-z]+", text) if t]


def name_key(raw: str | None) -> str | None:
    key = "".join(name_tokens(raw))
    return key or None


def id_country(raw: str | None) -> str | None:
    base = (raw or "").split("@", 1)[0].strip().lower()
    m = ID_COUNTRY.search(base)
    return m.group(1) if m else None


def parse_m3u(text: str) -> list[dict]:
    out = []
    for m in EXTINF.finditer(text):
        attrs = dict(ATTR.findall(m.group("attrs") or ""))
        out.append({"id": attrs.get("tvg-id", ""), "name": m.group("name").strip(), "country": attrs.get("tvg-country", "")})
    return out


def load_country(index: Path, cc: str, cache: dict) -> list[dict]:
    if cc not in cache:
        f = index / f"{cc}.json"
        cache[cc] = json.loads(f.read_text(encoding="utf-8"))["channels"] if f.exists() else []
    return cache[cc]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("m3u", type=Path)
    ap.add_argument("--index", default="dist/index", type=Path)
    ap.add_argument("--country", default=None, help="fallback country when tvg-id has no .cc suffix")
    ap.add_argument("--show", type=int, default=10, help="sample size per bucket")
    args = ap.parse_args(argv)

    playlist = parse_m3u(args.m3u.read_text(encoding="utf-8", errors="replace"))
    cache: dict[str, list[dict]] = {}
    buckets: dict[str, list[str]] = {"id": [], "name": [], "tokens": [], "none": []}

    for ch in playlist:
        cc = id_country(ch["id"]) or (ch["country"].split(";")[0].lower() or None) or args.country
        pool = load_country(args.index, cc, cache) if cc else []
        pool = pool + load_country(args.index, "intl", cache)

        key = id_key(ch["id"])
        if key and any(id_key(g["id"]) == key for g in pool):
            buckets["id"].append(f"{ch['name']}  <- {ch['id']}")
            continue

        nkey = name_key(ch["name"])
        hit = None
        if nkey:
            hit = next((g for g in pool if any(name_key(n) == nkey for n in g["n"])), None)
        if hit:
            buckets["name"].append(f"{ch['name']}  ->  {hit['id']}")
            continue

        toks = set(name_tokens(ch["name"]))
        if toks and sum(len(t) for t in toks) >= 3:
            cands = [g for g in pool if any(toks <= set(name_tokens(n)) for n in g["n"])]
            if len(cands) == 1:
                buckets["tokens"].append(f"{ch['name']}  ~>  {cands[0]['id']}")
                continue
        buckets["none"].append(f"{ch['name']}  [{ch['id'] or 'no tvg-id'}]")

    total = len(playlist)
    matched = total - len(buckets["none"])
    print(f"playlist channels: {total}")
    for k in ("id", "name", "tokens", "none"):
        print(f"  {k:6s} {len(buckets[k]):5d}  ({len(buckets[k]) * 100 / max(total, 1):.0f}%)")
    print(f"  total matched: {matched} ({matched * 100 / max(total, 1):.0f}%)")
    for k in ("name", "tokens", "none"):
        if buckets[k]:
            print(f"\n{k} sample:")
            for line in buckets[k][: args.show]:
                print("  " + line)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
