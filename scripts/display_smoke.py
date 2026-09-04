from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DISPLAY_ALLOW_MISSING_HARDWARE", "1")
os.environ.setdefault("DISPLAY_HEADLESS_PREVIEW", "0")
os.environ.setdefault("BOT_REPO_PATH", tempfile.gettempdir())
os.environ.setdefault("DISPLAY_DATABASE_PATH", str(Path(tempfile.gettempdir()) / "homepi-display-smoke-missing.sqlite3"))

from display_service.main import DEFAULT_LAYOUT, PAGES, Snapshot, check, render_image


def main() -> None:
    layout = dict(DEFAULT_LAYOUT)
    snapshot = Snapshot(
        cpu=42.0,
        ram=61.0,
        temp=48.0,
        uptime=123456,
        network=True,
        pihole=True,
        media_title="HomePi display smoke test",
        media_active=True,
    )
    for page in PAGES:
        image = render_image(page, snapshot, layout)
        assert image.size == (128, 64), (page, image.size)
        assert image.mode == "1", (page, image.mode)
        assert image.getbbox() is not None, f"{page} rendered blank"

    # Hardware is intentionally absent in CI. Deploy-safe diagnostics must still pass.
    assert check() == 0
    print("display smoke: ok")


if __name__ == "__main__":
    main()
