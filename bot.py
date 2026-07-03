"""
Digimon TCG Card Lookup Discord Bot
------------------------------------
Look up any Digimon Card Game card by name (optionally narrowed by set)
and get its official card image posted right in Discord.

Data source:  https://digimoncard.io  (free, no API key required)
Images:       https://world.digimoncard.com  (official Bandai card images)

Setup:
    1. pip install -r requirements.txt
    2. Copy .env.example to .env and fill in DISCORD_BOT_TOKEN
    3. python bot.py

Usage in Discord:
    /card name:Aldamon
    /card name:Aldamon set:BT4
    /card name:Omnimon set:booster great legend

    As you type in the `name` field, Discord suggests matching cards in
    the form "Aldamon — BT4-016" — the set/print code is shown right in
    the suggestion so you can tell duplicates apart before you even hit
    enter. Picking a suggestion resolves straight to that exact printing,
    no extra selection step needed.

    (or the classic prefix command)
    !card Aldamon
    !card Aldamon | BT4
"""

import os
import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

SEARCH_URL = "https://digimoncard.io/api-public/search.php"
ALL_CARDS_URL = "https://digimoncard.io/api-public/getAllCards"
IMAGE_URL_TEMPLATE = "https://world.digimoncard.com/images/cardlist/card/{code}.png"

# Only include cards from the current (2020 reboot) Digimon Card Game.
# This deliberately excludes both older lines this API tracks:
#   - "Digimon Digi-Battle Card Game" — the original Japanese/English card
#     game from 1997-2005 (ST-xx / BO-xxx style card numbers).
#   - "Digimon Collectible Card Game" — the pre-reboot English-only TCG
#     from Bandai/Wizards of the Coast, ~2000.
# Neither is selectable or searchable anywhere in this bot.
CURRENT_SERIES = "Digimon Card Game"

# Cache of every card (name + set/print code), used to power the slash
# command's live autocomplete. Refreshed once at startup and then
# periodically in the background (card lists grow with new set releases).
# Each entry: {"name": "Aldamon", "id": "BT4-016"}
ALL_CARDS: list[dict] = []

COLOR_HEX = {
    "Red": 0xE0403C,
    "Blue": 0x3A8FD9,
    "Yellow": 0xF2D024,
    "Green": 0x3FAE49,
    "Black": 0x2B2B2B,
    "Purple": 0x8E4FC4,
    "White": 0xE8E8E8,
    "Colorless": 0x9AA0A6,
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("digimon-bot")

intents = discord.Intents.default()
intents.message_content = True  # needed for the prefix command

bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------
# Data fetching / normalizing
# --------------------------------------------------------------------------

def _merge_card_rows(rows: list[dict]) -> list[dict]:
    """Collapse raw API rows (one row per TCGPlayer SKU/printing variant)
    down to one entry per unique card id, merging set names together."""
    merged: dict[str, dict] = {}
    for card in rows:
        cid = card.get("id")
        if not cid:
            continue
        if cid not in merged:
            merged[cid] = {
                "id": cid,
                "name": card.get("name"),
                "type": card.get("type"),
                "color": card.get("color"),
                "level": card.get("level"),
                "dp": card.get("dp"),
                "rarity": card.get("rarity"),
                "stage": card.get("stage"),
                "set_names": set(card.get("set_name") or []),
            }
        else:
            merged[cid]["set_names"].update(card.get("set_name") or [])
    return list(merged.values())


async def _search_api(session: aiohttp.ClientSession, params: dict) -> list[dict]:
    """Low-level call against digimoncard.io's search endpoint. Always
    scopes to the current TCG series and double-checks results client-side
    (see CURRENT_SERIES comment above) so older card lines can never
    surface, no matter how the search is triggered."""
    params = {**params, "series": CURRENT_SERIES}
    async with session.get(SEARCH_URL, params=params, timeout=10) as resp:
        if resp.status != 200:
            return []
        data = await resp.json(content_type=None)

    if not isinstance(data, list):
        return []

    return [card for card in data if card.get("series") == CURRENT_SERIES]


async def search_cards_by_name(session: aiohttp.ClientSession, name: str) -> list[dict]:
    """Search by (partial) card name, returning one merged entry per
    unique printing (card id)."""
    rows = await _search_api(session, {"n": name})
    return _merge_card_rows(rows)


async def search_card_by_id(session: aiohttp.ClientSession, card_id: str) -> list[dict]:
    """Search by an exact card/print number (e.g. BT4-016)."""
    rows = await _search_api(session, {"card": card_id})
    return _merge_card_rows(rows)


async def refresh_all_cards():
    """Fetch every (name, card id) pair from digimoncard.io and cache it
    in ALL_CARDS for use by the autocomplete handler."""
    global ALL_CARDS
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ALL_CARDS_URL, params={"series": CURRENT_SERIES}, timeout=15
            ) as resp:
                if resp.status != 200:
                    log.warning("getAllCards returned status %s", resp.status)
                    return
                data = await resp.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.warning("Failed to refresh card cache: %s", e)
        return

    if not isinstance(data, list):
        return

    cards = [
        {"name": row["name"], "id": row["cardnumber"]}
        for row in data
        if row.get("name") and row.get("cardnumber")
    ]
    cards.sort(key=lambda c: (c["name"], c["id"]))
    ALL_CARDS = cards
    log.info("Cached %d cards for autocomplete.", len(cards))


