# Digimon TCG Card Bot

A Discord bot that looks up **Digimon Card Game (TCG)** cards by name and
set, and posts the official card image straight into chat.

- Card data comes from the free public [digimoncard.io API](https://digimoncard.io/api-documentation) (no API key needed).
- Card images are pulled from Bandai's official site (`world.digimoncard.com`).

## Commands

## Commands

**Card lookup:**
```
/card name:Aldamon
/card name:Aldamon set:BT4
/card name:Omnimon set:"Great Legend"
```
As you type in the `name` field, Discord shows a live dropdown of matching
cards in the form `Aldamon — BT4-016` — the set/print code is shown right
in the suggestion, so duplicate names are easy to tell apart before you
even hit enter. Picking a suggestion jumps straight to that exact card —
no extra "which one did you mean" step. If you type a name manually
without using a suggestion and it still matches more than one printing,
the bot replies with a plain text list (showing each match's set) instead
of an interactive picker.

**Set release schedule:**
```
/release
```
Shows upcoming Digimon TCG set release dates (sourced from digimoncard.io's
set list), including already-announced future sets. If nothing is
upcoming, it shows the most recent releases instead.

**Official rulings:**
```
/ruling card_id:BT4-016
```
Looks up official Card Q&A rulings for a specific printing, scraped
directly from Bandai's own site (world.digimoncard.com). Requires an
exact card number — use the autocomplete suggestions to get it right.
Only covers sets in the bot's internal set-code lookup table; if a set
isn't mapped yet (very new or very obscure releases), you'll get a
direct link to search manually instead.

Every `/ruling` result pings `@zaneal` and `@taiyoukai99` (resolved to
real mentions by looking up their username in the server — configurable
via `RULING_PING_USERNAMES` near the top of `bot.py`), plus a 15% chance
of also tagging `@finn_thewhoman` as a running joke about his ruling
track record. If any of these usernames aren't found in the server, the
bot falls back to plain (non-pinging) text instead of erroring out.

**Print cycling:**
When a card has more than one known print (Standard, Alternate Art, Box
Topper, tournament promos, etc.), the card embed includes **◀ Prev Print**
/ **Next Print ▶** buttons. Cycling updates the rarity, print label, and
a direct link to that exact print's listing on TCGplayer. The image
itself intentionally stays the same across prints — neither
digimoncard.io nor Bandai expose a separate photo per print through any
public API, and TCGplayer's real product photos require API access
(TCGplayer has stopped granting new developer credentials, so this isn't
available to the bot). The TCGplayer link in each print's field is the
only way to see that print's actual distinct artwork.



**Prefix command:**
```
!card Aldamon
!card Aldamon | BT4
```

If a name matches multiple printings (different sets/rarities), the bot
replies with a plain text list of each match and its set — narrow it down
with `| SET` or by re-running with the exact card number shown.

## Setup

1. **Create a Discord bot application**
   - Go to https://discord.com/developers/applications → New Application
   - Go to the "Bot" tab → Add Bot → copy the **Token**
   - Under "Privileged Gateway Intents", enable **Message Content Intent**
     (only needed for the `!card` prefix command — you can skip this if
     you only want the slash command)
   - Under OAuth2 → URL Generator, check scopes `bot` and `applications.commands`,
     and permissions `Send Messages`, `Embed Links`. Use the generated URL
     to invite the bot to your server.

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your token**
   ```bash
   cp .env.example .env
   # then edit .env and paste your bot token in place of the placeholder
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

Slash commands can take up to an hour to appear globally the very first
time; they usually show up within a few minutes.

## Notes / Limitations

- **Only covers the current (2020 reboot) Digimon Card Game** — the old
  pre-reboot English "Digimon Collectible Card Game" (Bandai/Wizards of
  the Coast, ~2000) and the original Japanese "Digimon Digi-Battle Card
  Game" (~1997) are excluded from both search results and autocomplete.
- The underlying API is a community project (not official Bandai), and its
  rate limit is 15 requests / 10 seconds — plenty for casual server use.
- Set matching is fuzzy: it matches against the card's set code prefix
  (e.g. `BT4`) or against words in the full set name (e.g. `Great Legend`).
- If a card name has many printings across many sets, both the autocomplete
  suggestions and the plain-text match list are capped at 25 results
  (Discord's own limits) — narrow with `set` or an exact card number for
  anything past that.
