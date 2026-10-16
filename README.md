# Tempus Snooper

A Discord bot that shows who is playing on the [Tempus](https://tempus2.xyz) TF2 rocket and sticky jump servers, worldwide.

It keeps one panel in your `#tempus-status` channel up to date: how many players are on how many servers, what's usual for this day and hour, and the busy servers grouped by region. Each server row shows its flag, map, soldier and demoman tiers, player count and best-ranked players, with a **Join** button. A menu under the panel opens any server's full player list privately. New world records are posted in `#tempus-records`, with whose record they beat and by how much.

## How it works

Tempus publishes the state of every server in one request (`/servers/statusList`), so there's no server discovery: the bot reads that list every 30 seconds. Player ranks cost one request each, so they're looked up once per player in the background and cached for a day. A player whose rank isn't known yet is listed without one.

Tempus asks for at most 1 request per second. The bot stays under that across everything it does, and waits as long as Tempus says when it gets a 429.

Discord allows 40 components per message, and a server row with its Join button takes 3, so about ten servers get a row: the busiest ones. Every other busy server is listed on its region's "Also" line, and the menu covers the 25 busiest. `/servers region:Europe` shows one region with more room.

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
- **Join buttons:** Discord only allows `http(s)` links on buttons (it rejects `steam://`), so Join opens a small page that starts TF2: `CONNECT_URL`, with `{addr}` replaced by `ip:port`. The page is `docs/connect/`, published with GitHub Pages at <https://janplanneman.github.io/tempus-snooper/connect/> (the default). To host it elsewhere, copy that one file and change `CONNECT_URL`. It only accepts a plain `ip:port`, so it can't send people anywhere else. A server without an IP gets an Info button to its Tempus page instead.
- **WR posts:** the bot remembers each WR it sees, so a new one says whose record it beat and by how much. When it hasn't seen the old WR yet, it reads the zone's leaderboard (one request per WR) and says how far ahead of #2 the new time is.
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
