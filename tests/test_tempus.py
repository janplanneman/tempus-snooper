from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
import pytest

from tempus import history
from tempus.board import CONTINUATION, FOOTER, MESSAGE_LIMIT, embeds_size, render_board, units
from tempus.config import Config
from tempus.ranks import RankService, rank_from_reply
from tempus.regions import region_for
from tempus.storage import Storage
from tempus.tracker import next_map_state, parse_maps, parse_status

FIXTURES = Path(__file__).with_name("fixtures")
T = 1_800_000_000.0


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def tiers_from_fixture() -> Dict[str, Any]:
    return {m["name"]: (m["tier_info"].get("3"), m["tier_info"].get("4")) for m in fixture("detailedList.json")}


def check_limits(embeds: List[discord.Embed]) -> None:
    assert 1 <= len(embeds) <= 10
    assert embeds_size(embeds) + units(FOOTER) <= MESSAGE_LIMIT
    assert sum(len(e) for e in embeds) <= MESSAGE_LIMIT
    for e in embeds:
        assert len(e.fields) <= 25 and units(e.description) <= 4096 and units(e.title) <= 256
        for f in e.fields:
            assert 0 < units(f.name) <= 256 and 0 < units(f.value) <= 1024


def board_text(embeds: List[discord.Embed]) -> str:
    return "\n".join([e.description or "" for e in embeds] + [f"{f.name}\n{f.value}" for e in embeds for f in e.fields])


# --- statusList ---------------------------------------------------------------------------------------------------

def test_parse_status_fixture() -> None:
    servers, counts = parse_status(fixture("statusList.json"))
    assert counts == {"visible": 59, "hidden": 21, "offline": 0, "bad": 0}
    assert sum(s["players"] for s in servers) == 53 and sum(1 for s in servers if s["players"]) == 31
    au2 = next(s for s in servers if s["shortname"] == "AU2")
    assert au2["region"] == "Oceania" and au2["ip"] == "51.161.199.24" and au2["port"] == 27025 and au2["map"] == "jump_stella_v2"
    assert {"id": 664833, "name": "fishsoup"} in au2["users"]
    assert any(u["id"] == 0 for s in servers for u in s["users"])  # players Tempus hasn't registered yet
    assert any("멍멍" in s["name"] for s in servers)


def test_parse_status_survives_odd_entries() -> None:
    good = next(e for e in fixture("statusList.json") if not e["server_info"]["hidden"])
    offline = {"server_info": {"id": 900, "shortname": "X1", "name": "Offline", "country": "", "addr": "x.tempus2.xyz", "ipAddr": None, "port": 27015, "hidden": False}, "game_info": None}
    odd = {"server_info": {"id": 901, "shortname": "Y1", "name": "Bad\x07 bell", "country": "Narnia", "addr": "y.example", "ipAddr": None, "port": "27016", "hidden": False},
           "game_info": {"currentMap": "rj_test", "users": [{"name": "nameless"}, "junk", {"id": "12", "name": None}], "playerCount": 3, "maxPlayers": 16}}
    servers, counts = parse_status([good, "junk", {"no": "server_info"}, offline, odd, {"server_info": {"id": 5, "hidden": True}, "game_info": None}])
    assert counts == {"visible": 3, "hidden": 1, "offline": 1, "bad": 2}
    x, y = servers[1], servers[2]
    assert x["online"] is False and x["players"] == 0 and x["region"] == "Other" and x["ip"] is None and x["host"] == "x.tempus2.xyz"
    assert y["name"] == "Bad bell" and y["port"] == 27016 and y["players"] == 3 and [u["id"] for u in y["users"]] == [0, 12]
    with pytest.raises(Exception):
        parse_status({"error": "not a list"})


