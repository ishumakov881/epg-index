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

## Format

### `index/ids.json` — main lookup (any playlist, no country needed)

`tvg-id` is a global id, so one dictionary lookup resolves channels from any list —
country lists, categories, `index.m3u`, custom ones.

```json
{
  "version": 1,
  "generated": "…",
  "sources": [{"p": "epgshare01", "u": "https://…/epg_ripper_ES1.xml.gz"}, …],
  "ids": {
    "3catinfoes": ["3CatInfo.es", [12, 105]],
    "plutotvthrillersde": ["5dcddf1ed95e740009fef7ab", [101]]
  }
}
```

Key = normalized `tvg-id` or iptv-org alias (lowercase, drop `@feed`, letters/digits only).
Value = channel id inside the guide file + indexes into `sources`.

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