def filter_by_set(cards: list[dict], set_query: str) -> list[dict]:
    """Keep only cards whose id or set name loosely matches the given set
    query, e.g. 'BT4', 'BT-04', or 'Great Legend'."""
    q = set_query.strip().lower().replace(" ", "")
    results = []
    for card in cards:
        id_norm = card["id"].lower().replace(" ", "")
        set_blob = " ".join(card["set_names"]).lower()
        if id_norm.startswith(q) or q in set_blob.replace(" ", "") or q in set_blob:
            results.append(card)
    return results


def build_embed(card: dict) -> discord.Embed:
    color = COLOR_HEX.get(card.get("color"), 0x5865F2)
    sets = ", ".join(sorted(card["set_names"])) or "Unknown set"

    embed = discord.Embed(
        title=f"{card['name']} — {card['id']}",
        color=color,
    )
    embed.set_image(url=IMAGE_URL_TEMPLATE.format(code=card["id"]))

    details = []
    if card.get("type"):
        details.append(f"**Type:** {card['type']}")
    if card.get("level"):
        details.append(f"**Level:** {card['level']}")
    if card.get("dp"):
        details.append(f"**DP:** {card['dp']}")
    if card.get("color"):
        details.append(f"**Color:** {card['color']}")
    if card.get("rarity"):
        details.append(f"**Rarity:** {card['rarity']}")
    if details:
        embed.add_field(name="Details", value="\n".join(details), inline=True)

    embed.add_field(name="Set(s)", value=sets, inline=True)
    embed.set_footer(text="Card data: digimoncard.io  •  Images: world.digimoncard.com")
    return embed


def format_matches_message(query: str, cards: list[dict]) -> str:
    """Plain-text listing used when a search still matches multiple cards
    (e.g. someone typed a name manually without using autocomplete). Each
    line shows the card's set(s) so duplicates are easy to tell apart —
    no interactive picker involved."""
    cards = sorted(cards, key=lambda c: c["id"])
    shown = cards[:25]

    lines = [
        f"Found **{len(cards)}** cards matching **{query}**. "
        f"Use the autocomplete suggestions or add a `set` filter to get an exact match:"
    ]
    for card in shown:
        sets = ", ".join(sorted(card["set_names"])) or "Unknown set"
        lines.append(f"• **{card['name']}** — `{card['id']}` — {sets}")

    if len(cards) > len(shown):
        lines.append(f"...and {len(cards) - len(shown)} more. Try narrowing your search.")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Shared lookup logic used by both the slash command and prefix command
# --------------------------------------------------------------------------

