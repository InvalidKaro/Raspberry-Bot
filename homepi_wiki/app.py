from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import bleach
import markdown
from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE_DIR = Path(__file__).resolve().parent
CONTENT_DIR = Path(os.getenv("HOMEPI_WIKI_CONTENT", BASE_DIR / "content"))
DATA_DIR = Path(os.getenv("HOMEPI_WIKI_DATA", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
WIKI_PREFIX = os.getenv("HOMEPI_WIKI_PREFIX", "/wiki").rstrip("/")
KIWIX_URL = os.getenv("HOMEPI_KIWIX_URL", "/wikipedia/")
KIWIX_LIBRARY = Path(os.getenv("HOMEPI_KIWIX_LIBRARY", "/mnt/homepi-data/kiwix"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "search.sqlite3"

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "table", "thead", "tbody", "tr", "th", "td", "blockquote", "div", "span",
}
ALLOWED_ATTRS = {"a": ["href", "title", "class"], "code": ["class"], "div": ["class"], "span": ["class"]}


@dataclass
class Page:
    slug: str
    title: str
    category: str
    description: str
    markdown_text: str


def _parse_page(path: Path) -> Page:
    text = path.read_text(encoding="utf-8")
    title = path.stem.replace("-", " ").title()
    category = "Allgemein"
    description = ""
    body = text
    if text.startswith("---\n"):
        _, front, body = text.split("---\n", 2)
        for line in front.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key, value = key.strip().lower(), value.strip()
            if key == "title":
                title = value
            elif key == "category":
                category = value
            elif key == "description":
                description = value
    return Page(path.stem, title, category, description, body.strip())


def pages() -> list[Page]:
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    return sorted((_parse_page(p) for p in CONTENT_DIR.glob("*.md")), key=lambda p: (p.category.lower(), p.title.lower()))


def render_markdown(text: str) -> str:
    raw = markdown.markdown(text, extensions=["fenced_code", "tables", "toc", "sane_lists"])
    return bleach.clean(raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols={"http", "https", "mailto"}, strip=True)


def rebuild_search_index() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(slug UNINDEXED, title, category, description, body)")
        con.execute("DELETE FROM pages_fts")
        con.executemany(
            "INSERT INTO pages_fts(slug,title,category,description,body) VALUES(?,?,?,?,?)",
            [(p.slug, p.title, p.category, p.description, p.markdown_text) for p in pages()],
        )
        con.commit()
    finally:
        con.close()


def library_items() -> list[dict]:
    items: list[dict] = []
    if not KIWIX_LIBRARY.exists():
        return items
    for path in sorted(KIWIX_LIBRARY.glob("*.zim")):
        name = re.sub(r"[_-]+", " ", path.stem).strip()
        items.append({
            "name": name,
            "filename": path.name,
            "size_gb": round(path.stat().st_size / 1024**3, 2),
            "url": KIWIX_URL,
        })
    return items


def render_template(name: str, **ctx) -> web.Response:
    html = env.get_template(name).render(prefix=WIKI_PREFIX, kiwix_url=KIWIX_URL, **ctx)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def home(_: web.Request) -> web.Response:
    all_pages = pages()
    cats: dict[str, list[Page]] = {}
    for page in all_pages:
        cats.setdefault(page.category, []).append(page)
    return render_template("index.html", title="HomePi Wiki", categories=cats, library=library_items(), active="home")


async def page_view(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    page = next((p for p in pages() if p.slug == slug), None)
    if page is None:
        raise web.HTTPNotFound(text="Wiki page not found")
    return render_template(
        "index.html",
        title=page.title,
        page=page,
        page_html=render_markdown(page.markdown_text),
        categories={},
        library=library_items(),
        active="page",
    )


async def search(request: web.Request) -> web.Response:
    q = request.query.get("q", "").strip()
    results: list[dict] = []
    if q:
        try:
            con = sqlite3.connect(DB_PATH)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT slug,title,category,description,snippet(pages_fts,4,'<mark>','</mark>',' … ',18) snippet FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank LIMIT 20",
                (q.replace('"', '""'),),
            ).fetchall()
            results = [dict(r) for r in rows]
        except sqlite3.Error:
            results = []
        finally:
            if 'con' in locals():
                con.close()
    return web.json_response({"ok": True, "query": q, "results": results})


async def status(_: web.Request) -> web.Response:
    stat = None
    if KIWIX_LIBRARY.exists():
        try:
            fs = os.statvfs(KIWIX_LIBRARY)
            stat = {
                "total_gb": round(fs.f_blocks * fs.f_frsize / 1024**3, 1),
                "free_gb": round(fs.f_bavail * fs.f_frsize / 1024**3, 1),
            }
        except OSError:
            pass
    return web.json_response({
        "ok": True,
        "pages": len(pages()),
        "kiwix_library": str(KIWIX_LIBRARY),
        "kiwix_available": KIWIX_LIBRARY.exists(),
        "zim_count": len(library_items()),
        "storage": stat,
    })


def create_app() -> web.Application:
    rebuild_search_index()
    app = web.Application(client_max_size=1024**2)
    app.router.add_get(f"{WIKI_PREFIX}", home)
    app.router.add_get(f"{WIKI_PREFIX}/", home)
    app.router.add_get(f"{WIKI_PREFIX}/page/{{slug}}", page_view)
    app.router.add_get(f"{WIKI_PREFIX}/api/search", search)
    app.router.add_get(f"{WIKI_PREFIX}/api/status", status)
    app.router.add_static(f"{WIKI_PREFIX}/static/", STATIC_DIR, append_version=True)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host=os.getenv("HOMEPI_WIKI_HOST", "127.0.0.1"), port=int(os.getenv("HOMEPI_WIKI_PORT", "8092")))
