from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from aiohttp import web

from .workspace_editor_routes import RESOURCES, ResourceSpec

RESOURCES.setdefault("github-subscriptions", ResourceSpec("github_subscriptions", "GitHub Subscriptions", "Integrationen", description="Repository → Discord-Channel Routing und Event-Filter."))
RESOURCES.setdefault("github-events", ResourceSpec("github_events", "GitHub Deliveries", "Integrationen", can_create=False, can_update=False, description="Signierte, zuletzt empfangene GitHub-Webhook-Events."))
RESOURCES.setdefault("game-stats", ResourceSpec("game_stats", "Game Stats", "Community", can_create=False, can_update=False, description="Statistiken der 2-Spieler-Arcade."))


def _db_path(config) -> Path:
    configured = Path(config.database_path)
    if configured.is_absolute():
        return configured
    return Path(config.repo_path) / configured


def _connect(config) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS github_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            repo_full_name TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            events TEXT NOT NULL DEFAULT 'all',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,repo_full_name,channel_id)
        );
        CREATE TABLE IF NOT EXISTS github_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            action TEXT,
            repo_full_name TEXT,
            actor TEXT,
            summary TEXT,
            target_url TEXT,
            dispatched_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'received',
            error TEXT,
            received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _verify_signature(secret: str, body: bytes, supplied: str) -> bool:
    if not secret or not supplied.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _repository(payload: dict[str, Any]) -> tuple[str, str]:
    repo = payload.get("repository") or {}
    return str(repo.get("full_name") or ""), str(repo.get("html_url") or "")


def _actor(payload: dict[str, Any]) -> str:
    sender = payload.get("sender") or {}
    pusher = payload.get("pusher") or {}
    return str(sender.get("login") or pusher.get("name") or "GitHub")


