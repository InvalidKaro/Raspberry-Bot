from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import settings

_render_limit = asyncio.Semaphore(max(settings.image_render_concurrency, 1))


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render(member: discord.Member, avatar_bytes: bytes) -> BytesIO:
    width, height = 1000, 420
    image = Image.new("RGB", (width, height), (17, 19, 24))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=32, fill=(34, 37, 43))
    draw.rounded_rectangle((40, 40, 280, height - 40), radius=28, fill=(27, 30, 35))

    with Image.open(BytesIO(avatar_bytes)).convert("RGB") as avatar:
        avatar = ImageOps.fit(avatar, (180, 180))
        mask = Image.new("L", (180, 180), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 180, 180), fill=255)
        image.paste(avatar, (70, 80), mask)

    draw.text((330, 70), member.display_name[:30], font=_font(36, True), fill=(247, 248, 250))
    draw.text((330, 118), str(member), font=_font(18), fill=(162, 168, 179))
    joined = member.joined_at.strftime("%d.%m.%Y") if member.joined_at else "Unknown"
    created = member.created_at.strftime("%d.%m.%Y")
    top_role = member.top_role.name if member.top_role != member.guild.default_role else "None"
    facts = [
        ("SERVER JOIN", joined),
        ("ACCOUNT", created),
        ("ROLES", str(max(len(member.roles) - 1, 0))),
        ("TOP ROLE", top_role[:24]),
        ("USER ID", str(member.id)),
    ]
    x, y = 330, 195
    for index, (label, value) in enumerate(facts):
        col = index % 2
        row = index // 2
        px = x + col * 300
        py = y + row * 82
        draw.text((px, py), label, font=_font(14, True), fill=(105, 112, 125))
        draw.text((px, py + 24), value, font=_font(20, True), fill=(220, 224, 230))

    draw.text((70, 295), "Raspberry-Bot", font=_font(20, True), fill=(87, 242, 135))
    draw.text((70, 326), "Community Profile", font=_font(15), fill=(145, 151, 162))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    image.close()
    output.seek(0)
    return output


async def render_profile_card(member: discord.Member) -> BytesIO:
    async with _render_limit:
        avatar_bytes = await member.display_avatar.with_size(256).read()
        return await asyncio.to_thread(_render, member, avatar_bytes)
