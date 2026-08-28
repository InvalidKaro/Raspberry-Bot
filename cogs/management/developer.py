from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.maintenance import collect_garbage


class Developer(commands.GroupCog, group_name="dev", group_description="Bot owner maintenance commands"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.bot.settings.owner_ids:
            return True
        await interaction.response.send_message(
            embed=EmbedFactory.error(title="Owner only", description="This command is restricted to configured bot owners."),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="cache-stats", description="Show application cache usage.")
    async def cache_stats(self, interaction: discord.Interaction) -> None:
        stats = await self.bot.cache.stats()
        embed = EmbedFactory.system(title="Cache Status")
        for item in stats:
            embed.add_field(
                name=item.name,
                value=f"**{item.size} / {item.max_size}**\nTTL: {item.ttl}s",
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cache-clear", description="Clear one application cache or all caches.")
    async def cache_clear(self, interaction: discord.Interaction, cache_name: str = "all") -> None:
        if cache_name == "all":
            result = await self.bot.cache.clear_all()
            count = sum(result.values())
            text = f"Cleared **{count}** entries across all caches."
        else:
            try:
                count = await self.bot.cache.clear(cache_name)
            except KeyError:
                await interaction.response.send_message(
                    embed=EmbedFactory.error(title="Unknown cache", description=f"Available: {', '.join(self.bot.cache.names)}"),
                    ephemeral=True,
                )
                return
            text = f"Cleared **{count}** entries from `{cache_name}`."
        await interaction.response.send_message(embed=EmbedFactory.success(title="Cache cleared", description=text), ephemeral=True)

    @app_commands.command(name="gc", description="Run Python garbage collection and report process memory.")
    async def gc_command(self, interaction: discord.Interaction) -> None:
        result = await asyncio.to_thread(collect_garbage)
        freed = max(result.before_mb - result.after_mb, 0)
        await interaction.response.send_message(
            embed=EmbedFactory.system(
                title="Memory Cleanup",
                description=(
                    f"Before: **{result.before_mb:.1f} MB**\n"
                    f"After: **{result.after_mb:.1f} MB**\n"
                    f"RSS difference: **{freed:.1f} MB**\n"
                    f"Collected objects: **{result.collected_objects}**"
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="database-optimize", description="Run SQLite PRAGMA optimize.")
    async def database_optimize(self, interaction: discord.Interaction) -> None:
        await self.bot.database.optimize()
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Database optimized", description="SQLite PRAGMA optimize completed."),
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="Synchronize application commands.")
    async def sync(self, interaction: discord.Interaction) -> None:
        if self.bot.settings.dev_guild_id:
            guild = discord.Object(id=self.bot.settings.dev_guild_id)
            synced = await self.bot.tree.sync(guild=guild)
        else:
            synced = await self.bot.tree.sync()
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Commands synchronized", description=f"Synced **{len(synced)}** commands."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Developer(bot))
