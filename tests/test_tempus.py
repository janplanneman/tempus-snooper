from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import discord
import pytest

from tempus import history, records
from tempus.board import DETAILS_ID, MAX_COMPONENTS, MAX_TEXT, render_board, render_details, units
from tempus.config import Config
from tempus.ranks import RankService, rank_from_reply
from tempus.regions import flag, region_for
from tempus.storage import Storage
from tempus.tracker import next_map_state, parse_maps, parse_status

FIXTURES = Path(__file__).with_name("fixtures")
T = 1_800_000_000.0
CONNECT = "https://tf2sa-clips.co.za/connect#{addr}"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def tiers_from_fixture() -> Dict[str, Any]:
    return {m["name"]: (m["tier_info"].get("3"), m["tier_info"].get("4")) for m in fixture("detailedList.json")}


def run(coro: Any) -> Any:
    # discord.py views need a running event loop
    return asyncio.run(coro)


def render(*args: Any, **kwargs: Any) -> Any:
    async def go() -> Any:
        return render_board(*args, **kwargs)
    return run(go())


def walk(components: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    for c in components:
        yield c
        yield from walk(c.get("components") or [])
        if c.get("accessory"):
            yield c["accessory"]


def texts(view: Any) -> List[str]:
    return [c["content"] for c in walk(view.to_components()) if c["type"] == 10]


def buttons(view: Any) -> List[Dict[str, Any]]:
    return [c for c in walk(view.to_components()) if c["type"] == 2]


def select(view: Any) -> Optional[Dict[str, Any]]:
    return next((c for c in walk(view.to_components()) if c["type"] == 3), None)


def check_limits(view: Any) -> None:
    """What Discord checks on a components v2 message."""
    components = list(walk(view.to_components()))
    assert len(components) <= MAX_COMPONENTS
    assert sum(units(t) for t in texts(view)) <= MAX_TEXT
    for c in components:
        if c["type"] == 9:  # section: 1-3 text displays and an accessory
            assert 1 <= len(c["components"]) <= 3 and c["accessory"]
        if c["type"] == 1:  # action row
            assert 1 <= len(c["components"]) <= 5
        if c["type"] == 2:
            assert 0 < len(c["label"]) <= 80 and c["url"].startswith("https://") and len(c["url"]) <= 512
        if c["type"] == 3:
            assert 1 <= len(c["options"]) <= 25 and len(c.get("placeholder") or "") <= 150
            for o in c["options"]:
                assert 0 < len(o["label"]) <= 100 and len(o.get("description") or "") <= 100 and len(o["value"]) <= 100


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


def test_regions_and_flags() -> None:
    assert region_for("Germany") == "Europe" and region_for("russia ") == "Europe" and region_for("United States") == "North America"
    assert region_for("Brazil") == "South America" and region_for("Hong Kong") == "Asia" and region_for("New Zealand") == "Oceania"
    assert region_for("Israel") == "Middle East" and region_for("South Africa") == "Africa"
    assert region_for("") == "Other" and region_for(None) == "Other" and region_for("Atlantis") == "Other"
    servers, _ = parse_status(fixture("statusList.json"))
    assert not [s["country"] for s in servers if s["region"] == "Other"]
    assert flag("Germany") == "🇩🇪" and flag("united kingdom") == "🇬🇧" and flag(code="au") == "🇦🇺"
    assert flag("Atlantis") == "🌐" and flag("") == "🌐" and flag(code="ÄB") == "🌐"
    assert all(flag(s["country"]) != "🌐" for s in servers)


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

NB = " "


def no_dots_or_escaped_labels(view: Any) -> None:
    """The owner's rules: no middle dots anywhere, and no backslash escapes inside [link labels], which Discord shows."""
    import re
    for text in texts(view):
        assert "·" not in text
        for label in re.findall(r"\[([^\]]*)\]\(", text):
            assert "\\" not in label, label
    for c in walk(view.to_components()):
        if c["type"] == 3:
            assert not any("·" in o["label"] + (o.get("description") or "") for o in c["options"])


def test_board_from_fixture() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    ranks = {664833: 2498, 0: None}
    tiers = tiers_from_fixture()
    au2 = next(s for s in servers if s["shortname"] == "AU2")
    au2["map_since"] = T - 23 * 60
    board = render(servers, ranks, tiers, connect_url=CONNECT, usual=(61, "Fridays"))
    check_limits(board)
    no_dots_or_escaped_labels(board)
    t = texts(board)
    assert t[0] == "## Tempus jump servers\n**53 players** on **31 servers** right now, 28 empty\n-# Usually around 61 players at this time on Fridays (UTC)"
    headings = [line for text in t for line in text.splitlines() if line.startswith("### ")]
    assert headings == ["### Europe", "### Asia", "### Oceania", "### North America", "### South America"]
    assert "### Europe\n-# 32 players on 17 servers" in t
    au2_row = next(x for x in t if "**AU2**" in x).splitlines()
    assert au2_row[0] == "🇦🇺 **AU2** jump.tf (Sydney) All Maps #1 (3/24)"
    assert au2_row[1] == "[jump_stella_v2](https://tempus2.xyz/maps/jump_stella_v2), T5 soldier, T2 demo"
    assert au2_row[2] == f"`{NB * 2}#2498` fishsoup"  # ranked players first, one per line
    assert au2_row[3].startswith(f"`{NB * 6}–` ") and au2_row[-1] == "-# map started <t:1799998620:R>"
    assert {"type": 2, "style": 5, "label": "Join", "url": "https://tf2sa-clips.co.za/connect#51.161.199.24:27025"} in [
        {k: b[k] for k in ("type", "style", "label", "url")} for b in buttons(board)]
    joined = "\n".join(t)
    # every busy server is on the board: in a row or in its region's "Also" line
    for s in servers:
        if s["players"]:
            assert f"**{s['shortname']}**" in joined or f" {s['shortname']} {s['map']} ({s['players']}/" in joined
    menu = select(board)
    assert menu["custom_id"] == DETAILS_ID and len(menu["options"]) == 25
    assert menu["options"][0]["label"] == "DE1 jump_ataronchronon_b2" and menu["options"][0]["description"] == "4/24 players, jump.tf (Frankfurt) Beginners"
    assert t[-1] == "-# Data from [tempus2.xyz](https://tempus2.xyz). Pick a server below to see everyone on it."

    # nothing depends on the clock, and the footer's "last change" time doesn't count as a change
    again = render(servers, ranks, tiers, connect_url=CONNECT, usual=(61, "Fridays"))
    again.footer.content = "-# Last change <t:1:R>"
    assert again.signature() == board.signature() and again.footer.content == "-# Last change <t:1:R>"
    au2["users"].append({"id": 5, "name": "late joiner"})
    au2["players"] += 1
    assert render(servers, ranks, tiers, connect_url=CONNECT, usual=(61, "Fridays")).signature() != board.signature()

    europe = render(servers, ranks, tiers, connect_url=CONNECT, region="Europe", usual=(40, "Fridays"))
    check_limits(europe)
    assert texts(europe)[0].splitlines() == ["## Tempus jump servers in Europe", "**32 players** on **17 servers** in Europe right now, 8 empty",
                                             "-# Usually around 40 players in Europe at this time on Fridays (UTC)"]
    rows = lambda v: sum(1 for c in walk(v.to_components()) if c["type"] == 9)  # noqa: E731
    assert rows(europe) > sum(1 for x in t if x.startswith(("🇩🇪", "🇫🇷", "🇫🇮", "🇷🇺", "🇬🇧")))


def test_board_full_load_fits_discord_limits() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    tiers = tiers_from_fixture()
    names = ["プレイヤーの名前がとても長い", "😀😀 emoji_name_long_long 😀", "Zalgo̶̢̧ n̷a̸m̵e̴ here long", "back`tick`name__**bold**", "x" * 40]
    ranks: Dict[int, Optional[int]] = {}
    for s in servers:
        s["users"] = [{"id": s["id"] * 100 + n, "name": f"{names[n % len(names)]}{n}"} for n in range(24)]
        s["players"], s["max_players"] = 24, 24
        s["name"] = s["name"] + " | ★ Rank 500 Only ★ | Tempus Network | 日本語のサーバー名"
        ranks.update({u["id"]: 100000 + u["id"] for u in s["users"][::2]})
    board = render(servers, ranks, tiers, connect_url=CONNECT, usual=(1500, "Saturdays"), stale_since=T)
    check_limits(board)
    no_dots_or_escaped_labels(board)
    joined = "\n".join(texts(board))
    assert "**1416 players** on **59 servers**" in joined and "No answer from Tempus since <t:1800000000:R>" in joined
    assert "-# +20 more" in joined  # rows show 4 players, then the rest as a count

    # three times as many busy servers: the "Also" lines are cut short with "+N more"
    many = [dict(s, id=s["id"] + k * 1000, shortname=f"{s['shortname']}-{k}") for k in range(3) for s in servers]
    for i, s in enumerate(many):
        s["players"] = 1 + i % 24
    board = render(many, ranks, tiers, connect_url=CONNECT)
    check_limits(board)
    assert any("-# Also " in x and x.rstrip().endswith("more") for x in texts(board))


def test_board_rows_without_ip_and_details() -> None:
    s = {"id": 1, "shortname": "GBR3", "name": "jump.tf *London* All_Maps", "country": "United Kingdom", "region": "Europe", "online": True, "ip": None,
         "host": "london.tempus2.xyz", "port": 27035, "map": "rj_[odd]*map", "max_players": 24, "map_since": None, "players": 7,
         "users": [{"id": 0, "name": "new`guy"}, {"id": 7, "name": "漢字の名前"}, {"id": 8, "name": "top"}, {"id": 9, "name": "unranked"},
                   {"id": 10, "name": "five"}, {"id": 11, "name": "six"}]}
    board = render([s], {7: 2498, 8: 16, 9: None, 10: 102681}, {}, connect_url=CONNECT)
    check_limits(board)
    no_dots_or_escaped_labels(board)
    row = next(x for x in texts(board) if "**GBR3**" in x)
    assert row.splitlines() == ["🇬🇧 **GBR3** jump.tf \\*London\\* All\\_Maps (7/24)",
                                "[rj_(odd)∗map](https://tempus2.xyz/maps/rj_%5Bodd%5D%2Amap)",
                                f"`{NB * 4}#16` top", f"`{NB * 2}#2498` 漢字の名前", f"`#102681` five", f"`{NB * 6}–` new\\`guy",
                                "-# +3 more"]
    # no IP: the connect page can't take it, so the row links to the server's Tempus page
    assert [b["label"] for b in buttons(board)] == ["Info"] and buttons(board)[0]["url"] == "https://tempus2.xyz/servers/1"

    async def details() -> Any:
        return render_details(s, {7: 2498, 8: 16}, {}, CONNECT)
    view = run(details())
    check_limits(view)
    no_dots_or_escaped_labels(view)
    players = next(x for x in texts(view) if x.startswith("`"))
    lines = players.splitlines()
    assert [ln.split("` ", 1)[1] for ln in lines[:-1]] == ["top", "漢字の名前", "five", "new\\`guy", "six", "unranked"] and lines[-1] == "-# +1 more"
    assert "Join from the TF2 console: `connect london.tempus2.xyz:27035`" in texts(view)
    assert [b["label"] for b in buttons(view)] == ["Map on Tempus", "Server on Tempus"]
    s["ip"] = "1.2.3.4"
    assert [b["label"] for b in buttons(run(details()))] == ["Join", "Map on Tempus", "Server on Tempus"]


def test_board_nobody_playing() -> None:
    servers, _ = parse_status(fixture("statusList.json"))
    for s in servers:
        s["users"], s["players"] = [], 0
    board = render(servers, {}, {})
    check_limits(board)
    assert texts(board)[0] == "## Tempus jump servers\nNobody is playing on Tempus right now." and select(board) is None


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
    async def go() -> None:
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

    run(go())


# --- tempus client ------------------------------------------------------------------------------------------------

def test_client_rate_limit_429_invalid_utf8_and_404() -> None:
    import aiohttp
    from aiohttp import web

    from tempus.tempus_client import NotFound, TempusClient, TempusError

    hits: Dict[str, List[float]] = {}

    async def go() -> None:
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

    run(go())


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

    async def go() -> None:
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

    run(go())


# --- records feed -------------------------------------------------------------------------------------------------

def test_records_feed_selection() -> None:
    entries = records.flatten(fixture("activity.json"))
    assert len(entries) == 15 and {e["kind"] for e in entries} == set(records.KINDS) and all(e["zone_id"] for e in entries)
    newest = max(e["date"] for e in entries)
    newest_wr = max(e["date"] for e in entries if e["kind"] in records.WR_KINDS)
    fresh = records.new_records(entries, set(), include_tops=False, now_ts=newest_wr + 60)
    assert fresh and all(r["kind"] in records.WR_KINDS for r in fresh)
    assert [r["date"] for r in fresh] == sorted(r["date"] for r in fresh)
    assert len(records.new_records(entries, set(), include_tops=True, now_ts=newest + 60)) >= len(fresh)
    assert not records.new_records(entries, {e["id"] for e in entries}, True, newest + 60)
    assert not records.new_records(entries, set(), True, newest + 2 * 3600)  # set too long ago to announce
    assert records.new_records(entries, set(), True, newest + 1800)
    assert records.flatten({"map_wrs": [{"junk": 1}], "trick_wrs": None}) == []
    assert records.fmt_time(65.985) == "1:05.985" and records.fmt_time(9.5) == "9.500" and records.fmt_time(3725.01) == "1:02:05.010"
    memory = records.wr_memory_rows(entries)
    assert len({(m["zone_id"], m["class"]) for m in memory}) == len(memory) and all(m["record_id"] for m in memory)


def test_record_embeds() -> None:
    context = records.leaderboard_context(fixture("zone_records.json"), 4)
    assert context["tier"] == 5 and context["completions"] == 268 and context["country_code"] == "AU"
    assert context["second"]["duration"] == 66.51
    assert records.leaderboard_context(fixture("zone_records.json"), 3) == {"tier": 0, "completions": 0}

    wr = {"id": 8803077, "kind": "map_wrs", "class": 4, "duration": 65.985, "date": T, "server_id": 10, "rank": None, "zone_id": 10274,
          "player_id": 99892, "player": "geek_[x]", "map": "jump_lamia", "zone_type": "map", "zone_index": 1, "zone_name": ""}
    e = records.record_embed(wr, {}, {10: "AU2"}, previous=None, context=context)
    assert e.title == "🏆 New map WR on jump_lamia" and e.url == "https://tempus2.xyz/maps/jump_lamia"
    second = context["second"]["player"]
    # no backslashes inside the link label: Discord would show them
    assert e.description == f"🇦🇺 **[geek_(x)](https://tempus2.xyz/players/99892)** as Demoman in **1:05.985**\n**0.525 s** ahead of #2 {second}\n-# set <t:1800000000:R>"
    assert e.footer.text == "Tier 5, 268 demoman completions, set on AU2" and e.timestamp is not None

    old = {"record_id": 1, "player_id": 42, "player": "Farma", "duration": 66.516}
    assert records.record_embed(wr, {}, {}, previous=old, context={}).description.splitlines()[1] == "Beats the old WR by **0.531 s** (Farma, 1:06.516)"
    own = dict(old, player_id=99892)
    assert records.record_embed(wr, {}, {}, previous=own).description.splitlines()[1] == "Improves their own WR by **0.531 s**"
    first = records.record_embed(wr, {}, {}, context={"completions": 1})
    assert first.description.splitlines()[1] == "First completion" and first.footer.text == "1 demoman completion"

    bonus = dict(wr, kind="bonus_wrs", zone_type="bonus", zone_index=2)
    assert records.record_embed(bonus, {}, {}).title == "⭐ New bonus WR on jump_lamia, Bonus 2"
    top = dict(wr, kind="map_tops", rank=4)
    assert records.record_embed(top, {"jump_lamia": (2, 5)}, {}).title == "🎖️ #4 on jump_lamia"
    assert records.record_embed(top, {"jump_lamia": (2, 5)}, {}).footer.text == "Tier 5"


# --- discord side -------------------------------------------------------------------------------------------------

class FakePerms:
    def __init__(self, ok: bool) -> None:
        self.view_channel = self.send_messages = self.embed_links = ok


class FakeMessage:
    def __init__(self, channel: "FakeChannel", mid: int) -> None:
        self.channel, self.id = channel, mid

    def _check(self) -> None:
        if self.id not in self.channel.messages:
            import types
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), "gone")

    async def edit(self, view: Any) -> None:
        self._check()
        self.channel.edits += 1
        self.channel.last_view = view

    async def delete(self) -> None:
        self._check()
        self.channel.messages.remove(self.id)


