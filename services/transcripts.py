from __future__ import annotations

import html
from io import BytesIO

import discord

from config import settings


def _message_html(message: discord.Message) -> str:
    author = html.escape(str(message.author))
    avatar = html.escape(message.author.display_avatar.url)
    content = html.escape(message.content or "")
    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    attachments = "".join(
        f'<div class="attachment"><a href="{html.escape(a.url)}">{html.escape(a.filename)}</a></div>'
        for a in message.attachments
    )
    embed_count = f'<div class="meta">Embeds: {len(message.embeds)}</div>' if message.embeds else ""
    return (
        '<article class="message">'
        f'<img class="avatar" src="{avatar}" alt="avatar">'
        '<div class="body">'
        f'<div><strong>{author}</strong> <span class="time">{timestamp}</span></div>'
        f'<div class="content">{content}</div>{attachments}{embed_count}'
        '</div></article>'
    )


async def build_html_transcript(channel: discord.TextChannel, ticket: dict[str, object]) -> BytesIO:
    messages: list[discord.Message] = []
    async for message in channel.history(limit=settings.transcript_max_messages, oldest_first=True):
        messages.append(message)

    body = "\n".join(_message_html(message) for message in messages)
    ticket_id = int(ticket["id"])
    subject = html.escape(str(ticket.get("subject") or ""))
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ticket #{ticket_id}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#111318;color:#e8eaed;margin:0;padding:32px}}
.wrap{{max-width:1000px;margin:auto}}
h1{{margin-bottom:4px}}
.sub{{color:#9da3ae;margin-bottom:28px}}
.message{{display:flex;gap:14px;padding:14px;border-bottom:1px solid #292d34}}
.avatar{{width:42px;height:42px;border-radius:50%}}
.body{{flex:1}}
.time,.meta{{color:#8d94a0;font-size:12px}}
.content{{white-space:pre-wrap;margin-top:5px}}
a{{color:#62a8ff}}
</style>
</head>
<body><div class="wrap"><h1>Ticket #{ticket_id}</h1>
<div class="sub">Subject: {subject} • Messages: {len(messages)}</div>
{body}
</div></body></html>"""
    output = BytesIO(document.encode("utf-8"))
    output.seek(0)
    return output
