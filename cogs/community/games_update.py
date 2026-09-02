from __future__ import annotations

import asyncio
import json
import random
import string
import time
from collections import Counter
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0x8B5CF6
BLUE = 0x3B82F6
GREEN = 0x22C55E
RED = 0xEF4444
GOLD = 0xF59E0B


def card(title: str, text: str, color: int = ACCENT, *, footer: str | None = None) -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=color)
    if footer:
        e.set_footer(text=footer)
    return e


def valid_opponent(me: discord.Member, other: discord.Member) -> str | None:
    if other.bot:
        return "Bots können nicht als Gegner antreten."
    if other.id == me.id:
        return "Du kannst nicht gegen dich selbst spielen."
    return None


def coord_to_index(value: str) -> tuple[int, int] | None:
    value = value.strip().upper().replace(" ", "")
    if len(value) < 2 or value[0] not in string.ascii_uppercase[:10]:
        return None
    try:
        number = int(value[1:])
    except ValueError:
        return None
    if not 1 <= number <= 10:
        return None
    return ord(value[0]) - 65, number - 1


def generate_fleet() -> set[tuple[int, int]]:
    sizes = [5, 4, 3, 3, 2]
    cells: set[tuple[int, int]] = set()
    for size in sizes:
        for _ in range(500):
            horizontal = random.choice([True, False])
            r = random.randrange(10 if horizontal else 11 - size)
            c = random.randrange(11 - size if horizontal else 10)
            candidate = {(r, c + i) if horizontal else (r + i, c) for i in range(size)}
            if candidate.isdisjoint(cells):
                cells.update(candidate)
                break
    return cells


def render_battle_board(fleet: set[tuple[int, int]], incoming: set[tuple[int, int]]) -> str:
    out = ["   1 2 3 4 5 6 7 8 9 10"]
    for r in range(10):
        row = []
        for c in range(10):
            pos = (r, c)
            if pos in incoming and pos in fleet:
                row.append("💥")
            elif pos in incoming:
                row.append("·")
            elif pos in fleet:
                row.append("■")
            else:
                row.append("~")
        out.append(f"{chr(65+r)}  " + " ".join(row))
    return "```\n" + "\n".join(out) + "\n```"


class BattleshipFireModal(discord.ui.Modal, title="Battleship · Feuer!"):
    coordinate = discord.ui.TextInput(label="Koordinate", placeholder="z. B. B7", max_length=3)

    def __init__(self, view: "BattleshipView") -> None:
        super().__init__()
        self.game = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.game
        if view.finished or interaction.user.id != view.players[view.turn].id:
            await interaction.response.send_message("Du bist gerade nicht am Zug.", ephemeral=True)
            return
        pos = coord_to_index(str(self.coordinate))
        if pos is None:
            await interaction.response.send_message("Ungültige Koordinate. Nutze A1 bis J10.", ephemeral=True)
            return
        shooter = interaction.user.id
        target = view.players[1 - view.turn].id
        if pos in view.shots[shooter]:
            await interaction.response.send_message("Diese Position hast du bereits beschossen.", ephemeral=True)
            return
        view.shots[shooter].add(pos)
        hit = pos in view.fleets[target]
        if hit:
            view.hits[shooter].add(pos)
        sunk = len(view.hits[shooter]) >= len(view.fleets[target])
        if sunk:
            view.finished = True
            await view.cog.finish_match(view.guild_id, "battleship", view.players[0], view.players[1], interaction.user)
            text = f"💥 **{str(self.coordinate).upper()} – TREFFER!**\n\n🏆 {interaction.user.mention} hat die gesamte Flotte versenkt."
            for item in view.children:
                item.disabled = True
        else:
            icon = "💥 TREFFER" if hit else "🌊 Wasser"
            text = f"**{str(self.coordinate).upper()}** → {icon}\n\n"
            view.turn = 1 - view.turn
            text += f"Am Zug: {view.players[view.turn].mention}"
        await interaction.response.edit_message(embed=card("⚓ Battleship", text, BLUE, footer="Private Flotte über ›Mein Board‹"), view=view)


class BattleshipView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=1200)
        self.cog = cog
        self.guild_id = guild_id
        self.players = [a, b]
        self.turn = random.randrange(2)
        self.fleets = {a.id: generate_fleet(), b.id: generate_fleet()}
        self.shots = {a.id: set(), b.id: set()}
        self.hits = {a.id: set(), b.id: set()}
        self.finished = False

    @discord.ui.button(label="Fire", emoji="🎯", style=discord.ButtonStyle.danger)
    async def fire(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.players[self.turn].id:
            await interaction.response.send_message("Noch nicht dein Zug.", ephemeral=True)
            return
        await interaction.response.send_modal(BattleshipFireModal(self))

    @discord.ui.button(label="Mein Board", emoji="🗺️", style=discord.ButtonStyle.secondary)
    async def board(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id not in self.fleets:
            await interaction.response.send_message("Nur die beiden Spieler können ihre Flotte sehen.", ephemeral=True)
            return
        opponent = self.players[0] if self.players[1].id == interaction.user.id else self.players[1]
        await interaction.response.send_message(render_battle_board(self.fleets[interaction.user.id], self.shots[opponent.id]), ephemeral=True)

    @discord.ui.button(label="Aufgeben", style=discord.ButtonStyle.secondary)
    async def forfeit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id not in {p.id for p in self.players}:
            await interaction.response.send_message("Du spielst hier nicht mit.", ephemeral=True)
            return
        winner = self.players[0] if self.players[1].id == interaction.user.id else self.players[1]
        self.finished = True
        for item in self.children:
            item.disabled = True
        await self.cog.finish_match(self.guild_id, "battleship", self.players[0], self.players[1], winner)
        await interaction.response.edit_message(embed=card("⚓ Battleship", f"{interaction.user.mention} gibt auf.\n\n🏆 {winner.mention} gewinnt."), view=self)


class CipherSolveModal(discord.ui.Modal, title="Cipher Duel · Lösung"):
    answer = discord.ui.TextInput(label="Entschlüsselter Text", max_length=100)

    def __init__(self, view: "CipherView") -> None:
        super().__init__()
        self.game = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id not in self.game.answers:
            await interaction.response.send_message("Du bist nicht Teil dieses Duells.", ephemeral=True)
            return
        if interaction.user.id in self.game.solved:
            await interaction.response.send_message("Du hast bereits gelöst.", ephemeral=True)
            return
        expected = self.game.answers[interaction.user.id]
        if str(self.answer).strip().lower() != expected.lower():
            await interaction.response.send_message("❌ Noch nicht korrekt.", ephemeral=True)
            return
        self.game.solved.add(interaction.user.id)
        winner = next(p for p in self.game.players if p.id == interaction.user.id)
        self.game.finished = True
        for item in self.game.children:
            item.disabled = True
        await self.game.cog.finish_match(self.game.guild_id, "cipherduel", self.game.players[0], self.game.players[1], winner)
        await interaction.response.edit_message(embed=card("🔐 Cipher Duel", f"🏆 {winner.mention} hat den Code zuerst geknackt!", GREEN), view=self.game)


class CipherView(discord.ui.View):
    WORDS = ["RASPBERRY", "DISCORD", "SIGNAL", "MEDIZIN", "NETWORK", "PUZZLE", "PHANTOM", "VECTOR"]

    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=600)
        self.cog, self.guild_id, self.players = cog, guild_id, [a, b]
        self.finished = False
        self.solved: set[int] = set()
        self.puzzles: dict[int, tuple[str, int]] = {}
        self.answers: dict[int, str] = {}
        for p in self.players:
            raw = random.choice(self.WORDS)
            shift = random.randint(1, 12)
            enc = "".join(chr((ord(ch) - 65 + shift) % 26 + 65) for ch in raw)
            self.puzzles[p.id] = (enc, shift)
            self.answers[p.id] = raw

    @discord.ui.button(label="Mein Cipher", emoji="🔐", style=discord.ButtonStyle.primary)
    async def puzzle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        data = self.puzzles.get(interaction.user.id)
        if not data:
            await interaction.response.send_message("Nur die beiden Spieler erhalten einen Cipher.", ephemeral=True)
            return
        enc, shift = data
        await interaction.response.send_message(embed=card("Dein Cipher", f"`{enc}`\n\nCaesar-Shift: **{shift}**\nEntschlüssle zurück in Klartext."), ephemeral=True)

    @discord.ui.button(label="Lösung abgeben", emoji="⚡", style=discord.ButtonStyle.success)
    async def solve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CipherSolveModal(self))


