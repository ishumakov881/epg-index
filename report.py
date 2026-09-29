#!/usr/bin/env python3
"""Coverage of an M3U playlist against the built index (what the app would match on-device).

Country is resolved per channel (tvg-id suffix `.cc`, then `tvg-country`), never per playlist,
so mixed lists (all channels, categories) work the same as country lists.

Match levels, in order:
  id     normalized tvg-id == normalized guide id or iptv-org alias      (global, exact)
  name   normalized name == normalized display-name                     (same country;
                                                                          global only if unique)
  tokens name tokens contained in exactly one display-name               (same country only)

Usage: python report.py playlist.m3u [--index dist/index] [--show 10]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
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


class Index:
    """Lookup tables the app would build from the downloaded country files."""

    def __init__(self, index_dir: Path):
        self.by_id: dict[str, str] = {}
        self.by_name: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # nkey -> cc -> ids
        self.tokens: dict[str, list[tuple[frozenset, str]]] = defaultdict(list)  # cc -> [(tokens, id)]
        for f in sorted(index_dir.glob("*.json")):
            if f.name == "manifest.json":
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            cc = data["country"]
            for ch in data["channels"]:
                for raw in [ch["id"], *ch.get("a", ())]:
                    k = id_key(raw)
                    if k:
                        self.by_id.setdefault(k, ch["id"])
                for n in ch["n"]:
                    nk = name_key(n)
                    if nk:
                        self.by_name[nk][cc].add(ch["id"])
                    toks = frozenset(name_tokens(n))
                    if toks:
                        self.tokens[cc].append((toks, ch["id"]))

    def match(self, tvg_id: str, name: str, cc: str | None) -> tuple[str, str] | None:
        k = id_key(tvg_id)
        if k and k in self.by_id:
            return "id", self.by_id[k]

        nk = name_key(name)
        if nk and nk in self.by_name:
            per_cc = self.by_name[nk]
            for scope in ([cc, "intl"] if cc else []):
                if scope in per_cc and len(per_cc[scope]) >= 1:
                    return "name", sorted(per_cc[scope])[0]
            everywhere = set().union(*per_cc.values())
            if len(everywhere) == 1:
                return "name", next(iter(everywhere))

        toks = set(name_tokens(name))
        if cc and toks and sum(len(t) for t in toks) >= 3:
            cands = {cid for scope in (cc, "intl") for t, cid in self.tokens.get(scope, ()) if toks <= t}
            if len(cands) == 1:
                return "tokens", next(iter(cands))
        return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("m3u", type=Path)
    ap.add_argument("--index", default="dist/index", type=Path)
    ap.add_argument("--show", type=int, default=10, help="sample size per bucket")
    args = ap.parse_args(argv)

    playlist = parse_m3u(args.m3u.read_text(encoding="utf-8", errors="replace"))
    index = Index(args.index)
    buckets: dict[str, list[str]] = {"id": [], "name": [], "tokens": [], "none": []}
    no_country = 0
    for ch in playlist:
        cc = id_country(ch["id"]) or (ch["country"].split(";")[0].lower() or None)
        no_country += cc is None
        hit = index.match(ch["id"], ch["name"], cc)
        if hit:
            level, gid = hit
            buckets[level].append(f"{ch['name']}  ->  {gid}")
        else:
            buckets["none"].append(f"{ch['name']}  [{ch['id'] or 'no tvg-id'}]")

    total = len(playlist)
    matched = total - len(buckets["none"])
    print(f"playlist channels: {total} (without country: {no_country})")
    for k in ("id", "name", "tokens", "none"):
        print(f"  {k:6s} {len(buckets[k]):6d}  ({len(buckets[k]) * 100 / max(total, 1):.0f}%)")
    print(f"  total matched: {matched} ({matched * 100 / max(total, 1):.0f}%)")
    for k in ("name", "tokens", "none"):
        if buckets[k] and args.show:
            print(f"\n{k} sample:")
            for line in buckets[k][: args.show]:
                print("  " + line)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
