from __future__ import annotations
import asyncio, logging, queue, re
from dataclasses import dataclass
import discord

@dataclass(slots=True)
class LogItem:
    level:int; text:str; error_id:str|None=None

class QueueHandler(logging.Handler):
    def __init__(self,q): super().__init__(logging.DEBUG); self.q=q; self.dropped=0
    def emit(self,record):
        if record.name.startswith(("services.discord_log_forwarder","discord.http")): return
        try:
            text=self.format(record)
            m=re.search(r"\[([A-Z0-9]{8})\]",text)
            item=LogItem(record.levelno,text,m.group(1) if m else None)
            try:self.q.put_nowait(item)
            except queue.Full:self.dropped+=1
        except Exception:self.handleError(record)

class DiscordLogForwarder:
    def __init__(self,bot):
        self.bot=bot; self.q=queue.Queue(maxsize=3000); self.task=None; self.config={}; self.last_send={}
        self.handler=QueueHandler(self.q); self.handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s","%Y-%m-%d %H:%M:%S"))
    async def load(self):
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS discord_log_routes(
        guild_id INTEGER PRIMARY KEY, info_channel_id INTEGER, warning_channel_id INTEGER, error_channel_id INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        rows=await self.bot.database.fetchall("SELECT * FROM discord_log_routes WHERE enabled=1")
        self.config={int(r["guild_id"]):dict(r) for r in rows}
    async def start(self):
        await self.load(); logging.getLogger().addHandler(self.handler)
        self.task=asyncio.create_task(self.run(),name="discord-log-forwarder")
    async def stop(self):
        logging.getLogger().removeHandler(self.handler)
        if self.task:self.task.cancel()
    def route_for(self,cfg,level):
        if level>=logging.ERROR:return cfg.get("error_channel_id") or cfg.get("warning_channel_id") or cfg.get("info_channel_id")
        if level>=logging.WARNING:return cfg.get("warning_channel_id") or cfg.get("info_channel_id")
        return cfg.get("info_channel_id")
    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(2)
            items=[]
            for _ in range(500):
                try:items.append(self.q.get_nowait())
                except queue.Empty:break
            if not items:continue
            for gid,cfg in list(self.config.items()):
                buckets={}
                for item in items:
                    cid=self.route_for(cfg,item.level)
                    if cid:buckets.setdefault(int(cid),[]).append(item)
                for cid,group in buckets.items():
                    ch=self.bot.get_channel(cid)
                    if not isinstance(ch,discord.TextChannel):continue
                    text="\n".join(x.text for x in group)
                    parts=[text[i:i+1750] for i in range(0,len(text),1750)][:4]
                    for part in parts:
                        highest=max(x.level for x in group)
                        err=next((x.error_id for x in group if x.error_id),None)
                        title=("🔴 ERROR" if highest>=logging.ERROR else "🟠 WARNING" if highest>=logging.WARNING else "🔵 INFO")
                        if err:title+=f" · {err}"
                        embed=discord.Embed(title=title,description=f"```text\n{part}\n```",color=0xED4245 if highest>=40 else 0xFEE75C if highest>=30 else 0x3498DB)
                        try:await ch.send(embed=embed,silent=True,allowed_mentions=discord.AllowedMentions.none())
                        except discord.HTTPException:break