class TerritoryCell(discord.ui.Button):
    def __init__(self, idx: int) -> None:
        super().__init__(label="·", style=discord.ButtonStyle.secondary, row=idx // 5)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TerritoryView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.players[view.turn].id:
            await interaction.response.send_message("Noch nicht dein Zug.", ephemeral=True)
            return
        if view.board[self.idx] != -1:
            await interaction.response.send_message("Dieses Feld gehört bereits jemandem.", ephemeral=True)
            return
        player = view.turn
        mine = [i for i, owner in enumerate(view.board) if owner == player]
        if mine:
            r, c = divmod(self.idx, 5)
            if not any(abs(r - divmod(i, 5)[0]) + abs(c - divmod(i, 5)[1]) == 1 for i in mine):
                await interaction.response.send_message("Du musst an dein bestehendes Gebiet angrenzen.", ephemeral=True)
                return
        view.board[self.idx] = player
        self.label = "◆" if player == 0 else "●"
        self.style = discord.ButtonStyle.primary if player == 0 else discord.ButtonStyle.success
        if all(x != -1 for x in view.board):
            a = view.board.count(0)
            b = view.board.count(1)
            winner = view.players[0] if a > b else view.players[1] if b > a else None
            for item in view.children:
                item.disabled = True
            await view.cog.finish_match(view.guild_id, "territory", view.players[0], view.players[1], winner)
            result = f"🏆 {winner.mention} gewinnt **{max(a,b)}:{min(a,b)}**." if winner else f"🤝 Unentschieden **{a}:{b}**."
        else:
            view.turn = 1 - view.turn
            result = f"Am Zug: {view.players[view.turn].mention}"
        await interaction.response.edit_message(embed=view.embed(result), view=view)


class TerritoryView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=900)
        self.cog, self.guild_id, self.players = cog, guild_id, [a, b]
        self.board = [-1] * 25
        self.turn = random.randrange(2)
        for i in range(25):
            self.add_item(TerritoryCell(i))

    def embed(self, status: str | None = None) -> discord.Embed:
        return card("🗺️ Territory", f"◆ {self.players[0].mention}  ·  ● {self.players[1].mention}\n\n{status or f'Am Zug: {self.players[self.turn].mention}'}", BLUE, footer="Erobere angrenzende Felder · Mehrheit gewinnt")


