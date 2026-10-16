from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from typing import List

import aiohttp
import discord
from discord.ext import commands

from tempus.cog import ServersCog
from tempus.config import Config, ConfigError, load_config
from tempus.ranks import RankService
from tempus.storage import Storage
from tempus.tempus_client import TempusClient
from tempus.tracker import Tracker

log = logging.getLogger("tempus")


def setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    if cfg.log_file:
        fh = RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("discord").setLevel(logging.WARNING)


class TempusBot(commands.Bot):
    def __init__(self, cfg: Config, store: Storage, client: TempusClient, tracker: Tracker, ranks: RankService) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        # server and player names are untrusted
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.cfg, self.store, self.client, self.tracker, self.ranks = cfg, store, client, tracker, ranks
        self._bg: List[asyncio.Task] = []

    async def setup_hook(self) -> None:
        await self.add_cog(ServersCog(self, self.cfg, self.store, self.client, self.tracker, self.ranks))
        if self.cfg.guild_id:
            guild = discord.Object(id=self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), self.cfg.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take up to an hour to appear)", len(synced))
        self._bg.append(asyncio.create_task(self.tracker.run_forever(), name="status-poll"))
        self._bg.append(asyncio.create_task(self.ranks.run_forever(), name="rank-lookups"))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d servers", self.user, self.user.id if self.user else "?", len(self.guilds))

    async def close(self) -> None:
        for t in self._bg:
            t.cancel()
        await super().close()


async def main() -> None:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(2)
    setup_logging(cfg)
    # two copies on one token edit the same board in turn and it flickers: the host name tells them apart in the logs
    log.info("Starting on %s (pid %d)", socket.gethostname(), os.getpid())

    store = await Storage.open(cfg.db_path)
    async with aiohttp.ClientSession(headers={"User-Agent": cfg.user_agent}) as session:
        client = TempusClient(session, interval=cfg.request_interval_s, timeout=cfg.request_timeout_s)
        ranks = RankService(store, client, cfg.rank_ttl_s, cfg.rank_lookup_interval_s)
        await ranks.load()
        tracker = Tracker(cfg, client, store, ranks)
        await tracker.load()
        bot = TempusBot(cfg, store, client, tracker, ranks)
        try:
            async with bot:
                await bot.start(cfg.discord_token)
        finally:
            await store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
