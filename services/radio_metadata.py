from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

import aiohttp

USER_AGENT = "Raspberry-Bot-RadioMetadata/1.0"
MAX_REDIRECTS = 4
MAX_META_INTERVAL = 512 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=3, sock_connect=3, sock_read=5)

_STREAM_TITLE_RE = re.compile(r"StreamTitle=(?P<q>['\"])(?P<value>.*?)(?P=q);", re.IGNORECASE | re.DOTALL)
_STREAM_URL_RE = re.compile(r"StreamUrl=(?P<q>['\"])(?P<value>.*?)(?P=q);", re.IGNORECASE | re.DOTALL)


@dataclass(slots=True)
class RadioMetadata:
    stream_title: str = ""
    artist: str = ""
    track: str = ""
    stream_url: str = ""
    stream_name: str = ""
    stream_genre: str = ""
    bitrate_kbps: int | None = None
    codec: str = ""
    content_type: str = ""
    metadata_supported: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def split_stream_title(value: str) -> tuple[str, str]:
    clean = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if not clean:
        return "", ""

    for separator in (" - ", " – ", " — ", " | ", " • "):
        if separator in clean:
            artist, track = clean.split(separator, 1)
            artist = artist.strip(" -–—|•")
            track = track.strip(" -–—|•")
            if artist and track:
                return artist[:180], track[:220]
    return "", clean[:220]


def _clean_header(value: object, limit: int = 220) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()[:limit]


def _codec_from_content_type(value: str) -> str:
    content_type = value.split(";", 1)[0].strip().lower()
    mapping = {
        "audio/mpeg": "MP3",
        "audio/mp3": "MP3",
        "audio/aac": "AAC",
        "audio/aacp": "AAC+",
        "audio/x-aac": "AAC",
        "audio/ogg": "OGG",
        "application/ogg": "OGG",
        "audio/opus": "Opus",
        "audio/flac": "FLAC",
        "application/vnd.apple.mpegurl": "HLS",
        "application/x-mpegurl": "HLS",
        "audio/x-mpegurl": "HLS",
    }
    return mapping.get(content_type, content_type.upper()[:24] if content_type else "")


def _special_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def _assert_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Radio metadata requires a public HTTPS stream URL")

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Local radio metadata targets are not allowed")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _special_ip(literal):
            raise ValueError("Private/local radio metadata targets are not allowed")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Radio metadata DNS lookup failed: {exc}") from exc

    addresses: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            addresses.add(str(sockaddr[0]))
    if not addresses:
        raise ValueError("Radio metadata DNS lookup returned no addresses")

    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _special_ip(address):
            raise ValueError("Radio metadata target resolved to a private/local address")


def _decode_metadata(raw: bytes) -> str:
    payload = raw.rstrip(b"\x00").strip()
    if not payload:
        return ""
    for encoding in ("utf-8", "latin-1"):
        try:
            return payload.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace").strip()


def _parse_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    number = int(match.group(0))
    return number if number > 0 else None


async def fetch_radio_metadata(
    session: aiohttp.ClientSession,
    stream_url: str,
) -> RadioMetadata:
    current_url = str(stream_url or "").strip()
    result = RadioMetadata(stream_url=current_url)

    if not current_url:
        result.error = "Stream URL missing"
        return result

    headers = {
        "User-Agent": USER_AGENT,
        "Icy-MetaData": "1",
        "Accept": "*/*",
        "Connection": "close",
    }

    try:
        for redirect_index in range(MAX_REDIRECTS + 1):
            await _assert_public_https(current_url)
            async with session.get(
                current_url,
                headers=headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
                auto_decompress=False,
            ) as response:
                if 300 <= response.status < 400:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise RuntimeError(f"Radio stream redirect {response.status} has no Location header")
                    if redirect_index >= MAX_REDIRECTS:
                        raise RuntimeError("Radio stream redirected too many times")
                    current_url = urljoin(str(response.url), location)
                    continue

                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Radio metadata HTTP {response.status}")

                result.stream_url = str(response.url)
                result.stream_name = _clean_header(response.headers.get("icy-name"), 160)
                result.stream_genre = _clean_header(response.headers.get("icy-genre"), 120)
                result.bitrate_kbps = _parse_int(response.headers.get("icy-br"))
                result.content_type = _clean_header(response.headers.get("Content-Type"), 80)
                result.codec = _codec_from_content_type(result.content_type)

                metaint = _parse_int(response.headers.get("icy-metaint"))
                if metaint is None:
                    result.metadata_supported = False
                    return result
                if metaint > MAX_META_INTERVAL:
                    raise RuntimeError(f"ICY metadata interval too large: {metaint}")

                result.metadata_supported = True
                try:
                    await response.content.readexactly(metaint)
                    length_byte = await response.content.readexactly(1)
                except asyncio.IncompleteReadError as exc:
                    raise RuntimeError("Radio stream ended before ICY metadata arrived") from exc

                metadata_length = length_byte[0] * 16
                if metadata_length <= 0:
                    return result

                try:
                    raw_metadata = await response.content.readexactly(metadata_length)
                except asyncio.IncompleteReadError as exc:
                    raise RuntimeError("Radio stream ended while reading ICY metadata") from exc

                metadata_text = _decode_metadata(raw_metadata)
                if not metadata_text:
                    return result

                title_match = _STREAM_TITLE_RE.search(metadata_text)
                if title_match:
                    result.stream_title = _clean_header(title_match.group("value"), 400)
                    result.artist, result.track = split_stream_title(result.stream_title)

                url_match = _STREAM_URL_RE.search(metadata_text)
                if url_match:
                    metadata_url = _clean_header(url_match.group("value"), 500)
                    if metadata_url:
                        result.stream_url = metadata_url
                return result

        raise RuntimeError("Radio stream redirect loop")
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:500]
        return result
