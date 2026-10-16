"""The status board: a Discord "components v2" panel built from the last statusList, so it's easy to test.

One panel: a header, then a heading per region (busiest first), the busy servers as rows with a Join button,
a short line for the region's quieter servers, and a menu that opens any server's full player list privately.

A row is the server, its map and tiers, then one player per line with their overall rank in a fixed-width tag
in front, so names line up and ranks are easy to scan.

Discord allows 40 components and 4000 characters of text per message. A server row with its button costs 3
components, so only so many servers get a row; they go to the busiest servers first. Everything else is still
listed, without names, in its region's "Also" line, and is in the details menu.

Nothing in the text depends on the clock (map start times are Discord timestamps, rendered by each viewer),
so the same data renders the same board and the cog can skip edits when nothing changed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import discord
from discord import ui

from .regions import REGIONS, flag

BOARD_COLOUR = discord.Colour(0x2E86C1)
TITLE = "Tempus jump servers"
MAP_URL = "https://tempus2.xyz/maps/{}"
SERVER_URL = "https://tempus2.xyz/servers/{}"
SITE_URL = "https://tempus2.xyz"
DETAILS_ID = "tempus:details"
RANK_WIDTH = 7  # "#102681"
ROW_PLAYERS = 5  # player lines under a server row; more becomes "+N more" (the details menu lists everyone)
MAX_COMPONENTS = 40
MAX_TEXT = 4000
TEXT_BUDGET = 3900  # room for the footer's timestamp and rounding
MAX_OPTIONS = 25
NBSP = " "

Tiers = Tuple[Optional[int], Optional[int]]


def units(text: Optional[str]) -> int:
    """Length as Discord counts it (UTF-16 code units: an emoji counts twice)."""
    return len((text or "").encode("utf-16-le")) // 2


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def esc(text: Optional[str]) -> str:
    return discord.utils.escape_markdown(text or "?")


def inline_code(text: Optional[str]) -> str:
    return "`" + (text or "?").replace("`", "'") + "`"


_LABEL_SAFE = str.maketrans({"[": "(", "]": ")", "\\": "∖", "`": "'", "*": "∗", "~": "∼", "|": "∣"})


def link_label(text: Optional[str]) -> str:
    """Text for inside [label](url). Discord shows backslash escapes there literally (jump\\_map), so instead of
    escaping, the few characters that could break the link or start formatting are swapped for lookalikes.
    Underscores inside a word (jump_map) don't make italics, so they stay."""
    return (text or "?").translate(_LABEL_SAFE).strip() or "?"


def tier_text(tiers: Optional[Tiers]) -> str:
    """"T7 soldier, T3 demo", or "" for maps Tempus doesn't list."""
    if not tiers:
        return ""
    return ", ".join(f"T{t} {cls}" for t, cls in zip(tiers, ("soldier", "demo")) if t)


def connect_url_for(s: Dict[str, Any], connect_url: Optional[str]) -> Optional[str]:
    # the connect page only accepts ip:port
    if s.get("ip") and s.get("port") and connect_url:
        return connect_url.format(addr=f"{s['ip']}:{s['port']}")
    return None


def connect_command(s: Dict[str, Any]) -> Optional[str]:
    host = s.get("ip") or s.get("host")
    return inline_code(f"connect {host}:{s['port']}") if host and s.get("port") else None


def map_url(name: str) -> str:
    return MAP_URL.format(quote(name, safe=""))


def map_link(name: Optional[str]) -> str:
    if not name:
        return inline_code(None)
    return f"[{link_label(name)}]({map_url(name)})"


def sort_users(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]]) -> List[Dict[str, Any]]:
    """Best rank first; unranked and not yet looked up last."""
    def key(u: Dict[str, Any]) -> Tuple[bool, int, str]:
        r = ranks.get(u["id"])
        return r is None, r or 0, u["name"].casefold()
    return sorted(users, key=key)


def rank_tag(rank: Optional[int]) -> str:
    """The overall rank as a fixed-width grey tag, so the names after it line up: `   #277`, or `      –` when
    unranked or not looked up yet. Padded with no-break spaces, which Discord doesn't trim inside inline code."""
    text = f"#{rank}" if rank else "–"
    return "`" + NBSP * max(0, RANK_WIDTH - len(text)) + text + "`"


def player_lines(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], players: int, limit: Optional[int] = None) -> List[str]:
    """One player per line, rank tag first, best-ranked first. With a limit, the last line becomes "+N more"."""
    shown = sort_users(users, ranks)
    if limit is not None and len(shown) > limit:
        shown = shown[:limit - 1]
    lines = [f"{rank_tag(ranks.get(u['id']))} {esc((u['name'] or '(unnamed)')[:32])}" for u in shown]
    if players > len(shown):
        lines.append(f"-# +{players - len(shown)} more")
    return lines


