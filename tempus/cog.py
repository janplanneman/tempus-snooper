from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from . import alerts, history, records
from .board import BOARD_COLOUR, DETAILS_ID, Board, connect_command, connect_url_for, esc, footer_text, map_link, plural, rank_tag, render_board, render_details
from .config import Config
from .ranks import RankService
from .regions import REGIONS, flag
from .storage import Storage
from .tempus_client import NotFound, TempusClient
from .tracker import Tracker, now

log = logging.getLogger(__name__)

REGION_CHOICES = [app_commands.Choice(name=r, value=r) for r in REGIONS]
WHERE_MAX = 20
SEEN_RECORDS_KEEP_S = 30 * 86400
# the board's message format; a board in an older format is replaced by a new message, since Discord can't edit
# an embed message into a components v2 one
BOARD_FORMAT = "2"
# leaderboard lookups for WR posts per activity check; the rest are posted without "what it beat"
MAX_WR_LOOKUPS = 10
EPHEMERAL_VIEW_TIMEOUT = 600
# a new WR's demo is usually uploaded when the server's recording ends, often an hour later: look again after
# these delays (seconds after the post, then after each check), then give up
DEMO_CHECKS = (600, 1200, 2400, 4800, 9600, 19200)
DEMO_CHECKS_PER_RUN = 5


def _ts(t: Optional[float], style: str = "R") -> str:
    return f"<t:{int(t)}:{style}>" if t else "never"


def list_messages(title: str, lines: List[str]) -> List[List[discord.Embed]]:
    # discord allows 4096 chars per embed and 6000 across all embeds in one message
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 3900:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    chunks.append(current)
    messages, batch, size = [], [], 0
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(title=title if i == 0 else None, description=chunk, colour=BOARD_COLOUR)
        if batch and (size + len(chunk) + 100 > 5900 or len(batch) == 10):
            messages.append(batch)
            batch, size = [], 0
        batch.append(embed)
        size += len(chunk) + len(embed.title or "")
    messages.append(batch)
    return messages


