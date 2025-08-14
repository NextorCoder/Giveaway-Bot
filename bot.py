"""
Discord Giveaway Tracker Bot

Storage: SQLite (aiosqlite). Each giveaway stores entrants and winners. A background task closes giveaways at their deadlines.

Requires: Python 3.10+, discord.py 2.x

Install:
  pip install -U discord.py aiosqlite python-dotenv

Run:
  Create a .env file with TOKEN=your_bot_token_here
  python bot.py
"""
from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Set

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

DB_PATH = "giveaways.db"

# ------------- Data Models -------------
@dataclass
class Giveaway:
    id: int
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    host_id: int
    prize: str
    winners_count: int
    ends_at: datetime
    status: str  # "running" | "ended"

# ------------- Helpers -------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                host_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                winners_count INTEGER NOT NULL,
                ends_at TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entrants (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (giveaway_id, user_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS winners (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (giveaway_id, user_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS win_counts (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wins INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS vouches (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                giveaway_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, giveaway_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS vouch_blocks (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                giveaway_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, giveaway_id)
            );

            CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            vouch_channel_id INTEGER
        );



            """
        )
        await db.commit()

async def fetch_running_giveaways() -> List[Giveaway]:
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM giveaways WHERE status = 'running'"
        )
        return [row_to_giveaway(r) for r in rows]

async def insert_giveaway(g: Giveaway) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        cur = await db.execute(
            """
            INSERT INTO giveaways (guild_id, channel_id, message_id, host_id, prize, winners_count, ends_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.guild_id,
                g.channel_id,
                g.message_id,
                g.host_id,
                g.prize,
                g.winners_count,
                g.ends_at.isoformat(),
                g.status,
            ),
        )
        await db.commit()
        return cur.lastrowid

async def set_giveaway_message_id(giveaway_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (message_id, giveaway_id),
        )
        await db.commit()



async def add_entrant(giveaway_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        try:
            await db.execute(
                "INSERT OR IGNORE INTO entrants (giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, user_id),
            )
            await db.commit()
        except Exception:
            pass

async def remove_entrant(giveaway_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.execute(
            "DELETE FROM entrants WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        await db.commit()

async def get_entrants(giveaway_id: int) -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        rows = await db.execute_fetchall(
            "SELECT user_id FROM entrants WHERE giveaway_id = ?",
            (giveaway_id,),
        )
        return [r[0] for r in rows]

async def set_winners(giveaway_id: int, user_ids: List[int]):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.executemany(
            "INSERT OR IGNORE INTO winners (giveaway_id, user_id) VALUES (?, ?)",
            [(giveaway_id, uid) for uid in user_ids],
        )
        await db.commit()

async def get_winners(giveaway_id: int) -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        rows = await db.execute_fetchall(
            "SELECT user_id FROM winners WHERE giveaway_id = ?",
            (giveaway_id,),
        )
        return [r[0] for r in rows]

async def increment_win_counts(guild_id: int, user_ids: List[int]):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.executemany(
            """
            INSERT INTO win_counts (guild_id, user_id, wins) VALUES (?, ?, 1)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET wins = wins + 1
            """,
            [(guild_id, uid) for uid in user_ids],
        )
        await db.commit()

async def remove_winners(giveaway_id: int, user_ids: list[int]):
    """Remove specific winners from this giveaway."""
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.executemany(
            "DELETE FROM winners WHERE giveaway_id = ? AND user_id = ?",
            [(giveaway_id, uid) for uid in user_ids]
        )
        await db.commit()

async def decrement_win_counts(guild_id: int, user_ids: list[int]):
    """Decrease win count for users in this guild by 1 if > 0."""
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.executemany(
            "UPDATE win_counts SET wins = wins - 1 WHERE guild_id = ? AND user_id = ? AND wins > 0",
            [(guild_id, uid) for uid in user_ids]
        )
        # Remove zeroes for cleanliness
        await db.execute(
            "DELETE FROM win_counts WHERE guild_id = ? AND wins <= 0",
            (guild_id,)
        )
        await db.commit()

async def get_vouch_channel_id(guild_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT vouch_channel_id FROM guild_config WHERE guild_id = ?",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def set_vouch_channel_id(guild_id: int, channel_id: Optional[int]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_config (guild_id, vouch_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET vouch_channel_id = excluded.vouch_channel_id
            """,
            (guild_id, channel_id)
        )
        await db.commit()



async def add_manual_giveaway_with_winner(guild_id: int, prize: str, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        # Check if a giveaway with same prize and guild exists (optional uniqueness check)
        async with db.execute(
            "SELECT id FROM giveaways WHERE guild_id = ? AND prize = ?",
            (guild_id, prize)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            giveaway_id = row[0]
        else:
            # Insert a new ended giveaway row
            now = datetime.now(timezone.utc).isoformat()
            cur = await db.execute(
                """
                INSERT INTO giveaways (
                    guild_id, channel_id, message_id, host_id,
                    prize, winners_count, ends_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    0,  # no channel
                    None,  # no message
                    0,  # host unknown
                    prize,
                    1,  # winners count
                    now,
                    "ended"
                ),
            )
            giveaway_id = cur.lastrowid

        # Insert winner
        await db.execute(
            "INSERT OR IGNORE INTO winners (giveaway_id, user_id) VALUES (?, ?)",
            (giveaway_id, user_id),
        )

        # Increment win count
        await db.execute(
            """
            INSERT INTO win_counts (guild_id, user_id, wins) VALUES (?, ?, 1)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET wins = wins + 1
            """,
            (guild_id, user_id)
        )

        await db.commit()
    return giveaway_id



async def set_giveaway_status(giveaway_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.execute(
            "UPDATE giveaways SET status = ? WHERE id = ?",
            (status, giveaway_id),
        )
        await db.commit()


async def delete_giveaway(giveaway_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)

        # Get guild_id for decrement
        async with db.execute(
            "SELECT guild_id FROM giveaways WHERE id = ?",
            (giveaway_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        guild_id = row[0]

        # Collect winners for decrement
        async with db.execute(
            "SELECT user_id FROM winners WHERE giveaway_id = ?",
            (giveaway_id,)
        ) as cursor:
            winners = [r[0] for r in await cursor.fetchall()]

        # Explicitly delete vouches tied to this giveaway (belt-and-suspenders)
        await db.execute(
            "DELETE FROM vouches WHERE giveaway_id = ?",
            (giveaway_id,)
        )
        # Also clear any vouch_blocks for this giveaway
        await db.execute(
            "DELETE FROM vouch_blocks WHERE giveaway_id = ?",
            (giveaway_id,)
        )

        # Delete the giveaway (will cascade entrants/winners if FK works)
        await db.execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))

        # Adjust win_counts for each winner
        await db.executemany(
            "UPDATE win_counts SET wins = wins - 1 WHERE guild_id = ? AND user_id = ? AND wins > 0",
            [(guild_id, uid) for uid in winners]
        )
        # Remove zeroes
        await db.execute(
            "DELETE FROM win_counts WHERE guild_id = ? AND wins <= 0",
            (guild_id,)
        )

        await db.commit()
    return True



async def fetch_giveaway(giveaway_id: int) -> Optional[Giveaway]:
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM giveaways WHERE id = ?",
            (giveaway_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_giveaway(row) if row else None



async def top_winners(guild_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        rows = await db.execute_fetchall(
            """
            SELECT wc.user_id,
                   wc.wins,
                   COALESCE(vc.vouch_count, 0) AS vouch_count
            FROM win_counts wc
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS vouch_count
                FROM vouches
                WHERE guild_id = ?
                GROUP BY user_id
            ) vc
            ON wc.user_id = vc.user_id
            WHERE wc.guild_id = ?
            ORDER BY wc.wins DESC, vc.vouch_count DESC
            LIMIT ?
            """,
            (guild_id, guild_id, limit),
        )
    return [(r[0], r[1], r[2]) for r in rows]  # uid, wins, vouches


async def user_wins(guild_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        db.row_factory = aiosqlite.Row
        # Fetch giveaways this user has won in this guild
        async with db.execute("""
            SELECT g.id AS giveaway_id, g.prize
            FROM winners w
            JOIN giveaways g ON g.id = w.giveaway_id
            WHERE g.guild_id = ? AND w.user_id = ?
            ORDER BY g.id DESC
        """, (guild_id, user_id)) as cursor:
            rows = await cursor.fetchall()
    wins_list = [(row["giveaway_id"], row["prize"]) for row in rows]
    return len(wins_list), wins_list

async def add_vouch(guild_id: int, user_id: int, giveaway_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        await db.execute(
            "INSERT OR IGNORE INTO vouches (guild_id, user_id, giveaway_id) VALUES (?, ?, ?)",
            (guild_id, user_id, giveaway_id)
        )
        await db.commit()

async def get_user_vouches(guild_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.id, g.prize
            FROM vouches v
            JOIN giveaways g ON g.id = v.giveaway_id
            WHERE v.guild_id = ? AND v.user_id = ?
            ORDER BY g.id DESC
        """, (guild_id, user_id)) as cursor:
            rows = await cursor.fetchall()
    return [(row["id"], row["prize"]) for row in rows]

async def enable_fks(db: aiosqlite.Connection):
    await db.execute("PRAGMA foreign_keys = ON;")


async def adjust_win_for_gw(giveaway_id: int, user_id: int, change: int):
    """Adjust a user's win for one specific giveaway by ±1."""
    async with aiosqlite.connect(DB_PATH) as db:
        await enable_fks(db)
        # Get guild_id for update
        async with db.execute(
            "SELECT guild_id FROM giveaways WHERE id = ?",
            (giveaway_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False  # Giveaway not found
        guild_id = row[0]

        if change > 0:
            # Add to winners table + increment wins
            await db.execute(
                "INSERT OR IGNORE INTO winners (giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, user_id)
            )
            await db.execute(
                """
                INSERT INTO win_counts (guild_id, user_id, wins)
                VALUES (?, ?, 1)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET wins = wins + 1
                """,
                (guild_id, user_id)
            )

        elif change < 0:
            # Remove from winners table + decrement wins
            await db.execute(
                "DELETE FROM winners WHERE giveaway_id = ? AND user_id = ?",
                (giveaway_id, user_id)
            )
            await db.execute(
                "UPDATE win_counts SET wins = wins - 1 WHERE guild_id = ? AND user_id = ? AND wins > 0",
                (guild_id, user_id)
            )
            await db.execute(
                "DELETE FROM win_counts WHERE guild_id = ? AND wins <= 0",
                (guild_id,)
            )

        await db.commit()
    return True




def row_to_giveaway(r: aiosqlite.Row) -> Giveaway:
    return Giveaway(
        id=r["id"],
        guild_id=r["guild_id"],
        channel_id=r["channel_id"],
        message_id=r["message_id"],
        host_id=r["host_id"],
        prize=r["prize"],
        winners_count=r["winners_count"],
        ends_at=datetime.fromisoformat(r["ends_at"]),
        status=r["status"],
    )

# ------------- Discord Bot -------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # REQUIRED for reading message.content


class JoinView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    async def update_message_embed(self, interaction: discord.Interaction):
        """Fetch entrants count and update the giveaway embed."""
        g = await fetch_giveaway(self.giveaway_id)
        if not g:
            return

        entrants = await get_entrants(self.giveaway_id)
        count = len(entrants)

        # Fetch the original message
        channel = interaction.channel
        if g.channel_id != interaction.channel_id:
            channel = interaction.client.get_channel(g.channel_id) or await interaction.client.fetch_channel(g.channel_id)

        try:
            message = await channel.fetch_message(g.message_id)
        except discord.NotFound:
            return

        # Rebuild embed with updated count
        embed = message.embeds[0]
        embed.description = (
            f"Hosted by: <@{g.host_id}>\n"
            f"Winners: **{g.winners_count}**\n"
            f"Ends {ts(g.ends_at)}\n\n"
            f"Click **Join** below to enter.\n"
            f"**Current Entries:** {count}"
        )

        await message.edit(embed=embed, view=self)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Block users whose wins > vouches
        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Fetch wins
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            async with db.execute(
                "SELECT wins FROM win_counts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cur:
                row = await cur.fetchone()
        wins_count = row[0] if row else 0

        # Fetch vouches
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            async with db.execute(
                "SELECT COUNT(*) FROM vouches WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cur:
                vrow = await cur.fetchone()
        vouch_count = vrow[0] if vrow else 0

        # Enforce: must have vouches >= wins
        if wins_count > vouch_count:
            shortage = wins_count - vouch_count
            return await interaction.response.send_message(
                f"❌ You can’t join yet. You have {wins_count} wins but only {vouch_count} vouches. "
                f"Please complete {shortage} vouch{'es' if shortage != 1 else ''} first.",
                ephemeral=True
            )

        # Existing logic: prevent duplicate entry
        before = await get_entrants(self.giveaway_id)
        if user_id in before:
            await interaction.response.send_message("You're already in ✅", ephemeral=True)
        else:
            await add_entrant(self.giveaway_id, user_id)
            await interaction.response.send_message("You're in! ✅", ephemeral=True)
            await self.update_message_embed(interaction)


    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        before = await get_entrants(self.giveaway_id)
        if interaction.user.id not in before:
            await interaction.response.send_message("You are not in the giveaway.", ephemeral=True)
        else:
            await remove_entrant(self.giveaway_id, interaction.user.id)
            await interaction.response.send_message("You have left the giveaway.", ephemeral=True)
            await self.update_message_embed(interaction)


bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

def ts(dt: datetime) -> str:
    # Discord relative timestamp format
    return f"<t:{int(dt.replace(tzinfo=timezone.utc).timestamp())}:R>"

async def close_giveaway(g: Giveaway, reason: str = "Ended"):
    # Pick winners and announce
    entrants = await get_entrants(g.id)
    winners: List[int] = []
    if entrants:
        pool = set(entrants)
        k = min(g.winners_count, len(pool))
        winners = random.sample(list(pool), k)
        await set_winners(g.id, winners)
        await increment_win_counts(g.guild_id, winners)

    await set_giveaway_status(g.id, "ended")

    channel = bot.get_channel(g.channel_id) or await bot.fetch_channel(g.channel_id)
    message=None
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        if winners:
            mentions = ", ".join(f"<@{uid}>" for uid in winners)
            desc = f"**Prize:** {g.prize}\n**Winners:** {mentions}\n**Total Entries:** {len(entrants)}"
        else:
            desc = f"**Prize:** {g.prize}\nNo valid entries."
        embed = discord.Embed(title=f"🎉 Giveaway #{g.id} {reason}", description=desc, color=discord.Color.blurple())
        embed.set_footer(text="Use /gw reroll to draw again from the same entrants.")
        await channel.send(embed=embed)
    if isinstance(channel, (discord.TextChannel, discord.Thread)) and g.message_id:
        try:
            message = await channel.fetch_message(g.message_id)
        except discord.NotFound:
            message = None
    if message:
        view = JoinView(g.id)
        for item in view.children:
            item.disabled = True

        # Build updated embed
        if winners:
            mentions = ", ".join(f"<@{uid}>" for uid in winners)
            desc = f"**Prize:** {g.prize}\n**Winners:** {mentions}\n**Total Entries:** {len(entrants)}"
        else:
            desc = f"**Prize:** {g.prize}\nNo valid entries."

        ended_embed = discord.Embed(title=f"🎉 Giveaway #{g.id} {reason}", description=desc, color=discord.Color.blurple())
        ended_embed.set_footer(text="Giveaway has ended.")

        await message.edit(embed=ended_embed, view=view)

@tasks.loop(seconds=5)
async def scheduler():
    # Close giveaways that reached deadline
    now = datetime.now(timezone.utc)
    running = await fetch_running_giveaways()
    for g in running:
        # Normalize ends_at to an aware UTC datetime
        if g.ends_at.tzinfo is None:
            ends_at_utc = g.ends_at.replace(tzinfo=timezone.utc)
        else:
            ends_at_utc = g.ends_at.astimezone(timezone.utc)

        if ends_at_utc <= now:
            try:
                await close_giveaway(g)
            except Exception as e:
                print(f"Failed closing giveaway {g.id}: {e}")


@scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()
#variables

VOUCH_CHANNEL_ID = 1403882566200721469

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        return

    try:
        configured_vouch_channel_id = await get_vouch_channel_id(message.guild.id)
    except Exception:
        configured_vouch_channel_id = None

    vouch_channel_id = configured_vouch_channel_id or VOUCH_CHANNEL_ID


    # ... restricted vouch-handling logic ...

    if message.channel.id == vouch_channel_id:
        # Only proceed if the user explicitly typed "vouch" (case-insensitive)
        if "vouch" not in message.content.lower():
            # Ignore silently so we don't spam users with prompts or info
            return

        # Get all giveaways this user has won in this guild (ended only)
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT g.id, g.prize
                FROM winners w
                JOIN giveaways g ON g.id = w.giveaway_id
                WHERE g.guild_id = ? AND w.user_id = ?
                AND g.status = 'ended'
            """, (message.guild.id, message.author.id)) as cursor:
                won_rows = await cursor.fetchall()

        if not won_rows:
            await message.channel.send(
                f"❌ {message.author.mention}, you have not won any giveaways — you cannot vouch.",
                delete_after=5
            )
            return

        # Get already-vouched giveaways for this user
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            async with db.execute("""
                SELECT giveaway_id
                FROM vouches
                WHERE guild_id = ? AND user_id = ?
            """, (message.guild.id, message.author.id)) as cursor:
                vouched_ids = {row[0] for row in await cursor.fetchall()}

        # Compute remaining giveaways the user still needs to vouch for
        remaining_to_vouch = [(row["id"], row["prize"]) for row in won_rows if row["id"] not in vouched_ids]

        if not remaining_to_vouch:
            # User typed "vouch" but has nothing left to vouch; now we inform
            await message.channel.send(
                f"ℹ️ {message.author.mention}, you have already vouched for all your giveaways.",
                delete_after=5
            )
            return

        if len(remaining_to_vouch) == 1:
            # Single target: auto-add only now that they typed "vouch"
            gw_id, prize = remaining_to_vouch[0]

            # Check if mods blocked this specific vouch
            async with aiosqlite.connect(DB_PATH) as db:
                await enable_fks(db)
                async with db.execute(
                    "SELECT 1 FROM vouch_blocks WHERE guild_id = ? AND user_id = ? AND giveaway_id = ?",
                    (message.guild.id, message.author.id, gw_id)
                ) as cursor:
                    blocked = await cursor.fetchone()

            if blocked:
                await message.channel.send(
                    f"❌ {message.author.mention}, a moderator has blocked vouches for Giveaway #{gw_id}. Please contact staff.",
                    delete_after=5
                )
                return

            await add_vouch(message.guild.id, message.author.id, gw_id)
            await message.channel.send(
                f"✅ Vouch recorded for {message.author.mention} (Giveaway #{gw_id} - {prize})",
                delete_after=5
            )
            return

        # Multiple giveaways remaining: since they typed "vouch", show selection guidance
        gw_list = "\n".join([f"#{gid} — {prize}" for gid, prize in remaining_to_vouch])
        await message.channel.send(
            f"🔍 {message.author.mention}, you have won multiple giveaways:\n{gw_list}\n"
            f"Please use `/gw vouch giveaway_id:<id>` to specify which one you’re vouching for.",
            delete_after=10
        )
        return


    await bot.process_commands(message)

@bot.event
async def on_ready():
    await init_db()
    try:
        await bot.tree.sync()
        print(f"Synced slash commands for {bot.user}")
    except Exception as e:
        print("Slash sync failed:", e)
    scheduler.start()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# ------------- Slash Commands -------------
class GWGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="gw", description="Giveaway commands")

    @app_commands.command(name="start", description="Start a giveaway with a join button")
    @app_commands.describe(
        duration="Duration like 10m, 2h, 1d",
        winners="Number of winners",
        prize="Prize title"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start(self, interaction: discord.Interaction, duration: str, winners: app_commands.Range[int, 1, 50], prize: str):
        # Parse duration
        seconds = parse_duration(duration)
        if seconds is None or seconds < 10:
            return await interaction.response.send_message("Duration must be at least 10s (e.g. 10m, 2h, 1d).", ephemeral=True)

        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        g = Giveaway(
            id=0,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            message_id=None,
            host_id=interaction.user.id,
            prize=prize,
            winners_count=int(winners),
            ends_at=ends_at,
            status="running",
        )
        gid = await insert_giveaway(g)

        embed = discord.Embed(
            title=f"🎉 Giveaway #{gid} — {prize}",
            description=(
                f"Hosted by: <@{g.host_id}>\n"
                f"Winners: **{g.winners_count}**\n"
                f"Ends {ts(ends_at)}\n\n"
                f"Click **Join** below to enter.\n"
                f"**Current Entries:** 0"
            ),
            color=discord.Color.green(),
        )

        view = JoinView(gid)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        await set_giveaway_message_id(gid, msg.id)

    @app_commands.command(name="end", description="End a running giveaway now and pick winners")
    @app_commands.describe(giveaway_id="ID of the giveaway (shown in the title)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("Giveaway not found in this server.", ephemeral=True)
        if g.status != "running":
            return await interaction.response.send_message("That giveaway has already ended.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_giveaway(g, reason="Ended Early")
        await interaction.followup.send("Giveaway ended and winners announced.", ephemeral=True)

    @app_commands.command(name="reroll", description="Reroll winners for a finished giveaway (excludes all old winners)")
    @app_commands.describe(
        giveaway_id="ID of the giveaway to reroll",
        count="How many winners to reroll (default 1)",
        target_user="Specific previous winner to remove and reroll"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int, count: Optional[int] = 1, target_user: Optional[discord.Member] = None):
        # Fast validations FIRST
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("Giveaway not found in this server.", ephemeral=True)
        if g.status != "ended":
            return await interaction.response.send_message("Giveaway must be ended before rerolling.", ephemeral=True)

        prev_winners = list(set(await get_winners(g.id)))
        if not prev_winners:
            return await interaction.response.send_message("No previous winners found for this giveaway.", ephemeral=True)

        # ✅ Defer early to avoid Unknown interaction error
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # Decide which winners to remove
            if target_user:
                if target_user.id not in prev_winners:
                    return await interaction.followup.send("The specified user is not a winner of this giveaway.", ephemeral=True)
                to_remove = [target_user.id]
            else:
                k = min(max(1, count or 1), len(prev_winners))
                to_remove = prev_winners[-k:]  # or random.sample(prev_winners, k) if random removal is desired

            # Remove and decrement their counts
            await remove_winners(g.id, to_remove)
            await decrement_win_counts(g.guild_id, to_remove)

            entrants = await get_entrants(g.id)
            if not entrants:
                return await interaction.followup.send("No entrants to reroll from.", ephemeral=True)

            # ❗ Exclude ALL previous winners from new pool
            pool = list(set(entrants) - set(prev_winners))
            if not pool:
                return await interaction.followup.send("No eligible entrants remain to reroll from.", ephemeral=True)

            k = min(len(pool), len(to_remove))
            new_winners = random.sample(pool, k)

            # Save and increment counts
            await set_winners(g.id, new_winners)
            await increment_win_counts(g.guild_id, new_winners)

            # Announce in channel
            removed_mentions = ", ".join(f"<@{uid}>" for uid in to_remove)
            mentions = ", ".join(f"<@{uid}>" for uid in new_winners)
            embed = discord.Embed(
                title=f"🎲 Giveaway #{g.id} Reroll",
                description=f"Removed: {removed_mentions}\nNew winner(s): {mentions}",
                color=discord.Color.orange(),
            )

            channel = interaction.client.get_channel(g.channel_id) or await interaction.client.fetch_channel(g.channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(embed=embed)

                # Update original giveaway message
                if g.message_id:
                    try:
                        msg = await channel.fetch_message(g.message_id)
                        all_winners_now = await get_winners(g.id)
                        if all_winners_now:
                            all_mentions = ", ".join(f"<@{uid}>" for uid in all_winners_now)
                            desc = f"**Prize:** {g.prize}\n**Winners:** {all_mentions}"
                        else:
                            desc = f"**Prize:** {g.prize}\n🏆 None"
                        ended_embed = discord.Embed(
                            title=f"🎉 Giveaway #{g.id} Ended (Updated)",
                            description=desc,
                            color=discord.Color.blurple()
                        )
                        await msg.edit(embed=ended_embed, view=None)
                    except Exception:
                        pass

            await interaction.followup.send("Reroll complete — all old winners excluded from pool.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"Failed to reroll: {e}", ephemeral=True)




    @app_commands.command(name="wins", description="Show how many giveaways a user has won")
    async def wins(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        user = user or interaction.user
        count, wins_list = await user_wins(interaction.guild_id, user.id)

        if count == 0:
            return await interaction.response.send_message(
                f"**{user.display_name}** hasn't won any giveaways in this server."
            )

        # Create a nicely formatted list
        win_lines = [f"#{gid} — {prize}" for gid, prize in wins_list]
        description = "\n".join(win_lines)

        embed = discord.Embed(
            title=f"🏆 Giveaways Won by {user.display_name}",
            description=f"Total Wins: **{count}**\n\n{description}",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="adjustwins", description="Add or remove a win for a user for a specific giveaway")
    @app_commands.describe(
        giveaway_id="The giveaway ID to adjust",
        user="The user whose win will be changed",
        action="Choose whether to add or remove the win"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add win", value="add"),
        app_commands.Choice(name="Remove win", value="remove")
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def adjustwins(self, interaction: discord.Interaction, giveaway_id: int, user: discord.Member, action: app_commands.Choice[str]):
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("⚠️ Giveaway not found in this server.", ephemeral=True)

        change = 1 if action.value == "add" else -1
        success = await adjust_win_for_gw(giveaway_id, user.id, change)
        if not success:
            return await interaction.response.send_message("⚠️ Could not adjust win — giveaway may not exist.", ephemeral=True)

        if change > 0:
            msg = f"✅ Added 1 win for {user.mention} in Giveaway #{giveaway_id} — {g.prize}."
        else:
            msg = f"✅ Removed 1 win for {user.mention} in Giveaway #{giveaway_id} — {g.prize}."

        await interaction.response.send_message(msg, ephemeral=True)

    
    @app_commands.command(name="manual", description="Create a manual giveaway entry and assign a winner")
    @app_commands.describe(
        prize="Name of the giveaway/prize",
        winner="The member who won"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def manual(self, interaction: discord.Interaction, prize: str, winner: discord.Member):
        giveaway_id = await add_manual_giveaway_with_winner(interaction.guild_id, prize, winner.id)

        embed = discord.Embed(
            title=f"🏆 Manual Giveaway Recorded",
            description=f"Giveaway #{giveaway_id} — {prize}\nWinner: <@{winner.id}>",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="delete", description="Delete a giveaway from the database")
    @app_commands.describe(giveaway_id="ID of the giveaway to delete")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def delete(self, interaction: discord.Interaction, giveaway_id: int):
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("Giveaway not found in this server.", ephemeral=True)

        # Delete it
        success = await delete_giveaway(giveaway_id)
        if not success:
            return await interaction.response.send_message("Could not delete giveaway — it may not exist.", ephemeral=True)

        await interaction.response.send_message(f"✅ Giveaway #{giveaway_id} deleted successfully.", ephemeral=True)





    class LeaderboardView(discord.ui.View):
        def __init__(self, pages: list[discord.Embed], author_id: int):
            super().__init__(timeout=180)  # Buttons work for 3 minutes
            self.pages = pages
            self.current_page = 0
            self.author_id = author_id
            self.update_buttons()

        def update_buttons(self):
            self.prev_btn.disabled = self.current_page == 0
            self.next_btn.disabled = self.current_page == len(self.pages) - 1

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            """Only the command author can use the buttons."""
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("❌ This menu isn't for you!", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
        async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

        @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
        async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

        @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
        async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Delete the leaderboard message
            await interaction.message.delete()

    @app_commands.command(name="leaderboard", description="Top giveaway winners in this server (with vouches)")
    async def leaderboard(self, interaction: discord.Interaction):
        # 1) Acknowledge instantly so Discord stops "Thinking..."
        await interaction.response.defer(thinking=True)

        # 2) Fetch leaderboard data
        top = await top_winners(interaction.guild_id, limit=100)
        if not top:
            return await interaction.followup.send("No wins recorded yet.")

        # 3) Build lines without slow lookups
        lines = []
        for i, (uid, wins, vouches) in enumerate(top, start=1):
            if not uid:
                continue
            try:
                uid_int = int(uid)
            except (ValueError, TypeError):
                continue  # skip bad IDs

            # Try to resolve member from guild cache
            member = interaction.guild.get_member(uid_int) if interaction.guild else None
            if member:
                display = member.mention  # clickable, shows current display name
            else:
                display = f"User Left ({uid_int})"

            lines.append(f"**{i}.** {display} — 🏆 **{wins}** wins — 📝 **{vouches}** vouches")


        # Split into chunks of 25
        chunks = [lines[i:i + 25] for i in range(0, len(lines), 25)]
        pages = [
            discord.Embed(
                title=f"🏆 Giveaway Winners Leaderboard — Page {idx+1}/{len(chunks)}",
                description="\n".join(chunk),
                color=discord.Color.gold()
            )
            for idx, chunk in enumerate(chunks)
        ]

        # 4) Send with paginator view
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            view = self.LeaderboardView(pages, interaction.user.id)
            await interaction.followup.send(embed=pages[0], view=view)







    @app_commands.command(name="list", description="List all giveaways (active and past)")
    async def list(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            db.row_factory = aiosqlite.Row

            # Fetch all giveaways for this guild in one go
            rows = await db.execute_fetchall(
                "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC",
                (interaction.guild_id,)
            )

            if not rows:
                return await interaction.response.send_message("No giveaways found.", ephemeral=True)

            # Pre-fetch winners for all ended giveaways in a single batched query
            ended_ids = [r["id"] for r in rows if r["status"] == "ended"]
            winners_map = {}

            if ended_ids:
                placeholders = ",".join("?" for _ in ended_ids)
                winner_rows = await db.execute_fetchall(
                    f"SELECT giveaway_id, user_id FROM winners WHERE giveaway_id IN ({placeholders})",
                    ended_ids
                )
                # Build a map: giveaway_id -> [user_ids...]
                for wr in winner_rows:
                    winners_map.setdefault(wr["giveaway_id"], []).append(wr["user_id"])

            lines = []
            for r in rows:
                status_emoji = "🟢" if r["status"] == "running" else "🔴"

                # Robust timestamp handling
                try:
                    dt = datetime.fromisoformat(r["ends_at"])
                except Exception:
                    dt = None

                ends_info = ts(dt) if (r["status"] == "running" and dt is not None) else "Ended"

                winners_text = ""
                if r["status"] == "ended":
                    w_list = winners_map.get(r["id"], [])
                    if w_list:
                        mentions = ", ".join(f"<@{uid}>" for uid in w_list)
                        winners_text = f" — 🏆 {mentions}"
                    else:
                        winners_text = " — 🏆 None"

            

                lines.append(
                    f"{status_emoji} **#{r['id']}** — {r['prize']} — Winners: {r['winners_count']} — {ends_info}{winners_text}"
                )

            embed = discord.Embed(
                title="📜 Giveaways List",
                description="\n".join(lines),
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)


    @app_commands.command(name="vouches", description="Show how many vouches a user has and for which giveaways")
    async def vouches(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        user = user or interaction.user
        vch_list = await get_user_vouches(interaction.guild_id, user.id)

        if not vch_list:
            return await interaction.response.send_message(f"{user.display_name} has no recorded vouches.", ephemeral=True)

        lines = [f"#{gid} — {prize}" for gid, prize in vch_list]
        embed = discord.Embed(
            title=f"📝 Vouches for {user.display_name}",
            description=f"Total vouches: **{len(vch_list)}**\n\n" + "\n".join(lines),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="vouch", description="Record a vouch for a specific giveaway you have won")
    @app_commands.describe(giveaway_id="ID of the giveaway you are vouching for")
    async def vouch(self, interaction: discord.Interaction, giveaway_id: int):
        # Blocked vouch check (use cursor + fetchone)
        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            async with db.execute(
                "SELECT 1 FROM vouch_blocks WHERE guild_id = ? AND user_id = ? AND giveaway_id = ?",
                (interaction.guild_id, interaction.user.id, giveaway_id)
            ) as cursor:
                row = await cursor.fetchone()

        if row:
            return await interaction.response.send_message(
                "❌ This vouch has been blocked by a moderator. Please contact a mod if you think this is a mistake.",
                ephemeral=True
            )


        # Validate giveaway and win status
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("⚠️ Invalid giveaway ID.", ephemeral=True)

        winners_list = await get_winners(giveaway_id)
        if interaction.user.id not in winners_list:
            return await interaction.response.send_message(
                "❌ You have not won this giveaway — you cannot vouch for it.",
                ephemeral=True
            )

        # Check if already vouched
        vch_list = await get_user_vouches(interaction.guild_id, interaction.user.id)
        if any(giveaway_id == gid for gid, _ in vch_list):
            return await interaction.response.send_message(
                "ℹ️ You have already vouched for this giveaway.",
                ephemeral=True
            )

        # Record vouch
        await add_vouch(interaction.guild_id, interaction.user.id, giveaway_id)
        await interaction.response.send_message(
            f"✅ Vouch recorded for Giveaway #{giveaway_id} — {g.prize}",
            ephemeral=True
        )

    @app_commands.command(
        name="addvouch",
        description="Mod-only: record a vouch for a user for a specific ended giveaway"
    )
    @app_commands.describe(
        user="The member who is vouching",
        giveaway_id="ID of the ended giveaway the user is vouching for"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def addvouch(self, interaction: discord.Interaction, user: discord.Member, giveaway_id: int):
        # Validate giveaway
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message("⚠️ Invalid giveaway ID for this server.", ephemeral=True)

        if g.status != "ended":
            return await interaction.response.send_message("⚠️ Giveaway must be ended to record a vouch.", ephemeral=True)

        # Ensure the user actually won this giveaway (keeps vouches meaningful)
        winners_list = await get_winners(giveaway_id)
        if user.id not in winners_list:
            return await interaction.response.send_message(
                "❌ This user did not win that giveaway, so a vouch cannot be recorded.",
                ephemeral=True
            )

        # Prevent duplicates
        vch_list = await get_user_vouches(interaction.guild_id, user.id)
        if any(giveaway_id == gid for gid, _ in vch_list):
            return await interaction.response.send_message(
                "ℹ️ A vouch for this user and giveaway is already recorded.",
                ephemeral=True
            )

        # Record vouch
        await add_vouch(interaction.guild_id, user.id, giveaway_id)

        # Acknowledge
        await interaction.response.send_message(
            f"✅ Vouch recorded: {user.mention} for Giveaway #{giveaway_id} — {g.prize}",
            ephemeral=True
        )

    @app_commands.command(name="help", description="Show all giveaway commands and their descriptions")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📚 Giveaway Bot Commands",
            description="Here’s a list of all available `/gw` commands:",
            color=discord.Color.blue()
        )

        commands_info = [
            ("/gw start", "Start a giveaway with a join button. Usage: /gw start duration:<10m|2h|1d> winners:<count> prize:<text>"),
            ("/gw end", "End a running giveaway early and pick winners."),
            ("/gw reroll", "Reroll winners for a finished giveaway."),
            ("/gw wins", "Show how many giveaways (and which) a user has won."),
            ("/gw leaderboard", "Show the top giveaway winners in the server."),
            ("/gw list", "List all giveaways (active and past)."),
            ("/gw manual", "Create a manual giveaway with a prize name and winner (even if it wasn't hosted by the bot)."),
            ("/gw adjustwins", "Add or remove a win for a user for a specific giveaway."),
            ("/gw delete", "Delete a giveaway from the database."),
            ("/gw vouches", "Show how many vouches a user has and for which giveaways."),
            ("/gw vouch", "Record a vouch for a specific giveaway you have won."),
            ("/gw addvouch", "Mod-only: record a vouch for a user for a specific ended giveaway."),
            ("/gw removevouch", "Mod-only: remove a vouch for a user for a specific giveaway"),
            ("/gw config set", "Set the vouch channel for this server."),
            ("/gw config show", "Show the configured vouch channel."),
            ("/gw help", "Show this help message with all commands and descriptions."),
        ]

        for name, desc in commands_info:
            embed.add_field(name=name, value=desc, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(
        name="removevouch",
        description="Mod-only: remove a vouch for a user for a specific giveaway"
    )
    @app_commands.describe(
        user="The member whose vouch you want to remove",
        giveaway_id="ID of the giveaway from which to remove the vouch"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removevouch(self, interaction: discord.Interaction, user: discord.Member, giveaway_id: int):
        # Check giveaway exists
        g = await fetch_giveaway(giveaway_id)
        if not g or g.guild_id != interaction.guild_id:
            return await interaction.response.send_message(
                "⚠️ Invalid giveaway ID for this server.", ephemeral=True
            )

        # Check if the vouch exists
        vch_list = await get_user_vouches(interaction.guild_id, user.id)
        if all(giveaway_id != gid for gid, _ in vch_list):
            return await interaction.response.send_message(
                "ℹ️ No vouch recorded for this user and giveaway.",
                ephemeral=True
            )

        async with aiosqlite.connect(DB_PATH) as db:
            await enable_fks(db)
            # Remove the vouch
            await db.execute(
                "DELETE FROM vouches WHERE guild_id = ? AND user_id = ? AND giveaway_id = ?",
                (interaction.guild_id, user.id, giveaway_id)
            )
            # Mark as blocked
            await db.execute(
                "INSERT OR IGNORE INTO vouch_blocks (guild_id, user_id, giveaway_id) VALUES (?, ?, ?)",
                (interaction.guild_id, user.id, giveaway_id)
            )
            await db.commit()

        await interaction.response.send_message(
            f"✅ Removed and blocked vouch for {user.mention} in Giveaway #{giveaway_id} — {g.prize}",
            ephemeral=True
        )

    # ------------- Config Subcommands -------------
# Group: /gw config
class ConfigGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="config", description="Configure giveaway settings")

# Subgroup: /gw config vouch_channel
class VouchChannelGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="vouch_channel", description="Configure the vouch channel")

    @app_commands.command(name="show", description="Show the configured vouch channel")
    async def show(self, interaction: discord.Interaction):
        vcid = await get_vouch_channel_id(interaction.guild_id)
        if vcid:
            ch = interaction.guild.get_channel(vcid)
            if ch:
                return await interaction.response.send_message(f"Current vouch channel: {ch.mention}", ephemeral=True)
            return await interaction.response.send_message(f"Current vouch channel ID: {vcid} (channel not found)", ephemeral=True)
        # Fallback info
        fallback = f"(fallback is <#{VOUCH_CHANNEL_ID}>)" if VOUCH_CHANNEL_ID else ""
        await interaction.response.send_message(f"No vouch channel configured. {fallback}", ephemeral=True)

    @app_commands.command(name="set", description="Set the vouch channel for this server")
    @app_commands.describe(channel="Channel where users will post vouches")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # Optional: ensure bot can read/send in that channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.read_messages and perms.send_messages):
            return await interaction.response.send_message(
                "I don't have permission to read/send messages in that channel.",
                ephemeral=True
            )

        await set_vouch_channel_id(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"Vouch channel set to {channel.mention}.", ephemeral=True)

# Wire up: add subgroups to /gw

gw_group = GWGroup()

# Attach config group
config_group = ConfigGroup()
vouch_group = VouchChannelGroup()
config_group.add_command(vouch_group.show)
config_group.add_command(vouch_group.set)
gw_group.add_command(config_group)

bot.tree.add_command(gw_group)



# ------------- Utilities -------------

def parse_duration(s: str) -> Optional[int]:
    """Parse strings like '30s', '10m', '2h', '1d' into seconds."""
    try:
        s = s.strip().lower()
        unit = s[-1]
        value = float(s[:-1])
        if unit == 's':
            return int(value)
        if unit == 'm':
            return int(value * 60)
        if unit == 'h':
            return int(value * 3600)
        if unit == 'd':
            return int(value * 86400)
        # If no suffix, assume seconds
        return int(float(s))
    except Exception:
        return None

# ------------- Entry Point -------------
if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("TOKEN")
    if not token:
        raise SystemExit("Please set TOKEN in .env")
    bot.run(token)