class FakeChannel:
    def __init__(self, cid: int, name: str, ok: bool) -> None:
        self.id, self.name, self.ok = cid, name, ok
        self.messages: List[int] = []
        self.sent: List[Any] = []
        self.edits = 0
        self.last_view: Any = None

    def permissions_for(self, member: Any) -> FakePerms:
        return FakePerms(self.ok)

    async def send(self, embeds: Any = None, view: Any = None) -> FakeMessage:
        mid = self.id * 1000 + len(self.sent) + 1  # never the id of a message posted before the test
        self.messages.append(mid)
        self.sent.append(view if view is not None else embeds)
        self.last_view = view
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

    async def go() -> None:
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
            assert texts(locked.sent[0])[-1].startswith("-# Last change <t:")
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
            assert added.sent[0] is not locked.last_view  # one view object per message

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

    run(go())


def test_old_embed_board_is_replaced() -> None:
    from tempus.cog import ServersCog

    async def go() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            channel = FakeChannel(11, "tempus-status", ok=True)
            channel.messages.append(1101)  # the embed board from before
            await store.kv_set("status_board:1", "11:1101")
            cog = ServersCog(FakeBot([FakeGuild(1, "Home", [channel])]), Config(discord_token="x"), store, None, FakeTracker(), RankService(store, None, 1, 1))  # type: ignore[arg-type]
            await cog.update_board()
            assert 1101 not in channel.messages and len(channel.messages) == 1 and channel.edits == 0
            assert (await store.kv_get("status_board:1")) == f"11:{channel.messages[0]}:2"
            await store.close()

    run(go())


