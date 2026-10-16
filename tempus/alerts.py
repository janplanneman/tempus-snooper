"""Server down alerts for an admin channel: a visible Tempus server that stays offline for a few minutes is
reported once, and again when it's back. A server that's offline when the bot starts counts from then.

Hidden servers (dev, retired) are never reported, and a server that disappears from the list or becomes hidden
while it's down is dropped quietly: that's Tempus retiring it, not an outage. When the Tempus API itself doesn't
answer, nothing is known about the servers, so that's its own alert instead.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord

from .board import esc
from .regions import flag

DOWN_COLOUR = discord.Colour(0xC0392B)
UP_COLOUR = discord.Colour(0x27AE60)

State = Dict[str, Dict[str, Any]]  # server id -> {"since": offline since, "alerted": reported yet}


def check_servers(prev: State, servers: Sequence[Dict[str, Any]], now: float, threshold_s: float) -> Tuple[State, List[Dict[str, Any]]]:
    """(new state, events) where an event is {"kind": "down" | "up", "server": ..., "since": ...}."""
    state: State = {}
    events: List[Dict[str, Any]] = []
    for s in servers:
        key = str(s["id"])
        old = prev.get(key)
        if not s["online"]:
            since = old["since"] if old else now
            alerted = bool(old and old["alerted"])
            if not alerted and now - since >= threshold_s:
                events.append({"kind": "down", "server": s, "since": since})
                alerted = True
            state[key] = {"since": since, "alerted": alerted}
        elif old and old["alerted"]:
            events.append({"kind": "up", "server": s, "since": old["since"], "until": now})
    return state, events


def since_text(ts: float) -> str:
    """"17:54 (6 minutes ago)", each part shown by Discord in the viewer's own time."""
    return f"<t:{int(ts)}:t> (<t:{int(ts)}:R>)"


def duration_text(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def _addr(s: Dict[str, Any]) -> Optional[str]:
    host = s.get("ip") or s.get("host")
    return f"{host}:{s['port']}" if host and s.get("port") else None


def server_embed(event: Dict[str, Any]) -> discord.Embed:
    s = event["server"]
    who = f"{flag(s.get('country'))} **{esc(s['shortname'])}** {esc(s['name'])}"
    addr = _addr(s)
    if event["kind"] == "down":
        lines = [f"🔴 {who} is offline", f"-# Offline since {since_text(event['since'])}" + (f", address {addr}" if addr else "")]
        colour = DOWN_COLOUR
    else:
        lines = [f"🟢 {who} is back online", f"-# It was down for {duration_text(event['until'] - event['since'])}"]
        colour = UP_COLOUR
    return discord.Embed(description="\n".join(lines), colour=colour)


def api_embed(down: bool, since: float, now: float) -> discord.Embed:
    if down:
        text = f"⚠️ The Tempus API hasn't answered since {since_text(since)}, so server status is unknown.\n-# The board shows the last data it sent."
        return discord.Embed(description=text, colour=DOWN_COLOUR)
    return discord.Embed(description=f"✅ The Tempus API is answering again.\n-# It was out for {duration_text(now - since)}", colour=UP_COLOUR)
