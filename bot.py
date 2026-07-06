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
    /release
    /ruling card_id:BT4-016

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
import re
import random
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
HEALTHCHECK_PORT = int(os.getenv("HEALTHCHECK_PORT", "8080"))

# Every /ruling result pings these two people (by Discord username, resolved
# to a real mention at runtime by looking them up in the server's member
# list). There's also a 15% chance of additionally tagging a third person
# as a standing joke about their ruling track record — purely cosmetic,
# doesn't affect the actual ruling data returned.
RULING_PING_USERNAMES = ["zaneal", "taiyoukai99"]
RULING_BONUS_PING_USERNAME = "finn_thewhoman"
RULING_BONUS_PING_CHANCE = 0.15

SEARCH_URL = "https://digimoncard.io/api-public/search.php"
ALL_CARDS_URL = "https://digimoncard.io/api-public/getAllCards"
IMAGE_URL_TEMPLATE = "https://world.digimoncard.com/images/cardlist/card/{code}.png"
PACKS_URL = "https://digimoncard.io/packs"
TCGPLAYER_PRODUCT_URL = "https://www.tcgplayer.com/product/{product_id}"
# TCGplayer's public image CDN — no API key needed, unlike their catalog
# API. Each TCGplayer product (i.e. each distinct print/art variant) has
# its own real photo hosted here, keyed by its product id.
TCGPLAYER_IMAGE_URL_TEMPLATE = "https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"
OFFICIAL_RULINGS_URL = "https://world.digimoncard.com/cards/"

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

