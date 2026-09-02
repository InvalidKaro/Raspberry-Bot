from __future__ import annotations

import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands


GAME_COLORS = {
    "tictactoe": 0x8B5CF6,
    "connect4": 0x3B82F6,
    "rps": 0xEC4899,
    "quickdraw": 0xF59E0B,
}


def _game_embed(title: str, description: str, *, game: str, footer: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=GAME_COLORS.get(game, 0x8B5CF6))
    if footer:
        embed.set_footer(text=footer)
    return embed


class GameStats:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def ensure_schema(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS game_stats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                game TEXT NOT NULL,
                played INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                last_played TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,user_id,game)
            )
            """
        )

    async def _bump(self, guild_id: int, user_id: int, game: str, result: str) -> None:
        if result == "win":
            await self.bot.database.execute(
                """
                INSERT INTO game_stats(guild_id,user_id,game,played,wins,losses,draws,streak,best_streak)
                VALUES(?,?,?,1,1,0,0,1,1)
                ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
                    played=played+1,
                    wins=wins+1,
                    streak=streak+1,
                    best_streak=MAX(best_streak,streak+1),
                    last_played=CURRENT_TIMESTAMP
                """,
                (guild_id, user_id, game),
            )
        elif result == "loss":
            await self.bot.database.execute(
                """
                INSERT INTO game_stats(guild_id,user_id,game,played,wins,losses,draws,streak,best_streak)
                VALUES(?,?,?,1,0,1,0,0,0)
                ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
                    played=played+1,
                    losses=losses+1,
                    streak=0,
                    last_played=CURRENT_TIMESTAMP
                """,
                (guild_id, user_id, game),
            )
        else:
            await self.bot.database.execute(
                """
                INSERT INTO game_stats(guild_id,user_id,game,played,wins,losses,draws,streak,best_streak)
                VALUES(?,?,?,1,0,0,1,0,0)
                ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
                    played=played+1,
                    draws=draws+1,
                    last_played=CURRENT_TIMESTAMP
                """,
                (guild_id, user_id, game),
            )

    async def record(self, guild_id: int, game: str, player_a: int, player_b: int, winner: int | None) -> None:
        if winner is None:
            await self._bump(guild_id, player_a, game, "draw")
            await self._bump(guild_id, player_b, game, "draw")
            return
        loser = player_b if winner == player_a else player_a
        await self._bump(guild_id, winner, game, "win")
        await self._bump(guild_id, loser, game, "loss")


class TwoPlayerView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, p1: discord.Member, p2: discord.Member, game: str, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = guild_id
        self.p1 = p1
        self.p2 = p2
        self.game = game
        self.message: discord.Message | None = None
        self.finished = False
        self.stats = GameStats(bot)

    def player_index(self, user_id: int) -> int | None:
        if user_id == self.p1.id:
            return 0
        if user_id == self.p2.id:
            return 1
        return None

    async def reject_spectator(self, interaction: discord.Interaction) -> bool:
        if self.player_index(interaction.user.id) is None:
            await interaction.response.send_message("Dieses Match gehört nur den beiden markierten Spielern.", ephemeral=True)
            return True
        return False

    async def end(self, *, winner: int | None) -> None:
        if self.finished:
            return
        self.finished = True
        await self.stats.record(self.guild_id, self.game, self.p1.id, self.p2.id, winner)

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    embed=_game_embed(
                        "Match abgelaufen",
                        f"{self.p1.mention} vs. {self.p2.mention}\n\nKeine Eingabe mehr möglich.",
                        game=self.game,
                        footer="Starte einfach ein neues Match mit /games.",
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


class TicButton(discord.ui.Button):
    def __init__(self, index: int) -> None:
        super().__init__(label="\u200b", style=discord.ButtonStyle.secondary, row=index // 3)
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None or await view.reject_spectator(interaction):
            return
        player = view.player_index(interaction.user.id)
        if player != view.turn:
            await interaction.response.send_message("Noch nicht dein Zug.", ephemeral=True)
            return
        if view.board[self.index] != 0:
            await interaction.response.send_message("Dieses Feld ist bereits belegt.", ephemeral=True)
            return
        mark = 1 if player == 0 else 2
        view.board[self.index] = mark
        self.label = "✕" if mark == 1 else "◯"
        self.style = discord.ButtonStyle.primary if mark == 1 else discord.ButtonStyle.success
        winner_mark = view.winner_mark()
        if winner_mark:
            for item in view.children:
                item.disabled = True
            winner = view.p1 if winner_mark == 1 else view.p2
            await view.end(winner=winner.id)
            embed = view.render(f"🏆 {winner.mention} gewinnt!")
        elif all(view.board):
            for item in view.children:
                item.disabled = True
            await view.end(winner=None)
            embed = view.render("🤝 Unentschieden.")
        else:
            view.turn = 1 - view.turn
            embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class TicTacToeView(TwoPlayerView):
    def __init__(self, bot: commands.Bot, guild_id: int, p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(bot, guild_id, p1, p2, "tictactoe")
        self.turn = random.randint(0, 1)
        self.board = [0] * 9
        for i in range(9):
            self.add_item(TicButton(i))

    def winner_mark(self) -> int:
        for a, b, c in (
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        ):
            if self.board[a] and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return 0

    def render(self, status: str | None = None) -> discord.Embed:
        current = self.p1 if self.turn == 0 else self.p2
        body = f"**✕** {self.p1.mention}  ·  **◯** {self.p2.mention}\n\n" + (status or f"Am Zug: {current.mention}")
        return _game_embed("Tic-Tac-Toe", body, game=self.game, footer="3 in einer Reihe gewinnt.")


class ConnectColumn(discord.ui.Button):
    def __init__(self, column: int) -> None:
        super().__init__(label=str(column + 1), style=discord.ButtonStyle.secondary, row=0 if column < 5 else 1)
        self.column = column

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None or await view.reject_spectator(interaction):
            return
        player = view.player_index(interaction.user.id)
        if player != view.turn:
            await interaction.response.send_message("Noch nicht dein Zug.", ephemeral=True)
            return
        row = next((r for r in range(5, -1, -1) if view.board[r][self.column] == 0), None)
        if row is None:
            await interaction.response.send_message("Diese Spalte ist voll.", ephemeral=True)
            return
        mark = 1 if player == 0 else 2
        view.board[row][self.column] = mark
        if view.has_four(row, self.column, mark):
            for item in view.children:
                item.disabled = True
            winner = view.p1 if player == 0 else view.p2
            await view.end(winner=winner.id)
            embed = view.render(f"🏆 {winner.mention} verbindet vier!")
        elif all(view.board[0][c] for c in range(7)):
            for item in view.children:
                item.disabled = True
            await view.end(winner=None)
            embed = view.render("🤝 Das Board ist voll – Unentschieden.")
        else:
            view.turn = 1 - view.turn
            embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class Connect4View(TwoPlayerView):
    def __init__(self, bot: commands.Bot, guild_id: int, p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(bot, guild_id, p1, p2, "connect4")
        self.turn = random.randint(0, 1)
        self.board = [[0 for _ in range(7)] for _ in range(6)]
        for col in range(7):
            self.add_item(ConnectColumn(col))

    def has_four(self, row: int, col: int, mark: int) -> bool:
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            for sign in (-1, 1):
                rr, cc = row + dr * sign, col + dc * sign
                while 0 <= rr < 6 and 0 <= cc < 7 and self.board[rr][cc] == mark:
                    count += 1
                    rr += dr * sign
                    cc += dc * sign
            if count >= 4:
                return True
        return False

    def board_text(self) -> str:
        token = {0: "⚫", 1: "🔵", 2: "🟡"}
        rows = ["".join(token[cell] for cell in row) for row in self.board]
        rows.append("1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣")
        return "\n".join(rows)

    def render(self, status: str | None = None) -> discord.Embed:
        current = self.p1 if self.turn == 0 else self.p2
        body = f"🔵 {self.p1.mention}  ·  🟡 {self.p2.mention}\n\n{self.board_text()}\n\n" + (status or f"Am Zug: {current.mention}")
        return _game_embed("Connect Four", body, game=self.game, footer="Wähle oben eine Spalte · 4 in einer Reihe gewinnt.")


class RPSButton(discord.ui.Button):
    def __init__(self, label: str, value: str, emoji: str) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
        self.choice = value

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None or await view.reject_spectator(interaction):
            return
        uid = interaction.user.id
        if uid in view.choices:
            await interaction.response.send_message("Deine Wahl ist bereits gesperrt.", ephemeral=True)
            return
        view.choices[uid] = self.choice
        await interaction.response.send_message("🔒 Wahl gespeichert. Sie bleibt geheim, bis beide gewählt haben.", ephemeral=True)
        if len(view.choices) == 2:
            await view.resolve()


class RPSView(TwoPlayerView):
    ICONS = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}

    def __init__(self, bot: commands.Bot, guild_id: int, p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(bot, guild_id, p1, p2, "rps", timeout=300)
        self.choices: dict[int, str] = {}
        self.add_item(RPSButton("Stein", "rock", "🪨"))
        self.add_item(RPSButton("Papier", "paper", "📄"))
        self.add_item(RPSButton("Schere", "scissors", "✂️"))

    def render(self, status: str = "Beide wählen geheim. Die Auflösung kommt automatisch.") -> discord.Embed:
        locked = sum(1 for user in (self.p1, self.p2) if user.id in self.choices)
        return _game_embed(
            "Secret RPS",
            f"{self.p1.mention} vs. {self.p2.mention}\n\n🔐 Gesperrte Wahlen: **{locked}/2**\n\n{status}",
            game=self.game,
            footer="Stein schlägt Schere · Schere schlägt Papier · Papier schlägt Stein",
        )

    async def resolve(self) -> None:
        a = self.choices[self.p1.id]
        b = self.choices[self.p2.id]
        for item in self.children:
            item.disabled = True
        if a == b:
            winner = None
            result = "🤝 Unentschieden."
        elif (a, b) in {("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")}:
            winner = self.p1.id
            result = f"🏆 {self.p1.mention} gewinnt!"
        else:
            winner = self.p2.id
            result = f"🏆 {self.p2.mention} gewinnt!"
        await self.end(winner=winner)
        detail = f"{self.p1.mention}: {self.ICONS[a]} **{a.title()}**\n{self.p2.mention}: {self.ICONS[b]} **{b.title()}**\n\n{result}"
        if self.message:
            await self.message.edit(embed=self.render(detail), view=self)


class ReadyButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="READY", emoji="🎯", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None or await view.reject_spectator(interaction):
            return
        uid = interaction.user.id
        if uid in view.ready:
            await interaction.response.send_message("Du bist bereits bereit.", ephemeral=True)
            return
        view.ready.add(uid)
        await interaction.response.send_message("Bereit. Jetzt nicht blinzeln.", ephemeral=True)
        if len(view.ready) == 2 and not view.countdown_started:
            view.countdown_started = True
            asyncio.create_task(view.arm())


class FireButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="FIRE!", emoji="⚡", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None or await view.reject_spectator(interaction):
            return
        if view.finished or view.armed_at is None:
            return
        reaction_ms = int((time.perf_counter() - view.armed_at) * 1000)
        winner = interaction.user
        await view.end(winner=winner.id)
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=_game_embed(
                "⚡ Quick Draw",
                f"🏆 {winner.mention} war schneller!\n\nReaktionszeit: **{reaction_ms} ms**\n{view.p1.mention} vs. {view.p2.mention}",
                game=view.game,
                footer="Nochmal? /games quickdraw",
            ),
            view=view,
        )


class QuickDrawView(TwoPlayerView):
    def __init__(self, bot: commands.Bot, guild_id: int, p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(bot, guild_id, p1, p2, "quickdraw", timeout=180)
        self.ready: set[int] = set()
        self.countdown_started = False
        self.armed_at: float | None = None
        self.add_item(ReadyButton())

    def render(self, text: str | None = None) -> discord.Embed:
        return _game_embed(
            "🎯 Quick Draw",
            text or f"{self.p1.mention} vs. {self.p2.mention}\n\nBeide drücken **READY**. Danach erscheint nach einer zufälligen Pause der FIRE-Button.\nWer zuerst feuert, gewinnt.",
            game=self.game,
            footer="Reaktionsduell · zufällige Verzögerung",
        )

    async def arm(self) -> None:
        if not self.message:
            return
        self.clear_items()
        await self.message.edit(embed=self.render("Beide sind bereit.\n\n**Warte auf das Signal …**"), view=self)
        await asyncio.sleep(random.uniform(2.0, 5.0))
        if self.finished:
            return
        self.clear_items()
        self.add_item(FireButton())
        self.armed_at = time.perf_counter()
        await self.message.edit(embed=self.render("## ⚡ DRAW!\n**JETZT!**"), view=self)


class GamesPlus(commands.GroupCog, group_name="games", group_description="Polierte 2-Spieler-Games direkt in Discord"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.stats = GameStats(bot)

    async def cog_load(self) -> None:
        await self.stats.ensure_schema()

    async def _start(self, interaction: discord.Interaction, opponent: discord.Member, view: TwoPlayerView, embed: discord.Embed) -> None:
        if opponent.bot:
            await interaction.response.send_message("Bots sind als Gegner nicht zugelassen.", ephemeral=True)
            return
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("Du brauchst einen zweiten Spieler.", ephemeral=True)
            return
        await interaction.response.send_message(
            content=f"{interaction.user.mention} vs. {opponent.mention}",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        view.message = await interaction.original_response()

    @app_commands.command(name="tictactoe", description="Starte ein interaktives Tic-Tac-Toe-Duell.")
    async def tictactoe(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        view = TicTacToeView(self.bot, interaction.guild_id, interaction.user, gegner)
        await self._start(interaction, gegner, view, view.render())

    @app_commands.command(name="connect4", description="Starte Connect Four mit Buttons und Live-Board.")
    async def connect4(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        view = Connect4View(self.bot, interaction.guild_id, interaction.user, gegner)
        await self._start(interaction, gegner, view, view.render())

    @app_commands.command(name="rps", description="Geheimes Stein-Papier-Schere-Duell.")
    async def rps(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        view = RPSView(self.bot, interaction.guild_id, interaction.user, gegner)
        await self._start(interaction, gegner, view, view.render())

    @app_commands.command(name="quickdraw", description="Reaktionsduell: READY, warten, FIRE!")
    async def quickdraw(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        view = QuickDrawView(self.bot, interaction.guild_id, interaction.user, gegner)
        await self._start(interaction, gegner, view, view.render())

    @app_commands.command(name="stats", description="Spielstatistiken eines Mitglieds anzeigen.")
    async def stats_command(self, interaction: discord.Interaction, mitglied: discord.Member | None = None) -> None:
        if interaction.guild_id is None:
            return
        target = mitglied or interaction.user
        rows = await self.bot.database.fetchall(
            """SELECT game,played,wins,losses,draws,streak,best_streak FROM game_stats WHERE guild_id=? AND user_id=? ORDER BY wins DESC,played DESC,game""",
            (interaction.guild_id, target.id),
        )
        if not rows:
            description = "Noch keine abgeschlossenen Matches."
        else:
            labels = {"tictactoe": "Tic-Tac-Toe", "connect4": "Connect Four", "rps": "Secret RPS", "quickdraw": "Quick Draw"}
            description = "\n".join(
                f"**{labels.get(str(r['game']), str(r['game']))}** · `{r['wins']}W {r['losses']}L {r['draws']}D` · {r['played']} Spiele · 🔥 {r['best_streak']}"
                for r in rows
            )
        await interaction.response.send_message(
            embed=_game_embed(f"Game Profile · {getattr(target, 'display_name', target.name)}", description, game="tictactoe", footer="W = Wins · L = Losses · D = Draws")
        )

    @app_commands.command(name="leaderboard", description="Serverweite Game-Rangliste anzeigen.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall(
            """SELECT user_id,SUM(played) played,SUM(wins) wins,SUM(losses) losses,SUM(draws) draws,MAX(best_streak) best_streak FROM game_stats WHERE guild_id=? GROUP BY user_id ORDER BY wins DESC,best_streak DESC,played ASC LIMIT 15""",
            (interaction.guild_id,),
        )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows):
            prefix = medals[index] if index < 3 else f"`#{index + 1}`"
            lines.append(f"{prefix} <@{row['user_id']}> · **{row['wins']} Wins** · {row['played']} Spiele · 🔥 {row['best_streak']}")
        await interaction.response.send_message(
            embed=_game_embed("Arcade Leaderboard", "\n".join(lines) or "Noch keine abgeschlossenen Matches.", game="connect4", footer="Tic-Tac-Toe · Connect Four · Secret RPS · Quick Draw"),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamesPlus(bot))