def _short(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _github_card(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo, repo_url = _repository(payload)
    actor = _actor(payload)
    action = str(payload.get("action") or "")
    title = f"GitHub · {event}"
    text = f"**{actor}** triggered `{event}` in **{repo or 'unknown repository'}**."
    color = "#6E7681"
    url = repo_url
    fields: list[dict[str, Any]] = []

    if event == "push":
        ref = str(payload.get("ref") or "").removeprefix("refs/heads/")
        commits = payload.get("commits") or []
        head = payload.get("head_commit") or {}
        url = str(payload.get("compare") or head.get("url") or repo_url)
        title = f"🚀 Push · {repo}"
        text = f"**{actor}** pushed **{len(commits)} commit(s)** to `{ref or 'unknown'}`."
        if head.get("message"):
            fields.append({"name": "Head commit", "value": _short(head.get("message"), 240), "inline": False})
        if ref:
            fields.append({"name": "Branch", "value": f"`{ref}`", "inline": True})
        color = "#2DA44E"
    elif event == "issues":
        issue = payload.get("issue") or {}
        url = str(issue.get("html_url") or repo_url)
        title = f"🧩 Issue {action or 'updated'} · {repo}"
        text = f"**#{issue.get('number')} {_short(issue.get('title'), 220)}**\nby **{actor}**"
        labels = [str(item.get("name")) for item in (issue.get("labels") or []) if item.get("name")]
        if labels:
            fields.append({"name": "Labels", "value": ", ".join(f"`{x}`" for x in labels[:12]), "inline": False})
        color = "#A371F7" if action in {"opened", "reopened"} else "#6E7681"
    elif event == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        url = str(comment.get("html_url") or issue.get("html_url") or repo_url)
        title = f"💬 Comment · {repo}"
        text = f"**{actor}** commented on **#{issue.get('number')} {_short(issue.get('title'), 180)}**."
        if comment.get("body"):
            fields.append({"name": "Comment", "value": _short(comment.get("body"), 500), "inline": False})
        color = "#58A6FF"
    elif event == "pull_request":
        pr = payload.get("pull_request") or {}
        merged = bool(pr.get("merged"))
        effective = "merged" if action == "closed" and merged else action or "updated"
        url = str(pr.get("html_url") or repo_url)
        title = f"🔀 Pull Request {effective} · {repo}"
        text = f"**#{pr.get('number')} {_short(pr.get('title'), 220)}**\nby **{actor}**"
        head = str((pr.get("head") or {}).get("ref") or "?")
        base = str((pr.get("base") or {}).get("ref") or "?")
        fields.append({"name": "Branches", "value": f"`{head}` → `{base}`", "inline": False})
        if pr.get("changed_files") is not None:
            fields.append({"name": "Changed files", "value": str(pr.get("changed_files")), "inline": True})
        color = "#8957E5" if merged else "#2DA44E" if effective in {"opened", "reopened", "ready_for_review"} else "#CF222E"
    elif event == "pull_request_review":
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        state = str(review.get("state") or action or "updated")
        url = str(review.get("html_url") or pr.get("html_url") or repo_url)
        title = f"🔎 PR Review · {repo}"
        text = f"**{actor}** submitted **{state}** on PR **#{pr.get('number')} {_short(pr.get('title'), 160)}**."
        color = "#1F6FEB" if state == "approved" else "#D29922"
    elif event == "workflow_run":
        run = payload.get("workflow_run") or {}
        conclusion = str(run.get("conclusion") or run.get("status") or action or "updated")
        branch = str(run.get("head_branch") or "?")
        url = str(run.get("html_url") or repo_url)
        icon = "✅" if conclusion == "success" else "❌" if conclusion in {"failure", "cancelled", "timed_out"} else "⚙️"
        title = f"{icon} Action · {_short(run.get('name'), 180)}"
        text = f"**{repo}** · `{branch}`\nWorkflow **{conclusion}** · triggered by **{actor}**."
        fields.append({"name": "Event", "value": str(run.get("event") or "unknown"), "inline": True})
        color = "#2DA44E" if conclusion == "success" else "#CF222E" if conclusion in {"failure", "cancelled", "timed_out"} else "#D29922"
    elif event == "workflow_job":
        job = payload.get("workflow_job") or {}
        conclusion = str(job.get("conclusion") or job.get("status") or action or "updated")
        url = str(job.get("html_url") or repo_url)
        icon = "✅" if conclusion == "success" else "❌" if conclusion == "failure" else "🧱"
        title = f"{icon} Workflow Job · {_short(job.get('name'), 180)}"
        text = f"**{repo}** · job **{conclusion}**."
        if job.get("runner_name"):
            fields.append({"name": "Runner", "value": str(job.get("runner_name")), "inline": True})
        color = "#2DA44E" if conclusion == "success" else "#CF222E" if conclusion == "failure" else "#D29922"
    elif event == "release":
        release = payload.get("release") or {}
        tag = str(release.get("tag_name") or "?")
        url = str(release.get("html_url") or repo_url)
        title = f"🏷️ Release {action or 'updated'} · {repo}"
        text = f"**{release.get('name') or tag}** · `{tag}`\nby **{actor}**"
        color = "#BF8700"
    elif event in {"create", "delete"}:
        ref_type = str(payload.get("ref_type") or "ref")
        ref = str(payload.get("ref") or "?")
        icon = "🌱" if event == "create" else "🗑️"
        title = f"{icon} {ref_type.title()} {event}d · {repo}"
        text = f"**{actor}** {event}d `{ref}`."
        color = "#2DA44E" if event == "create" else "#CF222E"

    if url:
        text += f"\n\n[Open on GitHub]({url})"
    return {
        "title": _short(title, 256),
        "text": _short(text, 3900),
        "color": color,
        "author": "GitHub Live",
        "footer": f"{event}{('/' + action) if action else ''} · {repo}",
        "fields": fields[:25],
        "summary": _short(text.replace("\n", " "), 500),
        "url": url,
        "actor": actor,
        "action": action,
        "repo": repo,
    }


def _event_enabled(raw: str, event: str) -> bool:
    value = str(raw or "").strip().lower()
    if value == "all":
        return True
    return event.lower() in {item.strip() for item in value.split(",") if item.strip()}


async def github_webhook(request: web.Request) -> web.Response:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
    if len(secret) < 16:
        return web.json_response({"ok": False, "message": "GITHUB_WEBHOOK_SECRET is not configured."}, status=503)
    body = await request.read()
    if not _verify_signature(secret, body, request.headers.get("X-Hub-Signature-256", "")):
        return web.json_response({"ok": False, "message": "Invalid signature."}, status=401)
    event = request.headers.get("X-GitHub-Event", "").strip().lower()
    delivery = request.headers.get("X-GitHub-Delivery", "").strip() or hashlib.sha256(body).hexdigest()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "message": "Invalid JSON payload."}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "message": "Invalid payload."}, status=400)
    if event == "ping":
        return web.json_response({"ok": True, "event": "ping", "zen": payload.get("zen")})
    card = _github_card(event or "unknown", payload)
    config = request.app["config"]

    def dispatch() -> tuple[int, bool]:
        con = _connect(config)
        try:
            _ensure_schema(con)
            if con.execute("SELECT 1 FROM github_events WHERE delivery_id=?", (delivery,)).fetchone():
                return 0, True
            con.execute(
                """INSERT INTO github_events(delivery_id,event_type,action,repo_full_name,actor,summary,target_url,status) VALUES(?,?,?,?,?,?,?,'received')""",
                (delivery, event or "unknown", card["action"], card["repo"], card["actor"], card["summary"], card["url"]),
            )
            rows = con.execute(
                "SELECT channel_id,events FROM github_subscriptions WHERE enabled=1 AND lower(repo_full_name)=lower(?)",
                (card["repo"],),
            ).fetchall()
            count = 0
            for row in rows:
                if not _event_enabled(str(row["events"]), event):
                    continue
                payload_out = {
                    "channel_id": str(row["channel_id"]),
                    "title": card["title"],
                    "text": card["text"],
                    "color": card["color"],
                    "author": card["author"],
                    "footer": card["footer"],
                    "fields": card["fields"],
                }
                con.execute(
                    "INSERT INTO dashboard_commands(action,payload_json) VALUES('send-embed-v2',?)",
                    (json.dumps(payload_out, ensure_ascii=False),),
                )
                count += 1
            con.execute(
                "UPDATE github_events SET dispatched_count=?,status=? WHERE delivery_id=?",
                (count, "queued" if count else "no-subscription", delivery),
            )
            con.commit()
            return count, False
        finally:
            con.close()

    try:
        count, duplicate = await asyncio.to_thread(dispatch)
    except sqlite3.Error as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=500)
    return web.json_response({"ok": True, "event": event, "repository": card["repo"], "queued": count, "duplicate": duplicate})


def register_github_webhook_routes(app: web.Application) -> None:
    app.router.add_post("/github/webhook", github_webhook)
