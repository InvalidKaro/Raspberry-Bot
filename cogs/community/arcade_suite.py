from __future__ import annotations

import asyncio
import random
import re
import string
import time
import uuid
from collections import Counter
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands


PALETTE = {
    "battleship": 0x1F6FEB, "cipherduel": 0x8B5CF6, "blackjack": 0x111827,
    "territory": 0xEC4899, "wordchain": 0x14B8A6, "reactionbattle": 0xF97316,
    "escape": 0xF59E0B, "bossfight": 0xCF222E, "heist": 0x2DA44E,
    "detective": 0x6E7781, "story": 0x5865F2,
}


def card(title: str, text: str, game: str = "arcade", footer: str = "Raspberry Arcade · interactive") -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=PALETTE.get(game, 0x5865F2), timestamp=datetime.now(UTC))
    e.set_footer(text=footer)
    return e


class Stats:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def ensure(self) -> None:
        for sql in (
            """CREATE TABLE IF NOT EXISTS arcade_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,game TEXT NOT NULL,
            player_a INTEGER NOT NULL,player_b INTEGER,winner_id INTEGER,result TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS game_stats(
            guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,game TEXT NOT NULL,
            played INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,streak INTEGER NOT NULL DEFAULT 0,best_streak INTEGER NOT NULL DEFAULT 0,
            last_played TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id,game))""",
            """CREATE TABLE IF NOT EXISTS arcade_daily_boxes(
            guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,day_key TEXT NOT NULL,reward TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id,day_key))""",
            """CREATE TABLE IF NOT EXISTS arcade_hidden_unlocks(
            guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,achievement_key TEXT NOT NULL,
            unlocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id,achievement_key))""",
            """CREATE TABLE IF NOT EXISTS arcade_wyr_votes(
            guild_id INTEGER NOT NULL,question_key TEXT NOT NULL,user_id INTEGER NOT NULL,choice TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,question_key,user_id))""",
        ):
            await self.bot.database.execute(sql)

    async def bump(self, gid: int, uid: int, game: str, result: str) -> None:
        if result == "win":
            sql = """INSERT INTO game_stats(guild_id,user_id,game,played,wins,streak,best_streak)
            VALUES(?,?,?,1,1,1,1) ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
            played=played+1,wins=wins+1,streak=streak+1,best_streak=MAX(best_streak,streak+1),last_played=CURRENT_TIMESTAMP"""
        elif result == "loss":
            sql = """INSERT INTO game_stats(guild_id,user_id,game,played,losses,streak)
            VALUES(?,?,?,1,1,0) ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
            played=played+1,losses=losses+1,streak=0,last_played=CURRENT_TIMESTAMP"""
        else:
            sql = """INSERT INTO game_stats(guild_id,user_id,game,played,draws,streak)
            VALUES(?,?,?,1,1,0) ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
            played=played+1,draws=draws+1,streak=0,last_played=CURRENT_TIMESTAMP"""
        await self.bot.database.execute(sql, (gid, uid, game))

    async def result(self, gid: int, game: str, a: int, b: int | None, winner: int | None, note: str) -> None:
        await self.bot.database.execute(
            "INSERT INTO arcade_results(guild_id,game,player_a,player_b,winner_id,result) VALUES(?,?,?,?,?,?)",
            (gid, game, a, b, winner, note[:120]),
        )
        if b is None:
            return
        if winner is None:
            await self.bump(gid, a, game, "draw"); await self.bump(gid, b, game, "draw")
        else:
            loser = b if winner == a else a
            await self.bump(gid, winner, game, "win"); await self.bump(gid, loser, game, "loss")