def test_regions() -> None:
    assert region_for("Germany") == "Europe" and region_for("russia ") == "Europe" and region_for("United States") == "North America"
    assert region_for("Brazil") == "South America" and region_for("Hong Kong") == "Asia" and region_for("New Zealand") == "Oceania"
    assert region_for("Israel") == "Middle East" and region_for("South Africa") == "Africa"
    assert region_for("") == "Other" and region_for(None) == "Other" and region_for("Atlantis") == "Other"
    servers, _ = parse_status(fixture("statusList.json"))
    assert not [s["country"] for s in servers if s["region"] == "Other"]


def test_parse_maps() -> None:
    maps = fixture("detailedList.json")
    with pytest.raises(Exception):
        parse_maps(maps[:10])  # a short list is a glitch, not Tempus deleting maps
    rows = parse_maps(maps * 2)
    stella = next(r for r in rows if r["name"] == "jump_stella_v2")
    assert (stella["tier_soldier"], stella["tier_demoman"]) == (5, 2)


def test_map_state() -> None:
    first = next_map_state(None, "jump_a", 100.0, 600)
    assert first["exact"] == 0 and first["since"] == 100.0
    same = next_map_state(first, "jump_a", 130.0, 600)
    assert same["since"] == 100.0 and same["exact"] == 0 and same["last_seen"] == 130.0
    changed = next_map_state(same, "jump_b", 160.0, 600)
    assert changed == {"map": "jump_b", "since": 160.0, "exact": 1, "last_seen": 160.0}
    assert next_map_state(changed, "jump_b", 190.0, 600)["exact"] == 1
    # the bot was down for an hour: a new map's start time is unknown, and so is whether the old one changed meanwhile
    assert next_map_state(changed, "jump_c", 4000.0, 600)["exact"] == 0
    assert next_map_state(changed, "jump_b", 4000.0, 600)["exact"] == 0


# --- board --------------------------------------------------------------------------------------------------------

def test_board_from_fixture() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    ranks = {664833: 2498, 0: None}
    tiers = tiers_from_fixture()
    au2 = next(s for s in servers if s["shortname"] == "AU2")
    au2["map_since"] = T - 23 * 60
    embeds = render_board(servers, ranks, tiers, connect_url="https://tf2sa-clips.co.za/connect#{addr}")
    check_limits(embeds)
    assert embeds[0].title == "Tempus jump servers"
    assert embeds[0].description == "53 players on 31 servers right now (28 empty)"
    assert [e.title.split(" · ")[0] for e in embeds[1:]] == ["Europe", "Asia", "Oceania", "North America", "South America"]
    text = board_text(embeds)
    assert "[Connect](https://tf2sa-clips.co.za/connect#51.161.199.24:27025)" in text
    assert "[jump\\_stella\\_v2](https://tempus2.xyz/maps/jump_stella_v2) · T5 S / T2 D · started <t:1799998620:R>" in text
    assert "fishsoup" in text and "#2498" in text
    # quiet servers still show up as a compact line
    assert "**DE2** 1/24 · `jump_hulk_a4` · T3 S / T3 D · [Connect](https://tf2sa-clips.co.za/connect#135.125.189.212:27025)" in text and "+8 quieter servers" in text
    # nothing depends on the clock: the same data renders the same board, so no edit is needed
    again = render_board(servers, ranks, tiers, connect_url="https://tf2sa-clips.co.za/connect#{addr}")
    assert [e.to_dict() for e in again] == [e.to_dict() for e in embeds]

    usual = render_board(servers, ranks, tiers, usual=(61, "Fridays"))
    assert usual[0].description.splitlines()[1] == "Usually around 61 players at this time on Fridays (UTC)"
    assert "Connect" not in board_text(usual)  # no connect page configured: console command instead
    assert "`connect 51.161.199.24:27025`" in board_text(usual)

    europe = render_board(servers, ranks, tiers, region="Europe", usual=(40, "Fridays"))
    check_limits(europe)
    assert europe[0].title == "Tempus jump servers · Europe"
    assert europe[0].description.splitlines() == ["32 players on 17 servers in Europe right now (8 empty)", "Usually around 40 players in Europe at this time on Fridays (UTC)"]
    assert len(europe) == 2 and "quieter" not in board_text(europe)  # one region alone has room for every card