# Maps a card ID's set-code prefix (e.g. "BT4", "EX11", "ST7") to the exact
# label Bandai's official site uses for that set on world.digimoncard.com,
# used as the `notes` query param when looking up official rulings. Sourced
# directly from the set list at https://world.digimoncard.com/rule/ — Bandai
# is inconsistent about hyphens (older sets omit them, e.g. "[BT04]" vs
# "[BT-24]"), so this table exists rather than trying to derive the label.
SET_NOTES_MAP = {
    "BT25": "BOOSTER DUAL REVOLUTION [BT-25]",
    "AD1": "Advanced Booster DIGIMON GENERATION [AD-01]",
    "EX11": "EXTRA BOOSTER DAWN OF LIBERATOR [EX-11]",
    "BT24": "BOOSTER TIME STRANGER [BT-24]",
    "BT23": "BOOSTER HACKERS' SLUMBER [BT-23]",
    "EX10": "EXTRA BOOSTER SINISTER ORDER [EX-10]",
    "BT22": "BOOSTER CYBER EDEN [BT-22]",
    "EX9": "EXTRA BOOSTER VERSUS MONSTERS [EX-09]",
    "EX09": "EXTRA BOOSTER VERSUS MONSTERS [EX-09]",
    "BT21": "WORLD CONVERGENCE [BT-21]",
    "EX8": "EXTRA BOOSTER CHAIN OF LIBERATION [EX08]",
    "EX08": "EXTRA BOOSTER CHAIN OF LIBERATION [EX08]",
    "EX7": "EXTRA BOOSTER DIGIMON LIBERATOR [EX07]",
    "EX07": "EXTRA BOOSTER DIGIMON LIBERATOR [EX07]",
    "BT17": "BOOSTER SECRET CRISIS [BT17]",
    "EX6": "THEME BOOSTER INFERNAL ASCENSION [EX06]",
    "EX06": "THEME BOOSTER INFERNAL ASCENSION [EX06]",
    "BT16": "BOOSTER BEGINNING OBSERVER [BT16]",
    "BT15": "BOOSTER EXCEED APOCALYPSE [BT15]",
    "EX5": "THEME BOOSTER ANIMAL COLOSSEUM [EX05]",
    "EX05": "THEME BOOSTER ANIMAL COLOSSEUM [EX05]",
    "BT14": "BOOSTER BLAST ACE [BT14]",
    "RB1": "RESURGENCE BOOSTER [RB01]",
    "RB01": "RESURGENCE BOOSTER [RB01]",
    "BT13": "BOOSTER VERSUS ROYAL KNIGHTS [BT13]",
    "EX4": "THEME BOOSTER ALTERNATIVE BEING [EX04]",
    "EX04": "THEME BOOSTER ALTERNATIVE BEING [EX04]",
    "BT12": "BOOSTER ACROSS TIME [BT12]",
    "BT11": "BOOSTER DIMENSIONAL PHASE [BT11]",
    "EX3": "THEME BOOSTER DRACONIC ROAR [EX-03]",
    "EX03": "THEME BOOSTER DRACONIC ROAR [EX-03]",
    "BT10": "BOOSTER Xros Encounter [BT10]",
    "BT9": "BOOSTER X RECORD [BT09]",
    "BT09": "BOOSTER X RECORD [BT09]",
    "EX2": "THEME BOOSTER DIGITAL HAZARD [EX-02]",
    "EX02": "THEME BOOSTER DIGITAL HAZARD [EX-02]",
    "BT8": "BOOSTER NEW AWAKENING [BT08]",
    "BT08": "BOOSTER NEW AWAKENING [BT08]",
    "BT7": "BOOSTER NEXT ADVENTURE [BT07]",
    "BT07": "BOOSTER NEXT ADVENTURE [BT07]",
    "EX1": "THEME BOOSTER CLASSIC COLLECTION [EX-01]",
    "EX01": "THEME BOOSTER CLASSIC COLLECTION [EX-01]",
    "BT6": "BOOSTER DOUBLE DIAMOND [BT06]",
    "BT06": "BOOSTER DOUBLE DIAMOND [BT06]",
    "BT5": "BOOSTER BATTLE OF OMNI [BT05]",
    "BT05": "BOOSTER BATTLE OF OMNI [BT05]",
    "BT4": "BOOSTER GREAT LEGEND [BT04]",
    "BT04": "BOOSTER GREAT LEGEND [BT04]",
    "BT1": "RELEASE SPECIAL BOOSTER [BT01-03]",
    "BT2": "RELEASE SPECIAL BOOSTER [BT01-03]",
    "BT3": "RELEASE SPECIAL BOOSTER [BT01-03]",
    "ST24": "Starter Deck DIGIMON DATA SQUAD [ST-24]",
    "ST23": "Starter Deck DIGIMON BEATBREAK [ST-23]",
    "ST22": "Advanced Deck AMETHYST MANDALA [ST-22]",
    "ST21": "HERO OF HOPE [ST-21]",
    "ST20": "PROTECTOR OF LIGHT [ST-20]",
    "ST19": "Starter Deck FABLE WALTZ [ST19]",
    "ST18": "Starter Deck GUARDIAN VORTEX [ST18]",
    "ST17": "Advanced Deck DOUBLE TYPHOON [ST17]",
    "ST16": "Starter Deck Wolf of Friendship [ST16]",
    "ST15": "Starter Deck Dragon of Courage [ST15]",
    "ST14": "Advanced Deck Beelzemon [ST14]",
    "ST13": "Starter Deck RagnaLoardmon [ST-13]",
    "ST12": "Starter Deck Jesmon [ST-12]",
    "ST10": "Starter Deck PARALLEL WORLD TACTICIAN [ST-10]",
    "ST9": "Starter Deck ULTIMATE ANCIENT DRAGON [ST-9]",
    "ST8": "Starter Deck ULFORCEVEEDRAMON [ST-8]",
    "ST7": "Starter Deck GALLANTMON [ST-7]",
    "ST6": "Starter Deck VENOMOUS VIOLET [ST-6]",
    "ST5": "Starter Deck MACHINE BLACK [ST-5]",
    "ST4": "Starter Deck GIGA GREEN [ST-4]",
    "ST3": "Starter Deck HEAVEN'S YELLOW [ST-3]",
    "ST2": "Starter Deck COCYTUS BLUE [ST-2]",
    "ST1": "Starter Deck GAIA RED [ST-1]",
}

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
intents.members = True  # needed to look up users by username for /ruling pings

bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------
# Data fetching / normalizing
# --------------------------------------------------------------------------