class ServersCog(commands.Cog):
    def __init__(self, bot: commands.Bot, cfg: Config, store: Storage, client: TempusClient, tracker: Tracker, ranks: RankService) -> None:
        self.bot = bot
        self.cfg = cfg
        self.store = store
        self.client = client
        self.tracker = tracker
        self.ranks = ranks
        self._boards: Dict[int, Dict[str, Optional[int]]] = {}
        self._sent: Dict[int, str] = {}  # guild id -> signature of the board last sent there
        self._signature: Optional[str] = None
        self._changed_at = now()
        self._warned: set = set()
        self._last_snapshot: Optional[float] = None
        self._last_prune = 0.0
        self._last_records_prune = 0.0
        self._usual: Dict[Optional[str], Tuple[float, Optional[Tuple[int, str]]]] = {}
        self._alerts: Optional[Dict[str, Any]] = None
        tracker.after_cycle = self.update_board
        if cfg.records_channel_name:
            tracker.on_activity = self.on_activity
            tracker.on_new_maps = self.on_new_maps

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.cfg.admin_user_ids:
            return True
        perms = getattr(interaction.user, "guild_permissions", None)
        return bool(perms and perms.administrator)

    def _warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    def _ok(self, guild: discord.Guild, what: str) -> None:
        self._warned = {k for k in self._warned if not k.startswith(f"{guild.id}:{what}:")}

    async def build_board(self, region: Optional[str] = None, timeout: Optional[float] = None) -> Optional[Board]:
        """None until the first statusList has arrived."""
        if self.tracker.updated_at is None:
            return None
        servers = self.tracker.servers
        ranks = self.ranks.known(u["id"] for s in servers for u in s["users"])
        return render_board(servers, ranks, self.tracker.tiers, connect_url=self.cfg.connect_url, usual=await self._usual_now(region), region=region,
                            stale_since=self.tracker.stale_since(), timeout=timeout)

    async def _record_history(self) -> None:
        """A history snapshot every SNAPSHOT_INTERVAL_MINUTES: players online and the servers they're on."""
        if self.tracker.updated_at is None or self.tracker.stale_since() is not None:
            return  # stale numbers would make a fake flat line; a gap is honest
        t = now()
        if self._last_snapshot is None:
            saved = await self.store.kv_get("last_snapshot")
            self._last_snapshot = float(saved) if saved else 0.0
        if t - self._last_snapshot < self.cfg.snapshot_interval_s:
            return
        busy = [{"server_id": s["id"], "region": s["region"], "map": s["map"], "players": s["players"]} for s in self.tracker.servers if s["online"] and s["players"] > 0]
        await self.store.record_snapshot(t, busy)
        await self.store.kv_set("last_snapshot", str(t))
        self._last_snapshot = t
        if t - self._last_prune > 86400:
            self._last_prune = t
            await self.store.prune_snapshots(t - self.cfg.history_keep_days * 86400)

    async def _usual_now(self, region: Optional[str] = None) -> Optional[Tuple[int, str]]:
        # recalculated at most every few minutes; the board updates every cycle
        t = now()
        at, value = self._usual.get(region, (0.0, None))
        if t - at > 300:
            value = history.usual(await self.store.snapshots_since(t - 9 * 7 * 86400, region), t)
            self._usual[region] = (t, value)
        return value

    async def _board_state(self, guild: discord.Guild) -> Dict[str, Optional[int]]:
        if guild.id not in self._boards:
            saved = await self.store.kv_get(f"status_board:{guild.id}") or ""
            ch, msg, fmt = (saved.split(":") + ["", "", ""])[:3]
            self._boards[guild.id] = {"channel_id": int(ch) if ch.isdigit() else None, "msg_id": int(msg) if msg.isdigit() else None, "format": fmt or None}
        return self._boards[guild.id]

    async def _save_board(self, guild: discord.Guild, state: Dict[str, Any]) -> None:
        await self.store.kv_set(f"status_board:{guild.id}", f"{state['channel_id'] or ''}:{state['msg_id'] or ''}:{state.get('format') or ''}")

    async def _board_channel(self, guild: discord.Guild, state: Dict[str, Optional[int]]) -> Optional[discord.TextChannel]:
        if self.cfg.status_channel_id:
            channel = guild.get_channel(self.cfg.status_channel_id)
            if channel is not None:
                return channel
        if state["channel_id"]:
            channel = guild.get_channel(state["channel_id"])
            if channel is not None:
                return channel
            log.info("Status board channel in %s is gone; looking for #%s again", guild.name, self.cfg.status_channel_name)
        channel = discord.utils.get(guild.text_channels, name=self.cfg.status_channel_name)
        if channel is None:
            if state["channel_id"]:
                state.update(channel_id=None, msg_id=None)
                await self._save_board(guild, state)
            return None
        if channel.id != state["channel_id"]:
            state.update(channel_id=channel.id, msg_id=None)
            self._sent.pop(guild.id, None)
            await self._save_board(guild, state)
            log.info("Status board channel: #%s in %s (%s)", channel.name, guild.name, channel.id)
        return channel

    def _missing_perms(self, guild: discord.Guild, channel: Any, what: str) -> bool:
        perms = channel.permissions_for(guild.me)
        missing = [label for attr, label in (("view_channel", "View Channel"), ("send_messages", "Send Messages"), ("embed_links", "Embed Links")) if not getattr(perms, attr)]
        if missing:
            self._warn_once(f"{guild.id}:{what}:perms:{','.join(missing)}", "Found #%s in %s but the bot is missing %s there; will keep retrying", channel.name, guild.name, ", ".join(missing))
        return bool(missing)

    async def update_board(self) -> None:
        try:
            await self._record_history()
        except Exception:
            log.exception("Could not record a history snapshot")
        try:
            await self._check_alerts()
        except Exception:
            log.exception("Could not check for server alerts")
        if not self.bot.is_ready():
            return
        board = await self.build_board()
        if board is None:
            return
        sig = board.signature()
        if sig != self._signature:
            self._signature, self._changed_at = sig, now()
        for i, guild in enumerate(list(self.bot.guilds)):
            try:
                # a fresh panel per message: discord.py keeps one view object per message for the details menu
                guild_board = board if i == 0 else await self.build_board()
                if guild_board is None:
                    continue
                # the time is the last real change, so an unchanged board needs no edit
                if guild_board.footer is not None:
                    guild_board.footer.content = footer_text(self._changed_at)
                await self._update_guild_board(guild, guild_board, sig)
            except Exception:
                log.exception("Status board update failed in %s", guild.name)

    async def _update_guild_board(self, guild: discord.Guild, board: Board, sig: str) -> None:
        # every server with a #tempus-status channel gets its own board; missing perms are retried every update
        state = await self._board_state(guild)
        channel = await self._board_channel(guild, state)
        if channel is None or self._missing_perms(guild, channel, "board"):
            return
        if state["msg_id"] and state.get("format") != BOARD_FORMAT:
            try:
                await channel.get_partial_message(state["msg_id"]).delete()
            except discord.HTTPException as e:
                log.info("Could not delete the old-format status board in %s: %s", guild.name, e)
            log.info("Replacing the old-format status board in %s", guild.name)
            state["msg_id"] = None
        if state["msg_id"]:
            if self._sent.get(guild.id) == sig:
                return
            try:
                await channel.get_partial_message(state["msg_id"]).edit(view=board)
                self._sent[guild.id] = sig
                self._ok(guild, "board")
                return
            except discord.NotFound:
                log.info("Status board message in %s was deleted; posting a new one", guild.name)
                state["msg_id"] = None
            except discord.HTTPException as e:
                self._warn_once(f"{guild.id}:board:edit", "Could not edit the status board in %s: %s; will keep retrying", guild.name, e)
                return
        try:
            msg = await channel.send(view=board)
        except discord.HTTPException as e:
            self._warn_once(f"{guild.id}:board:send", "Could not post the status board in %s: %s; will keep retrying", guild.name, e)
            return
        state["msg_id"] = msg.id
        state["format"] = BOARD_FORMAT
        self._sent[guild.id] = sig
        await self._save_board(guild, state)
        self._ok(guild, "board")
        log.info("Posted a new status board in #%s (%s)", channel.name, guild.name)

    async def _post(self, channel_name: Optional[str], what: str, embeds: List[discord.Embed]) -> List[Tuple[int, int]]:
        """Post to the channel with this name in every server that has one; up to 10 embeds per message.
        Returns (channel id, message id) for each message sent."""
        sent: List[Tuple[int, int]] = []
        if not channel_name or not embeds or not self.bot.is_ready():
            return sent
        for guild in list(self.bot.guilds):
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel is None or self._missing_perms(guild, channel, what):
                continue
            try:
                for i in range(0, len(embeds), 10):
                    msg = await channel.send(embeds=embeds[i:i + 10])
                    sent.append((channel.id, msg.id))
                self._ok(guild, what)
            except discord.HTTPException as e:
                self._warn_once(f"{guild.id}:{what}:send", "Could not post %s in %s: %s", what, guild.name, e)
            except Exception:
                log.exception("Could not post %s in %s", what, guild.name)
        return sent

    async def on_activity(self, data: Any) -> None:
        entries = records.flatten(data)
        t = now()
        seen = await self.store.seen_record_ids()
        if not await self.store.kv_get("records_primed"):
            # first run: what's in the feed now is old news, but its WRs are what the next WRs will beat
            await self.store.mark_records_seen(((r["id"], r["kind"]) for r in entries), t)
            await self.store.save_wrs(records.wr_memory_rows(entries))
            await self.store.kv_set("records_primed", str(t))
            log.info("Records feed started; %d records already in the feed won't be posted", len(entries))
            return
        fresh = records.new_records(entries, seen, self.cfg.records_include_tops, t)
        # everything in the feed is marked seen, posted or not (tops when they're off, old ones after downtime)
        await self.store.mark_records_seen(((r["id"], r["kind"]) for r in entries if r["id"] not in seen), t)
        if t - self._last_records_prune > 86400:
            self._last_records_prune = t
            await self.store.prune_seen_records(t - SEEN_RECORDS_KEEP_S)
        posts = await self._record_posts(fresh) if fresh and self.bot.is_ready() else []
        # after the posts have used the old WRs
        await self.store.save_wrs(records.wr_memory_rows(entries))
        if posts:
            log.info("Posting %d new records", len(posts))
        for p in posts:
            # one message per record, so it can be edited when its demo turns up
            messages = await self._post(self.cfg.records_channel_name, "records", [self._record_embed(p)])
            if messages and p["record"]["kind"] in records.WR_KINDS and not p["demo"]:
                await self.store.add_pending_demo(p["record"]["id"], p, messages, t + DEMO_CHECKS[0])
        await self._check_demos()

    async def _record_posts(self, fresh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """What each new record's post is built from: kept as JSON, so the post can be rebuilt with its demo later."""
        names = {s["id"]: s["shortname"] for s in self.tracker.servers}
        posts = []
        lookups = 0
        for r in fresh:
            previous: Optional[Dict[str, Any]] = None
            context: Dict[str, Any] = {}
            if r["kind"] in records.WR_KINDS and r.get("zone_id") and r.get("class"):
                previous = await self.store.get_wr(r["zone_id"], r["class"])
                if lookups < MAX_WR_LOOKUPS:
                    lookups += 1
                    try:
                        context = records.leaderboard_context(await self.client.get(f"zones/id/{r['zone_id']}/records/list?limit=3"), r["class"])
                    except NotFound:
                        pass
                    except Exception as e:
                        log.warning("Could not read the leaderboard of zone %s: %s", r["zone_id"], e)
                # two WRs on one zone in the same check: the second beats the first
                await self.store.save_wrs(records.wr_memory_rows([r]))
            demo = context.get("demo") if (context.get("demo") or {}).get("record_id") == r["id"] else None
            posts.append({"record": r, "previous": previous, "context": context, "server": names.get(r.get("server_id") or -1), "demo": demo})
        return posts

    def _record_embed(self, post: Dict[str, Any]) -> discord.Embed:
        r = post["record"]
        names = {r["server_id"]: post["server"]} if post.get("server") and r.get("server_id") else {}
        return records.record_embed(r, self.tracker.tiers, names, post.get("previous"), post.get("context"), post.get("demo"))

    async def _check_demos(self) -> None:
        """WR posts whose demo wasn't uploaded yet: look again on a backoff and edit the posts once it's there."""
        if not self.bot.is_ready():
            return
        for p in await self.store.due_pending_demos(now(), DEMO_CHECKS_PER_RUN):
            rid = p["record_id"]
            try:
                demo = records.demo_from_overview(await self.client.get(f"records/id/{rid}/overview"))
            except NotFound:
                await self.store.delete_pending_demo(rid)
                continue
            except Exception as e:
                log.warning("Could not check the demo of record %s: %s", rid, e)
                demo = None
            if not demo:
                attempts = p["attempts"] + 1
                if attempts >= len(DEMO_CHECKS):
                    await self.store.delete_pending_demo(rid)
                else:
                    await self.store.retry_pending_demo(rid, attempts, now() + DEMO_CHECKS[attempts])
                continue
            post = dict(p["post"], demo=demo)
            embed = self._record_embed(post)
            for channel_id, message_id in p["messages"]:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    continue
                try:
                    await channel.get_partial_message(message_id).edit(embed=embed)
                except discord.HTTPException as e:
                    log.info("Could not add the demo to record post %s: %s", message_id, e)
            await self.store.delete_pending_demo(rid)
            log.info("Added the demo to the post of record %s", rid)

    async def on_new_maps(self, maps: List[Dict[str, Any]]) -> None:
        log.info("New maps on Tempus: %s", ", ".join(m["name"] for m in maps))
        await self._post(self.cfg.records_channel_name, "records", [records.new_map_embed(m) for m in maps])

    async def _alert_state_loaded(self) -> Dict[str, Any]:
        if self._alerts is None:
            saved = await self.store.kv_get("alert_state")
            self._alerts = json.loads(saved) if saved else {}
        return self._alerts

    async def _check_alerts(self) -> None:
        """Servers that went offline (or came back), and the Tempus API not answering, to the alerts channel."""
        if not self.cfg.alerts_channel_name or not self.bot.is_ready():
            return
        state = await self._alert_state_loaded()
        before = json.dumps(state, sort_keys=True)
        t = now()
        embeds: List[discord.Embed] = []
        last_ok = self.tracker.updated_at or self.tracker.health.get("started_at") or t
        api = state.get("api")
        if t - last_ok > max(self.cfg.offline_alert_s, 3 * self.cfg.poll_interval_s):
            if not api:
                embeds.append(alerts.api_embed(True, last_ok, t))
                state["api"] = {"since": last_ok}
        else:
            if api:
                embeds.append(alerts.api_embed(False, api["since"], t))
                state.pop("api")
            # server status is only known from a poll that worked
            if self.tracker.health.get("last_cycle_ok"):
                state["servers"], events = alerts.check_servers(state.get("servers", {}), self.tracker.servers, t, self.cfg.offline_alert_s)
                embeds += [alerts.server_embed(e) for e in events]
                for e in events:
                    log.info("Server %s is %s", e["server"]["shortname"], "offline" if e["kind"] == "down" else "back online")
        if json.dumps(state, sort_keys=True) != before:
            await self.store.kv_set("alert_state", json.dumps(state))
        await self._post(self.cfg.alerts_channel_name, "alerts", embeds)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """The board's "players and Connect" menu. Handled by its id, so menus on boards posted before a restart work too."""
        data = interaction.data or {}
        if interaction.type != discord.InteractionType.component or data.get("custom_id") != DETAILS_ID:
            return
        values = data.get("values") or []
        s = next((s for s in self.tracker.servers if values and str(s["id"]) == values[0]), None)
        if s is None or not s["online"]:
            await interaction.response.send_message("That server isn't online any more.", ephemeral=True)
            return
        ranks = self.ranks.known(u["id"] for u in s["users"])
        await interaction.response.send_message(view=render_details(s, ranks, self.tracker.tiers, self.cfg.connect_url), ephemeral=True)

    @app_commands.command(name="servers", description="Who is playing on Tempus jump servers right now")
    @app_commands.describe(region="Only this region, with room for more servers")
    @app_commands.choices(region=REGION_CHOICES)
    async def servers(self, interaction: discord.Interaction, region: Optional[app_commands.Choice[str]] = None) -> None:
        board = await self.build_board(region.value if region else None, timeout=EPHEMERAL_VIEW_TIMEOUT)
        if board is None:
            await interaction.response.send_message("Still waiting for the first reply from Tempus; try again in a minute.", ephemeral=True)
            return
        await interaction.response.send_message(view=board, ephemeral=True)

    @app_commands.command(name="where", description="Which Tempus server a player is on right now")
    @app_commands.describe(player="Part of the player's name")
    async def where(self, interaction: discord.Interaction, player: str) -> None:
        query = player.strip().casefold()
        if len(query) < 2:
            await interaction.response.send_message("Give me at least 2 characters of the name.", ephemeral=True)
            return
        if self.tracker.updated_at is None:
            await interaction.response.send_message("Still waiting for the first reply from Tempus; try again in a minute.", ephemeral=True)
            return
        found = [(u, s) for s in self.tracker.servers for u in s["users"] if query in u["name"].casefold()]
        found.sort(key=lambda us: us[0]["name"].casefold())
        if not found:
            await interaction.response.send_message(f"Nobody matching **{esc(player.strip())}** is on a Tempus server right now.", ephemeral=True)
            return
        ranks = self.ranks.known(u["id"] for u, _ in found)
        lines = []
        for u, s in found[:WHERE_MAX]:
            url = connect_url_for(s, self.cfg.connect_url)
            join = f", [Join]({url})" if url else (f", {connect_command(s)}" if connect_command(s) else "")
            lines.append(f"{rank_tag(ranks.get(u['id']))} **{esc(u['name'] or '(unnamed)')}** is on {flag(s.get('country'))} **{esc(s['shortname'])}** "
                         f"{esc(s['name'])}, {map_link(s.get('map'))}{join}")
        if len(found) > WHERE_MAX:
            lines.append(f"…and {len(found) - WHERE_MAX} more. Try more of the name.")
        stale = self.tracker.stale_since()
        if stale:
            lines.append(f"⚠️ No answer from Tempus since {_ts(stale)}, so this may be out of date.")
        title = f"{plural(len(found), 'player')} matching \"{esc(player.strip()[:50])}\""
        messages = list_messages(title, lines)
        await interaction.response.send_message(embeds=messages[0], ephemeral=True)

    @app_commands.command(name="activity", description="When Tempus is busiest, by day and hour (UTC)")
    @app_commands.describe(region="Only servers in this region")
    @app_commands.choices(region=REGION_CHOICES)
    async def activity(self, interaction: discord.Interaction, region: Optional[app_commands.Choice[str]] = None) -> None:
        r = region.value if region else None
        t = now()
        recent = await self.store.snapshots_since(t - 8 * 7 * 86400, r)
        cells = history.grid(recent)
        first = await self.store.first_snapshot()
        what = f"Tempus in {r}" if r else "Tempus"
        embed = discord.Embed(title=f"When {what} is busiest", colour=BOARD_COLOUR)
        if not any(v is not None for row in cells for v in row):
            embed.description = (f"Collecting history since {_ts(first, 'f') if first else 'now'}. Each hour of the week needs at least "
                                 f"{history.MIN_DAYS} days of data, so the chart fills in over the next couple of weeks. "
                                 "Time the bot is offline leaves gaps, which push this back.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed.description = f"Average players online{f' in {r}' if r else ''}, last 8 weeks (hours in UTC)\n```\n" + "\n".join(history.chart(cells)) + "\n```" + history.legend(cells)
        top = history.busiest(cells)
        if top:
            embed.add_field(name="Busiest", value="\n".join(f"{history.DAYS[d]} {h:02d}:00 UTC, about {plural(round(v), 'player')}" for d, h, v in top), inline=True)
        best = history.record(await self.store.snapshots_since(0, r))
        if best:
            embed.add_field(name="Record", value=f"{plural(best[0], 'player')}\n{_ts(best[1], 'f')}", inline=True)
        days = len({history.local(s["ts"]).date() for s in recent})
        embed.set_footer(text=f"From {plural(days, 'day')} of history")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="(Admin) Bot health, Tempus API usage and caches")
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        h = self.tracker.health
        if h.get("last_cycle_end") is None:
            health = "⏳ first poll not finished yet"
        elif h.get("last_cycle_ok"):
            health = f"✅ last poll {_ts(h['last_cycle_end'])} ({h['last_cycle_duration']:.1f}s)"
        else:
            health = f"❌ {h['consecutive_failures']} failed poll(s) in a row, last {_ts(h['last_cycle_end'])}\n`{(h.get('last_error') or '')[:200]}`"
        if h.get("last_cycle_end") and now() - h["last_cycle_end"] > 3 * self.cfg.poll_interval_s:
            health += "\n⚠️ poll loop looks stalled"
        if self.tracker.updated_at:
            health += f"\ndata from {_ts(self.tracker.updated_at)}"
        c = self.tracker.counts
        busy = [s for s in self.tracker.servers if s["online"] and s["players"]]
        st = self.client.stats()
        embed = discord.Embed(title="Tempus Snooper status", colour=discord.Colour.blurple())
        embed.add_field(name="Poll health", value=health, inline=False)
        embed.add_field(name="Servers", value=f"{c.get('visible', 0)} visible, {c.get('offline', 0)} offline\n{c.get('hidden', 0)} hidden, {c.get('bad', 0)} unreadable\n{len(busy)} busy, {sum(s['players'] for s in busy)} players", inline=True)
        embed.add_field(name="Ranks", value=f"{len(self.ranks)} cached\n{self.ranks.queued} queued\n{self.ranks.lookups} looked up, {self.ranks.failures} failed since start", inline=True)
        api = f"{st['requests_hour']} requests\n{st['rate_limited_hour']} × 429 ({st['rate_limited_total']} since start)\n{st['errors_hour']} errors"
        if st["last_error"]:
            api += f"\nlast: `{st['last_error'][:120]}` {_ts(st['last_error_at'])}"
        embed.add_field(name="Tempus API (last hour)", value=api, inline=True)
        first = await self.store.first_snapshot()
        embed.add_field(name="Data", value=f"{len(self.tracker.tiers)} maps, read {_ts(self.tracker.maps_at)}\n{await self.store.snapshot_count()} history snapshots since {_ts(first, 'd') if first else 'never'}", inline=True)
        feed = f"#{self.cfg.records_channel_name}, checked {_ts(self.tracker.activity_at)}\n{await self.store.pending_demo_count()} posts waiting for a demo" if self.cfg.records_channel_name else "off"
        embed.add_field(name="Records feed", value=feed, inline=True)
        offline = [s["shortname"] for s in self.tracker.servers if not s["online"]]
        if self.cfg.alerts_channel_name:
            down = esc(", ".join(offline)) if offline else "none"
            embed.add_field(name="Alerts", value=f"#{self.cfg.alerts_channel_name}, after {self.cfg.offline_alert_s // 60} min offline\nOffline now: {down}"[:1024], inline=True)
        embed.set_footer(text=f"Running on {socket.gethostname()}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"Slow down a little — try again in {error.retry_after:.0f} s."
        else:
            log.exception("Slash command error: %s", error, exc_info=error)
            msg = "Something went wrong handling that command; it has been logged."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.DiscordException:
            pass