def test_board_full_lists_fit_discord_limits() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    tiers = tiers_from_fixture()
    names = ["プレイヤーの名前がとても長い", "😀😀 emoji_name_long_long 😀", "Zalgo̶̢̧ n̷a̸m̵e̴ here long", "back`tick`name__**bold**", "x" * 32]
    ranks: Dict[int, Optional[int]] = {}
    for s in servers:
        s["users"] = [{"id": s["id"] * 100 + n, "name": f"{names[n % len(names)]}{n}"} for n in range(24)]
        s["players"], s["max_players"] = 24, 24
        s["name"] = s["name"] + " | ★ Rank 500 Only ★ | Tempus Network | 日本語のサーバー名"
        ranks.update({u["id"]: 100000 + u["id"] for u in s["users"][::2]})
    embeds = render_board(servers, ranks, tiers, connect_url="https://tf2sa-clips.co.za/connect#{addr}", usual=(150, "Saturdays"), stale_since=T)
    check_limits(embeds)
    text = board_text(embeds)
    assert embeds[0].description.startswith("1416 players on 59 servers right now\n") and "more servers" not in embeds[0].description
    assert "+16 more" in text  # cards are capped
    assert "quieter servers" in text and not any("Connect" in f.value for e in embeds for f in e.fields if "server" in f.name)  # compact lines lost Connect

    # three times as many busy servers: the quietest become "+N more servers"
    many = [dict(s, id=s["id"] + k * 1000, shortname=f"{s['shortname']}-{k}") for k in range(3) for s in servers]
    for i, s in enumerate(many):
        s["players"] = 1 + i % 24
    embeds = render_board(many, ranks, tiers, connect_url="https://tf2sa-clips.co.za/connect#{addr}")
    check_limits(embeds)
    assert "more servers" in embeds[0].description
    assert "`" not in text.replace("```", "").replace("`connect", "").replace("`jump", "").replace("`rj", "").replace("` ·", "").replace("`\n", "")

    # all servers busy in one region: field limits, not characters, are the constraint
    one_region = [dict(s, region="Europe", users=s["users"][:1], players=1) for s in servers]
    embeds = render_board(one_region, ranks, tiers, connect_url="https://tf2sa-clips.co.za/connect#{addr}")
    check_limits(embeds)
    shown = sum(1 for e in embeds for f in e.fields if f.name != CONTINUATION and "server" not in f.name) + sum(
        f.value.count("\n") + 1 for e in embeds for f in e.fields if "server" in f.name or f.name == CONTINUATION)
    dropped = int(embeds[0].description.split("+")[1].split()[0]) if "more servers" in embeds[0].description else 0
    assert shown + dropped == 59


def test_board_player_lines() -> None:
    s = {"id": 1, "shortname": "GBR3", "name": "jump.tf *London* All_Maps", "region": "Europe", "online": True, "ip": None, "host": "london.tempus2.xyz",
         "port": 27035, "map": "rj_unknown", "max_players": 24, "map_since": None, "players": 4,
         "users": [{"id": 0, "name": "new`guy"}, {"id": 7, "name": "漢字の名前"}, {"id": 8, "name": "top"}, {"id": 9, "name": "unranked"}]}
    e = render_board([s], {7: 2498, 8: 16, 9: None}, {}, connect_url="https://tf2sa-clips.co.za/connect#{addr}")
    check_limits(e)
    f = e[1].fields[0]
    assert f.name == "GBR3 · jump.tf \\*London\\* All\\_Maps"
    assert f.value.splitlines()[:2] == ["[rj\\_unknown](https://tempus2.xyz/maps/rj_unknown)", "4/24 · `connect london.tempus2.xyz:27035`"]
    lines = f.value.split("```")[1].strip("\n").splitlines()
    assert [ln.split()[0] for ln in lines] == ["top", "漢字の名前", "new'guy", "unranked"]
    assert lines[0].endswith("    #16") and lines[1].endswith("  #2498") and lines[2].endswith(" " * 7)
    # wide characters take two columns, so the rank column lines up
    assert all(sum(2 if ord(c) > 0x2E80 else 1 for c in ln) == 26 for ln in lines)