class Duel(discord.ui.View):
    def __init__(self, cog: "ArcadeSuite", game: str, p1: discord.Member, p2: discord.Member, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.cog, self.bot, self.game = cog, cog.bot, game
        self.p1, self.p2, self.guild_id = p1, p2, p1.guild.id
        self.sid, self.message, self.finished = uuid.uuid4().hex[:8], None, False

    def who(self, uid: int) -> int | None:
        return 0 if uid == self.p1.id else 1 if uid == self.p2.id else None

    async def end(self, winner: int | None, note: str) -> None:
        if self.finished:
            return
        self.finished = True
        for x in self.children:
            x.disabled = True
        await self.cog.stats.result(self.guild_id, self.game, self.p1.id, self.p2.id, winner, note)
        self.cog.recent[self.guild_id] = (self.game, self.p1.id, self.p2.id)
        self.cog.sessions.pop(self.sid, None)

    async def on_timeout(self) -> None:
        self.cog.sessions.pop(self.sid, None)
        for x in self.children:
            x.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except discord.HTTPException: pass


class Coord(discord.ui.Modal, title="Fire"):
    value = discord.ui.TextInput(label="Koordinate", placeholder="B4", min_length=2, max_length=2)
    def __init__(self, game: "Battleship") -> None:
        super().__init__(); self.game = game
    async def on_submit(self, i: discord.Interaction) -> None:
        await self.game.fire(i, str(self.value.value))


class Battleship(Duel):
    def __init__(self, cog: "ArcadeSuite", p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(cog, "battleship", p1, p2, 1200)
        cells = [(r, c) for r in range(5) for c in range(5)]
        self.ships = [set(random.sample(cells, 5)), set(random.sample(cells, 5))]
        self.shots: list[dict[tuple[int, int], bool]] = [{}, {}]
        self.turn = random.randrange(2)

    def render(self, status: str = "") -> discord.Embed:
        current = self.p1 if self.turn == 0 else self.p2
        return card("🚢 BATTLESHIP", f"{self.p1.mention} vs {self.p2.mention}\n\n"
                    f"{status or f'Am Zug: {current.mention}'}\n\n**Fire** → A1 bis E5 · **My Board** bleibt privat.",
                    "battleship", f"Match {self.sid} · 5 versteckte Schiffsfelder pro Person")

    async def fire(self, i: discord.Interaction, raw: str) -> None:
        who = self.who(i.user.id)
        if who is None or who != self.turn:
            await i.response.send_message("Nicht dein Zug.", ephemeral=True); return
        m = re.fullmatch(r"([A-Ea-e])([1-5])", raw.strip())
        if not m:
            await i.response.send_message("Nutze A1–E5.", ephemeral=True); return
        pos = (ord(m.group(1).upper()) - 65, int(m.group(2)) - 1)
        if pos in self.shots[who]:
            await i.response.send_message("Dort warst du schon.", ephemeral=True); return
        enemy = 1 - who
        hit = pos in self.ships[enemy]
        self.shots[who][pos] = hit
        if all(p in self.shots[who] and self.shots[who][p] for p in self.ships[enemy]):
            winner = self.p1 if who == 0 else self.p2
            await self.end(winner.id, "fleet destroyed")
            await i.response.edit_message(embed=self.render(f"🏆 {winner.mention} versenkt die komplette Flotte."), view=self); return
        self.turn = enemy
        await i.response.edit_message(embed=self.render("💥 Treffer!" if hit else "🌊 Wasser."), view=self)

    @discord.ui.button(label="Fire", emoji="🎯", style=discord.ButtonStyle.danger)
    async def fire_b(self, i: discord.Interaction, _: discord.ui.Button) -> None:
        if self.who(i.user.id) is None:
            await i.response.send_message("Nicht dein Match.", ephemeral=True); return
        await i.response.send_modal(Coord(self))

    @discord.ui.button(label="My Board", emoji="🗺️", style=discord.ButtonStyle.secondary)
    async def board(self, i: discord.Interaction, _: discord.ui.Button) -> None:
        who = self.who(i.user.id)
        if who is None:
            await i.response.send_message("Nicht dein Match.", ephemeral=True); return
        incoming = self.shots[1-who]
        lines = ["　1 2 3 4 5"]
        for r in range(5):
            row = []
            for c in range(5):
                p = (r, c)
                row.append("💥" if incoming.get(p) else "🌊" if p in incoming else "🚢" if p in self.ships[who] else "▫️")
            lines.append(f"{string.ascii_uppercase[r]} " + "".join(row))
        await i.response.send_message("**Dein Board**\n" + "\n".join(lines), ephemeral=True)


class CipherModal(discord.ui.Modal, title="Solve cipher"):
    answer = discord.ui.TextInput(label="Klartext")
    def __init__(self, game: "Cipher") -> None:
        super().__init__(); self.game = game
    async def on_submit(self, i: discord.Interaction) -> None:
        if self.game.who(i.user.id) is None:
            await i.response.send_message("Nicht dein Match.", ephemeral=True); return
        if str(self.answer.value).strip().lower() != self.game.answer:
            await i.response.send_message("❌ Falsch.", ephemeral=True); return
        await self.game.end(i.user.id, "cipher solved")
        if self.game.message:
            await self.game.message.edit(embed=self.game.render(f"🏆 {i.user.mention} löst: **{self.game.answer}**"), view=self.game)
        await i.response.send_message("✅ Richtig.", ephemeral=True)


class Cipher(Duel):
    PHRASES = ["raspberry power","discord arcade","hidden signal","secret protocol","system online","pixel victory"]
    def __init__(self, cog: "ArcadeSuite", p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(cog, "cipherduel", p1, p2, 600)
        self.answer = random.choice(self.PHRASES); self.shift = random.randint(1, 8)
        self.encrypted = "".join(chr((ord(x)-97+self.shift)%26+97) if x.isalpha() else x for x in self.answer)
    def render(self, status: str = "Wer zuerst löst, gewinnt.") -> discord.Embed:
        return card("🔐 CIPHER DUEL", f"{self.p1.mention} vs {self.p2.mention}\n\n```{self.encrypted.upper()}```\nShift **1–8**\n\n{status}",
                    "cipherduel", f"Match {self.sid}")
    @discord.ui.button(label="Solve", emoji="🔓", style=discord.ButtonStyle.primary)
    async def solve(self, i: discord.Interaction, _: discord.ui.Button) -> None:
        await i.response.send_modal(CipherModal(self))
    @discord.ui.button(label="Hint", emoji="💡", style=discord.ButtonStyle.secondary)
    async def hint(self, i: discord.Interaction, _: discord.ui.Button) -> None:
        await i.response.send_message(f"Shift = **{self.shift}**", ephemeral=True)


SUITS, RANKS = ["♠","♥","♦","♣"], ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
def hand_value(hand: list[str]) -> int:
    values = [(11 if c[:-1]=="A" else 10 if c[:-1] in {"J","Q","K"} else int(c[:-1])) for c in hand]
    value, aces = sum(values), sum(c.startswith("A") for c in hand)
    while value > 21 and aces: value -= 10; aces -= 1
    return value


class Blackjack(Duel):
    def __init__(self, cog: "ArcadeSuite", p1: discord.Member, p2: discord.Member) -> None:
        super().__init__(cog, "blackjack", p1, p2, 600)
        self.deck=[r+s for r in RANKS for s in SUITS]; random.shuffle(self.deck)
        self.hands=[[self.deck.pop(),self.deck.pop()],[self.deck.pop(),self.deck.pop()]]
        self.dealer=[self.deck.pop(),self.deck.pop()]; self.stand=[False,False]
    def render(self, reveal=False, status="") -> discord.Embed:
        dealer = f"{' '.join(self.dealer)} · **{hand_value(self.dealer)}**" if reveal else f"{self.dealer[0]} ??"
        return card("🃏 BLACKJACK DUEL", f"Dealer: {dealer}\n\n"
                    f"{self.p1.mention}: {' '.join(self.hands[0])} · **{hand_value(self.hands[0])}** {'STAND' if self.stand[0] else ''}\n"
                    f"{self.p2.mention}: {' '.join(self.hands[1])} · **{hand_value(self.hands[1])}** {'STAND' if self.stand[1] else ''}\n\n{status}",
                    "blackjack", f"Match {self.sid} · beide gegen denselben Dealer")
    async def maybe_end(self, i: discord.Interaction) -> bool:
        for n in (0,1):
            if hand_value(self.hands[n])>21: self.stand[n]=True
        if not all(self.stand): return False
        while hand_value(self.dealer)<17: self.dealer.append(self.deck.pop())
        d=hand_value(self.dealer); scores=[]
        for h in self.hands:
            v=hand_value(h); scores.append(-1 if v>21 else 2 if d>21 or v>d else 1 if v==d else 0)
        winner=None if scores[0]==scores[1] else self.p1.id if scores[0]>scores[1] else self.p2.id
        text="🤝 Gleichstand." if winner is None else f"🏆 <@{winner}> gewinnt."
        await self.end(winner, "blackjack"); await i.response.edit_message(embed=self.render(True,text),view=self); return True
    @discord.ui.button(label="Hit", emoji="➕", style=discord.ButtonStyle.primary)
    async def hit(self,i:discord.Interaction,_:discord.ui.Button)->None:
        n=self.who(i.user.id)
        if n is None or self.stand[n]: await i.response.send_message("Nicht möglich.",ephemeral=True); return
        self.hands[n].append(self.deck.pop())
        if not await self.maybe_end(i): await i.response.edit_message(embed=self.render(status=f"{i.user.mention} zieht."),view=self)
    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary)
    async def st(self,i:discord.Interaction,_:discord.ui.Button)->None:
        n=self.who(i.user.id)
        if n is None: await i.response.send_message("Nicht dein Match.",ephemeral=True); return
        self.stand[n]=True
        if not await self.maybe_end(i): await i.response.edit_message(embed=self.render(status=f"{i.user.mention} steht."),view=self)


class Cell(discord.ui.Button):
    def __init__(self,n:int)->None: super().__init__(label="·",style=discord.ButtonStyle.secondary,row=n//4); self.n=n
    async def callback(self,i:discord.Interaction)->None:
        v: Territory=self.view  # type: ignore
        who=v.who(i.user.id)
        if who is None or who!=v.turn or v.board[self.n]!=-1:
            await i.response.send_message("Nicht möglich.",ephemeral=True); return
        v.board[self.n]=who; self.label="◆" if who==0 else "●"; self.style=discord.ButtonStyle.primary if who==0 else discord.ButtonStyle.danger
        if all(x>=0 for x in v.board):
            a,b=v.board.count(0),v.board.count(1); winner=v.p1.id if a>b else v.p2.id if b>a else None
            await v.end(winner,f"{a}:{b}"); await i.response.edit_message(embed=v.render(f"🏁 **{a}:{b}**"),view=v); return
        v.turn=1-v.turn; await i.response.edit_message(embed=v.render(),view=v)


class Territory(Duel):
    def __init__(self,cog:"ArcadeSuite",p1:discord.Member,p2:discord.Member)->None:
        super().__init__(cog,"territory",p1,p2); self.board=[-1]*16; self.turn=random.randrange(2)
        for n in range(16): self.add_item(Cell(n))
    def render(self,status="")->discord.Embed:
        current=self.p1 if self.turn==0 else self.p2
        return card("🗺️ TERRITORY",f"◆ {self.p1.mention} · ● {self.p2.mention}\n\n{status or f'Am Zug: {current.mention}'}\nMehr Felder nach 16 Zügen gewinnt.",
                    "territory",f"Match {self.sid}")


class WordModal(discord.ui.Modal,title="Word Chain"):
    word=discord.ui.TextInput(label="Wort",min_length=3,max_length=30)
    def __init__(self,g:"WordChain")->None: super().__init__(); self.g=g
    async def on_submit(self,i:discord.Interaction)->None:
        who=self.g.who(i.user.id); w=re.sub(r"[^a-zA-ZäöüÄÖÜß]","",str(self.word.value)).lower()
        if who!=self.g.turn: await i.response.send_message("Nicht dein Zug.",ephemeral=True); return
        if w in self.g.used or (self.g.last and w[0]!=self.g.last[-1]):
            await i.response.send_message("Wiederholt oder falscher Anfangsbuchstabe.",ephemeral=True); return
        self.g.used.add(w); self.g.last=w; self.g.rounds+=1; self.g.turn=1-self.g.turn
        if self.g.rounds>=20:
            await self.g.end(None,"20 words"); await i.response.edit_message(embed=self.g.render("🤝 20 Wörter geschafft."),view=self.g)
        else: await i.response.edit_message(embed=self.g.render(f"✅ **{w}**"),view=self.g)


class WordChain(Duel):
    def __init__(self,cog:"ArcadeSuite",p1:discord.Member,p2:discord.Member)->None:
        super().__init__(cog,"wordchain",p1,p2,600); self.turn=random.randrange(2); self.used=set(); self.last=""; self.rounds=0
    def render(self,status="")->discord.Embed:
        current=self.p1 if self.turn==0 else self.p2
        return card("🔤 WORD CHAIN",f"{self.p1.mention} vs {self.p2.mention}\n\nLetztes Wort: **{self.last or '—'}** · Runde **{self.rounds+1}/20**\nAm Zug: {current.mention}\n{status}",
                    "wordchain",f"Match {self.sid}")
    @discord.ui.button(label="Submit word",emoji="✍️",style=discord.ButtonStyle.primary)
    async def submit(self,i:discord.Interaction,_:discord.ui.Button)->None: await i.response.send_modal(WordModal(self))


class Reaction(Duel):
    def __init__(self,cog:"ArcadeSuite",p1:discord.Member,p2:discord.Member)->None:
        super().__init__(cog,"reactionbattle",p1,p2,300); self.ready=set(); self.go=False; self.go_at=0.0; self.task=None
    def render(self,status="Beide READY drücken.")->discord.Embed:
        return card("⚡ REACTION BATTLE",f"{self.p1.mention} vs {self.p2.mention}\nReady **{len(self.ready)}/2**\n\n{status}",
                    "reactionbattle",f"Match {self.sid} · False start verliert")
    async def signal(self)->None:
        await asyncio.sleep(random.uniform(2,6))
        if self.finished:return
        self.go=True; self.go_at=time.perf_counter()
        if self.message:
            try: await self.message.edit(embed=self.render("🟢 **FIRE!**"),view=self)
            except discord.HTTPException: pass
    @discord.ui.button(label="READY",emoji="🎯",style=discord.ButtonStyle.primary)
    async def ready_b(self,i:discord.Interaction,b:discord.ui.Button)->None:
        if self.who(i.user.id) is None: await i.response.send_message("Nicht dein Match.",ephemeral=True); return
        self.ready.add(i.user.id)
        if len(self.ready)==2 and self.task is None: b.disabled=True; self.task=asyncio.create_task(self.signal())
        await i.response.edit_message(embed=self.render("⏳ Signal wird vorbereitet…" if len(self.ready)==2 else "Warte auf Gegner."),view=self)
    @discord.ui.button(label="FIRE",emoji="⚡",style=discord.ButtonStyle.danger)
    async def fire(self,i:discord.Interaction,_:discord.ui.Button)->None:
        if self.who(i.user.id) is None or i.user.id not in self.ready: await i.response.send_message("Erst READY.",ephemeral=True); return
        if not self.go:
            winner=self.p2 if i.user.id==self.p1.id else self.p1; await self.end(winner.id,"false start")
            await i.response.edit_message(embed=self.render(f"🚨 False start · {winner.mention} gewinnt."),view=self); return
        ms=(time.perf_counter()-self.go_at)*1000; await self.end(i.user.id,f"{ms:.0f}ms")
        await i.response.edit_message(embed=self.render(f"🏆 {i.user.mention} · **{ms:.0f} ms**"),view=self)


class SolveModal(discord.ui.Modal,title="Lösung"):
    answer=discord.ui.TextInput(label="Antwort")
    def __init__(self,v:"Scenario")->None: super().__init__(); self.v=v
    async def on_submit(self,i:discord.Interaction)->None:
        if str(self.answer.value).strip().lower()!=self.v.answer.lower():
            await i.response.send_message("❌ Falsch.",ephemeral=True); return
        self.v.stage+=1
        if self.v.stage>=len(self.v.steps):
            for x in self.v.children:x.disabled=True
            self.v.cog.sessions.pop(self.v.sid,None)
            await i.response.edit_message(embed=card(f"✅ {self.v.kind.upper()} COMPLETE",self.v.ending,self.v.kind),view=self.v); return
        self.v.load(); await i.response.edit_message(embed=self.v.render("✅ Nächste Phase."),view=self.v)


class Scenario(discord.ui.View):
    DATA={
        "escape":[("Cold Storage","2 · 4 · 8 · ?","16","Verdopplung."),
                  ("Cipher Hall","A=1…Z=26. B+O+T = ?","37","2+15+20"),
                  ("Core Chamber","RASPberry → erste vier Großbuchstaben","rasp","Nur Großbuchstaben.")],
        "detective":[("Evidence 1","Nova offline, Vera online. Wer hatte Zugriff?","vera","Tokenlog zeigt Vera."),
                     ("Evidence 2","Löschzeit 03:14, Token 03:13. Täter?","vera","Zeitstempel vergleichen.")],
    }
    def __init__(self,cog:"ArcadeSuite",owner:discord.Member,kind:str)->None:
        super().__init__(timeout=1200); self.cog=cog; self.owner=owner; self.guild_id=owner.guild.id; self.kind=kind; self.sid=uuid.uuid4().hex[:8]
        self.stage=0; self.team={owner.id}; self.risk=10; self.loot=0; self.boss=75
        self.steps=self.DATA.get(kind,[]); self.answer=""; self.hint_text=""; self.ending=""
        if kind in {"escape","detective"}: self.load()
        self.ending={"escape":"Das Team entkommt.","detective":"Case closed.","heist":"Crew entkommt mit dem Loot.",
                     "bossfight":"NULL TITAN besiegt.","story":"Die Community hat ihre Timeline geschrieben."}.get(kind,"Complete.")
        self.refresh()
    def load(self)->None:
        _,_,self.answer,self.hint_text=self.steps[self.stage]
    def refresh(self)->None:
        if self.kind=="heist": self.a.label="Silent route"; self.b.label="Fast breach"
        elif self.kind=="bossfight": self.a.label="Attack"; self.b.label="Heal"
        elif self.kind=="story": self.a.label="Open"; self.b.label="Quarantine"
        else: self.a.label="Solve"; self.b.label="Hint"
    def render(self,status="")->discord.Embed:
        if self.kind in {"escape","detective"}:
            name,clue,_,_=self.steps[self.stage]
            text=f"Phase **{self.stage+1}/{len(self.steps)}** · **{name}**\n\n{clue}\n\n{status}"
        elif self.kind=="bossfight":
            text=f"Boss HP **{max(0,self.boss)}/75** · Team **{len(self.team)}**\n\n{status or 'Join, Attack oder Heal.'}"
        elif self.kind=="heist":
            text=f"Phase **{self.stage+1}/4** · Risk **{self.risk}%** · Loot **{self.loot}** · Crew **{len(self.team)}**\n\n{status or 'Wählt den nächsten Schritt.'}"
        else:
            chapters=["Ein unbekanntes Signal erscheint auf dem HomePi.","Die Spur führt zu `FINAL_DOOR`.","Die Datei beginnt zu antworten."]
            text=f"Kapitel **{self.stage+1}/3**\n\n{chapters[min(self.stage,2)]}\n\n{status or 'Community entscheidet.'}"
        return card({"escape":"🚪 ESCAPE","detective":"🕵️ DETECTIVE","bossfight":"👾 BOSS FIGHT","heist":"💼 HEIST","story":"📖 STORY"}[self.kind],text,self.kind,f"Session {self.sid}")
    @discord.ui.button(label="Join",emoji="➕",style=discord.ButtonStyle.secondary,row=0)
    async def join(self,i:discord.Interaction,_:discord.ui.Button)->None:
        self.team.add(i.user.id); await i.response.send_message("Joined.",ephemeral=True)
    @discord.ui.button(label="A",style=discord.ButtonStyle.primary,row=1)
    async def a(self,i:discord.Interaction,_:discord.ui.Button)->None:
        if self.kind in {"escape","detective"}: await i.response.send_modal(SolveModal(self)); return
        if self.kind=="bossfight":
            if i.user.id not in self.team: await i.response.send_message("Join zuerst.",ephemeral=True); return
            dmg=random.randint(5,13); self.boss-=dmg
            if self.boss<=0:
                for x in self.children:x.disabled=True
                self.cog.sessions.pop(self.sid,None); await i.response.edit_message(embed=card("👾 BOSS DOWN",f"{i.user.mention} setzt den letzten Treffer.",self.kind),view=self); return
            await i.response.edit_message(embed=self.render(f"{i.user.mention}: **{dmg} Damage**"),view=self); return
        if self.kind=="heist":
            self.stage+=1; self.risk=min(100,self.risk+random.randint(3,13)); self.loot+=random.randint(10,25)
            await self.progress(i); return
        if self.kind=="story": self.stage+=1; await self.progress(i,"Pfad: **Open**")
    @discord.ui.button(label="B",style=discord.ButtonStyle.danger,row=1)
    async def b(self,i:discord.Interaction,_:discord.ui.Button)->None:
        if self.kind in {"escape","detective"}: await i.response.send_message(self.hint_text,ephemeral=True); return
        if self.kind=="bossfight":
            self.team.add(i.user.id); await i.response.send_message("💚 Support-Aktion: Team stabilisiert.",ephemeral=True); return
        if self.kind=="heist":
            self.stage+=1; self.risk=min(100,self.risk+random.randint(12,28)); self.loot+=random.randint(25,50)
            if random.randint(1,100)<=self.risk//4:
                for x in self.children:x.disabled=True
                self.cog.sessions.pop(self.sid,None); await i.response.edit_message(embed=card("🚨 HEIST FAILED",f"Alarm bei **{self.risk}% Risk**.",self.kind),view=self); return
            await self.progress(i); return
        if self.kind=="story": self.stage+=1; await self.progress(i,"Pfad: **Quarantine**")
    async def progress(self,i:discord.Interaction,status="✅ Phase geschafft.")->None:
        limit=4 if self.kind=="heist" else 3
        if self.stage>=limit:
            for x in self.children:x.disabled=True
            self.cog.sessions.pop(self.sid,None); extra=f"\nLoot **{self.loot}** · Risk **{self.risk}%**" if self.kind=="heist" else ""
            await i.response.edit_message(embed=card(f"✅ {self.kind.upper()} COMPLETE",self.ending+extra,self.kind),view=self)
        else: await i.response.edit_message(embed=self.render(status),view=self)
    async def on_timeout(self)->None: self.cog.sessions.pop(self.sid,None)


class RankButton(discord.ui.Button):
    def __init__(self,n:int)->None: super().__init__(label=f"#{n}",style=discord.ButtonStyle.secondary); self.n=n
    async def callback(self,i:discord.Interaction)->None:
        v:BlindRank=self.view  # type: ignore
        if i.user.id!=v.user.id or self.n in v.ranks: await i.response.send_message("Nicht möglich.",ephemeral=True); return
        v.ranks[self.n]=v.items[v.idx]; v.idx+=1; self.disabled=True
        if v.idx>=5:
            for x in v.children:x.disabled=True
        await i.response.edit_message(embed=v.render(),view=v)


class BlindRank(discord.ui.View):
    POOL=["Pizza","Döner","Pasta","Kaffee","Minecraft","Discord","GitHub","Spotify","YouTube","Netflix"]
    def __init__(self,user:discord.User)->None:
        super().__init__(timeout=300); self.user=user; self.items=random.sample(self.POOL,5); self.idx=0; self.ranks={}
        for n in range(1,6): self.add_item(RankButton(n))
    def render(self)->discord.Embed:
        if self.idx>=5:return card("🙈 BLIND RANK · Ergebnis","\n".join(f"**#{n}** {self.ranks[n]}" for n in sorted(self.ranks)))
        return card("🙈 BLIND RANK",f"Item **{self.idx+1}/5**\n\n# {self.items[self.idx]}\n\nJetzt ranken – das nächste Item kennst du noch nicht.")


class WYR(discord.ui.View):
    def __init__(self,cog:"ArcadeSuite",gid:int,key:str,a:str,b:str)->None:
        super().__init__(timeout=600); self.cog=cog; self.gid=gid; self.key=key; self.a_text=a; self.b_text=b
        self.a.label=a[:80]; self.b.label=b[:80]
    async def vote(self,i:discord.Interaction,ch:str)->None:
        await self.cog.bot.database.execute("""INSERT INTO arcade_wyr_votes(guild_id,question_key,user_id,choice) VALUES(?,?,?,?)
        ON CONFLICT(guild_id,question_key,user_id) DO UPDATE SET choice=excluded.choice,updated_at=CURRENT_TIMESTAMP""",(self.gid,self.key,i.user.id,ch))
        rows=await self.cog.bot.database.fetchall("SELECT choice,COUNT(*) c FROM arcade_wyr_votes WHERE guild_id=? AND question_key=? GROUP BY choice",(self.gid,self.key))
        c={"A":0,"B":0}
        for r in rows:c[str(r["choice"])]=int(r["c"])
        t=max(1,sum(c.values())); await i.response.send_message(f"🅰️ **{c['A']/t*100:.0f}%** · 🅱️ **{c['B']/t*100:.0f}%**",ephemeral=True)
    @discord.ui.button(label="A",style=discord.ButtonStyle.primary)
    async def a(self,i:discord.Interaction,_:discord.ui.Button)->None: await self.vote(i,"A")
    @discord.ui.button(label="B",style=discord.ButtonStyle.danger)
    async def b(self,i:discord.Interaction,_:discord.ui.Button)->None: await self.vote(i,"B")


class Hotseat(discord.ui.View):
    Q=["Welches Game könntest du blind spielen?","Unnötigstes Talent?","Welche App würdest du löschen?","Welches Bot-Feature fehlt?","Chaotischster Discord-Moment?"]
    def __init__(self,target:discord.Member)->None: super().__init__(timeout=60); self.target=target; self.q=random.sample(self.Q,5); self.n=0; self.start=time.monotonic()
    def render(self)->discord.Embed:return card("🔥 HOT SEAT",f"{self.target.mention}\n\n**Q{self.n+1}/5** · {self.q[self.n]}\n\n⏱️ ca. **{max(0,60-int(time.monotonic()-self.start))}s**")
    @discord.ui.button(label="Next",emoji="⏭️",style=discord.ButtonStyle.primary)
    async def nxt(self,i:discord.Interaction,b:discord.ui.Button)->None:
        if i.user.id!=self.target.id: await i.response.send_message("Nur Hotseat-Spieler.",ephemeral=True); return
        self.n+=1
        if self.n>=5: b.disabled=True; await i.response.edit_message(embed=card("🔥 HOT SEAT COMPLETE",f"{self.target.mention} schafft alle Fragen."),view=self)
        else: await i.response.edit_message(embed=self.render(),view=self)


class ArcadeSuite(commands.GroupCog,group_name="arcade",group_description="Advanced Games, Co-op, Party und Arcade Seasons"):
    def __init__(self,bot:commands.Bot)->None:
        self.bot=bot; self.stats=Stats(bot); self.sessions={}; self.recent={}
    async def cog_load(self)->None: await self.stats.ensure()
    async def pair(self,i:discord.Interaction,u:discord.Member):
        if not isinstance(i.user,discord.Member) or u.bot or u.id==i.user.id:
            await i.response.send_message("Wähle einen anderen menschlichen Spieler.",ephemeral=True); return None
        return i.user,u
    async def launch(self,i:discord.Interaction,v:Duel)->None:
        self.sessions[v.sid]=v; await i.response.send_message(embed=v.render(),view=v); v.message=await i.original_response()
    async def scenario(self,i:discord.Interaction,kind:str)->None:
        if not isinstance(i.user,discord.Member): return
        v=Scenario(self,i.user,kind); self.sessions[v.sid]=v; await i.response.send_message(embed=v.render(),view=v)
    @app_commands.command(name="menu",description="Öffnet die komplette Raspberry Arcade.")
    async def menu(self,i:discord.Interaction)->None:
        await i.response.send_message(embed=card("🎮 RASPBERRY ARCADE",
        "**Duelle** · battleship · cipherduel · blackjack · territory · wordchain · reactionbattle\n"
        "**Co-op** · escape · bossfight · heist · detective · story\n"
        "**Party** · blindrank · wouldyourather · hotseat · mysterybox\n"
        "**Meta** · achievementhunt · season · rivalry · rematch · spectate\n\n"
        "Alles liegt unter `/arcade …`, damit das Discord-Limit für Top-Level-Commands sauber bleibt."),ephemeral=True)
    @app_commands.command(name="battleship",description="2-Spieler-Schiffeversenken mit privaten Boards.")
    async def battleship(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,Battleship(self,*p))
    @app_commands.command(name="cipherduel",description="Cipher-Duell: erster korrekter Klartext gewinnt.")
    async def cipherduel(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,Cipher(self,*p))
    @app_commands.command(name="blackjack",description="Zwei Spieler gegen dieselbe Dealer-Hand.")
    async def blackjack(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,Blackjack(self,*p))
    @app_commands.command(name="territory",description="Strategisches 4×4-Gebietsduell.")
    async def territory(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,Territory(self,*p))
    @app_commands.command(name="wordchain",description="Live-Wortketten-Duell.")
    async def wordchain(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,WordChain(self,*p))
    @app_commands.command(name="reactionbattle",description="Reaktionsduell mit False-Start.")
    async def reactionbattle(self,i:discord.Interaction,gegner:discord.Member)->None:
        p=await self.pair(i,gegner)
        if p: await self.launch(i,Reaction(self,*p))
    @app_commands.command(name="escape",description="Kooperativer Mini-Escape-Room.")
    async def escape(self,i:discord.Interaction)->None: await self.scenario(i,"escape")
    @app_commands.command(name="bossfight",description="Öffentlicher Co-op-Bossfight.")
    async def bossfight(self,i:discord.Interaction)->None: await self.scenario(i,"bossfight")
    @app_commands.command(name="heist",description="Kooperativer Heist mit Risk/Reward.")
    async def heist(self,i:discord.Interaction)->None: await self.scenario(i,"heist")
    @app_commands.command(name="detective",description="Kooperativer Mystery-Fall.")
    async def detective(self,i:discord.Interaction)->None: await self.scenario(i,"detective")
    @app_commands.command(name="story",description="Community baut gemeinsam eine verzweigte Story.")
    async def story(self,i:discord.Interaction)->None: await self.scenario(i,"story")
    @app_commands.command(name="blindrank",description="Ranke fünf Dinge blind.")
    async def blindrank(self,i:discord.Interaction)->None:
        v=BlindRank(i.user); await i.response.send_message(embed=v.render(),view=v)
    @app_commands.command(name="wouldyourather",description="Entweder-oder mit Serverstatistik.")
    @app_commands.guild_only()
    async def wouldyourather(self,i:discord.Interaction)->None:
        a,b=random.choice([("Nie wieder Musik","Nie wieder Games"),("Teleportieren","Gedanken lesen"),("Darkmode","Lightmode"),
                           ("Perfektes Gedächtnis","Nie wieder müde"),("Serverraum","Cloud-Budget")])
        v=WYR(self,int(i.guild_id or 0),f"{a}|{b}",a,b); await i.response.send_message(embed=card("🤔 WOULD YOU RATHER",f"🅰️ **{a}**\n\nODER\n\n🅱️ **{b}**"),view=v)
    @app_commands.command(name="hotseat",description="60-Sekunden-Hotseat mit fünf Fragen.")
    @app_commands.guild_only()
    async def hotseat(self,i:discord.Interaction,person:discord.Member|None=None)->None:
        if not i.guild:return
        target=person or random.choice([m for m in i.guild.members if not m.bot])
        v=Hotseat(target); await i.response.send_message(embed=v.render(),view=v)
    @app_commands.command(name="mysterybox",description="Einmal täglich eine Mystery Box.")
    @app_commands.guild_only()
    async def mysterybox(self,i:discord.Interaction)->None:
        gid=int(i.guild_id or 0); day=datetime.now(UTC).strftime("%Y-%m-%d")
        old=await self.bot.database.fetchone("SELECT reward FROM arcade_daily_boxes WHERE guild_id=? AND user_id=? AND day_key=?",(gid,i.user.id,day))
        if old: await i.response.send_message(f"Heute bereits geöffnet: **{old['reward']}**",ephemeral=True); return
        if random.random()<.65:
            xp=random.randint(15,60); reward=f"+{xp} XP"
            await self.bot.database.execute("""INSERT INTO xp_profiles(guild_id,user_id,xp) VALUES(?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=xp+excluded.xp,updated_at=CURRENT_TIMESTAMP""",(gid,i.user.id,xp))
        else: reward=random.choice(["Challenge · Gewinne ein Match","Challenge · Löse einen Cipher","LEGENDARY · Hidden Hunt Bonus"])
        await self.bot.database.execute("INSERT INTO arcade_daily_boxes(guild_id,user_id,day_key,reward) VALUES(?,?,?,?)",(gid,i.user.id,day,reward))
        await i.response.send_message(embed=card("🎁 MYSTERY BOX",f"{i.user.mention}\n\n# {reward}"))
    @app_commands.command(name="achievementhunt",description="Sucht versteckte Arcade-Achievements.")
    @app_commands.guild_only()
    async def achievementhunt(self,i:discord.Interaction)->None:
        gid=int(i.guild_id or 0); uid=i.user.id
        rows=await self.bot.database.fetchall("SELECT game,played,wins,best_streak FROM game_stats WHERE guild_id=? AND user_id=?",(gid,uid))
        played=sum(int(r["played"]) for r in rows); wins=sum(int(r["wins"]) for r in rows); games=sum(int(r["played"])>0 for r in rows); best=max([int(r["best_streak"]) for r in rows] or [0])
        c=await self.bot.database.fetchone("SELECT COUNT(*) c FROM command_usage WHERE guild_id=? AND user_id=?",(gid,uid)); commands=int(c["c"] if c else 0)
        cond={"first":(played>=1,"First Contact"),"ten":(played>=10,"Arcade Regular"),"genres":(games>=5,"Genre Hopper"),"streak":(best>=3,"On Fire"),"wins":(wins>=10,"Double Digits"),"power":(commands>=100,"Power User")}
        new=[]
        for k,(ok,title) in cond.items():
            if ok and not await self.bot.database.fetchone("SELECT 1 FROM arcade_hidden_unlocks WHERE guild_id=? AND user_id=? AND achievement_key=?",(gid,uid,k)):
                await self.bot.database.execute("INSERT INTO arcade_hidden_unlocks(guild_id,user_id,achievement_key) VALUES(?,?,?)",(gid,uid,k)); new.append(title)
        unlocked=await self.bot.database.fetchall("SELECT achievement_key FROM arcade_hidden_unlocks WHERE guild_id=? AND user_id=?",(gid,uid)); names={k:v[1] for k,v in cond.items()}
        text=("\n".join(f"🏆 **{names.get(str(r['achievement_key']),r['achievement_key'])}**" for r in unlocked) or "Noch nichts entdeckt.")
        if new:text="✨ Neu: **"+", ".join(new)+"**\n\n"+text
        await i.response.send_message(embed=card("🕵️ ACHIEVEMENT HUNT",text),ephemeral=True)
    @app_commands.command(name="season",description="Aktuelle monatliche Arcade-Season.")
    @app_commands.guild_only()
    async def season(self,i:discord.Interaction)->None:
        month=datetime.now(UTC).strftime("%Y-%m"); gid=int(i.guild_id or 0)
        rows=await self.bot.database.fetchall("""SELECT winner_id,COUNT(*) wins FROM arcade_results WHERE guild_id=? AND winner_id IS NOT NULL
        AND substr(created_at,1,7)=? GROUP BY winner_id ORDER BY wins DESC LIMIT 10""",(gid,month))
        text="\n".join(f"**#{n}** <@{r['winner_id']}> · **{r['wins']} Wins**" for n,r in enumerate(rows,1)) or "Noch keine Ergebnisse."
        await i.response.send_message(embed=card(f"🏁 ARCADE SEASON · {month}",text))
    @app_commands.command(name="rivalry",description="Direkter Head-to-Head-Verlauf.")
    @app_commands.guild_only()
    async def rivalry(self,i:discord.Interaction,gegner:discord.Member)->None:
        gid=int(i.guild_id or 0); a,b=i.user.id,gegner.id
        rows=await self.bot.database.fetchall("""SELECT game,winner_id,COUNT(*) c FROM arcade_results WHERE guild_id=? AND
        ((player_a=? AND player_b=?) OR (player_a=? AND player_b=?)) GROUP BY game,winner_id""",(gid,a,b,b,a))
        wa=sum(int(r["c"]) for r in rows if r["winner_id"]==a); wb=sum(int(r["c"]) for r in rows if r["winner_id"]==b); d=sum(int(r["c"]) for r in rows if r["winner_id"] is None)
        games=Counter()
        for r in rows:games[str(r["game"])]+=int(r["c"])
        await i.response.send_message(embed=card("⚔️ RIVALRY",f"{i.user.mention} **{wa} — {wb}** {gegner.mention}\nDraws **{d}**\n\n"+("\n".join(f"`{g}` · {n}" for g,n in games.items()) or "Noch keine Matches.")))
    async def start_named(self,i:discord.Interaction,game:str,p1:discord.Member,p2:discord.Member)->bool:
        cls={"battleship":Battleship,"cipherduel":Cipher,"blackjack":Blackjack,"territory":Territory,"wordchain":WordChain,"reactionbattle":Reaction}.get(game)
        if not cls:return False
        await self.launch(i,cls(self,p1,p2));return True
    @app_commands.command(name="rematch",description="Letztes unterstütztes Duell erneut starten.")
    @app_commands.guild_only()
    async def rematch(self,i:discord.Interaction)->None:
        if not i.guild or int(i.guild_id or 0) not in self.recent: await i.response.send_message("Kein Rematch in dieser Session.",ephemeral=True); return
        game,a,b=self.recent[int(i.guild_id)]
        if i.user.id not in {a,b}: await i.response.send_message("Nur die letzten Spieler.",ephemeral=True); return
        p1,p2=i.guild.get_member(a),i.guild.get_member(b)
        if not p1 or not p2 or not await self.start_named(i,game,p1,p2):
            if not i.response.is_done():await i.response.send_message("Rematch nicht möglich.",ephemeral=True)
    @app_commands.command(name="spectate",description="Listet laufende Arcade-Sessions.")
    @app_commands.guild_only()
    async def spectate(self,i:discord.Interaction)->None:
        out=[]
        for sid,v in self.sessions.items():
            gid=getattr(v,"guild_id",None)
            if gid!=i.guild_id:continue
            game=getattr(v,"game",getattr(v,"kind",v.__class__.__name__))
            players=f"{v.p1.mention} vs {v.p2.mention}" if hasattr(v,"p1") else f"Team {len(getattr(v,'team',[]))}"
            out.append(f"🟣 `{sid}` **{game}** · {players}")
        await i.response.send_message(embed=card("👁️ LIVE ARCADE","\n".join(out[:20]) or "Keine laufenden Sessions."),ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArcadeSuite(bot))