def region_order(busy: Sequence[Dict[str, Any]]) -> List[str]:
    totals: Dict[str, int] = {}
    for s in busy:
        totals[s["region"]] = totals.get(s["region"], 0) + s["players"]
    known = {r: i for i, r in enumerate(REGIONS)}
    return sorted(totals, key=lambda r: (-totals[r], known.get(r, len(known)), r))


def server_line(s: Dict[str, Any]) -> str:
    return f"{flag(s.get('country'))} **{esc(s['shortname'])}** {esc(s['name'])} ({s['players']}/{s.get('max_players') or '?'})"


def map_line(s: Dict[str, Any], tiers: Dict[str, Tiers]) -> str:
    t = tier_text(tiers.get(s.get("map") or ""))
    return map_link(s.get("map")) + (f", {t}" if t else "")


def row_text(s: Dict[str, Any], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers]) -> str:
    lines = [server_line(s), map_line(s, tiers)] + player_lines(s["users"], ranks, s["players"], ROW_PLAYERS)
    if s.get("map_since"):
        lines.append(f"-# map started <t:{int(s['map_since'])}:R>")
    return "\n".join(lines)


def row_button(s: Dict[str, Any], connect_url: Optional[str]) -> ui.Button:
    url = connect_url_for(s, connect_url)
    if url:
        return ui.Button(label="Join", url=url, style=discord.ButtonStyle.link)
    return ui.Button(label="Info", url=SERVER_URL.format(s["id"]), style=discord.ButtonStyle.link)


def also_line(servers: Sequence[Dict[str, Any]], budget: int) -> str:
    """The quieter servers of a region on one subtext line, cut short with "+N more" if it gets long."""
    parts: List[str] = []
    used = 0
    for i, s in enumerate(servers):
        part = f"{esc(s['shortname'])} {link_label(s.get('map'))} ({s['players']}/{s.get('max_players') or '?'})"
        if parts and used + len(part) + 2 > budget:
            parts.append(f"+{len(servers) - i} more")
            break
        parts.append(part)
        used += len(part) + 2
    return "-# Also " + ", ".join(parts)


def region_heading(region: str, servers: Sequence[Dict[str, Any]]) -> str:
    return f"### {region}\n-# {plural(sum(s['players'] for s in servers), 'player')} on {plural(len(servers), 'server')}"


def header_text(title: str, busy: Sequence[Dict[str, Any]], empty: int, region: Optional[str], usual: Optional[Tuple[int, str]], stale_since: Optional[float]) -> str:
    where = f" in {region}" if region else ""
    lines = [f"## {title}"]
    if busy:
        players = sum(s["players"] for s in busy)
        lines.append(f"**{plural(players, 'player')}** on **{plural(len(busy), 'server')}**{where} right now" + (f", {empty} empty" if empty else ""))
    else:
        lines.append(f"Nobody is playing on Tempus{where} right now.")
    if usual:
        lines.append(f"-# Usually around {plural(usual[0], 'player')}{where} at this time on {usual[1]} (UTC)")
    if stale_since:
        lines.append(f"⚠️ No answer from Tempus since <t:{int(stale_since)}:t> (<t:{int(stale_since)}:R>), so this is the last data it sent.")
    return "\n".join(lines)


def footer_text(changed_at: Optional[float]) -> str:
    when = f"Last change <t:{int(changed_at)}:R>. " if changed_at else ""
    return f"-# {when}Data from [tempus2.xyz]({SITE_URL}). Pick a server below to see everyone on it."


class Board(ui.LayoutView):
    """The panel, with a handle on the footer so the cog can stamp the time of the last change after comparing."""

    def __init__(self, timeout: Optional[float]) -> None:
        super().__init__(timeout=timeout)
        self.footer: Optional[ui.TextDisplay] = None

    def signature(self) -> str:
        """Everything but the footer's timestamp: equal signatures mean nothing to edit."""
        saved = self.footer.content if self.footer else None
        if self.footer:
            self.footer.content = ""
        try:
            return json.dumps(self.to_components(), sort_keys=True, ensure_ascii=False)
        finally:
            if self.footer:
                self.footer.content = saved or ""


def details_options(busy: Sequence[Dict[str, Any]]) -> List[discord.SelectOption]:
    return [discord.SelectOption(label=f"{s['shortname']} {s.get('map') or '?'}"[:100], value=str(s["id"]), emoji=flag(s.get("country")),
                                 description=f"{s['players']}/{s.get('max_players') or '?'} players, {s['name']}"[:100]) for s in busy[:MAX_OPTIONS]]