def _merge_card_rows(rows: list[dict]) -> list[dict]:
    """Collapse raw API rows (one row per TCGPlayer SKU/printing variant)
    down to one entry per unique card id, merging set names together.

    Each merged card also keeps a `variants` list — the distinct physical
    prints of that card id (e.g. standard vs "Alternate Art" vs "Box
    Topper"), each tagged with its own TCGplayer product id. This powers
    the Alternative Art cycling feature: the base game data (name, effect
    text, rarity) is identical across variants, but each has its own
    real photo on TCGplayer, which digimoncard.io and Bandai's own site
    don't expose separately.
    """
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
                "variants": [],
                "_seen_variant_ids": set(),
            }
        entry = merged[cid]
        entry["set_names"].update(card.get("set_name") or [])

        tcg_id = card.get("tcgplayer_id")
        tcg_name = card.get("tcgplayer_name") or entry["name"]
        if tcg_id and tcg_id not in entry["_seen_variant_ids"]:
            entry["_seen_variant_ids"].add(tcg_id)
            # Label is whatever's in parentheses in tcgplayer_name (e.g.
            # "Alternate Art", "Box Topper"), or "Standard" if the name
            # matches the base card name with no extra qualifier.
            match = re.search(r"\(([^)]+)\)\s*$", tcg_name)
            label = match.group(1) if match else "Standard"
            entry["variants"].append({
                "label": label,
                "tcgplayer_id": tcg_id,
                "rarity": card.get("rarity"),
            })

    results = []
    for entry in merged.values():
        del entry["_seen_variant_ids"]
        # Standard/base print first, then everything else in the order
        # the API returned it.
        entry["variants"].sort(key=lambda v: 0 if v["label"] == "Standard" else 1)
        results.append(entry)
    return results


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


def get_tcgplayer_image_url(product_id: int) -> str:
    """Builds the direct image URL for one specific TCGplayer listing
    (i.e. one specific print/art variant)."""
    return TCGPLAYER_IMAGE_URL_TEMPLATE.format(product_id=product_id)


async def resolve_variant_image_url(session: aiohttp.ClientSession, tcgplayer_id: int) -> str | None:
    """Tries each candidate TCGplayer image URL for this product id and
    returns the first one that actually loads as an image. This is an
    unofficial CDN pattern (TCGplayer's real catalog/media API needs
    OAuth credentials), so it's verified live rather than trusted blindly
    — if every candidate fails, the caller falls back to the official
    card image instead of risking a broken embed."""
    candidates = [
        f"https://tcgplayer-cdn.tcgplayer.com/product/{tcgplayer_id}_in_1000x1000.jpg",
        f"https://product-images.tcgplayer.com/fit-in/437x437/{tcgplayer_id}.jpg",
        f"https://tcgplayer-cdn.tcgplayer.com/product/{tcgplayer_id}_200w.jpg",
    ]
    for url in candidates:
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=4), allow_redirects=True
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if resp.status == 200 and content_type.startswith("image"):
                    return url
        except (asyncio.TimeoutError, aiohttp.ClientError):
            continue
    return None


def build_embed(card: dict, variant_index: int = 0, image_url: str | None = None) -> discord.Embed:
    color = COLOR_HEX.get(card.get("color"), 0x5865F2)
    sets = ", ".join(sorted(card["set_names"])) or "Unknown set"

    variants = card.get("variants") or []
    variant = variants[variant_index] if variants else None
    is_alt = variant and variant["label"] != "Standard"

    title = f"{card['name']} — {card['id']}"
    if is_alt:
        title += f" ({variant['label']})"

    embed = discord.Embed(title=title, color=color)

    # Non-standard prints (Alternate Art, Box Topper, etc.) show that
    # print's real photo when one was successfully verified (see
    # resolve_variant_image_url) — otherwise fall back to the one
    # official image every print shares.
    used_real_photo = is_alt and image_url is not None
    embed.set_image(url=image_url or IMAGE_URL_TEMPLATE.format(code=card["id"]))

    details = []
    if card.get("type"):
        details.append(f"**Type:** {card['type']}")
    if card.get("level"):
        details.append(f"**Level:** {card['level']}")
    if card.get("dp"):
        details.append(f"**DP:** {card['dp']}")
    if card.get("color"):
        details.append(f"**Color:** {card['color']}")
    rarity = variant["rarity"] if variant else card.get("rarity")
    if rarity:
        details.append(f"**Rarity:** {rarity}")
    if details:
        embed.add_field(name="Details", value="\n".join(details), inline=True)

    embed.add_field(name="Set(s)", value=sets, inline=True)

    if is_alt and variant.get("tcgplayer_id"):
        tcg_url = TCGPLAYER_PRODUCT_URL.format(product_id=variant["tcgplayer_id"])
        if used_real_photo:
            field_value = f"[View this listing on TCGplayer]({tcg_url})"
        else:
            field_value = (
                f"Couldn't load this print's photo, showing the standard image instead. "
                f"[View the real {variant['label']} photo on TCGplayer]({tcg_url})."
            )
        embed.add_field(name="Alternate Art", value=field_value, inline=False)

    footer = "Card data: digimoncard.io  •  Images: world.digimoncard.com"
    if used_real_photo:
        footer += " / TCGplayer"
    if len(variants) > 1:
        footer += f"  •  Print {variant_index + 1}/{len(variants)}"
    embed.set_footer(text=footer)
    return embed


