"""Contract + scenario tests for the lookup API. Run after a build:

    python build_index.py && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import random
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epgkeys import SHARDS, id_key, name_key, name_tokens, shard_of  # noqa: E402
from resolve import Entry, local_fetcher, resolve  # noqa: E402

DIST = ROOT / "dist"


class ContractVectors(unittest.TestCase):
    """Fixed vectors the Kotlin client must reproduce."""

    def test_id_key(self):
        self.assertEqual(id_key("BBCNews.uk@HD"), "bbcnewsuk")
        self.assertEqual(id_key(" 3CatInfo.es "), "3catinfoes")
        self.assertEqual(id_key("AMC+.Connect.es"), "amcconnectes")
        self.assertIsNone(id_key(""))
        self.assertIsNone(id_key("@SD"))
        self.assertIsNone(id_key(None))

    def test_name_key(self):
        self.assertEqual(name_key("Canal Sur Andalucía (1080p) [Geo-blocked]"), "canalsurandalucia")
        self.assertEqual(name_key("CNN HD"), "cnn")
        self.assertEqual(name_key("Das Erste 720p"), "daserste")
        self.assertEqual(name_tokens("À Punt (720p)"), ["a", "punt"])
        self.assertIsNone(name_key("(720p)"))

    def test_shard_is_standard_crc32(self):
        self.assertEqual(SHARDS, 256)
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(shard_of("123456789"), 0xCBF43926 % SHARDS)
        self.assertEqual(shard_of("123456789"), 38)
        self.assertEqual(shard_of("bbcnewsuk"), zlib.crc32(b"bbcnewsuk") % SHARDS)


@unittest.skipUnless((DIST / "index" / "api.json").exists(), "run build_index.py first")
class BuildIntegrity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.api = json.loads((DIST / "index/api.json").read_text(encoding="utf-8"))
        cls.full = json.loads((DIST / "index/ids.json").read_text(encoding="utf-8"))["ids"]

    def test_api_entry_point(self):
        self.assertEqual(self.api["shards"], SHARDS)
        self.assertEqual(self.api["hash"], "crc32-utf8")
        self.assertGreater(len(self.api["sources"]), 10)
        for s in self.api["sources"]:
            self.assertTrue(s["u"].startswith("https://"))

    def test_shards_partition_full_map(self):
        union: dict = {}
        for n in range(SHARDS):
            part = json.loads((DIST / f"index/ids/{n:03d}.json").read_text(encoding="utf-8"))["ids"]
            for k in part:
                self.assertEqual(shard_of(k), n, k)
            union.update(part)
        self.assertEqual(union, self.full)

    def test_values_reference_valid_sources(self):
        n = len(self.api["sources"])
        for k, v in self.full.items():
            self.assertTrue(v[0])
            self.assertTrue(v[1])
            self.assertTrue(all(0 <= s < n for s in v[1]), k)

    def test_names_are_sharded_and_unambiguous(self):
        seen = set()
        for n in range(SHARDS):
            part = json.loads((DIST / f"index/names/{n:03d}.json").read_text(encoding="utf-8"))["names"]
            for k in part:
                self.assertEqual(shard_of(k), n)
                self.assertNotIn(k, seen)
                seen.add(k)


@unittest.skipUnless((DIST / "index" / "api.json").exists(), "run build_index.py first")
class PlaylistScenarios(unittest.TestCase):
    """Playlists of different completeness and size, built from the real index."""

    @classmethod
    def setUpClass(cls):
        cls.fetch = staticmethod(local_fetcher(DIST))
        full = json.loads((DIST / "index/ids.json").read_text(encoding="utf-8"))["ids"]
        # Canonical guide channels only (key == id_key(guide id)), deterministic order.
        canon = sorted((k, v) for k, v in full.items() if id_key(v[0]) == k)
        cls.canon = canon
        names: dict = {}
        for n in range(SHARDS):
            names.update(json.loads((DIST / f"index/names/{n:03d}.json").read_text(encoding="utf-8"))["names"])
        cls.names = names
        rnd = random.Random(42)
        cls.sample = rnd.sample(canon, 40)
        cls.with_icon = [kv for kv in canon if len(kv[1]) > 2][:20]
        # Names that resolve unambiguously; pair (display name as typed in a playlist, expected guide id).
        cls.named = [(k, v) for k, v in sorted(names.items()) if len(k) >= 6][:20]

    def run_entries(self, entries):
        return resolve(entries, self.fetch)

    def test_full_entries_id_name_logo(self):
        entries = [Entry(v[0], v[0], "http://logo/x.png") for _, v in self.sample]
        res = self.run_entries(entries)
        self.assertEqual(len(res.matches), len(entries))
        self.assertTrue(all(m.level == "id" for m in res.matches.values()))
        self.assertEqual(res.mode, "shards+none")

    def test_id_only_gets_programme_and_logo(self):
        entries = [Entry(v[0], "", "") for _, v in self.with_icon]
        res = self.run_entries(entries)
        self.assertEqual(len(res.matches), len(entries))
        self.assertTrue(all(m.icon for m in res.matches.values()))

    def test_id_case_and_feed_suffix(self):
        entries = [Entry(v[0].upper() + "@HD", "", "") for _, v in self.sample[:10]]
        res = self.run_entries(entries)
        self.assertEqual(len(res.matches), 10)

    def test_name_only_with_playlist_noise(self):
        entries = [Entry("", f"{k.upper()} (720p) [Geo-blocked]", "") for k, _ in self.named]
        res = self.run_entries(entries)
        self.assertEqual(len(res.matches), len(entries))
        for i, (k, v) in enumerate(self.named):
            self.assertEqual(res.matches[i].level, "name")
            self.assertEqual(res.matches[i].guide_id, v[0])

    def test_unknown_id_falls_back_to_name(self):
        k, v = self.named[0]
        res = self.run_entries([Entry("Totally.Unknown.zz", k, "")])
        self.assertEqual(res.matches[0].level, "name")
        self.assertEqual(res.matches[0].guide_id, v[0])

    def test_unknown_channels_do_not_match(self):
        entries = [Entry(f"NoSuchChannel{i}.zz", f"No Such Channel {i} xyzzy", "") for i in range(30)]
        res = self.run_entries(entries)
        self.assertEqual(res.matches, {})

    def test_empty_playlist_only_fetches_api(self):
        res = self.run_entries([])
        self.assertEqual(res.requests, ["index/api.json"])
        self.assertEqual(res.mode, "none+none")

    def test_many_nameless_unknowns_use_one_names_request(self):
        entries = [Entry("", f"Unknown Channel Number {i}", "") for i in range(500)]
        res = self.run_entries(entries)
        self.assertEqual(res.mode, "none+full")
        self.assertEqual(res.requests, ["index/api.json", "index/names.json"])

    def test_size_scaling_switches_to_full_map(self):
        rows = []
        for size in (1, 10, 50, 200, 1000, 10000):
            picked = self.canon[: min(size, len(self.canon))]
            entries = [Entry(v[0], "", "") for _, v in picked]
            res = self.run_entries(entries)
            rows.append((size, res.mode, len(res.requests), res.bytes_gzip))
            self.assertEqual(len(res.matches), len(entries))
            if size <= 10:
                self.assertEqual(res.mode, "shards+none")
                self.assertLessEqual(len(res.requests), 1 + size)
            if size >= 200:
                self.assertEqual(res.mode, "full+none")
                self.assertEqual(len(res.requests), 2)
            api = res.api
            self.assertLessEqual(
                len(res.requests) - 1,
                api["idsFullGzip"] // (api["idsShardGzipAvg"] + api["requestCostBytes"]) + 1,
            )
        print("\n  size  mode    requests  download")
        for size, mode, req, b in rows:
            print(f"  {size:5d}  {mode:6s}  {req:8d}  {b / 1024:7.0f} KB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