def test_board_nobody_playing() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    for s in servers:
        s["users"], s["players"] = [], 0
    e = render_board(servers, {}, {})
    assert len(e) == 1 and e[0].description == "Nobody is playing on Tempus right now."


# --- history ------------------------------------------------------------------------------------------------------

def test_history_usual_grid_and_chart_in_utc() -> None:
    from datetime import datetime, timezone
    # three Fridays at 21:xx UTC with 10, 20 and 30 players, then "now" on the fourth Friday
    fris = [datetime(2026, 9, d, 21, 0, tzinfo=timezone.utc).timestamp() for d in (4, 11, 18, 25)]
    snaps = [{"ts": f + m * 60, "players": p} for f, p in zip(fris[:3], (10, 20, 30)) for m in (0, 5, 10)]
    snaps += [{"ts": fris[3] + 60, "players": 99}]  # today's own numbers don't count towards "usual"
    assert history.usual(snaps, fris[3] + 600) == (20, "Fridays")
    assert history.usual(snaps[:3], fris[3]) is None  # one earlier day isn't enough
    assert history.usual(snaps, fris[3] + 3 * 3600) is None  # nothing recorded at midnight
    cells = history.grid(snaps)
    assert cells[4][21] == (10 + 20 + 30 + 99) / 4 and cells[4][20] is None
    lines = history.chart(cells)
    assert len(lines) == 8 and all(len(line) == 29 for line in lines[1:]) and lines[5][5 + 21] == "█"
    assert history.busiest(cells)[0][:2] == (4, 21) and history.record(snaps) == (99, fris[3] + 60)
    assert history.record([]) is None


def test_history_storage_and_region_series() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            await store.record_snapshot(1000.0, [{"server_id": 1, "region": "Europe", "map": "jump_a", "players": 5}, {"server_id": 2, "region": "Asia", "map": "jump_b", "players": 3},
                                                 {"server_id": 3, "region": "Europe", "map": "jump_c", "players": 2}])
            await store.record_snapshot(2000.0, [{"server_id": 2, "region": "Asia", "map": "jump_b", "players": 4}])
            await store.record_snapshot(3000.0, [])
            assert await store.snapshots_since(0) == [{"ts": 1000.0, "players": 10, "servers": 3}, {"ts": 2000.0, "players": 4, "servers": 1}, {"ts": 3000.0, "players": 0, "servers": 0}]
            assert [(s["ts"], s["players"]) for s in await store.snapshots_since(0, "Europe")] == [(1000.0, 7), (2000.0, 0), (3000.0, 0)]
            assert await store.first_snapshot() == 1000.0 and await store.snapshot_count() == 3
            await store.prune_snapshots(1500.0)
            assert [s["ts"] for s in await store.snapshots_since(0, "Asia")] == [2000.0, 3000.0]
            await store.close()

    asyncio.run(run())


# --- tempus client ------------------------------------------------------------------------------------------------

