#!/usr/bin/env python3
"""Build a per-country channel index (ids, names, icons, source URLs) from public XMLTV guides.

Only <channel> elements are kept. For providers flagged `channels_first` the download is
aborted at the first <programme>, so multi-MB guides cost a few KB each; others are streamed
fully (programmes are discarded as they are read). Standard library only.

Usage: python build_index.py [--sources sources.json] [--out dist] [--workers 8] [--min-channels 10000]
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENT = "epg-index/1.0 (+https://github.com/ishumakov881/epg-index)"
TIMEOUT = 60
RETRIES = 2

# ISO 3166-1 alpha-2 plus "uk" (iptv-org / most guides use .uk, not .gb).
COUNTRIES = set("""
ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw
by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi
fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in
io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me
mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf
pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv
sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg vi vn vu wf ws xk
ye yt za zm zw uk
""".split())

ID_COUNTRY = re.compile(r"\.([a-z]{2})$")


@dataclass
class SourceFile:
    provider: str
    url: str
    country: str | None = None
    # True only when <channel> elements are known to precede all <programme> ones.
    channels_first: bool = False


@dataclass
class SourceResult:
    source: SourceFile
    channels: list[dict] = field(default_factory=list)
    error: str | None = None


def http_open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def fetch_text(url: str) -> str:
    with http_open(url) as r:
        return r.read().decode("utf-8", errors="replace")


def expand_sources(config: dict) -> list[SourceFile]:
    out: list[SourceFile] = []
    for p in config["providers"]:
        pid = p["id"]
        static_country = p.get("country", {})
        first = bool(p.get("channels_first", False))
        if "listing" in p:
            html = fetch_text(p["listing"])
            names = sorted(set(re.findall(p["pattern"], html)) - set(p.get("exclude", [])))
            cc_re = re.compile(p["country_from_file"]) if p.get("country_from_file") else None
            for name in names:
                cc = None
                if cc_re and (m := cc_re.match(name)):
                    cc = m.group(1).lower()
                    cc = "uk" if cc == "gb" else cc
                out.append(SourceFile(pid, p["base"] + name, cc if cc in COUNTRIES else None, first))
        for url in p.get("files", []):
            out.append(SourceFile(pid, url, static_country.get(url), first))
    return out


def open_xml_stream(raw) -> io.BufferedReader:
    buffered = io.BufferedReader(raw, buffer_size=64 * 1024)
    if buffered.peek(2)[:2] == b"\x1f\x8b":
        return io.BufferedReader(gzip.GzipFile(fileobj=buffered))
    return buffered


def read_channels(src: SourceFile) -> SourceResult:
    last_error = None
    for _ in range(RETRIES + 1):
        channels: list[dict] = []
        try:
            with http_open(src.url) as raw:
                stream = open_xml_stream(raw)
                for event, elem in ET.iterparse(stream, events=("start", "end")):
                    if event == "start" and elem.tag == "programme" and src.channels_first:
                        break
                    if event == "end" and elem.tag == "programme":
                        elem.clear()
                        continue
                    if event == "end" and elem.tag == "channel":
                        cid = (elem.get("id") or "").strip()
                        if cid:
                            names = []
                            for dn in elem.findall("display-name"):
                                text = (dn.text or "").strip()
                                if text and text not in names:
                                    names.append(text)
                            icon = elem.find("icon")
                            ch = {"id": cid, "n": names}
                            if icon is not None and icon.get("src"):
                                ch["i"] = icon.get("src")
                            channels.append(ch)
                        elem.clear()
            return SourceResult(src, channels)
        except ET.ParseError as e:
            # Truncated/odd tail after channels: keep what was read.
            if channels:
                return SourceResult(src, channels, error=f"parse: {e}")
            last_error = f"parse: {e}"
        except Exception as e:  # network, HTTP, gzip
            last_error = f"{type(e).__name__}: {e}"
    return SourceResult(src, [], error=last_error)


def load_iptv_org_aliases(url: str | None) -> dict[str, set[str]]:
    """Guide channel id -> iptv-org channel ids (their `tvg-id`), from guides.json `site_id` tails.

    e.g. site_id `au/Adelaide/epg#mjh-7afl-fast` maps guide id `mjh-7afl-fast` to `7AFL.au`.
    """
    if not url:
        return {}
    try:
        with http_open(url) as r:
            guides = json.load(r)
    except Exception as e:
        print(f"aliases: skipped ({type(e).__name__}: {e})", flush=True)
        return {}
    out: dict[str, set[str]] = {}
    for g in guides:
        channel, site_id = g.get("channel"), g.get("site_id") or ""
        if not channel or not site_id:
            continue
        key = site_id.split("#", 1)[-1]
        out.setdefault(key, set()).add(channel)
    print(f"aliases: {len(out)} guide ids from iptv-org", flush=True)
    return out


def channel_country(cid: str, fallback: str | None) -> str:
    base = cid.split("@", 1)[0].strip().lower()
    m = ID_COUNTRY.search(base)
    if m and m.group(1) in COUNTRIES:
        return m.group(1)
    return fallback or "intl"


def build(sources_path: Path, out_dir: Path, workers: int, min_channels: int) -> None:
    config = json.loads(sources_path.read_text(encoding="utf-8"))
    files = expand_sources(config)
    print(f"sources: {len(files)} files", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(read_channels, files))

    read_total = sum(len(r.channels) for r in results)
    if read_total < min_channels:
        for r in results:
            print(f"  {len(r.channels):6d} ch  {r.source.url}  {r.error or ''}", flush=True)
        raise SystemExit(f"only {read_total} channels read (< {min_channels}); refusing to publish")

    # country -> id -> merged record (sources as global indexes for now)
    by_country: dict[str, dict[str, dict]] = {}
    for gi, res in enumerate(results):
        status = f"{len(res.channels):6d} ch" + (f"  [{res.error}]" if res.error else "")
        print(f"  {status}  {res.source.url}", flush=True)
        for ch in res.channels:
            cc = channel_country(ch["id"], res.source.country)
            bucket = by_country.setdefault(cc, {})
            rec = bucket.get(ch["id"])
            if rec is None:
                rec = {"id": ch["id"], "n": list(ch["n"]), "s": [gi]}
                if "i" in ch:
                    rec["i"] = ch["i"]
                bucket[ch["id"]] = rec
            else:
                for name in ch["n"]:
                    if name not in rec["n"]:
                        rec["n"].append(name)
                if gi not in rec["s"]:
                    rec["s"].append(gi)
                if "i" not in rec and "i" in ch:
                    rec["i"] = ch["i"]

    aliases = load_iptv_org_aliases(config.get("aliases", {}).get("iptv_org_guides"))
    aliased = 0
    for bucket in by_country.values():
        for rec in bucket.values():
            extra = aliases.get(rec["id"])
            if extra:
                rec["a"] = sorted(extra - {rec["id"]})
                if rec["a"]:
                    aliased += 1
                else:
                    del rec["a"]
    print(f"aliases: attached to {aliased} channels", flush=True)

    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    index_dir = out_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    countries_meta = {}
    for cc in sorted(by_country):
        records = sorted(by_country[cc].values(), key=lambda r: r["id"].lower())
        used = sorted({gi for r in records for gi in r["s"]})
        local = {gi: li for li, gi in enumerate(used)}
        for r in records:
            r["s"] = [local[gi] for gi in r["s"]]
        payload = {
            "version": 1,
            "generated": generated,
            "country": cc,
            "sources": [{"p": results[gi].source.provider, "u": results[gi].source.url} for gi in used],
            "channels": records,
        }
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (index_dir / f"{cc}.json").write_bytes(data)
        countries_meta[cc] = {
            "file": f"index/{cc}.json",
            "channels": len(records),
            "bytes": len(data),
            "gzipBytes": len(gzip.compress(data)),
        }

    manifest = {
        "version": 1,
        "generated": generated,
        "countries": countries_meta,
        "sources": [
            {
                "provider": r.source.provider,
                "url": r.source.url,
                "country": r.source.country,
                "channels": len(r.channels),
                **({"error": r.error} if r.error else {}),
            }
            for r in results
        ],
    }
    (index_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(m["channels"] for m in countries_meta.values())
    raw = sum(m["bytes"] for m in countries_meta.values())
    gz = sum(m["gzipBytes"] for m in countries_meta.values())
    failed = [r for r in results if not r.channels]
    print(
        f"done: {len(countries_meta)} countries, {total} channels, "
        f"{raw / 1024:.0f} KB raw / {gz / 1024:.0f} KB gzip, failed sources: {len(failed)}",
        flush=True,
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default="sources.json", type=Path)
    ap.add_argument("--out", default="dist", type=Path)
    ap.add_argument("--workers", default=8, type=int)
    ap.add_argument("--min-channels", default=10000, type=int, help="fail instead of publishing a gutted index")
    args = ap.parse_args(argv)
    build(args.sources, args.out, args.workers, args.min_channels)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
