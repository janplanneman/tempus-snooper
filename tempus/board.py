"""The status board: a Discord "components v2" panel built from the last statusList, so it's easy to test.

One panel: a header, then a heading per region (busiest first), the busy servers as rows with a Join button,
a short line for the region's quieter servers, and a menu that opens any server's full player list privately.

Discord allows 40 components and 4000 characters of text per message. A server row with its button costs 3
components, so about ten servers get a row; they go to the busiest servers first. Everything else is still
listed, without names, in its region's "Also" line, and is in the details menu.

Nothing in the text depends on the clock (map start times are Discord timestamps, rendered by each viewer),
so the same data renders the same board and the cog can skip edits when nothing changed.
"""
from __future__ import annotations

import json
import unicodedata
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
NAME_WIDTH = 18
RANK_WIDTH = 7
MAX_COMPONENTS = 40
MAX_TEXT = 4000
TEXT_BUDGET = 3900  # room for the footer's timestamp and rounding
MAX_OPTIONS = 25
NAMES_CHARS = 90  # roughly one line of names under a server row

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
    return f"[{esc(name)}]({map_url(name)})"


def sort_users(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]]) -> List[Dict[str, Any]]:
    """Best rank first; unranked and not yet looked up last."""
    def key(u: Dict[str, Any]) -> Tuple[bool, int, str]:
        r = ranks.get(u["id"])
        return r is None, r or 0, u["name"].casefold()
    return sorted(users, key=key)


def player_lines(users: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], players: int) -> List[str]:
    """Code block lines: name and overall rank, columns aligned even with wide characters."""
    shown = sort_users(users, ranks)
    lines = []
    for u in shown:
        r = ranks.get(u["id"])
        lines.append(f"{fit(display_name(u['name']) or '(unnamed)', NAME_WIDTH)} {('#' + str(r)) if r else '':>{RANK_WIDTH}}")
    if players > len(shown):
        lines.append(f"+{players - len(shown)} more")
    return lines


def names_line(s: Dict[str, Any], ranks: Dict[int, Optional[int]], budget: int = NAMES_CHARS) -> str:
    """"sgt Bytheway #50 · Fluffie #60 · +2 more": best-ranked players first, as many as fit on about one line."""
    parts: List[str] = []
    used = 0
    users = sort_users(s["users"], ranks)
    for u in users:
        name = esc((u["name"] or "(unnamed)")[:24])
        r = ranks.get(u["id"])
        part = f"{name} #{r}" if r else name
        if parts and used + len(part) + 3 > budget:
            break
        parts.append(part)
        used += len(part) + 3
    rest = s["players"] - len(parts)
    if rest > 0:
        parts.append(f"+{rest} more")
    return " · ".join(parts)


def region_order(busy: Sequence[Dict[str, Any]]) -> List[str]:
    totals: Dict[str, int] = {}
    for s in busy:
        totals[s["region"]] = totals.get(s["region"], 0) + s["players"]
    known = {r: i for i, r in enumerate(REGIONS)}
    return sorted(totals, key=lambda r: (-totals[r], known.get(r, len(known)), r))


def row_text(s: Dict[str, Any], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers]) -> str:
    first = [f"{flag(s.get('country'))} **{esc(s['shortname'])}**", map_link(s.get("map"))]
    t = tier_text(tiers.get(s.get("map") or ""))
    if t:
        first.append(t)
    first.append(f"**{s['players']}**/{s.get('max_players') or '?'}")
    lines = [" · ".join(first)]
    names = names_line(s, ranks)
    if names:
        lines.append(names)
    sub = esc(s["name"])
    if s.get("map_since"):
        sub += f" · map started <t:{int(s['map_since'])}:R>"
    lines.append(f"-# {sub}")
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
        part = f"{esc(s['shortname'])} {esc(s.get('map') or '?')} {s['players']}/{s.get('max_players') or '?'}"
        if parts and used + len(part) + 3 > budget:
            parts.append(f"+{len(servers) - i} more")
            break
        parts.append(part)
        used += len(part) + 3
    return "-# Also: " + " · ".join(parts)


