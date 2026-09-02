from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _tokens(value: str) -> list[str]:
    return [part for part in re.findall(r"[a-z0-9_-]+", normalize(value)) if len(part) >= 2]


def _score(candidate: dict, query: str) -> float:
    q = normalize(query)
    if not q:
        return 0.0
    title = normalize(candidate.get("title"))
    key = normalize(candidate.get("key"))
    tags = normalize(candidate.get("tags"))
    content = normalize(candidate.get("content"))
    category = normalize(candidate.get("category"))
    hay = " ".join((title, key, tags, category, content))
    score = 0.0
    if q == key:
        score += 160
    if q == title:
        score += 150
    if key.startswith(q):
        score += 100
    if title.startswith(q):
        score += 95
    if q in key:
        score += 70
    if q in title:
        score += 65
    if q in tags:
        score += 40
    if q in category:
        score += 30
    if q in content:
        score += 18
    q_tokens = _tokens(q)
    if q_tokens:
        matched = sum(1 for token in q_tokens if token in hay)
        score += (matched / len(q_tokens)) * 45
        if matched == len(q_tokens):
            score += 20
    ratio = max(SequenceMatcher(None, q, title).ratio(), SequenceMatcher(None, q, key).ratio())
    score += ratio * 35
    return score


def rank_candidates(candidates: Iterable[dict], query: str, limit: int = 15) -> list[dict]:
    rows: list[dict] = []
    for raw in candidates:
        item = dict(raw)
        item["score"] = round(_score(item, query), 2)
        if item["score"] >= 10:
            rows.append(item)
    rows.sort(key=lambda row: (-float(row["score"]), normalize(row.get("title")), normalize(row.get("key"))))
    return rows[: max(1, min(int(limit), 25))]


def _like_terms(query: str) -> list[str]:
    terms = _tokens(query)
    if not terms:
        raw = normalize(query)
        return [raw] if raw else []
    return terms[:4]


async def fetch_candidates(bot, guild_id: int, query: str, *, kinds: set[str] | None = None) -> list[dict]:
    terms = _like_terms(query)
    if not terms:
        return []
    primary = f"%{terms[0]}%"
    candidates: list[dict] = []

    if kinds is None or kinds.intersection({"wiki", "faq", "med", "knowledge"}):
        rows = await bot.database.fetchall(
            """
            SELECT kind,title,entry_key AS key,content,COALESCE(tags,'') AS tags,'' AS category
            FROM knowledge_entries
            WHERE guild_id=? AND (
                lower(title) LIKE lower(?) OR lower(entry_key) LIKE lower(?) OR
                lower(content) LIKE lower(?) OR lower(COALESCE(tags,'')) LIKE lower(?)
            )
            ORDER BY updated_at DESC LIMIT 60
            """,
            (guild_id, primary, primary, primary, primary),
        )
        for row in rows:
            item = dict(row)
            if kinds is None or item["kind"] in kinds or "knowledge" in kinds:
                candidates.append(item)

    if kinds is None or "training" in kinds:
        rows = await bot.database.fetchall(
            """
            SELECT 'training' AS kind,title,CAST(id AS TEXT) AS key,content,'' AS tags,category
            FROM training_library
            WHERE guild_id=? AND (
                lower(title) LIKE lower(?) OR lower(content) LIKE lower(?) OR lower(category) LIKE lower(?)
            )
            ORDER BY updated_at DESC LIMIT 40
            """,
            (guild_id, primary, primary, primary),
        )
        candidates.extend(dict(row) for row in rows)

    if kinds is None or "quiz" in kinds:
        rows = await bot.database.fetchall(
            """
            SELECT 'quiz' AS kind,question AS title,CAST(id AS TEXT) AS key,
                   answer || CASE WHEN COALESCE(explanation,'')='' THEN '' ELSE ' · ' || explanation END AS content,
                   '' AS tags,category
            FROM quiz_questions
            WHERE guild_id=? AND (
                lower(question) LIKE lower(?) OR lower(answer) LIKE lower(?) OR lower(category) LIKE lower(?)
            )
            ORDER BY id DESC LIMIT 30
            """,
            (guild_id, primary, primary, primary),
        )
        candidates.extend(dict(row) for row in rows)

    if kinds is None or "template" in kinds:
        rows = await bot.database.fetchall(
            """
            SELECT 'template' AS kind,title,name AS key,body AS content,'' AS tags,'' AS category
            FROM content_templates
            WHERE guild_id=? AND (lower(title) LIKE lower(?) OR lower(name) LIKE lower(?) OR lower(body) LIKE lower(?))
            ORDER BY updated_at DESC LIMIT 30
            """,
            (guild_id, primary, primary, primary),
        )
        candidates.extend(dict(row) for row in rows)

    if kinds is None or "form" in kinds:
        rows = await bot.database.fetchall(
            """
            SELECT 'form' AS kind,title,name AS key,questions_json AS content,'' AS tags,'' AS category
            FROM forms
            WHERE guild_id=? AND (lower(title) LIKE lower(?) OR lower(name) LIKE lower(?) OR lower(questions_json) LIKE lower(?))
            ORDER BY updated_at DESC LIMIT 30
            """,
            (guild_id, primary, primary, primary),
        )
        candidates.extend(dict(row) for row in rows)

    if kinds is None or "command" in kinds:
        rows = await bot.database.fetchall(
            """
            SELECT 'command' AS kind,'!' || name AS title,name AS key,response AS content,'' AS tags,'' AS category
            FROM custom_commands
            WHERE guild_id=? AND enabled=1 AND (lower(name) LIKE lower(?) OR lower(response) LIKE lower(?))
            ORDER BY updated_at DESC LIMIT 30
            """,
            (guild_id, primary, primary),
        )
        candidates.extend(dict(row) for row in rows)

    return candidates


async def search(bot, guild_id: int, query: str, *, limit: int = 15, kinds: set[str] | None = None) -> list[dict]:
    candidates = await fetch_candidates(bot, guild_id, query, kinds=kinds)
    return rank_candidates(candidates, query, limit=limit)


async def autocomplete(bot, guild_id: int, current: str, *, kinds: set[str] | None = None, limit: int = 25) -> list[tuple[str, str]]:
    query = current.strip()
    if not query:
        # Useful recent defaults instead of an empty autocomplete box.
        rows = await bot.database.fetchall(
            "SELECT kind,title,entry_key AS key FROM knowledge_entries WHERE guild_id=? ORDER BY updated_at DESC LIMIT ?",
            (guild_id, min(limit, 25)),
        )
        return [(f"[{row['kind']}] {str(row['title'])[:80]}", str(row['key'])[:100]) for row in rows]
    ranked = await search(bot, guild_id, query, limit=limit, kinds=kinds)
    result: list[tuple[str, str]] = []
    for row in ranked:
        label = f"[{row['kind']}] {row['title']}"
        value = str(row.get("key") or row.get("title") or query)
        result.append((label[:100], value[:100]))
    return result