class BlackjackView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=600)
        self.cog, self.guild_id, self.players = cog, guild_id, [a, b]
        self.deck = [v for v in range(2, 12) for _ in range(4)]
        random.shuffle(self.deck)
        self.hands = {a.id: [self.deck.pop(), self.deck.pop()], b.id: [self.deck.pop(), self.deck.pop()]}
        self.stood: set[int] = set()
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.finished = False

    @staticmethod
    def score(hand: list[int]) -> int:
        total = sum(hand)
        aces = hand.count(11)
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    def render(self, final: str | None = None) -> discord.Embed:
        lines = []
        for p in self.players:
            hand = self.hands[p.id]
            state = "STAND" if p.id in self.stood else "PLAY"
            lines.append(f"**{p.display_name}** · {self.score(hand)} · `{state}`\n" + " ".join(f"`{x}`" for x in hand))
        dealer = self.score(self.dealer) if self.finished else self.dealer[0]
        lines.append(f"\n**Dealer:** {dealer}{'' if self.finished else ' + ?'}")
        if final:
            lines.append("\n" + final)
        return card("🃏 Blackjack Duel", "\n\n".join(lines), GOLD)

    async def maybe_finish(self, interaction: discord.Interaction) -> None:
        for p in self.players:
            if self.score(self.hands[p.id]) > 21:
                self.stood.add(p.id)
        if len(self.stood) < 2:
            await interaction.response.edit_message(embed=self.render(), view=self)
            return
        while self.score(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        ds = self.score(self.dealer)
        results: dict[int, int] = {}
        for p in self.players:
            s = self.score(self.hands[p.id])
            results[p.id] = -1 if s > 21 else (s if ds > 21 or s > ds else 0 if s == ds else -1)
        if results[self.players[0].id] > results[self.players[1].id]:
            winner = self.players[0]
        elif results[self.players[1].id] > results[self.players[0].id]:
            winner = self.players[1]
        else:
            winner = None
        self.finished = True
        for item in self.children:
            item.disabled = True
        await self.cog.finish_match(self.guild_id, "blackjack", self.players[0], self.players[1], winner)
        final = f"🏆 {winner.mention} gewinnt das Duell." if winner else "🤝 Das Duell endet unentschieden."
        await interaction.response.edit_message(embed=self.render(final), view=self)

    @discord.ui.button(label="Hit", emoji="➕", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id not in self.hands or interaction.user.id in self.stood:
            await interaction.response.send_message("Du kannst gerade nicht ziehen.", ephemeral=True)
            return
        self.hands[interaction.user.id].append(self.deck.pop())
        await self.maybe_finish(interaction)

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id not in self.hands:
            await interaction.response.send_message("Du bist nicht Teil dieses Duells.", ephemeral=True)
            return
        self.stood.add(interaction.user.id)
        await self.maybe_finish(interaction)


class WordModal(discord.ui.Modal, title="Word Chain"):
    word = discord.ui.TextInput(label="Dein Wort", max_length=40)

    def __init__(self, view: "WordChainView") -> None:
        super().__init__()
        self.game = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        v = self.game
        if interaction.user.id != v.players[v.turn].id:
            await interaction.response.send_message("Noch nicht dein Zug.", ephemeral=True)
            return
        word = str(self.word).strip().lower()
        if not word.isalpha() or len(word) < 2:
            await interaction.response.send_message("Nutze ein Wort nur aus Buchstaben.", ephemeral=True)
            return
        if word in v.used:
            await interaction.response.send_message("Dieses Wort wurde bereits benutzt.", ephemeral=True)
            return
        if v.last and word[0] != v.last[-1]:
            await interaction.response.send_message(f"Das Wort muss mit **{v.last[-1].upper()}** beginnen.", ephemeral=True)
            return
        v.used.add(word)
        v.last = word
        v.turn = 1 - v.turn
        await interaction.response.edit_message(embed=v.render(), view=v)


class WordChainView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=600)
        self.cog, self.guild_id, self.players = cog, guild_id, [a, b]
        self.turn = random.randrange(2)
        self.last = random.choice(["signal", "radio", "vector", "medizin", "python"])
        self.used = {self.last}

    def render(self) -> discord.Embed:
        return card("🔤 Word Chain", f"Letztes Wort: **{self.last}**\nNächstes beginnt mit **{self.last[-1].upper()}**\n\nAm Zug: {self.players[self.turn].mention}\nWörter: **{len(self.used)}**", ACCENT)

    @discord.ui.button(label="Wort spielen", emoji="⌨️", style=discord.ButtonStyle.primary)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(WordModal(self))

    @discord.ui.button(label="Aufgeben", style=discord.ButtonStyle.danger)
    async def giveup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id not in {p.id for p in self.players}:
            await interaction.response.send_message("Du spielst nicht mit.", ephemeral=True)
            return
        winner = self.players[0] if self.players[1].id == interaction.user.id else self.players[1]
        for item in self.children:
            item.disabled = True
        await self.cog.finish_match(self.guild_id, "wordchain", self.players[0], self.players[1], winner)
        await interaction.response.edit_message(embed=card("🔤 Word Chain", f"🏆 {winner.mention} gewinnt nach **{len(self.used)}** Wörtern."), view=self)


class ReactionButton(discord.ui.Button):
    def __init__(self, idx: int, token: str) -> None:
        super().__init__(label=token, style=discord.ButtonStyle.secondary)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction) -> None:
        v: ReactionBattleView = self.view  # type: ignore[assignment]
        if interaction.user.id not in {p.id for p in v.players}:
            await interaction.response.send_message("Nur die beiden Spieler können reagieren.", ephemeral=True)
            return
        if v.locked:
            await interaction.response.send_message("Diese Runde wurde schon entschieden.", ephemeral=True)
            return
        if self.idx != v.correct:
            v.score[interaction.user.id] -= 1
            await interaction.response.send_message("❌ Fake-Button. -1 Punkt.", ephemeral=True)
            return
        v.locked = True
        v.score[interaction.user.id] += 1
        v.round += 1
        if v.round >= 5:
            for item in v.children:
                item.disabled = True
            a, b = v.players
            winner = a if v.score[a.id] > v.score[b.id] else b if v.score[b.id] > v.score[a.id] else None
            await v.cog.finish_match(v.guild_id, "reactionbattle", a, b, winner)
            result = f"🏆 {winner.mention} gewinnt." if winner else "🤝 Gleichstand."
            await interaction.response.edit_message(embed=v.render(result), view=v)
            return
        await interaction.response.defer()
        await asyncio.sleep(0.8)
        v.next_round()
        if interaction.message:
            await interaction.message.edit(embed=v.render(), view=v)


class ReactionBattleView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=300)
        self.cog, self.guild_id, self.players = cog, guild_id, [a, b]
        self.score = {a.id: 0, b.id: 0}
        self.round = 0
        self.correct = 0
        self.locked = False
        self.next_round()

    def next_round(self) -> None:
        self.clear_items()
        self.correct = random.randrange(4)
        target = random.choice(["RED", "BLUE", "7", "★", "GO"])
        tokens = [random.choice(["RED", "BLUE", "9", "☆", "STOP", "X"]) for _ in range(4)]
        tokens[self.correct] = target
        self.target = target
        self.locked = False
        for i, token in enumerate(tokens):
            self.add_item(ReactionButton(i, token))

    def render(self, extra: str | None = None) -> discord.Embed:
        a, b = self.players
        return card("⚡ Reaction Battle", f"Runde **{self.round + 1}/5**\nKlicke zuerst auf **{self.target}**.\n\n{a.mention}: **{self.score[a.id]}** · {b.mention}: **{self.score[b.id]}**" + (f"\n\n{extra}" if extra else ""), GOLD)