def header_text(title: str, busy: Sequence[Dict[str, Any]], empty: int, region: Optional[str], usual: Optional[Tuple[int, str]], stale_since: Optional[float]) -> str:
    where = f" in {region}" if region else ""
    lines = [f"## {title}"]
    if busy:
        players = sum(s["players"] for s in busy)
        lines.append(f"**{plural(players, 'player')}** on **{plural(len(busy), 'server')}**{where} right now" + (f" · {empty} empty" if empty else ""))
    else:
        lines.append(f"Nobody is playing on Tempus{where} right now.")
    if usual:
        lines.append(f"-# Usually around {plural(usual[0], 'player')}{where} at this time on {usual[1]} (UTC)")
    if stale_since:
        lines.append(f"⚠️ No answer from Tempus since <t:{int(stale_since)}:R>, so this is the last data it sent.")
    return "\n".join(lines)


def footer_text(changed_at: Optional[float]) -> str:
    when = f"Last change <t:{int(changed_at)}:R> · " if changed_at else ""
    return f"-# {when}Data from [tempus2.xyz]({SITE_URL}) · Pick a server below for its players and Connect"


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
    return [discord.SelectOption(label=f"{s['shortname']} · {s.get('map') or '?'}"[:100], value=str(s["id"]), emoji=flag(s.get("country")),
                                 description=f"{s['players']}/{s.get('max_players') or '?'} · {s['name']}"[:100]) for s in busy[:MAX_OPTIONS]]


def render_board(servers: Sequence[Dict[str, Any]], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str] = None,
                 usual: Optional[Tuple[int, str]] = None, region: Optional[str] = None, stale_since: Optional[float] = None,
                 timeout: Optional[float] = None) -> Board:
    """usual: (players, "Fridays") usually online at this day and hour (UTC), from history.usual(). Needs a running event loop."""
    online = [s for s in servers if s.get("online") and (region is None or s["region"] == region)]
    busy = [s for s in online if s["players"] > 0]
    order = region_order(busy)
    # busiest server first; on a tie the busier region's server, so the regions shown first get the rows
    busy.sort(key=lambda s: (-s["players"], order.index(s["region"]), s["shortname"]))
    title = f"{TITLE} · {region}" if region else TITLE
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
            heading = region_heading(r, here)
            components += 1
            text += units(heading)
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
        board.add_item(ui.ActionRow(ui.Select(custom_id=DETAILS_ID, placeholder="Players and Connect for any server…", options=details_options(busy))))
    return board


def region_heading(region: str, servers: Sequence[Dict[str, Any]]) -> str:
    return f"### {region} · {plural(sum(s['players'] for s in servers), 'player')} on {plural(len(servers), 'server')}"


def render_details(s: Dict[str, Any], ranks: Dict[int, Optional[int]], tiers: Dict[str, Tiers], connect_url: Optional[str] = None) -> ui.LayoutView:
    """One server in full, for the person who picked it in the menu."""
    view = ui.LayoutView(timeout=None)
    panel = ui.Container(accent_colour=BOARD_COLOUR)
    first = [map_link(s.get("map"))]
    t = tier_text(tiers.get(s.get("map") or ""))
    if t:
        first.append(t)
    first.append(f"**{s['players']}**/{s.get('max_players') or '?'} players")
    text = f"### {flag(s.get('country'))} {esc(s['shortname'])} · {esc(s['name'])}\n" + " · ".join(first)
    if s.get("map_since"):
        text += f"\n-# Map started <t:{int(s['map_since'])}:R>"
    panel.add_item(ui.TextDisplay(text))
    lines = player_lines(s["users"], ranks, s["players"])
    if lines:
        panel.add_item(ui.TextDisplay("```\n" + "\n".join(lines) + "\n```"))
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
