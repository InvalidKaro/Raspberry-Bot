from __future__ import annotations

import io
import textwrap
from datetime import UTC, datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

ACCENT = (139, 92, 246)
BG = (17, 21, 28)
CARD = (28, 34, 45)
TEXT = (244, 246, 250)
MUTED = (153, 163, 177)

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default()


def _gradient(width: int, height: int, start=(17, 21, 28), end=(40, 31, 58)) -> Image.Image:
    image = Image.new("RGB", (width, height), start)
    px = image.load()
    for y in range(height):
        t = y / max(1, height - 1)
        row = tuple(int(start[i] * (1 - t) + end[i] * t) for i in range(3))
        for x in range(width):
            px[x, y] = row
    return image


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 8) -> list[str]:
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines)) < len(" ".join(words)):
        lines[-1] = lines[-1].rstrip(" .") + "…"
    return lines


def _save(image: Image.Image, name: str) -> discord.File:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return discord.File(out, filename=name)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


class VisualSuite(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="quoteimage", description="Erstellt eine hochwertige Quote-Karte als PNG.")
    async def quoteimage(self, interaction: discord.Interaction, text: str, autor: str = "") -> None:
        await interaction.response.defer()
        image = _gradient(1400, 800)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((100, 90, 1300, 710), radius=42, fill=CARD, outline=(58, 48, 77), width=2)
        draw.rectangle((100, 90, 116, 710), fill=ACCENT)
        quote_font = _font(54, bold=True)
        author_font = _font(30)
        mark_font = _font(110, bold=True)
        draw.text((165, 118), "“", font=mark_font, fill=ACCENT)
        lines = _wrap(draw, text[:1200], quote_font, 1020, max_lines=7)
        y = 265
        for line in lines:
            draw.text((185, y), line, font=quote_font, fill=TEXT)
            y += 72
        author = autor.strip() or str(interaction.user.display_name)
        draw.text((185, 635), f"— {author[:100]}", font=author_font, fill=MUTED)
        draw.text((1070, 660), "Raspberry-Bot", font=_font(20, bold=True), fill=(110, 101, 128))
        await interaction.followup.send(file=_save(image, "quote.png"))

    @app_commands.command(name="poster", description="Erstellt ein Event-/Announcement-Poster.")
    async def poster(self, interaction: discord.Interaction, titel: str, untertitel: str = "", datum: str = "") -> None:
        await interaction.response.defer()
        image = _gradient(1200, 1500, (11, 14, 20), (49, 33, 78))
        draw = ImageDraw.Draw(image)
        draw.ellipse((770, -180, 1370, 420), fill=(87, 54, 132))
        draw.ellipse((-230, 980, 430, 1640), fill=(45, 82, 142))
        draw.rounded_rectangle((80, 80, 1120, 1420), radius=46, outline=(99, 78, 134), width=3)
        draw.text((120, 130), "EVENT / ANNOUNCEMENT", font=_font(26, bold=True), fill=(186, 164, 233))
        title_font = _font(82, bold=True)
        lines = _wrap(draw, titel[:300], title_font, 920, max_lines=5)
        y = 360
        for line in lines:
            draw.text((120, y), line, font=title_font, fill=TEXT)
            y += 105
        if untertitel.strip():
            sub_font = _font(36)
            for line in _wrap(draw, untertitel[:700], sub_font, 900, max_lines=5):
                draw.text((120, y + 30), line, font=sub_font, fill=(202, 207, 218))
                y += 52
        if datum.strip():
            draw.rounded_rectangle((120, 1220, 1080, 1330), radius=24, fill=(25, 29, 38))
            draw.text((160, 1247), datum[:120], font=_font(38, bold=True), fill=(226, 216, 250))
        draw.text((120, 1362), f"created by {interaction.user.display_name}", font=_font(22), fill=MUTED)
        await interaction.followup.send(file=_save(image, "poster.png"))

    @app_commands.command(name="banner", description="Erstellt einen Server-/Abteilungsbanner.")
    async def banner(self, interaction: discord.Interaction, titel: str, untertitel: str = "") -> None:
        await interaction.response.defer()
        image = _gradient(1600, 600, (10, 15, 24), (55, 34, 92))
        draw = ImageDraw.Draw(image)
        for x in (1010, 1140, 1270, 1400):
            draw.rounded_rectangle((x, -80, x + 72, 680), radius=36, fill=(64, 46, 98))
        draw.rounded_rectangle((80, 72, 940, 528), radius=38, fill=(21, 26, 36), outline=(77, 62, 104), width=2)
        draw.text((125, 120), "RASPBERRY / COMMUNITY", font=_font(24, bold=True), fill=(183, 160, 231))
        title_font = _font(72, bold=True)
        y = 225
        for line in _wrap(draw, titel[:220], title_font, 750, max_lines=3):
            draw.text((125, y), line, font=title_font, fill=TEXT)
            y += 88
        if untertitel.strip():
            draw.text((127, 450), untertitel[:180], font=_font(28), fill=MUTED)
        await interaction.followup.send(file=_save(image, "banner.png"))

    async def _read_attachment(self, attachment: discord.Attachment | None) -> Image.Image | None:
        if attachment is None:
            return None
        if attachment.size > 8 * 1024 * 1024:
            raise ValueError("Bild ist größer als 8 MB.")
        if attachment.content_type and not attachment.content_type.startswith("image/"):
            raise ValueError("Der Anhang muss ein Bild sein.")
        data = await attachment.read()
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image.load()
            return image
        except Exception as exc:
            raise ValueError("Das Bild konnte nicht gelesen werden.") from exc

    @app_commands.command(name="meme", description="Erstellt ein Meme aus einem Bild oder einer neutralen Vorlage.")
    async def meme(self, interaction: discord.Interaction, oben: str, unten: str = "", bild: discord.Attachment | None = None) -> None:
        await interaction.response.defer()
        try:
            source = await self._read_attachment(bild)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if source is None:
            source = _gradient(1200, 900, (28, 30, 36), (61, 62, 76))
        image = ImageOps.fit(source, (1200, 900), method=Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        font = _font(62, bold=True)

        def draw_caption(text: str, y: int, anchor: str) -> None:
            lines = _wrap(draw, text[:350].upper(), font, 1080, max_lines=3)
            offset = 0
            for line in lines:
                box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
                width = box[2] - box[0]
                x = (1200 - width) // 2
                yy = y + offset if anchor == "top" else y - (len(lines) - offset // 78) * 78
                draw.text((x, yy), line, font=font, fill="white", stroke_width=5, stroke_fill="black")
                offset += 78

        draw_caption(oben, 35, "top")
        if unten.strip():
            lines = _wrap(draw, unten[:350].upper(), font, 1080, max_lines=3)
            start_y = 900 - 55 - len(lines) * 78
            for index, line in enumerate(lines):
                box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
                x = (1200 - (box[2] - box[0])) // 2
                draw.text((x, start_y + index * 78), line, font=font, fill="white", stroke_width=5, stroke_fill="black")
        await interaction.followup.send(file=_save(image, "meme.png"))

    @app_commands.command(name="avatarstyle", description="Verwandelt einen Discord-Avatar in eine stylische Profilkarte.")
    async def avatarstyle(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        await interaction.response.defer()
        url = target.display_avatar.with_size(512).with_format("png").url
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    avatar_bytes = await response.read()
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        except Exception:
            await interaction.followup.send("Avatar konnte nicht geladen werden.", ephemeral=True)
            return
        image = _gradient(1400, 800, (12, 17, 25), (46, 31, 72))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((80, 80, 1320, 720), radius=46, fill=(22, 27, 37), outline=(75, 58, 103), width=2)
        avatar = ImageOps.fit(avatar, (430, 430), method=Image.Resampling.LANCZOS)
        mask = _rounded_mask((430, 430), 72)
        image.paste(avatar, (135, 185), mask)
        draw.rounded_rectangle((615, 180, 1240, 615), radius=34, fill=(28, 34, 45))
        display = getattr(target, "display_name", str(target))
        draw.text((665, 235), display[:34], font=_font(52, bold=True), fill=TEXT)
        draw.text((667, 315), f"@{target.name}"[:48], font=_font(28), fill=(171, 151, 215))
        if isinstance(target, discord.Member):
            roles = [role.name for role in reversed(target.roles[1:]) if not role.managed][:4]
            role_text = " · ".join(roles) if roles else "Member"
            draw.text((667, 395), role_text[:62], font=_font(25), fill=MUTED)
            draw.text((667, 468), f"Joined {target.joined_at.strftime('%d.%m.%Y') if target.joined_at else 'unknown'}", font=_font(23), fill=(135, 145, 160))
        draw.text((667, 548), f"ID {target.id}", font=_font(22), fill=(112, 120, 134))
        draw.text((135, 660), datetime.now(UTC).strftime("Raspberry Profile · %Y-%m-%d"), font=_font(20, bold=True), fill=(115, 105, 135))
        await interaction.followup.send(file=_save(image, "avatarstyle.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VisualSuite(bot))
