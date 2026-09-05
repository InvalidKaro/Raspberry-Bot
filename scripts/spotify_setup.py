from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
STATE_PATH = REPO_ROOT / "data" / "spotify_oauth_state.txt"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SCOPES = ("playlist-read-private", "playlist-read-collaborative")
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _quote_env(value: str) -> str:
    if not value:
        return ""
    if all(ch.isalnum() or ch in "-._:/" for ch in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_env_updates(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={_quote_env(remaining.pop(key))}")
                continue
        output.append(raw)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Spotify integration")
        for key, value in remaining.items():
            output.append(f"{key}={_quote_env(value)}")
    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _credentials(env: dict[str, str]) -> tuple[str, str]:
    return env.get("SPOTIFY_CLIENT_ID", "").strip(), env.get("SPOTIFY_CLIENT_SECRET", "").strip()


def _token_request(data: dict[str, str], client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Spotify HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Spotify-Verbindung fehlgeschlagen: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Spotify hat eine ungültige Token-Antwort geliefert.")
    return payload


def command_credentials(_: argparse.Namespace) -> int:
    env = _read_env()
    current_id, current_secret = _credentials(env)
    client_id = input(f"Spotify Client ID{f' [{current_id[:6]}…]' if current_id else ''}: ").strip() or current_id
    secret_prompt = "Spotify Client Secret"
    if current_secret:
        secret_prompt += " [Enter = vorhandenes behalten]"
    client_secret = getpass.getpass(secret_prompt + ": ").strip() or current_secret
    if not client_id or not client_secret:
        print("Client ID und Client Secret werden beide benötigt.", file=sys.stderr)
        return 2

    print("Teste Client-Credentials bei Spotify …")
    payload = _token_request({"grant_type": "client_credentials"}, client_id, client_secret)
    if not payload.get("access_token"):
        print("Spotify hat kein Access Token geliefert.", file=sys.stderr)
        return 3

    _write_env_updates(
        {
            "SPOTIFY_CLIENT_ID": client_id,
            "SPOTIFY_CLIENT_SECRET": client_secret,
            "SPOTIFY_REDIRECT_URI": env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI) or DEFAULT_REDIRECT_URI,
            "SPOTIFY_MARKET": env.get("SPOTIFY_MARKET", "DE") or "DE",
        }
    )
    print("OK: Client ID/Secret validiert und in .env gespeichert (Dateirechte 600, soweit unterstützt).")
    print("Nächster Schritt für Playlists: python scripts/spotify_setup.py auth-url")
    return 0


def command_test(_: argparse.Namespace) -> int:
    env = _read_env()
    client_id, client_secret = _credentials(env)
    if not client_id or not client_secret:
        print("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET fehlen in .env.", file=sys.stderr)
        return 2
    payload = _token_request({"grant_type": "client_credentials"}, client_id, client_secret)
    expires = int(payload.get("expires_in") or 0)
    print(f"OK: Spotify Client Credentials funktionieren; Token-Laufzeit {expires}s.")
    return 0


def command_auth_url(_: argparse.Namespace) -> int:
    env = _read_env()
    client_id, client_secret = _credentials(env)
    if not client_id or not client_secret:
        print("Erst Client ID/Secret einrichten: python scripts/spotify_setup.py credentials", file=sys.stderr)
        return 2
    redirect_uri = env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI
    state = secrets.token_urlsafe(24)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(state, encoding="utf-8")
    try:
        STATE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": " ".join(SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
            "show_dialog": "true",
        }
    )
    print("Öffne diese URL im Browser und erlaube den Playlist-Zugriff:\n")
    print(f"{AUTHORIZE_URL}?{query}\n")
    print(f"Spotify Redirect URI in deiner Developer-App muss EXAKT sein: {redirect_uri}")
    print("Nach der Freigabe kopierst du die komplette Callback-URL aus der Browserzeile und führst aus:")
    print("python scripts/spotify_setup.py exchange '<CALLBACK-URL>'")
    return 0


def command_exchange(args: argparse.Namespace) -> int:
    env = _read_env()
    client_id, client_secret = _credentials(env)
    if not client_id or not client_secret:
        print("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET fehlen.", file=sys.stderr)
        return 2
    redirect_uri = env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI
    parsed = urllib.parse.urlparse(args.callback_url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("error"):
        print(f"Spotify OAuth Fehler: {query['error'][0]}", file=sys.stderr)
        return 3
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    if not code:
        print("In der Callback-URL wurde kein OAuth-Code gefunden.", file=sys.stderr)
        return 3
    if STATE_PATH.exists():
        expected = STATE_PATH.read_text(encoding="utf-8").strip()
        if expected and state != expected:
            print("OAuth state stimmt nicht überein. Bitte auth-url erneut ausführen.", file=sys.stderr)
            return 4

    payload = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        client_id,
        client_secret,
    )
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token:
        print("Spotify hat keinen Refresh Token geliefert.", file=sys.stderr)
        return 5
    _write_env_updates({"SPOTIFY_REFRESH_TOKEN": refresh_token})
    try:
        STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    scope = str(payload.get("scope") or "")
    print("OK: Spotify User-OAuth eingerichtet; Refresh Token sicher in .env gespeichert.")
    print(f"Scopes: {scope or ', '.join(SCOPES)}")
    print("Bot danach neu starten: sudo systemctl restart raspberry-bot")
    return 0


def command_status(_: argparse.Namespace) -> int:
    env = _read_env()
    client_id, client_secret = _credentials(env)
    refresh = env.get("SPOTIFY_REFRESH_TOKEN", "").strip()
    redirect = env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI
    print(f"Client ID:      {'gesetzt' if client_id else 'FEHLT'}")
    print(f"Client Secret:  {'gesetzt' if client_secret else 'FEHLT'}")
    print(f"Refresh Token:  {'gesetzt' if refresh else 'FEHLT'}")
    print(f"Redirect URI:   {redirect}")
    print(f"Market:         {env.get('SPOTIFY_MARKET', 'DE') or 'DE'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Spotify API/OAuth Setup für Raspberry-Bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("credentials", help="Client ID/Secret sicher abfragen, testen und in .env speichern")
    sub.add_parser("test", help="Client Credentials gegen Spotify testen")
    sub.add_parser("auth-url", help="OAuth-URL für Playlist-Zugriff erzeugen")
    exchange = sub.add_parser("exchange", help="Spotify Callback-URL gegen Refresh Token tauschen")
    exchange.add_argument("callback_url", help="Komplette Redirect-/Callback-URL mit ?code=...&state=...")
    sub.add_parser("status", help="Lokalen Spotify-Konfigurationsstatus anzeigen")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "credentials": command_credentials,
        "test": command_test,
        "auth-url": command_auth_url,
        "exchange": command_exchange,
        "status": command_status,
    }
    try:
        return handlers[args.command](args)
    except (RuntimeError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
