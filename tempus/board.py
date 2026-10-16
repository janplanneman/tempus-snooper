"""The status board: pure rendering from the last statusList, so it's easy to test.

One embed for the headline, then one per region (busiest first). Discord allows 6000 characters across all
embeds of a message, and a server card costs ~200 before its first player (two links), so not every server
can have a card: every busy server gets a compact line, then the busiest servers are upgraded to full cards
(map, tiers, Connect, player list) while there's room. At peaks the compact lines lose their Connect link,
and if even that doesn't fit, the quietest servers become "+N more servers".

Nothing in the text depends on the clock (map start times are Discord timestamps, rendered by each viewer),
so the same data renders the same board and the cog can skip edits when nothing changed.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import discord

from .regions import REGIONS

BOARD_COLOUR = discord.Colour(0x2E86C1)
TITLE = "Tempus jump servers"
FOOTER = "Last change"
MAP_URL = "https://tempus2.xyz/maps/{}"
NAME_WIDTH = 18
RANK_WIDTH = 7
SHORT_CAP = 4  # player lines on a card when the full list doesn't fit
FIELD_LIMIT = 1024
FIELD_NAME_LIMIT = 256
MAX_FIELDS = 25
MAX_EMBEDS = 10
MESSAGE_LIMIT = 6000
# room left for the footer, the "+N more servers" line and rounding; sizes are counted in UTF-16 units as Discord does
BUDGET = 5850
CONTINUATION = "​"

Tiers = Tuple[Optional[int], Optional[int]]


def units(text: Optional[str]) -> int:
    """Length as Discord counts it (UTF-16 code units: an emoji counts twice)."""
    return len((text or "").encode("utf-16-le")) // 2


def embeds_size(embeds: Sequence[discord.Embed]) -> int:
    total = 0
    for e in embeds:
        total += units(e.title) + units(e.description) + units(e.footer.text if e.footer else None)
        total += sum(units(f.name) + units(f.value) for f in e.fields)
    return total


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def esc(text: Optional[str]) -> str:
    return discord.utils.escape_markdown(text or "?")


def inline_code(text: Optional[str]) -> str:
    return "`" + (text or "?").replace("`", "'") + "`"


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_name(name: str) -> str:
    # emoji, zero width and combining characters don't line up in a code block
    kept = [ch for ch in unicodedata.normalize("NFC", name) if ord(ch) < 128 or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "So", "Sk")]
    return "".join(kept).replace("`", "'").strip()


def fit(name: str, width: int) -> str:
    total = sum(char_width(ch) for ch in name)
    if total > width:
        out, used = [], 0
        for ch in name:
            if used + char_width(ch) > width - 1:
                break
            out.append(ch)
            used += char_width(ch)
        name, total = "".join(out) + "…", used + 1
    return name + " " * (width - total)


def tier_text(tiers: Optional[Tiers]) -> str:
    """"T4 S / T2 D" (soldier / demoman), or "" for maps Tempus doesn't list."""
    if not tiers:
        return ""
    return " / ".join(f"T{t} {cls}" for t, cls in zip(tiers, ("S", "D")) if t)


def connect_text(s: Dict[str, Any], connect_url: Optional[str]) -> str:
    # the connect page only accepts ip:port, so a server without an IP gets the console command instead
    port = s.get("port")
    if not port:
        return ""
    if s.get("ip") and connect_url:
        addr = f"{s['ip']}:{port}"
        return f"[Connect]({connect_url.format(addr=addr)})"
    host = s.get("ip") or s.get("host")
    return inline_code(f"connect {host}:{port}") if host else ""


def map_link(name: Optional[str]) -> str:
    if not name:
        return inline_code(None)
    return f"[{esc(name)}]({MAP_URL.format(quote(name, safe=''))})"


def sort_users(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]]) -> List[Dict[str, Any]]:
    """Best rank first; unranked and not yet looked up last."""
    def key(u: Dict[str, Any]) -> Tuple[bool, int, str]:
        r = ranks.get(u["id"])
        return r is None, r or 0, u["name"].casefold()
    return sorted(users, key=key)


def player_lines(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], max_lines: int, players: int) -> List[str]:
    shown = sort_users(users, ranks)[:max_lines]
    lines = []
    for u in shown:
        r = ranks.get(u["id"])
        name = display_name(u["name"]) or "(unnamed)"
        lines.append(f"{fit(name, NAME_WIDTH)} {('#' + str(r)) if r else '':>{RANK_WIDTH}}")
    if players > len(shown):
        lines.append(f"+{players - len(shown)} more")
    return lines


def server_title(s: Dict[str, Any]) -> str:
    return esc(f"{s['shortname']} · {s['name']}"[:200])[:FIELD_NAME_LIMIT]


def card(s: Dict[str, Any], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str], max_lines: int) -> Tuple[str, str]:
    """A full server card: (field name, field value)."""
    first = [map_link(s.get("map"))]
    t = tier_text(tiers.get(s.get("map") or ""))
    if t:
        first.append(t)
    if s.get("map_since"):
        first.append(f"started <t:{int(s['map_since'])}:R>")
    second = [f"{s['players']}/{s.get('max_players') or '?'}"]
    c = connect_text(s, connect_url)
    if c:
        second.append(c)
    value = " · ".join(first) + "\n" + " · ".join(second)
    lines = player_lines(s["users"], ranks, max_lines, s["players"])
    if lines:
        value += "\n```\n" + "\n".join(lines) + "\n```"
    return server_title(s), value


