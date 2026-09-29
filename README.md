# epg-index

Per-country **channel index** for public XMLTV guides: which guide file carries a given channel,
under which id and names. It is *not* a programme guide — the app downloads only the XMLTV files
it needs and parses programmes itself.

Rebuilt weekly by GitHub Actions and published to GitHub Pages (primary):

```
https://ishumakov881.github.io/epg-index/index/manifest.json
https://ishumakov881.github.io/epg-index/index/<cc>.json      # es, de, uk, us, ..., intl
```

Fallback mirror — the same files force-pushed as a single commit to the `data` branch:

```
https://raw.githubusercontent.com/ishumakov881/epg-index/data/index/<file>
```

Manual rebuild: Actions → build-index → Run workflow, or push a tag `build-<anything>`.

## Client contract (lookup API)

Entry point `index/api.json`:

```json
{
  "version": 2, "hash": "crc32-utf8", "shards": 256,
  "idsFull": "index/ids.json",     "idsShard": "index/ids/{shard}.json",
  "namesFull": "index/names.json", "namesShard": "index/names/{shard}.json",
  "idsFullGzip": 1062798, "idsShardGzipAvg": 6920,
  "namesFullGzip": 456493, "namesShardGzipAvg": 3049,
  "requestCostBytes": 16384,
  "sources": [{"p": "epgshare01", "u": "https://…/epg_ripper_ES1.xml.gz"}, …]
}
```

Per playlist:

1. `id_key(tvg-id)` for every channel → `shard = CRC32(utf8(key)) % shards` (zero-padded to 3 digits
   in paths). If `needed * (idsShardGzipAvg + requestCostBytes) >= idsFullGzip` fetch `idsFull`,
   else only the needed shards.
2. Channels not found by id: same with `name_key(name)` and the `names*` files.
3. Value `[guideChannelId, [sourceIndex…], icon?]` → group channels by `sources[i].u`, download
   only those XMLTV files, keep only programmes of `guideChannelId`.
4. `icon` is used only when the playlist has no `tvg-logo`.

Normalization (`epgkeys.py`, must be identical in the app):

- `id_key`: text before `@`, trimmed, lowercased, letters/digits only. `BBCNews.uk@HD` → `bbcnewsuk`.
- `name_key`: NFKD + strip accents, lowercase, remove `(…)`/`[…]`, remove whole-word quality tokens
  (`hd fhd uhd sd 4k 8k hevc h264 h265 720p 1080i …`), letters/digits only.
  `Canal Sur Andalucía (1080p) [Geo-blocked]` → `canalsurandalucia`.
- Test vector: `CRC32("123456789") = 0xCBF43926` → shard 38.

`names` contains only names that map to exactly one guide channel; ambiguous ones are dropped.

Reference client: `python resolve.py playlist.m3u [--local dist]`. Tests (local only, not in CI):
`python -m unittest discover -s tests`.

App side: FireTv `:core:epg` — `EpgKey.kt` (same normalization and vectors), `index/EpgIndexClient.kt`.

## Format

### `index/ids.json` — full id map (same content as all `index/ids/NNN.json` shards)

`tvg-id` is a global id, so one dictionary lookup resolves channels from any list —
country lists, categories, `index.m3u`, custom ones.

```json
{
  "version": 2,
  "generated": "…",
  "ids": {
    "3catinfoes": ["3CatInfo.es", [12, 105], "https://…/449-square.jpg"],
    "plutotvthrillersde": ["5dcddf1ed95e740009fef7ab", [101]]
  }
}
```

Key = normalized `tvg-id` or iptv-org alias. Value = channel id inside the guide file, indexes into
`api.json` `sources`, optional icon. `names.json` / `names/NNN.json` have the same value format.

### `index/<cc>.json` — per-country details (names, icons) for name-based fallback

```json
{
  "version": 1,
  "generated": "2026-09-29T13:30:00+00:00",
  "country": "es",
  "sources": [
    {"p": "epgshare01", "u": "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz"},
    {"p": "open-epg",   "u": "https://www.open-epg.com/files/spain1.xml.gz"}
  ],
  "channels": [
    {"id": "3CatInfo.es", "n": ["3CatInfo"], "i": "https://…/logo.png", "s": [0, 1]}
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | channel id exactly as in the XMLTV `<channel id>` / `<programme channel>` |
| `n` | all `display-name` values (merged across sources) |
| `i` | icon URL, optional |
| `s` | indexes into `sources` — guide files that contain this channel |

Country comes from the id suffix (`Foo.es`), otherwise from the guide file (`epg_ripper_ES1`),
otherwise `intl` (FAST services with hash ids: Pluto TV, Samsung TV Plus, Plex, Roku).

`index/manifest.json` lists countries (channel count, size) and every source with its status.

## Matching on the device

1. **id** — look up normalized `tvg-id` in `ids.json`. Exact, global.
2. **name** (only for channels without a usable `tvg-id`) — normalized channel name equals a
   normalized `display-name` (accents folded, `(…)`/`[…]` and quality tokens like `HD`, `720p`
   removed, letters/digits only); accepted only when unambiguous.

Then download only the `sources` of matched channels and keep only their programmes.

`python report.py playlist.m3u --country es` prints the coverage a playlist would get.

## Local run

```
python build_index.py            # -> dist/index/*.json (stdlib only, Python 3.10+)
python report.py some.m3u --country es
```

## Adding sources

Edit `sources.json`:

- `listing` + `pattern` — discover files from a directory page;
- `files` — explicit URLs; `country` maps a URL to a country;
- `channels_first: true` — only if `<channel>` elements precede all `<programme>` ones
  (lets the build stop reading early).

The build refuses to publish if fewer than `--min-channels` (default 10000) channels were read.
