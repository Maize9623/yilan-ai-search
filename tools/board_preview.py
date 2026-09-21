"""At most one still of the crawler Chrome. Continuous screenshots flash captchas."""

from __future__ import annotations

import os
from pathlib import Path

from tools import utils


async def take_still(context) -> None:
    raw = os.getenv("BOARD_PREVIEW_PATH", "").strip()
    if not raw:
        return
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = [page for page in context.pages if not page.is_closed()]
    http_pages = [page for page in pages if (page.url or "").startswith("http")]
    page = http_pages[-1] if http_pages else (pages[-1] if pages else None)
    if page is None:
        return
    tmp = path.with_suffix(".part.jpg")
    data = await page.screenshot(type="jpeg", quality=52, timeout=5000)
    tmp.write_bytes(data)
    tmp.replace(path)


async def attach_preview(context) -> None:
    raw = os.getenv("BOARD_PREVIEW_PATH", "").strip()
    if not raw:
        return
    Path(raw).parent.mkdir(parents=True, exist_ok=True)
    utils.logger.info(f"[board_preview] 登录验证时不连拍。稳定后只留一帧：{raw}")


def hide_browser_args(existing: list[str] | None = None) -> list[str]:
    args = list(existing or [])
    if os.getenv("BOARD_HIDE_BROWSER") == "1":
        args.extend(
            [
                "--window-position=40,40",
                "--window-size=1280,860",
                "--disable-session-crashed-bubble",
            ]
        )
    return args
