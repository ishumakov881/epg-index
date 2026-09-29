#!/usr/bin/env python3
"""Reference client for the lookup API — does exactly what the app does.

  1. GET index/api.json
  2. shards needed = {shard_of(id_key(tvg-id))}; if needed * (idsShardGzipAvg + requestCostBytes)
     >= idsFullGzip -> GET ids.json (one request), else GET only index/ids/NNN.json
  3. channels not found by id -> {shard_of(name_key(name))} -> same rule with names.json /
     index/names/NNN.json
  4. group matched channels by guide file (api.sources) -> those are the XMLTV files to download

Usage:
  python resolve.py playlist.m3u                       # against GitHub Pages
  python resolve.py playlist.m3u --local dist          # against a local build
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from epgkeys import id_key, name_key, shard_of

DEFAULT_BASE = "https://ishumakov881.github.io/epg-index/"
EXTINF = re.compile(r"#EXTINF:[^,\n]*?(?P<attrs>(?:\s+[\w-]+=\"[^\"]*\")*)\s*,(?P<name>[^\n]*)")
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


@dataclass
class Entry:
    tvg_id: str = ""
    name: str = ""
    logo: str = ""


@dataclass
class Match:
    level: str          # "id" | "name"
    guide_id: str
    sources: list[int]
    icon: str | None


@dataclass
class Result:
    matches: dict[int, Match] = field(default_factory=dict)   # entry index -> match
    requests: list[str] = field(default_factory=list)
    bytes_gzip: int = 0
    mode: str = ""                                             # "shards" | "full"
    api: dict = field(default_factory=dict)

    def guide_files(self) -> set[str]:
        return {self.api["sources"][s]["u"] for m in self.matches.values() for s in m.sources}


def parse_m3u(text: str) -> list[Entry]:
    out = []
    for m in EXTINF.finditer(text):
        a = dict(ATTR.findall(m.group("attrs") or ""))
        out.append(Entry(a.get("tvg-id", ""), m.group("name").strip(), a.get("tvg-logo", "")))
    return out


Fetcher = Callable[[str], tuple[dict, int]]   # path -> (json, gzip bytes on the wire)


def http_fetcher(base: str) -> Fetcher:
    def fetch(path: str) -> tuple[dict, int]:
        req = urllib.request.Request(base + path, headers={"Accept-Encoding": "gzip", "User-Agent": "epg-index-resolve"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            wire = len(body)
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            else:
                wire = len(gzip.compress(body))
        return json.loads(body), wire
    return fetch


def local_fetcher(root: Path) -> Fetcher:
    def fetch(path: str) -> tuple[dict, int]:
        data = (root / path).read_bytes()
        return json.loads(data), len(gzip.compress(data))
    return fetch


def resolve(entries: list[Entry], fetch: Fetcher) -> Result:
    res = Result()

    def get(path: str) -> dict:
        data, wire = fetch(path)
        res.requests.append(path)
        res.bytes_gzip += wire
        return data

    api = res.api = get("index/api.json")
    shards = api["shards"]
    cost = api.get("requestCostBytes", 0)

    def load(kind: str, keys) -> tuple[dict[str, list], str]:
        needed = {shard_of(k, shards) for k in keys}
        if not needed:
            return {}, "none"
        if len(needed) * (api[f"{kind}ShardGzipAvg"] + cost) >= api[f"{kind}FullGzip"]:
            return get(api[f"{kind}Full"])[kind], "full"
        table: dict[str, list] = {}
        for n in sorted(needed):
            table.update(get(api[f"{kind}Shard"].format(shard=f"{n:03d}"))[kind])
        return table, "shards"

    id_keys = {i: k for i, e in enumerate(entries) if (k := id_key(e.tvg_id))}
    table, res.mode = load("ids", id_keys.values())
    for i, k in id_keys.items():
        if (v := table.get(k)) is not None:
            res.matches[i] = Match("id", v[0], v[1], v[2] if len(v) > 2 else None)

    name_keys = {i: k for i, e in enumerate(entries) if i not in res.matches and (k := name_key(e.name))}
    names, names_mode = load("names", name_keys.values())
    res.mode = f"{res.mode}+{names_mode}"
    for i, k in name_keys.items():
        if (v := names.get(k)) is not None:
            res.matches[i] = Match("name", v[0], v[1], v[2] if len(v) > 2 else None)
    return res


def summary(entries: list[Entry], res: Result) -> str:
    total = len(entries)
    by_id = sum(1 for m in res.matches.values() if m.level == "id")
    by_name = sum(1 for m in res.matches.values() if m.level == "name")
    no_logo = [i for i, e in enumerate(entries) if not e.logo]
    logo_filled = sum(1 for i in no_logo if i in res.matches and res.matches[i].icon)
    pct = lambda n: f"{n * 100 / max(total, 1):.0f}%"
    return (
        f"channels={total} with tvg-id={sum(1 for e in entries if id_key(e.tvg_id))} | "
        f"matched id={by_id} ({pct(by_id)}) name={by_name} ({pct(by_name)}) total={pct(by_id + by_name)} | "
        f"logo filled {logo_filled}/{len(no_logo)} | mode={res.mode} requests={len(res.requests)} "
        f"download={res.bytes_gzip / 1024:.0f} KB | guide files={len(res.guide_files())}"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("m3u", type=Path)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--local", type=Path, help="use a local build dir instead of --base")
    args = ap.parse_args(argv)
    entries = parse_m3u(args.m3u.read_text(encoding="utf-8", errors="replace"))
    fetch = local_fetcher(args.local) if args.local else http_fetcher(args.base)
    print(summary(entries, resolve(entries, fetch)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