def compact_line(s: Dict[str, Any], tiers: Dict[str, Tiers], connect_url: Optional[str], with_connect: bool) -> str:
    parts = [f"**{esc(s['shortname'])}** {s['players']}/{s.get('max_players') or '?'}", inline_code(s.get("map"))]
    t = tier_text(tiers.get(s.get("map") or ""))
    if t:
        parts.append(t)
    c = connect_text(s, connect_url) if with_connect else ""
    if c:
        parts.append(c)
    return " · ".join(parts)


def _chunks(lines: Sequence[str], limit: int = FIELD_LIMIT) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in lines:
        if current and units(current) + 1 + units(line) > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def headline(busy: Sequence[Dict[str, Any]], empty: int, region: Optional[str]) -> str:
    where = f" in {region}" if region else ""
    if not busy:
        return f"Nobody is playing on Tempus{where} right now."
    players = sum(s["players"] for s in busy)
    text = f"{plural(players, 'player')} on {plural(len(busy), 'server')}{where} right now"
    return text + (f" ({empty} empty)" if empty else "")


def region_order(busy: Sequence[Dict[str, Any]]) -> List[str]:
    totals: Dict[str, int] = {}
    for s in busy:
        totals[s["region"]] = totals.get(s["region"], 0) + s["players"]
    known = {r: i for i, r in enumerate(REGIONS)}
    return sorted(totals, key=lambda r: (-totals[r], known.get(r, len(known)), r))


def render_board(servers: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str] = None,
                 usual: Optional[Tuple[int, str]] = None, region: Optional[str] = None, stale_since: Optional[float] = None,
                 max_lines: int = 8) -> List[discord.Embed]:
    """usual: (players, "Fridays") usually online at this day and hour (UTC), from history.usual()."""
    online = [s for s in servers if s.get("online") and (region is None or s["region"] == region)]
    busy = [s for s in online if s["players"] > 0]
    order = region_order(busy)
    # busiest server first; on a tie the busier region's server, so the regions shown first get the cards
    busy.sort(key=lambda s: (-s["players"], order.index(s["region"]), s["shortname"]))
    empty = len(online) - len(busy)
    where = f" in {region}" if region else ""

    def description(dropped: int) -> str:
        lines = [headline(busy, empty, region)]
        if usual:
            lines.append(f"Usually around {plural(usual[0], 'player')}{where} at this time on {usual[1]} (UTC)")
        if stale_since:
            lines.append(f"⚠️ No answer from Tempus since <t:{int(stale_since)}:R>, so this is the last data it sent.")
        if dropped:
            lines.append(f"+{plural(dropped, 'more server')}")
        return "\n".join(lines)

    title = f"{TITLE} · {region}" if region else TITLE
    by_region: Dict[str, List[Dict[str, Any]]] = {r: [s for s in busy if s["region"] == r] for r in order}
    region_titles = {r: f"{r} · {plural(sum(s['players'] for s in v), 'player')} on {plural(len(v), 'server')}" for r, v in by_region.items()}
    fixed = units(title) + units(description(999)) + units(FOOTER) + sum(units(t) for t in region_titles.values())
    # each region's compact list has a field name ("+12 quieter servers")
    fixed += 24 * len(order)

    def compact_lines(with_connect: bool) -> Dict[int, str]:
        return {s["id"]: compact_line(s, tiers, connect_url, with_connect) for s in busy}

    compact = compact_lines(True)
    cost = fixed + sum(units(line) + 1 for line in compact.values())
    if cost > BUDGET:
        compact = compact_lines(False)
        cost = fixed + sum(units(line) + 1 for line in compact.values())
    shown = list(busy)
    dropped = 0
    while cost > BUDGET and shown:
        s = shown.pop()  # busy is busiest first, so this is the quietest left
        cost -= units(compact[s["id"]]) + 1
        dropped += 1

    full: Dict[int, Tuple[str, str]] = {}
    for s in shown:
        for cap in dict.fromkeys((max_lines, min(SHORT_CAP, max_lines))):
            name, value = card(s, ranks, tiers, connect_url, cap)
            extra = units(name) + units(value) - units(compact[s["id"]]) - 1
            if cost + extra <= BUDGET:
                full[s["id"]] = (name, value)
                cost += extra
                break

    embeds = [discord.Embed(title=title, description=description(dropped), colour=BOARD_COLOUR)]
    shown_ids = {s["id"] for s in shown}
    for r in order:
        servers_here = [s for s in by_region[r] if s["id"] in shown_ids]
        if not servers_here:
            continue
        cards = [s for s in servers_here if s["id"] in full]
        while True:
            rest = [s for s in servers_here if s["id"] not in {c["id"] for c in cards}]
            chunks = _chunks([compact[s["id"]] for s in rest])
            if len(cards) + len(chunks) <= MAX_FIELDS or not cards:
                break
            cards.pop()  # too many fields: the quietest card goes back to a compact line
        e = discord.Embed(title=region_titles[r], colour=BOARD_COLOUR)
        for s in cards:
            name, value = full[s["id"]]
            e.add_field(name=name, value=value, inline=False)
        for i, chunk in enumerate(chunks):
            label = f"+{plural(len(rest), 'quieter server')}" if cards else plural(len(rest), "server")
            e.add_field(name=label if i == 0 else CONTINUATION, value=chunk, inline=False)
        embeds.append(e)
    return _enforce_limits(embeds[:MAX_EMBEDS])


def _enforce_limits(embeds: List[discord.Embed]) -> List[discord.Embed]:
    """Last line of defence: the budget above should already keep the board inside Discord's limits."""
    while embeds_size(embeds) + units(FOOTER) > MESSAGE_LIMIT:
        last = next((e for e in reversed(embeds) if e.fields), None)
        if last is None:
            break
        last.remove_field(len(last.fields) - 1)
    return [e for i, e in enumerate(embeds) if i == 0 or e.fields]