def test_details_menu() -> None:
    import types
    from tempus.cog import ServersCog

    sent: List[Dict[str, Any]] = []

    class Response:
        async def send_message(self, content: Optional[str] = None, view: Any = None, ephemeral: bool = False) -> None:
            sent.append({"content": content, "view": view, "ephemeral": ephemeral})

    def interaction(value: str, custom_id: str = DETAILS_ID) -> Any:
        return types.SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": custom_id, "values": [value]}, response=Response())

    async def go() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            tracker = FakeTracker()
            cog = ServersCog(FakeBot([]), Config(discord_token="x"), store, None, tracker, RankService(store, None, 1, 1))  # type: ignore[arg-type]
            au2 = next(s for s in tracker.servers if s["shortname"] == "AU2")
            await cog.on_interaction(interaction(str(au2["id"])))
            await cog.on_interaction(interaction("999999"))
            await cog.on_interaction(interaction(str(au2["id"]), custom_id="something:else"))
            assert len(sent) == 2 and all(m["ephemeral"] for m in sent)
            assert "fishsoup" in "\n".join(texts(sent[0]["view"])) and [b["label"] for b in buttons(sent[0]["view"])][0] == "Join"
            assert sent[1]["content"] == "That server isn't online any more."
            await store.close()

    run(go())