def render_board(servers: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str] = None,
                 usual: Optional[Tuple[int, str]] = None, region: Optional[str] = None, stale_since: Optional[float] = None,
                 timeout: Optional[float] = None) -> Board:
    """usual: (players, "Fridays") usually online at this day and hour (UTC), from history.usual(). Needs a running event loop."""
    online = [s for s in servers if s.get("online") and (region is None or s["region"] == region)]
    busy = [s for s in online if s["players"] > 0]
    order = region_order(busy)
    # busiest server first; on a tie the busier region's server, so the regions shown first get the rows
    busy.sort(key=lambda s: (-s["players"], order.index(s["region"]), s["shortname"]))
    title = f"{TITLE} in {region}" if region else TITLE
    header = header_text(title, busy, len(online) - len(busy), region, usual, stale_since)
    footer = footer_text(0)  # same length as with a real timestamp, near enough
    rows: Dict[int, str] = {}

    def layout(with_rows: Dict[int, str]) -> Tuple[int, int, Dict[str, str]]:
        """(components, text units, region -> its "Also" line) for a set of rows."""
        components = 4 + (2 if busy else 0)  # container, header, separator, footer; the menu's row and select
        text = units(header) + units(footer)
        also: Dict[str, str] = {}
        for r in order:
            here = [s for s in busy if s["region"] == r]
            rest = [s for s in here if s["id"] not in with_rows]
            components += 1
            text += units(region_heading(r, here))
            for s in here:
                if s["id"] in with_rows:
                    components += 3
                    text += units(with_rows[s["id"]])
            if rest:
                also[r] = also_line(rest, 400)
                text += units(also[r]) + 1
                components += 1 if len(rest) < len(here) else 0  # without rows it shares the heading's text
        return components, text, also

    for s in busy:
        trial = dict(rows)
        trial[s["id"]] = row_text(s, ranks, tiers)
        components, text, _ = layout(trial)
        if components <= MAX_COMPONENTS and text <= TEXT_BUDGET:
            rows = trial
    _, text, also = layout(rows)
    if text > TEXT_BUDGET:  # only with very many busy servers: shorten the "Also" lines
        share = max(60, (TEXT_BUDGET - (text - sum(units(a) for a in also.values()))) // max(1, len(also)))
        also = {r: also_line([s for s in busy if s["region"] == r and s["id"] not in rows], share) for r in also}

    board = Board(timeout=timeout)
    panel = ui.Container(accent_colour=BOARD_COLOUR)
    panel.add_item(ui.TextDisplay(header))
    # one rule under the header; the region headings separate the rest, and every component counts
    panel.add_item(ui.Separator())
    for r in order:
        here = [s for s in busy if s["region"] == r]
        heading = region_heading(r, here)
        with_rows = [s for s in here if s["id"] in rows]
        if not with_rows:
            panel.add_item(ui.TextDisplay(f"{heading}\n{also[r]}" if r in also else heading))
            continue
        panel.add_item(ui.TextDisplay(heading))
        for s in with_rows:
            panel.add_item(ui.Section(ui.TextDisplay(rows[s["id"]]), accessory=row_button(s, connect_url)))
        if r in also:
            panel.add_item(ui.TextDisplay(also[r]))
    board.footer = ui.TextDisplay(footer_text(None))
    panel.add_item(board.footer)
    board.add_item(panel)
    if busy:
        board.add_item(ui.ActionRow(ui.Select(custom_id=DETAILS_ID, placeholder="See everyone on a server…", options=details_options(busy))))
    return board


def render_details(s: Dict[str, Any], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str] = None) -> ui.LayoutView:
    """One server in full, for the person who picked it in the menu."""
    view = ui.LayoutView(timeout=None)
    panel = ui.Container(accent_colour=BOARD_COLOUR)
    text = f"### {server_line(s)}\n{map_line(s, tiers)}"
    if s.get("map_since"):
        text += f"\n-# map started <t:{int(s['map_since'])}:R>"
    panel.add_item(ui.TextDisplay(text))
    lines = player_lines(s["users"], ranks, s["players"])
    if lines:
        panel.add_item(ui.TextDisplay("\n".join(lines)))
    url = connect_url_for(s, connect_url)
    if not url and connect_command(s):
        panel.add_item(ui.TextDisplay(f"Join from the TF2 console: {connect_command(s)}"))
    buttons = [ui.Button(label="Join", url=url, style=discord.ButtonStyle.link)] if url else []
    if s.get("map"):
        buttons.append(ui.Button(label="Map on Tempus", url=map_url(s["map"]), style=discord.ButtonStyle.link))
    buttons.append(ui.Button(label="Server on Tempus", url=SERVER_URL.format(s["id"]), style=discord.ButtonStyle.link))
    panel.add_item(ui.ActionRow(*buttons))
    view.add_item(panel)
    return view
