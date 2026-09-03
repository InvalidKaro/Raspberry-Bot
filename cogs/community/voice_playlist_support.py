from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

PLAYLIST_TIMEOUT_SECONDS = 7
PLAYLIST_MAX_BYTES = 64 * 1024
USER_AGENT = "Raspberry-Bot-Radio/1.0"


def _validate_public_https(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Playlist-Ziele müssen öffentliche HTTPS-URLs sein.")

    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Lokale Playlist-Ziele sind nicht erlaubt.")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and (
        literal.is_private
        or literal.is_loopback
        or literal.is_link_local
        or literal.is_reserved
        or literal.is_multicast
    ):
        raise ValueError("Private/lokale Playlist-Ziele sind nicht erlaubt.")

    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Playlist-Host konnte nicht aufgelöst werden: {host}") from exc
    for item in addresses:
        try:
            address = ipaddress.ip_address(item[4][0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError("Playlist-Host löst auf eine private/lokale Adresse auf.")
    return url


def _playlist_kind(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return None  # HLS: FFmpeg versteht dieses Format direkt.
    if path.endswith(".m3u"):
        return "m3u"
    if path.endswith(".pls"):
        return "pls"
    return None


def _parse_playlist(body: str, *, base_url: str, kind: str) -> str:
    candidates: list[str] = []
    if kind == "pls":
        for raw in body.splitlines():
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().lower().startswith("file") and value.strip():
                candidates.append(value.strip())
    else:
        for raw in body.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                candidates.append(line)

    for candidate in candidates:
        target = urljoin(base_url, candidate)
        try:
            return _validate_public_https(target)
        except ValueError:
            continue
    raise ValueError("Die Playlist enthält keinen nutzbaren öffentlichen HTTPS-Audiostream.")


def resolve_radio_url(url: str) -> str:
    """Resolve simple M3U/PLS indirection before FFmpeg sees the input.

    FFmpeg handles HLS (.m3u8) itself, but classic internet-radio .m3u/.pls
    files are often only text files containing the real MP3 stream URL. Feeding
    those text files to FFmpeg as audio can result in AVERROR_INVALIDDATA, which
    appears in the shell as return code 183.
    """
    source = _validate_public_https(url)
    kind = _playlist_kind(source)
    if kind is None:
        return source

    request = Request(source, headers={"User-Agent": USER_AGENT, "Accept": "audio/x-mpegurl,audio/x-scpls,text/plain,*/*"})
    try:
        with urlopen(request, timeout=PLAYLIST_TIMEOUT_SECONDS) as response:  # noqa: S310 - URL is validated above
            final_url = _validate_public_https(response.geturl())
            raw = response.read(PLAYLIST_MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Radio-Playlist konnte nicht geladen werden: {exc}") from exc

    if len(raw) > PLAYLIST_MAX_BYTES:
        raise ValueError("Radio-Playlist ist ungewöhnlich groß und wurde abgelehnt.")
    text = raw.decode("utf-8-sig", errors="replace")
    resolved = _parse_playlist(text, base_url=final_url, kind=kind)
    logger.info("Resolved radio playlist %s -> %s", source, resolved)
    return resolved


class VoicePlaylistSupport(commands.Cog):
    """Compatibility layer for classic internet-radio playlist URLs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        voice = bot.get_cog("VoiceSuite")
        if voice is None:
            raise RuntimeError("VoiceSuite must be loaded before VoicePlaylistSupport")
        self.voice = voice
        self._original_remote_source = voice._remote_source
        voice._remote_source = self._remote_source

    def _remote_source(self, url: str) -> discord.AudioSource:
        resolved = resolve_radio_url(url)
        return self._original_remote_source(resolved)

    def cog_unload(self) -> None:
        current = getattr(self.voice, "_remote_source", None)
        if current == self._remote_source:
            self.voice._remote_source = self._original_remote_source


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoicePlaylistSupport(bot))