def test_records_posted_once_with_what_they_beat() -> None:
    from tempus.cog import ServersCog

    class FakeClient:
        def __init__(self) -> None:
            self.calls: List[str] = []

        async def get(self, path: str) -> Any:
            self.calls.append(path)
            return fixture("zone_records.json")

    async def go() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = await Storage.open(os.path.join(tmp, "t.db"))
            feed = FakeChannel(30, "tempus-records", ok=True)
            client = FakeClient()
            cog = ServersCog(FakeBot([FakeGuild(3, "G", [feed])]), Config(discord_token="x"), store, client, FakeTracker(), RankService(store, None, 1, 1))  # type: ignore[arg-type]
            activity = fixture("activity.json")
            await cog.on_activity(activity)
            assert feed.sent == [] and client.calls == []  # what's in the feed at the start is old news

            # a new WR on a zone whose old WR the bot saw in the first feed
            old = activity["map_wrs"][0]
            wr = json.loads(json.dumps(old))
            wr["record_info"].update(id=999_999_999, date=time.time() - 60, duration=old["record_info"]["duration"] - 0.5, user_id=1)
            wr["player_info"].update(id=1, name="newcomer")
            top = json.loads(json.dumps(activity["map_tops"][0]))
            top["record_info"].update(id=999_999_998, date=time.time() - 60)
            activity["map_wrs"].insert(0, wr)
            activity["map_tops"].insert(0, top)
            await cog.on_activity(activity)
            await cog.on_activity(activity)
            assert len(feed.sent) == 1 and len(feed.sent[0]) == 1 and len(client.calls) == 1
            embed = feed.sent[0][0]
            assert embed.title.startswith("🏆 New map WR on ") and "newcomer" in embed.description and "·" not in embed.description
            assert f"Beats the old WR by **0.500 s** ({old['player_info']['name']}" in embed.description
            assert client.calls[0] == f"zones/id/{old['record_info']['zone_id']}/records/list?limit=3"
            # the new WR is now the one to beat
            assert (await store.get_wr(old["record_info"]["zone_id"], old["record_info"]["class"]))["record_id"] == 999_999_999
            await store.close()

    run(go())


def test_list_messages_stay_within_discord_limits() -> None:
    from tempus.cog import list_messages
    lines = [f"**Player {i}**, [jump_map_{i}](https://tempus2.xyz/maps/jump_map_{i}), `connect 1.2.3.4:27015`" for i in range(300)]
    messages = list_messages("300 players matching \"p\"", lines)
    shown = "\n".join(e.description for m in messages for e in m)
    assert all(f"**Player {i}**" in shown for i in range(300))
    for embeds in messages:
        assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000 and all(len(e.description) <= 4096 for e in embeds)
