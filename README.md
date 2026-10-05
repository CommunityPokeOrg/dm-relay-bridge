# dm-relay-bridge

A small two-way DM relay:

- A **discord.py bot** listens for DMs from **one allowlisted Discord user ID** (and ignores everyone else and all guild messages).
- A **Telethon user session** (your own Telegram account) forwards each allowed DM to a configurable Telegram chat — e.g. your Poke chat — sent *as your own account*.
- Incoming replies in that Telegram chat are relayed back into the same Discord DM.
- Files are relayed in both directions: Discord DM attachments are downloaded and sent via Telethon `send_file` (message text becomes the caption when it fits Telegram's 1024-char caption limit), and media/documents from the Telegram chat are downloaded and re-uploaded as Discord attachments — oversized files produce a `[relay]` note instead.
- When the Telegram target is typing, the bot shows a typing indicator in the Discord DM (mirrored via Telethon `UserUpdate` events; since Discord's indicator expires after ~10s it's re-triggered while Telegram keeps sending typing updates, and stops once the reply is relayed).
- Telegram text starting with markdown constructs Discord renders (`- `/`* ` bullets, `1. ` ordered lists, `> ` quotes, `#` headings) is escaped before sending to Discord so it arrives verbatim instead of being reformatted.

Configuration is loaded from a `.env` file (see `.env.example`). Core logic is a single file (`relay.py`, ~300 lines) with long-message splitting and automatic reconnect.

## Requirements

- Python 3.10+
- A Discord bot token
- Telegram `api_id` / `api_hash` and a Telegram account you can log in to interactively once

## Setup

### 1. Discord bot token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Open your app → **Bot** → **Reset Token** → copy the token (this is `BOT_TOKEN`).
3. On the same **Bot** page, enable the **Message Content Intent** (required so the bot can read DM text).
4. The bot can only DM users it shares a server with: go to **OAuth2 → URL Generator**, select the `bot` scope (no permissions needed), open the URL, and invite it to any guild the allowlisted user is in.
5. Find the allowlisted user's numeric ID: enable Discord **Settings → Advanced → Developer Mode**, right-click the user → **Copy User ID** (this is `USER_ID`).

### 2. Telegram api_id / api_hash

1. Log in at [my.telegram.org](https://my.telegram.org) with your Telegram account.
2. Go to **API development tools** → create an application → copy `api_id` (`TG_API_ID`) and `api_hash` (`TG_API_HASH`).

### 3. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Discord bot token from step 1 |
| `USER_ID` | The **only** Discord user ID whose DMs are relayed |
| `TG_API_ID` / `TG_API_HASH` | From my.telegram.org |
| `TG_SESSION` | Path for the Telethon session file, e.g. `./tg_session` |
| `TG_TARGET` | Telegram chat to relay to: `@username`, phone number, numeric chat ID, or `me` (Saved Messages) |

### 4. Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python relay.py
```

On the **first run**, Telethon prompts for your phone number and the login code Telegram sends you (plus your 2FA password if enabled). It then writes the session file to `TG_SESSION`; later runs are non-interactive.

Once running, DM the Discord bot from the allowlisted account — messages appear in the Telegram chat, and replies there come back to the Discord DM.

## Notes & security

- **Access control:** only `USER_ID` can use the relay. All other users' DMs and all guild messages are ignored.
- Messages **you** send in the Telegram chat (outgoing) are never echoed back to Discord, so there is no loop.
- Telegram reactions on relayed messages are mirrored as Discord reactions (standard emoji only; custom/paid emoji have no Discord equivalent). Telegram does not reliably push reaction updates for private chats, so reactions are mirrored by polling recently relayed messages every ~10s (plus any pushed `UpdateMessageReactions` updates).
- Typing mirroring is one-way (Telegram -> Discord) and only reacts to the configured `TG_TARGET` chat; typing updates from anyone else are ignored.
- Messages longer than the Telegram (4096) or Discord (2000) limits are split automatically. Attachments are relayed as real files via temporary downloads (always cleaned up), not as URLs.
- **Never commit** `.env`, `*.session`, or `*.session-journal` — they're in `.gitignore`. Anyone holding your session file or `api_hash` can act as your Telegram account; anyone holding the bot token can act as the bot.
- If a side disconnects, the clients reconnect automatically; a crash restarts the relay after 5 seconds.
