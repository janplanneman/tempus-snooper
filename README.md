# Tempus Snooper

A Discord bot that shows who is playing on the [Tempus](https://tempus2.xyz) TF2 rocket and sticky jump servers, worldwide.

It keeps one message in your `#tempus-status` channel up to date: how many players are on how many servers, what's usual for this day and hour, and the busy servers grouped by region, each with its map, the map's soldier and demoman tiers, how long the map has been on, a Connect link and the players with their overall Tempus rank. New world records are posted in `#tempus-records`.

## How it works

Tempus publishes the state of every server in one request (`/servers/statusList`), so there's no server discovery: the bot reads that list every 30 seconds. Player ranks cost one request each, so they're looked up once per player in the background and cached for a day. A player whose rank isn't known yet is listed without one.

Tempus asks for at most 1 request per second. The bot stays under that across everything it does, and waits as long as Tempus says when it gets a 429.

Discord allows 6,000 characters per message, which isn't enough for every busy server in full. The busiest servers get a full card with their player list (up to 8 players, then "+N more"). The rest get one line each. At busy times those lines lose their Connect link, and if even that doesn't fit, the quietest servers are summed up as "+N more servers". `/servers region:Europe` shows one region with more room.

## Setup

You need Python 3.9 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Setting | Where to get it |
|---|---|
| `DISCORD_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications): **New Application** → **Bot** → **Reset Token**. Use a new application, never Server Snooper's token. |
| `CONTACT` | How Tempus can reach you, e.g. `discord: yourname`. It's sent in the User-Agent. |
| `GUILD_ID` | In Discord, enable Developer Mode, then right-click your server → **Copy Server ID**. Commands appear there instantly; without it they're global and take up to an hour. |

Invite the bot with the `bot` and `applications.commands` scopes, plus the permissions View Channels, Send Messages and Embed Links:

```
https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=19456
```

Create a text channel called `#tempus-status` (and `#tempus-records` for the record feed), then start the bot. Every Discord server the bot is in that has those channels gets its own board and feed. If the bot is missing permissions there, it keeps retrying until it has them.

```bash
python bot.py
```

## Commands

| Command | What it does |
|---|---|
| `/servers [region]` | Show the board privately, optionally for one region. |
| `/where <player>` | Which server a player is on right now, with the map and a Connect link. |
| `/activity [region]` | When Tempus is busiest: average players by day and hour (UTC) over the last 8 weeks, the busiest times and the record. The bot saves a snapshot every 5 minutes, so this fills in over the first couple of weeks. |
| `/status` | Admins only: poll health, server counts, rank cache, Tempus API requests and 429s in the last hour, and the host the bot runs on. |

## Good to know

- **One copy per token:** two copies of the bot on the same token edit the same board in turn and it flickers. The host name is in the log at startup and in `/status`.
- **"Usually around N players":** this appears once the same weekday and hour has data from 2 earlier days, so after about a week. Time the bot is offline leaves gaps that push it back.
- **Map times:** "started 23 minutes ago" only shows once the bot has seen the map change. After a restart it's back on the next map change.
- **Record feed:** posts new map, course, bonus and trick world records. Set `RECORDS_INCLUDE_TOPS=true` to add top-10 map times, or leave `RECORDS_CHANNEL_NAME` empty to turn it off. Records already in the feed when the bot first starts aren't posted.
- **Connect links:** the link page only accepts `ip:port`. A server without an IP shows the `connect` console command instead.
- **Settings:** all other options are in `.env.example`. The defaults are fine to start with.

## Security

- Secrets only go in `.env`, which is never committed.
- After cloning, run `git config core.hooksPath .githooks`. The hook blocks commits that contain tokens, `.env`, logs or the database.
- If the token ever leaks, reset it right away in the Discord portal.

## Tests

```bash
pip install pytest
python -m pytest
```

The tests never touch the network. `tests/fixtures/` holds trimmed Tempus replies, with SteamIDs replaced.
