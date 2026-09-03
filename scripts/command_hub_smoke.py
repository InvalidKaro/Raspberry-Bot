from __future__ import annotations

import pathlib
import sys

import discord
from discord import app_commands

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.command_hubs import compact_command_tree, prepare_extension_unload  # noqa: E402


async def dummy(interaction: discord.Interaction) -> None:
    pass


client = discord.Client(intents=discord.Intents.none())
tree = app_commands.CommandTree(client)

battle = app_commands.Command(name="battleship", description="test", callback=dummy)
battle.module = "cogs.community.games_update"
tree.add_command(battle)

blackjack = app_commands.Group(name="blackjack", description="test")
duel = app_commands.Command(name="duel", description="test", callback=dummy)
blackjack.add_command(duel)
blackjack.module = "cogs.community.games_update"
tree.add_command(blackjack)

profile = app_commands.Command(name="profilecard", description="test", callback=dummy)
profile.module = "cogs.community.wizard_suite"
tree.add_command(profile)

stats = compact_command_tree(tree)
assert stats["roots"] == 2, stats

play = tree.get_command("play")
info = tree.get_command("info")
assert isinstance(play, app_commands.Group)
assert isinstance(info, app_commands.Group)
assert tree.get_command("battleship") is None
assert tree.get_command("blackjack") is None
assert tree.get_command("profilecard") is None
assert play.get_command("battleship") is battle
assert play.get_command("blackjack") is blackjack
assert battle.parent is play
assert blackjack.parent is play
assert duel.parent is blackjack
assert info.get_command("profilecard") is profile

restored = prepare_extension_unload(tree, "cogs.community.games_update")
assert restored == 2, restored
assert tree.get_command("battleship") is battle
assert tree.get_command("blackjack") is blackjack
assert battle.parent is None
assert blackjack.parent is None
assert duel.parent is blackjack

compact_command_tree(tree)
play = tree.get_command("play")
assert isinstance(play, app_commands.Group)
assert play.get_command("battleship") is battle
assert play.get_command("blackjack") is blackjack

print("Command hub runtime smoke test passed.")