def test_client_rate_limit_429_invalid_utf8_and_404() -> None:
    import aiohttp
    from aiohttp import web

    from tempus.tempus_client import NotFound, TempusClient, TempusError

    hits: Dict[str, List[float]] = {}

    async def run() -> None:
        async def handler(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            hits.setdefault(name, []).append(time.monotonic())
            assert request.headers["User-Agent"] == "tempus-snooper/0.1 (discord: test)"
            if name == "limited" and len(hits[name]) == 1:
                return web.Response(status=429, headers={"Retry-After": "1"})
            if name == "bad":
                return web.Response(body=b'[{"name": "caf\xe9 \xff"}]', content_type="application/json")
            if name == "missing":
                return web.Response(status=404)
            if name == "broken":
                return web.Response(body=b'{"cut off', content_type="application/json")
            if name == "down":
                return web.Response(status=503)
            return web.json_response({"ok": name})

        app = web.Application()
        app.router.add_get("/api/v0/{name}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": Config(discord_token="x", contact="discord: test").user_agent}) as session:
                client = TempusClient(session, interval=0.2, timeout=5, base_url=f"http://127.0.0.1:{port}/api/v0")
                t0 = time.monotonic()
                assert await client.get("a") == {"ok": "a"}
                assert await client.get("b") == {"ok": "b"}
                assert await client.get("c") == {"ok": "c"}
                assert time.monotonic() - t0 >= 0.39  # request starts are spaced by the limiter

                assert await client.get("limited") == {"ok": "limited"}
                assert hits["limited"][1] - hits["limited"][0] >= 0.95  # Retry-After was honoured
                assert client.stats()["rate_limited_hour"] == 1 and client.rate_limited_total == 1

                assert await client.get("bad") == [{"name": "caf� �"}]
                with pytest.raises(NotFound):
                    await client.get("missing")
                with pytest.raises(TempusError):
                    await client.get("broken")
                client.retries = 0
                with pytest.raises(TempusError):
                    await client.get("down")
                assert client.stats()["requests_hour"] >= 8
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_retry_after_parsing() -> None:
    from tempus.tempus_client import parse_retry_after
    assert parse_retry_after("3") == 3 and parse_retry_after(None) == 5 and parse_retry_after("soon") == 5
    assert parse_retry_after("0") == 1 and parse_retry_after("99999") == 300
    assert 1 <= parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") <= 300


# --- ranks --------------------------------------------------------------------------------------------------------

def test_rank_replies() -> None:
    assert rank_from_reply(fixture("rank.json")) == (2498, 878.5, 102681, "Australia")
    assert rank_from_reply({"rank_info": {"points": 0, "rank": 102682, "total_ranked": 102681}, "player_info": {}})[0] is None
    assert rank_from_reply(None) == (None, None, None, None)


def test_rank_service_queue() -> None:
    from tempus.tempus_client import NotFound

    class FakeClient:
        def __init__(self) -> None:
            self.calls: List[str] = []

        async def get(self, path: str) -> Any:
            self.calls.append(path)
            if path == "players/id/5/rank":
                raise NotFound(path)
            return fixture("rank.json")

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            client = FakeClient()
            svc = RankService(store, client, ttl_s=3600, interval_s=0)  # type: ignore[arg-type]
            svc.want([0, 5, 664833, 5, -1])
            assert svc.queued == 2  # id 0 isn't registered with Tempus yet; duplicates are queued once
            while svc.queued:
                pid = svc._next()
                await svc.lookup(pid)
                svc._queued.discard(pid)
            assert svc.known([0, 5, 664833, 7]) == {5: None, 664833: 2498}
            svc.want([5, 664833])
            assert svc.queued == 0  # fresh in the cache
            svc.want([664833], now=time.time() + 7200)
            assert svc.queued == 1  # stale after the TTL
            again = RankService(store, client, 3600, 0)  # type: ignore[arg-type]
            await again.load()
            assert again.known([5, 664833]) == {5: None, 664833: 2498}
            await store.close()

    asyncio.run(run())


# --- records feed -------------------------------------------------------------------------------------------------