class ArtCycleView(discord.ui.View):
    """Lets the user cycle between known prints/art variants of a card
    (standard, Alternate Art, Box Topper, event promos, etc.) using
    Prev/Next buttons. Only attached to a message when a card actually
    has more than one known variant."""

    def __init__(self, card: dict, requester_id: int, variant_index: int = 0):
        super().__init__(timeout=120)
        self.card = card
        self.requester_id = requester_id
        self.variant_index = variant_index

    @discord.ui.button(label="◀ Prev Art", style=discord.ButtonStyle.secondary)
    async def prev_art(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._cycle(interaction, -1)

    @discord.ui.button(label="Next Art ▶", style=discord.ButtonStyle.secondary)
    async def next_art(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._cycle(interaction, 1)

    async def _cycle(self, interaction: discord.Interaction, direction: int):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who ran the command can cycle art for this card.",
                ephemeral=True,
            )
            return
        variants = self.card.get("variants") or []
        if not variants:
            await interaction.response.defer()
            return

        # Verifying the TCGplayer image needs a network round-trip, so
        # defer first (buys up to 15 minutes instead of Discord's normal
        # 3-second response window) and edit the original message once
        # we know which image URL is actually usable.
        await interaction.response.defer()

        self.variant_index = (self.variant_index + direction) % len(variants)
        variant = variants[self.variant_index]

        image_url = None
        if variant["label"] != "Standard" and variant.get("tcgplayer_id"):
            async with aiohttp.ClientSession() as session:
                image_url = await resolve_variant_image_url(session, variant["tcgplayer_id"])

        embed = build_embed(self.card, self.variant_index, image_url=image_url)
        await interaction.edit_original_response(embed=embed, view=self)


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
    """Returns (card_or_none, matches_or_none, error_message_or_none).

    `card` is the single resolved card dict (build the embed/view from it
    yourself). `matches` is only populated when multiple cards still match
    after filtering — callers should render it as a plain text list (see
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
        return cards[0], None, None

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
    card, matches, error = await do_lookup(name, set)

    if error:
        await interaction.followup.send(error)
        return

    if card:
        embed = build_embed(card)
        view = ArtCycleView(card, interaction.user.id) if len(card.get("variants") or []) > 1 else None
        await interaction.followup.send(embed=embed, view=view)
        return

    await interaction.followup.send(format_matches_message(name, matches))


# --------------------------------------------------------------------------
# Set release schedule
# --------------------------------------------------------------------------

# Sent with every scraping request (the /packs and rulings pages, unlike
# the JSON search API, are human-facing pages that some sites treat
# differently — or outright block — for requests that don't look like a
# real browser).
SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def fetch_set_schedule(session: aiohttp.ClientSession) -> list[dict]:
    """Scrapes digimoncard.io's Card Sets page (default sort = newest
    first), which lists every set with its release date, including
    already-announced future releases. Returns a list of
    {"name": str, "date": datetime, "date_text": str}."""
    try:
        async with session.get(PACKS_URL, timeout=15, headers=SCRAPE_HEADERS) as resp:
            if resp.status != 200:
                log.warning("Set schedule fetch got HTTP %s", resp.status)
                return []
            html = await resp.text()
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.warning("Failed to fetch set schedule: %s", e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        log.warning(
            "Set schedule page fetched OK (%d bytes) but no <table> found — "
            "page layout may have changed.", len(html)
        )
        return []

    sets = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[1].find("a")
        name = link.get_text(strip=True) if link else cells[1].get_text(strip=True)
        date_text = cells[2].get_text(strip=True)
        try:
            date_obj = datetime.strptime(date_text, "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        sets.append({"name": name, "date": date_obj, "date_text": date_text})

    if not sets:
        log.warning("Set schedule table found but no rows parsed successfully.")
    return sets


@bot.tree.command(name="release", description="Show upcoming Digimon TCG set release dates.")
async def release_slash(interaction: discord.Interaction):
    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        sets = await fetch_set_schedule(session)

    if not sets:
        await interaction.followup.send(
            "Couldn't fetch the release schedule right now. Try again later."
        )
        return

    now = datetime.now(timezone.utc)
    upcoming = sorted((s for s in sets if s["date"] >= now), key=lambda s: s["date"])

    embed = discord.Embed(title="📅 Upcoming Digimon TCG Set Releases", color=0x5865F2)

    if upcoming:
        lines = [f"**{s['date_text']}** — {s['name']}" for s in upcoming[:12]]
        embed.description = "\n".join(lines)
    else:
        # Nothing announced yet — show the most recent releases instead
        recent = sorted(sets, key=lambda s: s["date"], reverse=True)[:8]
        lines = [f"**{s['date_text']}** — {s['name']}" for s in recent]
        embed.description = "No upcoming releases are announced yet. Most recent sets:\n" + "\n".join(lines)

    embed.set_footer(text="Source: digimoncard.io/packs")
    await interaction.followup.send(embed=embed)


# --------------------------------------------------------------------------
# Official rulings lookup
# --------------------------------------------------------------------------

def _set_code_from_card_id(card_id: str) -> str | None:
    """Extracts the set-code prefix from a card id, e.g. 'BT4-016' -> 'BT4',
    'EX11-045' -> 'EX11', 'ST7-09' -> 'ST7'."""
    match = re.match(r"^([A-Za-z]+\d+)-", card_id)
    return match.group(1).upper() if match else None


async def fetch_card_ruling(session: aiohttp.ClientSession, card_id: str) -> list[dict]:
    """Scrapes Bandai's official Card Q&A for one specific card, by
    fetching that card's full set page and extracting just the Q&A block
    following that card's own entry. Returns a list of
    {"question": str, "answer": str, "date": str}, or [] if none found
    (either the card has no published rulings, or the page layout has
    changed since this was written).
    """
    set_code = _set_code_from_card_id(card_id)
    notes_label = SET_NOTES_MAP.get(set_code) if set_code else None
    if not notes_label:
        return []

    try:
        async with session.get(
            OFFICIAL_RULINGS_URL,
            params={"search": "true", "notes": notes_label},
            timeout=15,
            headers=SCRAPE_HEADERS,
        ) as resp:
            if resp.status != 200:
                log.warning("Rulings fetch for %s got HTTP %s", card_id, resp.status)
                return []
            html = await resp.text()
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.warning("Failed to fetch rulings page: %s", e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    # Find this specific card's block: it starts at "{card_id} ·" (id
    # followed by its rarity, e.g. "BT4-016 · SR ·") and runs until the
    # next card's own id-prefix pattern begins.
    start_match = re.search(rf"{re.escape(card_id)}\s*·", text)
    if not start_match:
        return []

    rest = text[start_match.end():]
    next_card_match = re.search(r"[A-Z]{1,3}\d+-\d+\s*·", rest)
    block = rest[:next_card_match.start()] if next_card_match else rest

    qa_pattern = re.compile(
        r"Q(\d+)\s*·\s*([A-Za-z]{3}\.?\s*\d{1,2},\s*\d{4})\s*·\s*(.*?)(?=Q\d+\s*·|\Z)",
        re.DOTALL,
    )
    rulings = []
    for qa_match in qa_pattern.finditer(block):
        q_num, date_text, body = qa_match.groups()
        rulings.append({
            "id": q_num,
            "date": date_text.strip(),
            "text": body.strip(),
        })
    return rulings


async def _resolve_mention(guild: discord.Guild | None, username: str) -> str:
    """Looks up a member by their exact Discord username (not display
    name/nickname) and returns a real @mention if found. Falls back to
    plain text if the person isn't in this server or the command was run
    in a DM — a non-functional @name is better than a silent failure."""
    if guild:
        member = discord.utils.find(
            lambda m: m.name.lower() == username.lower(), guild.members
        )
        if member:
            return member.mention
        # Cache miss (e.g. right after startup, before the member list has
        # finished populating) — ask Discord directly as a fallback rather
        # than immediately giving up.
        try:
            results = await guild.query_members(query=username, limit=5)
            match = next(
                (m for m in results if m.name.lower() == username.lower()), None
            )
            if match:
                return match.mention
        except discord.HTTPException as e:
            log.warning("query_members failed for '%s': %s", username, e)

        log.warning(
            "Couldn't find a member named '%s' in guild '%s' — check the "
            "username is exactly right and that Server Members Intent is "
            "enabled in the Discord Developer Portal.", username, guild.name
        )
    return f"@{username}"


async def build_ruling_ping_line(guild: discord.Guild | None) -> str:
    """Builds the mention line attached to every /ruling result: the two
    standing pings, plus a 15% chance of a bonus joke ping."""
    mentions = [await _resolve_mention(guild, u) for u in RULING_PING_USERNAMES]
    line = " ".join(mentions)
    if random.random() < RULING_BONUS_PING_CHANCE:
        bonus_mention = await _resolve_mention(guild, RULING_BONUS_PING_USERNAME)
        line += f" {bonus_mention} (for incorrect ruling 😏)"
    return line


@bot.tree.command(name="ruling", description="Look up official rulings (Card Q&A) for a Digimon TCG card.")
@app_commands.describe(
    card_id="Exact card number, e.g. BT4-016 (pick a suggestion to get the exact number)",
)
@app_commands.autocomplete(card_id=name_autocomplete)
async def ruling_slash(interaction: discord.Interaction, card_id: str):
    await interaction.response.defer()
    card_id = card_id.strip()

    set_code = _set_code_from_card_id(card_id)
    if not set_code or set_code not in SET_NOTES_MAP:
        await interaction.followup.send(
            f"I don't have a rulings lookup mapped for that card's set yet. "
            f"You can search manually at {OFFICIAL_RULINGS_URL}?search=true"
        )
        return

    async with aiohttp.ClientSession() as session:
        rulings = await fetch_card_ruling(session, card_id)

    notes_label = SET_NOTES_MAP[set_code]
    source_url = f"{OFFICIAL_RULINGS_URL}?search=true&notes={notes_label.replace(' ', '+')}"

    if not rulings:
        ping_line = await build_ruling_ping_line(interaction.guild)
        await interaction.followup.send(
            f"{ping_line}\n"
            f"No published rulings found for **{card_id}**. "
            f"Either none have been issued yet, or you can double check on the official set page: {source_url}"
        )
        return

    embed = discord.Embed(
        title=f"📖 Official Rulings — {card_id}",
        color=0x5865F2,
        description=f"[View full set rulings page]({source_url})",
    )
    for ruling in rulings[:5]:
        embed.add_field(
            name=f"Q{ruling['id']} ({ruling['date']})",
            value=ruling["text"][:1000],
            inline=False,
        )
    if len(rulings) > 5:
        embed.set_footer(text=f"+{len(rulings) - 5} more — see the full set page above.")
    else:
        embed.set_footer(text="Source: world.digimoncard.com (official)")

    ping_line = await build_ruling_ping_line(interaction.guild)
    await interaction.followup.send(content=ping_line, embed=embed)


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
        card, matches, error = await do_lookup(name, set_query)

    if error:
        await ctx.send(error)
        return

    if card:
        embed = build_embed(card)
        view = ArtCycleView(card, ctx.author.id) if len(card.get("variants") or []) > 1 else None
        await ctx.send(embed=embed, view=view)
        return

    await ctx.send(format_matches_message(name, matches))


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

_cache_refresher_started = False
_healthcheck_started = False


async def periodic_cache_refresh():
    """Keep the autocomplete cache reasonably fresh without hammering the
    API — once a day is plenty since new sets release infrequently."""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await refresh_all_cards()


async def start_healthcheck_server():
    """Tiny HTTP server so external uptime monitors (UptimeRobot, Docker
    healthcheck, etc.) can verify the bot process is alive and actually
    connected to Discord — not just that the process exists."""
    async def health(request):
        healthy = bot.is_ready() and not bot.is_closed()
        return web.json_response(
            {"status": "ok" if healthy else "not_ready", "latency_ms": round(bot.latency * 1000, 1)},
            status=200 if healthy else 503,
        )

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTHCHECK_PORT)
    await site.start()
    log.info("Healthcheck server listening on :%d/health", HEALTHCHECK_PORT)


@bot.event
async def on_ready():
    global _cache_refresher_started, _healthcheck_started

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

    if not _healthcheck_started:
        bot.loop.create_task(start_healthcheck_server())
        _healthcheck_started = True

    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id)


def main():
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and add your bot token."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
