"""Key normalization and sharding shared by the build, the report and the reference client.

The app must implement exactly the same rules (see README, "Client contract").
"""
from __future__ import annotations

import re
import unicodedata
import zlib

SHARDS = 256

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_QUALITY = re.compile(r"\b(?:hd|fhd|uhd|sd|4k|8k|hevc|h\.?26[45]|\d{3,4}[pi])\b")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def id_key(raw: str | None) -> str | None:
    """`BBCNews.uk@HD` -> `bbcnewsuk`: drop `@feed`, lowercase, letters/digits only."""
    base = (raw or "").split("@", 1)[0].strip().lower()
    key = "".join(ch for ch in base if ch.isalnum())
    return key or None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def name_tokens(raw: str | None) -> list[str]:
    """`Canal Sur Andalucía (1080p) [Geo-blocked]` -> [canal, sur, andalucia]."""
    text = _BRACKETS.sub(" ", _fold(raw or ""))
    text = _QUALITY.sub(" ", text)
    return [t for t in _NON_ALNUM.split(text) if t]


def name_key(raw: str | None) -> str | None:
    key = "".join(name_tokens(raw))
    return key or None


def shard_of(key: str, shards: int = SHARDS) -> int:
    """CRC-32 (zlib / java.util.zip.CRC32) of the UTF-8 key, modulo shard count."""
    return zlib.crc32(key.encode("utf-8")) % shards