def test_records_feed() -> None:
    from tempus import records
    entries = records.flatten(fixture("activity.json"))
    assert len(entries) == 15 and {e["kind"] for e in entries} == set(records.KINDS)
    newest = max(e["date"] for e in entries)
    fresh = records.new_records(entries, set(), include_tops=False, now_ts=newest + 60)
    assert fresh and all(r["kind"] in records.WR_KINDS for r in fresh)
    assert [r["date"] for r in fresh] == sorted(r["date"] for r in fresh)
    assert len(records.new_records(entries, set(), include_tops=True, now_ts=newest + 60)) >= len(fresh)
    assert not records.new_records(entries, {e["id"] for e in entries}, True, newest + 60)
    assert not records.new_records(entries, set(), True, newest + 30 * 86400)  # too old to announce
    assert records.fmt_time(65.985) == "1:05.985" and records.fmt_time(9.5) == "9.500" and records.fmt_time(3725.01) == "1:02:05.010"

    wr = dict(entries[0], kind="map_wrs", map="jump_stella_v2", player="geek_[x]", player_id=99892, **{"class": 4}, duration=65.985, server_id=10)
    line = records.record_line(wr, tiers_from_fixture(), {10: "AU2"})
    assert line == "🏆 **Map WR** · [jump\\_stella\\_v2](https://tempus2.xyz/maps/jump_stella_v2) T2 · [geek\\_(x)](https://tempus2.xyz/players/99892) · Demoman · **1:05.985** · on AU2"
    bonus = dict(wr, kind="bonus_wrs", zone_type="bonus", zone_index=2, zone_name=None, server_id=None)
    assert " Bonus 2 · " in records.record_line(bonus, {}, {}) and " on " not in records.record_line(bonus, {}, {})
    top = dict(wr, kind="map_tops", rank=4)
    assert records.record_line(top, {}, {}).startswith("🎖️ **#4 map time**")
    assert records.flatten({"map_wrs": [{"junk": 1}], "trick_wrs": None}) == []


# --- discord side -------------------------------------------------------------------------------------------------

class FakePerms:
    def __init__(self, ok: bool) -> None:
        self.view_channel = self.send_messages = self.embed_links = ok


class FakeMessage:
    def __init__(self, channel: "FakeChannel", mid: int) -> None:
        self.channel, self.id = channel, mid

    async def edit(self, embeds: Any) -> None:
        if self.id not in self.channel.messages:
            import types
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), "gone")
        self.channel.edits += 1


class FakeChannel:
    def __init__(self, cid: int, name: str, ok: bool) -> None:
        self.id, self.name, self.ok = cid, name, ok
        self.messages: List[int] = []
        self.sent: List[Any] = []
        self.edits = 0

    def permissions_for(self, member: Any) -> FakePerms:
        return FakePerms(self.ok)

    async def send(self, embeds: Any) -> FakeMessage:
        mid = self.id * 100 + len(self.messages) + 1
        self.messages.append(mid)
        self.sent.append(embeds)
        return FakeMessage(self, mid)

    def get_partial_message(self, mid: int) -> FakeMessage:
        return FakeMessage(self, mid)


class FakeGuild:
    def __init__(self, gid: int, name: str, channels: List[FakeChannel]) -> None:
        self.id, self.name, self.text_channels, self.me = gid, name, channels, object()

    def get_channel(self, cid: int) -> Optional[FakeChannel]:
        return next((c for c in self.text_channels if c.id == cid), None)


class FakeBot:
    def __init__(self, guilds: List[FakeGuild]) -> None:
        self.guilds = guilds

    def is_ready(self) -> bool:
        return True


class FakeTracker:
    after_cycle = on_activity = None

    def __init__(self) -> None:
        self.servers, _ = parse_status(fixture("statusList.json"))
        self.updated_at: Optional[float] = time.time()
        self.tiers = tiers_from_fixture()

    def stale_since(self) -> Optional[float]:
        return None


