"""New world records (and optionally top-10 map times) from the activity feed, as embeds for the records channel.

GET /activity lists the latest ~20 of each kind. Each record id is posted once: ids already in the feed when it
first starts are marked seen without posting, so a new install doesn't flood the channel.

A WR post says what it beat. The bot remembers every WR it sees in the feed, so when a zone's WR changes it
knows the old one (even when the holder improved their own). When it hasn't seen the old WR, it reads the
zone's leaderboard (one request) and says how far ahead of #2 the new time is, which is always true.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord

from .board import esc, map_url
from .regions import NO_FLAG, flag
from .tracker import _int, clean_name

log = logging.getLogger(__name__)

# WR kinds first: a time that is both a map WR and a map top is posted as the WR
KINDS = {"map_wrs": "Map WR", "course_wrs": "Course WR", "bonus_wrs": "Bonus WR", "trick_wrs": "Trick WR", "map_tops": "Top 10"}
WR_KINDS = ("map_wrs", "course_wrs", "bonus_wrs", "trick_wrs")
COLOURS = {"map_wrs": 0xF1C40F, "course_wrs": 0xE67E22, "bonus_wrs": 0x9B59B6, "trick_wrs": 0x1ABC9C, "map_tops": 0x95A5A6}
CLASSES = {3: "Soldier", 4: "Demoman"}
CLASS_KEYS = {3: "soldier", 4: "demoman"}
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
                            "zone_id": _int(ri.get("zone_id")) or _int(zi.get("id")), "player_id": _int(pi.get("id")),
                            "player": clean_name(pi.get("name")) or "?", "map": clean_name(mi.get("name")) or None,
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


def leaderboard_context(data: Any, cls: Optional[int]) -> Dict[str, Any]:
    """From zones/id/{id}/records/list: the zone's tier for this class, completions, the holder's country and #2."""
    data = data if isinstance(data, dict) else {}
    key = CLASS_KEYS.get(cls or 0)
    rows = (data.get("results") or {}).get(key) or [] if key else []
    tiers = data.get("tier_info") or {}
    out: Dict[str, Any] = {"tier": _int(tiers.get(str(cls))), "completions": _int((data.get("completion_info") or {}).get(key or ""))}
    for row in rows:
        if not isinstance(row, dict):
            continue
        info = row.get("player_info") or {}
        if row.get("rank") == 1:
            out["country_code"] = info.get("country_code")
        elif row.get("rank") == 2:
            out["second"] = {"player": clean_name(row.get("name") or info.get("name")) or "?", "duration": float(row["duration"])}
    return out


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


def fmt_gap(seconds: float) -> str:
    return f"{seconds:.3f} s" if seconds < 60 else fmt_time(seconds)


def _zone(r: Dict[str, Any]) -> str:
    if r["kind"] in ("map_wrs", "map_tops") or not r.get("zone_type") or r["zone_type"] == "map":
        return ""
    text = f"{r['zone_type'].capitalize()} {r['zone_index'] or ''}".strip()
    return f"{text} ({r['zone_name']})" if r.get("zone_name") else text


def _title(r: Dict[str, Any]) -> str:
    where = r.get("map") or "?"
    zone = _zone(r)
    if r["kind"] == "map_tops":
        return f"🎖️ #{r['rank']} on {where}" if r.get("rank") else f"🎖️ Top 10 time on {where}"
    icon = "🏆" if r["kind"] == "map_wrs" else "⭐"
    return f"{icon} New {KINDS[r['kind']].replace(' WR', '').lower()} WR · {where}" + (f" · {zone}" if zone else "")


def _beat_line(r: Dict[str, Any], previous: Optional[Dict[str, Any]], context: Dict[str, Any]) -> Optional[str]:
    if previous and previous.get("record_id") != r["id"] and previous["duration"] > r["duration"]:
        gap = fmt_gap(previous["duration"] - r["duration"])
        if previous.get("player_id") and previous["player_id"] == r.get("player_id"):
            return f"Improves their own WR by **{gap}**"
        return f"Beats the old WR by **{gap}** ({esc(previous['player'])}, {fmt_time(previous['duration'])})"
    second = context.get("second")
    if second and second["duration"] > r["duration"]:
        return f"**{fmt_gap(second['duration'] - r['duration'])}** ahead of #2 {esc(second['player'])}"
    if context and not second and context.get("completions") == 1:
        return "First completion"
    return None


def record_embed(r: Dict[str, Any], tiers: Dict[str, Tuple[Optional[int], Optional[int]]], server_names: Dict[int, str],
                 previous: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> discord.Embed:
    context = context or {}
    url = map_url(r["map"]) if r.get("map") else None
    embed = discord.Embed(title=_title(r)[:256], url=url, colour=COLOURS[r["kind"]])
    pid = r.get("player_id")
    who = esc(r["player"])
    if pid:
        who = f"[{who.replace('[', '(').replace(']', ')')}]({PLAYER_URL.format(pid)})"
    holder_flag = flag(code=context.get("country_code")) if context.get("country_code") else ""
    first = f"{holder_flag + ' ' if holder_flag and holder_flag != NO_FLAG else ''}**{who}** · {CLASSES.get(r.get('class') or 0, '?')} · **{fmt_time(r['duration'])}**"
    lines = [first]
    if r["kind"] in WR_KINDS:
        beat = _beat_line(r, previous, context)
        if beat:
            lines.append(beat)
    embed.description = "\n".join(lines)
    footer = []
    tier = context.get("tier")
    if tier is None and r["kind"] in ("map_wrs", "map_tops") and r.get("map") in tiers and r.get("class") in (3, 4):
        tier = tiers[r["map"]][0 if r["class"] == 3 else 1]
    if tier:
        footer.append(f"Tier {tier}")
    if context.get("completions") and r.get("class") in CLASS_KEYS:
        n = context["completions"]
        footer.append(f"{n:,} {CLASS_KEYS[r['class']]} completion{'' if n == 1 else 's'}")
    server = server_names.get(r.get("server_id") or -1)
    if server:
        footer.append(f"set on {server}")
    if footer:
        embed.set_footer(text=" · ".join(footer))
    if r.get("date"):
        embed.timestamp = datetime.fromtimestamp(r["date"], tz=timezone.utc)
    return embed


def wr_memory_rows(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The WRs in a feed, for remembering: the newest per (zone, class)."""
    best: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for r in sorted(entries, key=lambda r: r["date"]):
        if r["kind"] in WR_KINDS and r.get("zone_id") and r.get("class"):
            best[(r["zone_id"], r["class"])] = {"zone_id": r["zone_id"], "class": r["class"], "record_id": r["id"], "player_id": r.get("player_id"),
                                                "player": r["player"], "duration": r["duration"], "date": r["date"]}
    return list(best.values())