class EscapeView(discord.ui.View):
    ROOMS = [
        ("Archiv", ["Die rote Akte", "Die Uhr", "Das Fenster"], 0, "🔑 Messingschlüssel"),
        ("Labor", ["Reagenzglas 7", "Sicherung B", "Notdusche"], 1, "🧩 Zahlencode 431"),
        ("Tresorraum", ["431", "314", "134"], 0, "🚪 Ausgangscode"),
    ]

    def __init__(self, owner: discord.Member) -> None:
        super().__init__(timeout=900)
        self.owner = owner
        self.players = {owner.id}
        self.stage = -1
        self.inventory: list[str] = []

    def lobby(self) -> discord.Embed:
        return card("🚪 Escape Protocol", f"Kooperativer Escape Room\n\nSpieler: **{len(self.players)}**\n\n[Join] und danach startet der Host.", ACCENT)

    def room(self, status: str = "") -> discord.Embed:
        name, options, _, _ = self.ROOMS[self.stage]
        return card(f"🚪 Raum {self.stage+1}/3 · {name}", f"Hinweis: *Nur eine Spur bringt euch weiter.*\n\n" + "\n".join(f"**{i+1}.** {x}" for i, x in enumerate(options)) + f"\n\nInventar: {', '.join(self.inventory) or 'leer'}" + (f"\n\n{status}" if status else ""), ACCENT)

    def set_room_buttons(self) -> None:
        self.clear_items()
        for idx, label in enumerate(self.ROOMS[self.stage][1]):
            b = discord.ui.Button(label=f"{idx+1} · {label[:30]}", style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, choice: int = idx) -> None:
                if interaction.user.id not in self.players:
                    await interaction.response.send_message("Erst dem Escape-Team beitreten.", ephemeral=True)
                    return
                correct = self.ROOMS[self.stage][2]
                if choice != correct:
                    await interaction.response.send_message("🔒 Die Spur führt in eine Sackgasse.", ephemeral=True)
                    return
                self.inventory.append(self.ROOMS[self.stage][3])
                self.stage += 1
                if self.stage >= len(self.ROOMS):
                    self.clear_items()
                    await interaction.response.edit_message(embed=card("🚪 ESCAPED", f"Das Team entkommt mit **{len(self.players)} Spielern**.\n\nInventar: {', '.join(self.inventory)}", GREEN), view=self)
                else:
                    self.set_room_buttons()
                    await interaction.response.edit_message(embed=self.room("✅ Schloss geöffnet."), view=self)
            b.callback = cb
            self.add_item(b)

    @discord.ui.button(label="Join", emoji="➕", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.players.add(interaction.user.id)
        await interaction.response.edit_message(embed=self.lobby(), view=self)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Nur der Host startet das Spiel.", ephemeral=True)
            return
        self.stage = 0
        self.set_room_buttons()
        await interaction.response.edit_message(embed=self.room(), view=self)


class BossFightView(discord.ui.View):
    def __init__(self, owner: discord.Member) -> None:
        super().__init__(timeout=1200)
        self.owner = owner
        self.players: dict[int, int] = {owner.id: 100}
        self.boss_hp = 500
        self.started = False
        self.acted: set[int] = set()
        self.round = 1

    def embed(self, text: str = "") -> discord.Embed:
        roster = " · ".join(f"<@{u}> **{hp}HP**" for u, hp in self.players.items())
        return card("👾 BOSS // NULL TITAN", f"Boss: **{max(0,self.boss_hp)}/500 HP**\nRunde: **{self.round}**\n\n{roster}" + (f"\n\n{text}" if text else ""), RED)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.started:
            await interaction.response.send_message("Der Kampf läuft bereits.", ephemeral=True)
            return
        self.players.setdefault(interaction.user.id, 100)
        await interaction.response.edit_message(embed=self.embed("Team formiert sich."), view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Nur der Host startet.", ephemeral=True)
            return
        self.started = True
        button.disabled = True
        await interaction.response.edit_message(embed=self.embed("⚔️ Kampf gestartet."), view=self)

    async def act(self, interaction: discord.Interaction, kind: str) -> None:
        uid = interaction.user.id
        if not self.started or uid not in self.players or self.players[uid] <= 0:
            await interaction.response.send_message("Du kannst gerade nicht handeln.", ephemeral=True)
            return
        if uid in self.acted:
            await interaction.response.send_message("Du hast in dieser Runde bereits gehandelt.", ephemeral=True)
            return
        self.acted.add(uid)
        if kind == "attack":
            dmg = random.randint(18, 38)
            self.boss_hp -= dmg
            action = f"{interaction.user.mention} verursacht **{dmg} Schaden**."
        elif kind == "heal":
            heal = random.randint(12, 28)
            self.players[uid] = min(100, self.players[uid] + heal)
            action = f"{interaction.user.mention} heilt **{heal} HP**."
        else:
            self.players[uid] = min(120, self.players[uid] + 10)
            action = f"{interaction.user.mention} geht in Deckung."
        if self.boss_hp <= 0:
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(embed=self.embed("🏆 **NULL TITAN besiegt!**"), view=self)
            return
        living = {u for u, hp in self.players.items() if hp > 0}
        if self.acted >= living:
            target = random.choice(list(living))
            damage = random.randint(12, 30)
            self.players[target] -= damage
            action += f"\nBoss trifft <@{target}> für **{damage}**."
            self.acted.clear()
            self.round += 1
            if all(hp <= 0 for hp in self.players.values()):
                for item in self.children:
                    item.disabled = True
                action += "\n\n💀 **Team wipe.**"
        await interaction.response.edit_message(embed=self.embed(action), view=self)

    @discord.ui.button(label="Attack", emoji="⚔️", style=discord.ButtonStyle.danger, row=1)
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.act(interaction, "attack")

    @discord.ui.button(label="Defend", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def defend(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.act(interaction, "defend")

    @discord.ui.button(label="Heal", emoji="💚", style=discord.ButtonStyle.success, row=1)
    async def heal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.act(interaction, "heal")


class HeistView(discord.ui.View):
    ROLES = ["Hacker", "Driver", "Scout"]
    STAGES = [
        ("Perimeter", ["Leise rein", "Ablenkung", "Frontdoor"]),
        ("Vault", ["Code knacken", "Tunnel", "Sprengen"]),
        ("Escape", ["Van", "Dächer", "U-Bahn"]),
    ]

    def __init__(self, owner: discord.Member) -> None:
        super().__init__(timeout=900)
        self.owner = owner
        self.roles: dict[int, str] = {}
        self.stage = -1
        self.risk = 10
        self.loot = 0

    def render(self, note: str = "") -> discord.Embed:
        roster = "\n".join(f"<@{u}> · **{r}**" for u, r in self.roles.items()) or "Noch keine Rollen"
        stage = "Lobby" if self.stage < 0 else self.STAGES[self.stage][0] if self.stage < 3 else "Done"
        return card("💼 HEIST // OPERATION NIGHTGLASS", f"Phase: **{stage}** · Risiko: **{self.risk}%** · Loot: **{self.loot}**\n\n{roster}" + (f"\n\n{note}" if note else ""), GOLD)

    def role_buttons(self) -> None:
        self.clear_items()
        for role in self.ROLES:
            b = discord.ui.Button(label=role, style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, selected: str = role) -> None:
                self.roles[interaction.user.id] = selected
                await interaction.response.edit_message(embed=self.render(f"{interaction.user.mention} übernimmt **{selected}**."), view=self)
            b.callback = cb
            self.add_item(b)
        start = discord.ui.Button(label="Start Heist", style=discord.ButtonStyle.primary)
        async def start_cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.owner.id or len(self.roles) < 2:
                await interaction.response.send_message("Host + mindestens 2 Rollen nötig.", ephemeral=True)
                return
            self.stage = 0
            self.stage_buttons()
            await interaction.response.edit_message(embed=self.render("Die Operation läuft."), view=self)
        start.callback = start_cb
        self.add_item(start)

    def stage_buttons(self) -> None:
        self.clear_items()
        for idx, choice in enumerate(self.STAGES[self.stage][1]):
            b = discord.ui.Button(label=choice, style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, option: int = idx, label: str = choice) -> None:
                if interaction.user.id not in self.roles:
                    await interaction.response.send_message("Nur Crew-Mitglieder entscheiden.", ephemeral=True)
                    return
                risk_add = [5, 15, 30][option]
                self.risk = min(95, self.risk + risk_add)
                self.loot += [20, 35, 55][option]
                fail = random.randint(1, 100) <= self.risk // 3
                if fail:
                    self.clear_items()
                    await interaction.response.edit_message(embed=self.render(f"🚨 **{label}** ging schief. Die Crew muss abbrechen."), view=self)
                    return
                self.stage += 1
                if self.stage >= 3:
                    self.clear_items()
                    await interaction.response.edit_message(embed=self.render(f"🏆 Heist erfolgreich. Score: **{self.loot - self.risk}**"), view=self)
                else:
                    self.stage_buttons()
                    await interaction.response.edit_message(embed=self.render(f"✅ **{label}** erfolgreich."), view=self)
            b.callback = cb
            self.add_item(b)


class DetectiveAccuseModal(discord.ui.Modal, title="Detective · Anklage"):
    suspect = discord.ui.TextInput(label="Verdächtiger", placeholder="A, B oder C", max_length=1)

    def __init__(self, view: "DetectiveView") -> None:
        super().__init__()
        self.case = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        choice = str(self.suspect).strip().upper()
        if choice not in {"A", "B", "C"}:
            await interaction.response.send_message("Bitte A, B oder C angeben.", ephemeral=True)
            return
        for item in self.case.children:
            item.disabled = True
        if choice == self.case.culprit:
            result = f"🕵️ **Fall gelöst.** {interaction.user.mention} identifiziert Verdächtigen **{choice}** korrekt."
            color = GREEN
        else:
            result = f"❌ Falsche Anklage. Täter war **{self.case.culprit}**."
            color = RED
        await interaction.response.edit_message(embed=card("🕵️ Detective Case", result, color), view=self.case)


class DetectiveView(discord.ui.View):
    CASES = [
        ("Die verschwundene Schlüsselkarte", ["A · Nachtwache", "B · Techniker", "C · Kurier"], "B", ["Log: Tür 02:13 geöffnet", "Techniker-Token wurde 02:12 verwendet", "Kurier war ab 01:50 offline"]),
        ("Der manipulierte Server", ["A · Analyst", "B · Admin", "C · Besucher"], "A", ["Konsole zeigt lokalen Login", "Admin war im Voice-Call", "Analyst kannte den Wartungs-Code"]),
    ]

    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.title, self.suspects, self.culprit, self.evidence = random.choice(self.CASES)

    def render(self) -> discord.Embed:
        return card("🕵️ " + self.title, "**Verdächtige**\n" + "\n".join(self.suspects) + "\n\nUntersucht Beweise und erhebt anschließend Anklage.", BLUE)

    @discord.ui.button(label="Beweise", emoji="🔎", style=discord.ButtonStyle.primary)
    async def evidence_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        clues = random.sample(self.evidence, k=len(self.evidence))
        await interaction.response.send_message(embed=card("Fallakte · Beweise", "\n".join(f"• {x}" for x in clues)), ephemeral=True)

    @discord.ui.button(label="Anklagen", emoji="⚖️", style=discord.ButtonStyle.danger)
    async def accuse(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(DetectiveAccuseModal(self))


class ChoiceVoteView(discord.ui.View):
    def __init__(self, title: str, a: str, b: str, *, timeout: float = 600) -> None:
        super().__init__(timeout=timeout)
        self.title_text, self.a, self.b = title, a, b
        self.votes: dict[int, str] = {}

    def render(self) -> discord.Embed:
        count = Counter(self.votes.values())
        total = max(1, len(self.votes))
        return card(self.title_text, f"**A** · {self.a}\n**B** · {self.b}\n\nA: **{count['A']/total:.0%}** · B: **{count['B']/total:.0%}**\nVotes: **{len(self.votes)}**", ACCENT)

    @discord.ui.button(label="A", style=discord.ButtonStyle.primary)
    async def vote_a(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.votes[interaction.user.id] = "A"
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="B", style=discord.ButtonStyle.success)
    async def vote_b(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.votes[interaction.user.id] = "B"
        await interaction.response.edit_message(embed=self.render(), view=self)


class BlindRankView(discord.ui.View):
    def __init__(self, owner: discord.Member, items: list[str]) -> None:
        super().__init__(timeout=600)
        self.owner = owner
        random.shuffle(items)
        self.items = items[:5]
        self.current = 0
        self.ranks: dict[int, str] = {}
        for rank in range(1, 6):
            b = discord.ui.Button(label=str(rank), style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, r: int = rank) -> None:
                if interaction.user.id != self.owner.id:
                    await interaction.response.send_message("Das ist nicht dein Ranking.", ephemeral=True)
                    return
                if r in self.ranks:
                    await interaction.response.send_message("Dieser Platz ist bereits belegt.", ephemeral=True)
                    return
                self.ranks[r] = self.items[self.current]
                self.current += 1
                if self.current >= len(self.items):
                    self.clear_items()
                    text = "\n".join(f"**{r}.** {self.ranks[r]}" for r in sorted(self.ranks))
                    await interaction.response.edit_message(embed=card("🙈 Blind Rank · Finale", text, GOLD), view=self)
                else:
                    await interaction.response.edit_message(embed=self.render(), view=self)
            b.callback = cb
            self.add_item(b)

    def render(self) -> discord.Embed:
        return card("🙈 Blind Rank", f"Ordne **{self.items[self.current]}** ein, ohne die nächsten Items zu kennen.\n\nFreie Plätze: " + ", ".join(str(x) for x in range(1,6) if x not in self.ranks), GOLD)


class StoryView(discord.ui.View):
    CHOICES = [
        ("Eine verschlüsselte Tür blockiert den Weg.", ["Terminal hacken", "Lüftungsschacht", "Zurückgehen"]),
        ("Dahinter pulsiert ein unbekanntes Signal.", ["Signal verfolgen", "Strom trennen", "Probe sichern"]),
        ("Der Countdown startet bei 30 Sekunden.", ["Fliehen", "System resetten", "Signal senden"]),
    ]

    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.stage = 0
        self.history: list[str] = []
        self.set_buttons()

    def set_buttons(self) -> None:
        self.clear_items()
        for choice in self.CHOICES[self.stage][1]:
            b = discord.ui.Button(label=choice, style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, selected: str = choice) -> None:
                self.history.append(selected)
                self.stage += 1
                if self.stage >= len(self.CHOICES):
                    self.clear_items()
                    ending = random.choice(["Die Crew entkommt knapp.", "Das Signal antwortet.", "Das System fährt kontrolliert herunter."])
                    await interaction.response.edit_message(embed=card("📖 Community Story · Ende", " → ".join(self.history) + f"\n\n**{ending}**", GREEN), view=self)
                else:
                    self.set_buttons()
                    await interaction.response.edit_message(embed=self.render(), view=self)
            b.callback = cb
            self.add_item(b)

    def render(self) -> discord.Embed:
        return card(f"📖 Community Story · Kapitel {self.stage+1}", self.CHOICES[self.stage][0] + "\n\nDie nächste Entscheidung verändert den Verlauf.", ACCENT)


class HotseatView(discord.ui.View):
    QUESTIONS = ["Was würdest du sofort lernen, wenn Zeit egal wäre?", "Welches Projekt würdest du neu anfangen?", "Welche Fähigkeit unterschätzen andere?", "Was war dein bester spontaner Einfall?", "Welche Regel würdest du für einen Tag ändern?"]

    def __init__(self, target: discord.Member) -> None:
        super().__init__(timeout=300)
        self.target = target
        self.index = 0

    def render(self) -> discord.Embed:
        return card("🔥 HOT SEAT", f"{self.target.mention}\n\n**{self.index+1}/5** · {self.QUESTIONS[self.index]}", RED)

    @discord.ui.button(label="Nächste Frage", emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_q(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Nur die Person im Hot Seat steuert die Runde.", ephemeral=True)
            return
        self.index += 1
        if self.index >= 5:
            self.clear_items()
            await interaction.response.edit_message(embed=card("🔥 HOT SEAT", f"{self.target.mention} hat alle **5 Fragen** überstanden.", GREEN), view=self)
        else:
            await interaction.response.edit_message(embed=self.render(), view=self)


class ArcadeView(discord.ui.View):
    def __init__(self, cog: "GamesUpdate", guild_id: int) -> None:
        super().__init__(timeout=600)
        self.cog, self.guild_id = cog, guild_id

    @discord.ui.button(label="Quick Match", emoji="⚡", style=discord.ButtonStyle.primary)
    async def quick(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        queue = self.cog.queues.setdefault(self.guild_id, [])
        queue[:] = [u for u in queue if u.id != interaction.user.id]
        if queue:
            other = queue.pop(0)
            game = random.choice(["cipherduel", "reactionbattle", "territory"])
            await interaction.response.send_message(f"⚡ Match gefunden: {other.mention} vs {interaction.user.mention} · **{game}**\nStartet mit `/{game}` und markiert den Gegner.")
        else:
            queue.append(interaction.user)
            await interaction.response.send_message("Du bist jetzt in der **Quick-Match Queue**. Der nächste Spieler wird mit dir gepaart.", ephemeral=True)

    @discord.ui.button(label="Live Matches", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def live(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        sessions = [x for x in self.cog.active.values() if x["guild_id"] == self.guild_id]
        text = "\n".join(f"• **{x['game']}** · <@{x['players'][0]}> vs <@{x['players'][1]}>" for x in sessions) or "Keine laufenden Expansion-Matches."
        await interaction.response.send_message(embed=card("👁️ Live Arcade", text), ephemeral=True)

    @discord.ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary)
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        rows = await self.cog.bot.database.fetchall("SELECT user_id,SUM(wins) wins,SUM(played) played FROM game_stats WHERE guild_id=? GROUP BY user_id ORDER BY wins DESC,played ASC LIMIT 10", (self.guild_id,))
        text = "\n".join(f"**{i}.** <@{r['user_id']}> · **{r['wins']}W** / {r['played']} games" for i, r in enumerate(rows, 1)) or "Noch keine Stats."
        await interaction.response.send_message(embed=card("🏆 Arcade Leaderboard", text, GOLD), ephemeral=True)


class Blackjack(commands.GroupCog, group_name="blackjack", group_description="Blackjack ohne Echtgeld oder Economy"):
    def __init__(self, bot: commands.Bot, games: "GamesUpdate") -> None:
        self.bot, self.games = bot, games

    @app_commands.command(name="duel", description="Spiele parallel gegen denselben Dealer.")
    async def duel(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        err = valid_opponent(interaction.user, gegner)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        view = BlackjackView(self.games, interaction.guild_id, interaction.user, gegner)
        self.games.track(interaction.channel_id or 0, interaction.guild_id, "blackjack", interaction.user.id, gegner.id)
        await interaction.response.send_message(embed=view.render(), view=view)


class GamesUpdate(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active: dict[int, dict[str, object]] = {}
        self.queues: dict[int, list[discord.Member]] = {}

    async def cog_load(self) -> None:
        await self.bot.database.execute("""
            CREATE TABLE IF NOT EXISTS game_match_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,game TEXT NOT NULL,
                player_a INTEGER NOT NULL,player_b INTEGER NOT NULL,winner_id INTEGER,
                played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.bot.database.execute("""
            CREATE TABLE IF NOT EXISTS mysterybox_claims(
                guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,claim_date TEXT NOT NULL,reward TEXT NOT NULL,
                PRIMARY KEY(guild_id,user_id,claim_date)
            )
        """)

    def track(self, channel_id: int, guild_id: int, game: str, a: int, b: int) -> None:
        self.active[channel_id] = {"guild_id": guild_id, "game": game, "players": [a, b], "started": time.time()}

    async def finish_match(self, guild_id: int, game: str, a: discord.Member, b: discord.Member, winner: discord.Member | None) -> None:
        await self.bot.database.execute("INSERT INTO game_match_history(guild_id,game,player_a,player_b,winner_id) VALUES(?,?,?,?,?)", (guild_id, game, a.id, b.id, winner.id if winner else None))
        for p, result in ((a, "draw" if winner is None else "win" if winner.id == a.id else "loss"), (b, "draw" if winner is None else "win" if winner.id == b.id else "loss")):
            wins, losses, draws = (1,0,0) if result == "win" else (0,1,0) if result == "loss" else (0,0,1)
            streak = 1 if result == "win" else 0
            await self.bot.database.execute("""
                INSERT INTO game_stats(guild_id,user_id,game,played,wins,losses,draws,streak,best_streak)
                VALUES(?,?,?,1,?,?,?,?,?)
                ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
                  played=played+1,wins=wins+excluded.wins,losses=losses+excluded.losses,draws=draws+excluded.draws,
                  streak=CASE WHEN excluded.wins=1 THEN streak+1 ELSE 0 END,
                  best_streak=MAX(best_streak,CASE WHEN excluded.wins=1 THEN streak+1 ELSE best_streak END),
                  last_played=CURRENT_TIMESTAMP
            """, (guild_id, p.id, game, wins, losses, draws, streak, streak))
        for channel, data in list(self.active.items()):
            if data["guild_id"] == guild_id and data["game"] == game and set(data["players"]) == {a.id,b.id}:
                self.active.pop(channel, None)

    async def launch_two(self, interaction: discord.Interaction, opponent: discord.Member, game: str, factory) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        err = valid_opponent(interaction.user, opponent)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        view = factory(self, interaction.guild_id, interaction.user, opponent)
        self.track(interaction.channel_id or 0, interaction.guild_id, game, interaction.user.id, opponent.id)
        game_embed = view.embed() if hasattr(view, "embed") else view.render() if hasattr(view, "render") else card(game.title(), f"{interaction.user.mention} vs {opponent.mention}")
        await interaction.response.send_message(embed=game_embed, view=view)

    @app_commands.command(name="battleship", description="2-Spieler-Schiffeversenken mit privaten Flotten.")
    async def battleship(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        err = valid_opponent(interaction.user, gegner)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        view = BattleshipView(self, interaction.guild_id, interaction.user, gegner)
        self.track(interaction.channel_id or 0, interaction.guild_id, "battleship", interaction.user.id, gegner.id)
        await interaction.response.send_message(embed=card("⚓ Battleship", f"{interaction.user.mention} vs {gegner.mention}\n\nAm Zug: {view.players[view.turn].mention}\nFlotten wurden **geheim automatisch platziert**.", BLUE), view=view)

    @app_commands.command(name="cipherduel", description="Knacke deinen Cipher schneller als dein Gegner.")
    async def cipherduel(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            return
        err = valid_opponent(interaction.user, gegner)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        view = CipherView(self, interaction.guild_id, interaction.user, gegner)
        self.track(interaction.channel_id or 0, interaction.guild_id, "cipherduel", interaction.user.id, gegner.id)
        await interaction.response.send_message(embed=card("🔐 Cipher Duel", f"{interaction.user.mention} vs {gegner.mention}\n\nJeder erhält einen **eigenen geheimen Cipher**. Wer zuerst löst, gewinnt."), view=view)

    @app_commands.command(name="escape", description="Kooperativer Discord Escape Room.")
    async def escape(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        view = EscapeView(interaction.user)
        await interaction.response.send_message(embed=view.lobby(), view=view)

    @app_commands.command(name="bossfight", description="Kooperativer Bossfight mit Rollenaktionen pro Runde.")
    async def bossfight(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        view = BossFightView(interaction.user)
        await interaction.response.send_message(embed=view.embed("Team zusammenstellen und Start drücken."), view=view)

    @app_commands.command(name="heist", description="Kooperativer Heist mit Rollen, Risiko und mehreren Enden.")
    async def heist(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        view = HeistView(interaction.user)
        view.role_buttons()
        await interaction.response.send_message(embed=view.render("Wählt Rollen."), view=view)

    @app_commands.command(name="detective", description="Löse einen generierten Kriminalfall gemeinsam.")
    async def detective(self, interaction: discord.Interaction) -> None:
        view = DetectiveView()
        await interaction.response.send_message(embed=view.render(), view=view)

    @app_commands.command(name="territory", description="Strategisches 5x5-Gebietsduell.")
    async def territory(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        await self.launch_two(interaction, gegner, "territory", TerritoryView)

    @app_commands.command(name="wordchain", description="Live-Wortketten-Duell mit Wiederholungsschutz.")
    async def wordchain(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        await self.launch_two(interaction, gegner, "wordchain", WordChainView)

    @app_commands.command(name="reactionbattle", description="5 Runden Reaktionsduell mit Fake-Buttons.")
    async def reactionbattle(self, interaction: discord.Interaction, gegner: discord.Member) -> None:
        await self.launch_two(interaction, gegner, "reactionbattle", ReactionBattleView)

    @app_commands.command(name="blindrank", description="Ordne 5 Dinge ein, ohne die nächsten zu kennen.")
    async def blindrank(self, interaction: discord.Interaction, items: str) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        values = [x.strip() for x in items.split(",") if x.strip()]
        if len(values) < 5:
            await interaction.response.send_message("Bitte mindestens 5 Items kommasepariert angeben.", ephemeral=True)
            return
        view = BlindRankView(interaction.user, values)
        await interaction.response.send_message(embed=view.render(), view=view)

    @app_commands.command(name="wouldyourather", description="Interaktive Entweder-oder-Abstimmung.")
    async def wouldyourather(self, interaction: discord.Interaction, option_a: str, option_b: str) -> None:
        view = ChoiceVoteView("⚖️ Would You Rather", option_a, option_b)
        await interaction.response.send_message(embed=view.render(), view=view)

    @app_commands.command(name="hotseat", description="Setzt einen zufälligen oder gewählten Spieler in den Hot Seat.")
    async def hotseat(self, interaction: discord.Interaction, spieler: discord.Member | None = None) -> None:
        if interaction.guild is None:
            return
        candidates = [m for m in interaction.guild.members if not m.bot]
        target = spieler or (random.choice(candidates) if candidates else interaction.user)
        view = HotseatView(target)
        await interaction.response.send_message(embed=view.render(), view=view)

    @app_commands.command(name="story", description="Community-Story mit verzweigenden Entscheidungen.")
    async def story(self, interaction: discord.Interaction) -> None:
        view = StoryView()
        await interaction.response.send_message(embed=view.render(), view=view)

    @app_commands.command(name="mysterybox", description="Tägliche Mystery Box mit XP oder Challenge.")
    async def mysterybox(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        today = datetime.now(UTC).date().isoformat()
        row = await self.bot.database.fetchone("SELECT reward FROM mysterybox_claims WHERE guild_id=? AND user_id=? AND claim_date=?", (interaction.guild_id, interaction.user.id, today))
        if row:
            await interaction.response.send_message(f"Heute bereits geöffnet: **{row['reward']}**", ephemeral=True)
            return
        rewards = [("+25 XP", 25), ("+50 XP", 50), ("Rare: +100 XP", 100), ("Challenge: Gewinne heute ein Arcade-Match", 10), ("Challenge: Nutze 3 verschiedene Commands", 10)]
        label, xp = random.choice(rewards)
        await self.bot.database.execute("INSERT INTO mysterybox_claims(guild_id,user_id,claim_date,reward) VALUES(?,?,?,?)", (interaction.guild_id, interaction.user.id, today, label))
        await self.bot.database.execute("INSERT INTO xp_profiles(guild_id,user_id,xp) VALUES(?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=xp+excluded.xp,updated_at=CURRENT_TIMESTAMP", (interaction.guild_id, interaction.user.id, xp))
        await interaction.response.send_message(embed=card("🎁 Mystery Box", f"{interaction.user.mention}\n\n**{label}**", GOLD))

    @app_commands.command(name="achievementhunt", description="Zeigt Fortschritt bei versteckten Bot-Achievements.")
    async def achievementhunt(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        stats = await self.bot.database.fetchone("SELECT COALESCE(SUM(wins),0) wins,COALESCE(SUM(played),0) played,COALESCE(MAX(best_streak),0) streak FROM game_stats WHERE guild_id=? AND user_id=?", (interaction.guild_id, interaction.user.id))
        usage = await self.bot.database.fetchone("SELECT COUNT(*) c,COUNT(DISTINCT command_name) d FROM command_usage WHERE guild_id=? AND user_id=?", (interaction.guild_id, interaction.user.id))
        checks = [("First Blood", int(stats['wins']) >= 1), ("Arcade Veteran", int(stats['played']) >= 25), ("On Fire", int(stats['streak']) >= 5), ("Command Explorer", int(usage['d']) >= 15), ("Power User", int(usage['c']) >= 100)]
        unlocked = [name for name, ok in checks if ok]
        hidden = len(checks) - len(unlocked)
        text = "\n".join(f"✅ **{x}**" for x in unlocked) or "Noch kein verstecktes Achievement entdeckt."
        text += f"\n\n🔒 **{hidden} versteckte Achievements** verbleiben." if hidden else "\n\n🏆 Alle gefunden!"
        await interaction.response.send_message(embed=card("🕵️ Achievement Hunt", text, GOLD), ephemeral=True)

    @app_commands.command(name="season", description="Aktuelle monatliche Arcade-Season und Ranking.")
    async def season(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        month = datetime.now(UTC).strftime("%Y-%m")
        rows = await self.bot.database.fetchall("SELECT winner_id,COUNT(*) wins FROM game_match_history WHERE guild_id=? AND winner_id IS NOT NULL AND substr(played_at,1,7)=? GROUP BY winner_id ORDER BY wins DESC LIMIT 10", (interaction.guild_id, month))
        text = "\n".join(f"**{i}.** <@{r['winner_id']}> · **{r['wins']} Wins**" for i,r in enumerate(rows,1)) or "Noch keine Expansion-Matches in dieser Season."
        await interaction.response.send_message(embed=card(f"🏆 Arcade Season · {month}", text, GOLD))

    @app_commands.command(name="rivalry", description="Head-to-Head-Verlauf zweier Spieler.")
    async def rivalry(self, interaction: discord.Interaction, spieler: discord.Member) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT game,winner_id,COUNT(*) c FROM game_match_history WHERE guild_id=? AND ((player_a=? AND player_b=?) OR (player_a=? AND player_b=?)) GROUP BY game,winner_id ORDER BY game", (interaction.guild_id, interaction.user.id, spieler.id, spieler.id, interaction.user.id))
        me = sum(int(r['c']) for r in rows if r['winner_id'] == interaction.user.id)
        them = sum(int(r['c']) for r in rows if r['winner_id'] == spieler.id)
        draws = sum(int(r['c']) for r in rows if r['winner_id'] is None)
        games = Counter()
        for r in rows:
            games[str(r['game'])] += int(r['c'])
        detail = "\n".join(f"• `{g}` · {n} matches" for g,n in games.items()) or "Noch keine gemeinsamen Expansion-Matches."
        await interaction.response.send_message(embed=card("⚔️ Rivalry", f"{interaction.user.mention} **{me}** : **{them}** {spieler.mention}\nDraws: **{draws}**\n\n{detail}", RED))

    @app_commands.command(name="rematch", description="Zeigt dein letztes Match und startet die Revanche-Route.")
    async def rematch(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT game,player_a,player_b FROM game_match_history WHERE guild_id=? AND (player_a=? OR player_b=?) ORDER BY id DESC LIMIT 1", (interaction.guild_id, interaction.user.id, interaction.user.id))
        if not row:
            await interaction.response.send_message("Noch kein Match für eine Revanche gefunden.", ephemeral=True)
            return
        other = row['player_b'] if row['player_a'] == interaction.user.id else row['player_a']
        await interaction.response.send_message(embed=card("🔁 Rematch", f"Letztes Spiel: **{row['game']}**\nGegner: <@{other}>\n\nStarte direkt `/{row['game']}` und markiere den Gegner."))

    @app_commands.command(name="spectate", description="Listet aktuell laufende Expansion-Matches.")
    async def spectate(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        sessions = [x for x in self.active.values() if x["guild_id"] == interaction.guild_id and time.time() - float(x["started"]) < 1800]
        text = "\n".join(f"👁️ **{x['game']}** · <@{x['players'][0]}> vs <@{x['players'][1]}>" for x in sessions) or "Keine laufenden Expansion-Matches."
        await interaction.response.send_message(embed=card("👁️ Spectate", text, BLUE))

    @app_commands.command(name="arcade", description="Öffnet die neue Arcade-Zentrale mit Quick Match und Leaderboard.")
    async def arcade(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        text = "**DUELS**\n`/battleship` · `/cipherduel` · `/blackjack duel` · `/territory` · `/wordchain` · `/reactionbattle`\n\n**CO-OP**\n`/escape` · `/bossfight` · `/heist` · `/detective`\n\n**PARTY**\n`/blindrank` · `/wouldyourather` · `/hotseat` · `/story` · `/mysterybox`\n\n**META**\n`/season` · `/rivalry` · `/rematch` · `/spectate` · `/achievementhunt`"
        await interaction.response.send_message(embed=card("🕹️ RASPBERRY ARCADE", text, ACCENT, footer="Quick Match · Seasons · Hidden Achievements"), view=ArcadeView(self, interaction.guild_id))


async def setup(bot: commands.Bot) -> None:
    games = GamesUpdate(bot)
    await bot.add_cog(games)
    await bot.add_cog(Blackjack(bot, games))