def test_status_board_per_guild_permissions_and_skipped_edits(caplog) -> None:
    import logging
    from tempus.cog import ServersCog

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            cfg = Config(discord_token="x", guild_id=1)
            locked = FakeChannel(11, "tempus-status", ok=False)
            home = FakeGuild(1, "Home", [FakeChannel(10, "general", True), locked])
            other = FakeGuild(2, "Other", [FakeChannel(20, "general", True)])
            tracker = FakeTracker()
            ranks = RankService(store, None, 3600, 2)  # type: ignore[arg-type]
            cog = ServersCog(FakeBot([home, other]), cfg, store, None, tracker, ranks)  # type: ignore[arg-type]
            assert tracker.after_cycle == cog.update_board and tracker.on_activity == cog.on_activity

            with caplog.at_level(logging.WARNING):
                await cog.update_board()
                await cog.update_board()
            assert locked.messages == []
            assert sum("is missing View Channel, Send Messages, Embed Links" in r.message for r in caplog.records) == 1

            locked.ok = True
            await cog.update_board()
            assert len(locked.messages) == 1 and locked.edits == 0
            board = locked.sent[0]
            assert board[-1].footer.text == FOOTER and board[-1].timestamp is not None
            await cog.update_board()
            assert locked.edits == 0  # nothing changed, so no edit

            tracker.servers[0]["users"].append({"id": 123, "name": "newcomer"})
            tracker.servers[0]["players"] += 1
            await cog.update_board()
            assert locked.edits == 1

            added = FakeChannel(21, "tempus-status", ok=True)
            other.text_channels.append(added)
            await cog.update_board()
            assert added.messages and len(locked.messages) == 1 and locked.edits == 1

            locked.messages.clear()  # someone deleted the board
            tracker.servers[0]["players"] += 1
            await cog.update_board()
            assert len(locked.messages) == 1

            fresh = ServersCog(FakeBot([home, other]), cfg, store, None, tracker, ranks)  # type: ignore[arg-type]
            await fresh.update_board()
            assert len(locked.messages) == 1 and len(added.messages) == 1  # a restart edits the saved messages

            tracker.updated_at = None
            assert await fresh.build_board() is None  # no board before the first reply from Tempus
            await store.close()

    asyncio.run(run())


def test_records_posted_once_and_not_on_first_run() -> None:
    from tempus.cog import ServersCog

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            feed = FakeChannel(30, "tempus-records", ok=True)
            guild = FakeGuild(3, "G", [feed])
            cog = ServersCog(FakeBot([guild]), Config(discord_token="x"), store, None, FakeTracker(), RankService(store, None, 1, 1))  # type: ignore[arg-type]
            activity = fixture("activity.json")
            await cog.on_activity(activity)
            assert feed.sent == []  # what's in the feed at the start is old news

            newest = max(e["record_info"]["date"] for k in activity for e in activity[k])
            wr = json.loads(json.dumps(activity["map_wrs"][0]))
            wr["record_info"].update(id=999_999_999, date=time.time() - 60)
            top = json.loads(json.dumps(activity["map_tops"][0]))
            top["record_info"].update(id=999_999_998, date=time.time() - 60)
            activity["map_wrs"].insert(0, wr)
            activity["map_tops"].insert(0, top)
            await cog.on_activity(activity)
            await cog.on_activity(activity)
            assert len(feed.sent) == 1 and len(feed.sent[0]) == 1
            assert feed.sent[0][0].title == "New Tempus record" and "**Map WR**" in feed.sent[0][0].description
            assert newest < time.time()
            await store.close()

    asyncio.run(run())


def test_list_messages_stay_within_discord_limits() -> None:
    from tempus.cog import list_messages
    lines = [f"🏆 **Map WR** · [jump_map_{i}](https://tempus2.xyz/maps/jump_map_{i}) T5 · [Player {i}](https://tempus2.xyz/players/{i}) · Soldier · **1:05.985**" for i in range(300)]
    messages = list_messages("300 new Tempus records", lines)
    shown = "\n".join(e.description for m in messages for e in m)
    assert all(f"[Player {i}]" in shown for i in range(300))
    for embeds in messages:
        assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000 and all(len(e.description) <= 4096 for e in embeds)