async def do_lookup(query: str, set_query: str | None):
    """Returns (embed_or_none, matches_or_none, error_message_or_none).

    `matches` is only populated when multiple cards still match after
    filtering — callers should render it as a plain text list (see
    format_matches_message), not an interactive component.
    """
    if not query or not query.strip():
        return None, None, "Please provide a card name to search for."

    query = query.strip()

    async with aiohttp.ClientSession() as session:
        try:
            # If `query` is an exact card/print number (as it will be when
            # the user picks an autocomplete suggestion), this resolves
            # straight to that single printing — no further steps needed.
            id_matches = await search_card_by_id(session, query)
            cards = id_matches if len(id_matches) == 1 else await search_cards_by_name(session, query)
        except asyncio.TimeoutError:
            return None, None, "The card database timed out. Please try again."
        except aiohttp.ClientError as e:
            log.warning("Request error: %s", e)
            return None, None, "Couldn't reach the card database. Please try again later."

    if not cards:
        return None, None, f"No cards found matching **{query}**."

    if set_query:
        filtered = filter_by_set(cards, set_query)
        if not filtered:
            available = ", ".join(sorted({s for c in cards for s in c["set_names"]}))
            return None, None, (
                f"Found **{query}** but none of its printings match set "
                f"**{set_query}**.\nAvailable sets: {available}"
            )
        cards = filtered

    if len(cards) == 1:
        return build_embed(cards[0]), None, None

    return None, cards, None


# --------------------------------------------------------------------------
# Autocomplete
# --------------------------------------------------------------------------

async def name_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggests matching cards as the user types in the `name` field. Each
    suggestion is shown as "Name — SetCode-Number" so the set is visible
    right in the dropdown, making duplicate names easy to tell apart.
    Choosing a suggestion sends its exact card id as the command's value,
    resolving directly to that one printing."""
    if not ALL_CARDS:
        return []

    typed = current.strip().lower()

    if not typed:
        matches = ALL_CARDS[:25]
    else:
        # Prefix-of-name matches first (most relevant), then any
        # substring match against name or card id (so power users can
        # also jump straight to a set by typing e.g. "bt4-016").
        starts = [c for c in ALL_CARDS if c["name"].lower().startswith(typed)]
        starts_ids = {c["id"] for c in starts}
        contains = [
            c for c in ALL_CARDS
            if c["id"] not in starts_ids
            and (typed in c["name"].lower() or typed in c["id"].lower())
        ]
        matches = (starts + contains)[:25]

    choices = []
    for card in matches:
        label = f"{card['name']} — {card['id']}"[:100]
        choices.append(app_commands.Choice(name=label, value=card["id"]))
    return choices


# --------------------------------------------------------------------------
# Slash command
# --------------------------------------------------------------------------

@bot.tree.command(name="card", description="Look up a Digimon TCG card image by name and (optionally) set.")
@app_commands.describe(
    name="Card name, e.g. Aldamon (pick a suggestion to jump straight to that set)",
    set="Set name or code, e.g. BT4 or 'Great Legend' (optional)",
)
@app_commands.autocomplete(name=name_autocomplete)
async def card_slash(interaction: discord.Interaction, name: str, set: str = None):
    await interaction.response.defer()
    embed, matches, error = await do_lookup(name, set)

    if error:
        await interaction.followup.send(error)
        return

    if embed:
        await interaction.followup.send(embed=embed)
        return

    await interaction.followup.send(format_matches_message(name, matches))


# --------------------------------------------------------------------------
# Classic prefix command: !card <name> | <set>
# --------------------------------------------------------------------------

@bot.command(name="card")
async def card_prefix(ctx: commands.Context, *, query: str):
    if "|" in query:
        name, set_query = query.split("|", 1)
        name, set_query = name.strip(), set_query.strip()
    else:
        name, set_query = query.strip(), None

    async with ctx.typing():
        embed, matches, error = await do_lookup(name, set_query)

    if error:
        await ctx.send(error)
        return

    if embed:
        await ctx.send(embed=embed)
        return

    await ctx.send(format_matches_message(name, matches))


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

_cache_refresher_started = False


async def periodic_cache_refresh():
    """Keep the autocomplete cache reasonably fresh without hammering the
    API — once a day is plenty since new sets release infrequently."""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await refresh_all_cards()


@bot.event
async def on_ready():
    global _cache_refresher_started

    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash command(s).", len(synced))
    except Exception as e:
        log.exception("Failed to sync slash commands: %s", e)

    if not ALL_CARDS:
        await refresh_all_cards()

    if not _cache_refresher_started:
        bot.loop.create_task(periodic_cache_refresh())
        _cache_refresher_started = True

    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id)


def main():
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and add your bot token."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
