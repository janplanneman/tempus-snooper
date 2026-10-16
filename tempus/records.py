"""New world records (and optionally top-10 map times) from the activity feed, one line each for the records channel.

GET /activity lists the latest ~20 of each kind. Each record id is posted once: ids already in the feed when
it first starts are marked seen without posting, so a new install doesn't flood the channel.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from .board import esc, map_link
from .tracker import _int, clean_name

log = logging.getLogger(__name__)

# WR kinds first: a time that is both a map WR and a map top is posted as the WR
KINDS = {"map_wrs": "Map WR", "course_wrs": "Course WR", "bonus_wrs": "Bonus WR", "trick_wrs": "Trick WR", "map_tops": "Top 10"}
WR_KINDS = ("map_wrs", "course_wrs", "bonus_wrs", "trick_wrs")
CLASSES = {3: "Soldier", 4: "Demoman"}
PLAYER_URL = "https://tempus2.xyz/players/{}"
# records older than this when first seen (e.g. after the bot was down) aren't posted
MAX_AGE_S = 24 * 3600


def flatten(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError(f"activity is a {type(data).__name__}, not an object")
    out = []
    for kind in KINDS:
        for e in data.get(kind) or []:
            try:
                ri = e["record_info"]
                pi = e.get("player_info") or {}
                mi = e.get("map_info") or {}
                zi = e.get("zone_info") or {}
                out.append({"id": int(ri["id"]), "kind": kind, "class": _int(ri.get("class")), "duration": float(ri["duration"]),
                            "date": float(ri.get("date") or 0), "server_id": _int(ri.get("server_id")), "rank": _int(ri.get("rank")),
                            "player_id": _int(pi.get("id")), "player": clean_name(pi.get("name")) or "?", "map": clean_name(mi.get("name")) or None,
                            "zone_type": clean_name(zi.get("type")), "zone_index": _int(zi.get("zoneindex")), "zone_name": clean_name(zi.get("custom_name"))})
            except Exception:
                log.warning("Skipping an activity entry I couldn't read: %.300r", e, exc_info=True)
    return out


def new_records(entries: Iterable[Dict[str, Any]], seen: set, include_tops: bool, now_ts: float) -> List[Dict[str, Any]]:
    """Records not posted yet, oldest first, one per record id."""
    out: Dict[int, Dict[str, Any]] = {}
    for r in entries:
        if r["id"] in seen or r["id"] in out:
            continue
        if r["kind"] not in WR_KINDS and not include_tops:
            continue
        if r["date"] and now_ts - r["date"] > MAX_AGE_S:
            continue
        out[r["id"]] = r
    return sorted(out.values(), key=lambda r: (r["date"], r["id"]))


def fmt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    if m:
        return f"{m}:{s:02d}.{ms:03d}"
    return f"{s}.{ms:03d}"


def _label(r: Dict[str, Any]) -> str:
    if r["kind"] == "map_tops":
        return f"#{r['rank']} map time" if r.get("rank") else "Top 10 map time"
    return KINDS[r["kind"]]


def _zone(r: Dict[str, Any]) -> str:
    if r["kind"] in ("map_wrs", "map_tops") or not r.get("zone_type") or r["zone_type"] == "map":
        return ""
    text = f"{r['zone_type'].capitalize()} {r['zone_index'] or ''}".strip()
    return f"{text} ({r['zone_name']})" if r.get("zone_name") else text


def record_line(r: Dict[str, Any], tiers: Dict[str, Tuple[Optional[int], Optional[int]]], server_names: Dict[int, str]) -> str:
    icon = "🏆" if r["kind"] in WR_KINDS else "🎖️"
    where = map_link(r.get("map"))
    zone = _zone(r)
    if zone:
        where += f" {esc(zone)}"
    elif r.get("map") in tiers and r.get("class") in (3, 4):
        tier = tiers[r["map"]][0 if r["class"] == 3 else 1]
        if tier:
            where += f" T{tier}"
    pid = r.get("player_id")
    player = f"[{esc(r['player']).replace('[', '(').replace(']', ')')}]({PLAYER_URL.format(quote(str(pid)))})" if pid else esc(r["player"])
    parts = [f"{icon} **{_label(r)}**", where, player, CLASSES.get(r.get("class") or 0, "?"), f"**{fmt_time(r['duration'])}**"]
    server = server_names.get(r.get("server_id") or -1)
    if server:
        parts.append(f"on {esc(server)}")
    return " · ".join(parts)
