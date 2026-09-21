"""Launch the machine's installed Chrome with a persistent profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from tools import utils
from tools.board_preview import attach_preview, hide_browser_args, take_still


async def adopt_page(context):
    """Reuse a restored tab. Extra about:blank tabs make the preview flip."""
    pages = [page for page in context.pages if not page.is_closed()]
    chosen = None
    for page in pages:
        url = (page.url or "").strip()
        if url.startswith("http"):
            chosen = page
            break
    if chosen is None:
        chosen = pages[0] if pages else await context.new_page()
    for page in pages:
        if page is chosen:
            continue
        url = (page.url or "").strip()
        if url.startswith("about:"):
            try:
                await page.close()
            except Exception:
                pass
    return chosen


def system_chrome() -> Optional[str]:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


async def launch_persistent_chrome(chromium, user_data_dir: str, **kwargs: Any):
    kwargs.setdefault("accept_downloads", True)
    # Playwright 默认关沙箱、锁死 viewport，微博/抖音的验证码会显示但点不进去。
    kwargs.setdefault("chromium_sandbox", True)
    ignored = list(kwargs.pop("ignore_default_args", None) or [])
    if "--enable-automation" not in ignored:
        ignored.append("--enable-automation")
    kwargs["ignore_default_args"] = ignored
    args = hide_browser_args(kwargs.pop("args", None))
    if args:
        kwargs["args"] = args
    kwargs["viewport"] = None
    try:
        context = await chromium.launch_persistent_context(
            user_data_dir,
            channel="chrome",
            **kwargs,
        )
    except Exception as error:
        message = str(error)
        if "already in use" in message.lower() or "ProcessSingleton" in message:
            raise
        chrome = system_chrome()
        if not chrome:
            raise
        utils.logger.info(f"[chrome_launch] channel=chrome 失败，改用本机浏览器：{chrome}")
        context = await chromium.launch_persistent_context(
            user_data_dir,
            executable_path=chrome,
            **kwargs,
        )
    await attach_preview(context)
    return context


async def settle_if_idle(page, label: str = "") -> None:
    """Minimize unless a visible login/verify gate is on screen."""
    if page is None or not hasattr(page, "context"):
        return
    try:
        from tools.login_ready import page_needs_verify

        if await page_needs_verify(page) is True:
            return
    except Exception:
        pass
    await settle_crawler_window(page, label)


async def settle_crawler_window(page, label: str = "") -> None:
    """Keep the window visible for login/verify, then minimize only this Chrome."""
    name = label or "爬虫"
    try:
        await take_still(page.context)
    except Exception:
        pass
    try:
        session = await page.context.new_cdp_session(page)
        info = await session.send("Browser.getWindowForTarget")
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": info["windowId"],
                "bounds": {"windowState": "minimized"},
            },
        )
        try:
            await session.detach()
        except Exception:
            pass
    except Exception as error:
        utils.logger.info(f"[chrome_launch] {name}窗口没能最小化：{error}")
        return
    utils.logger.info(
        f"[chrome_launch] {name}已稳定，爬虫 Chrome 已最小化。看板继续收回，不用再盯那个窗口。"
    )


async def restore_crawler_window(page) -> None:
    if page is None:
        return
    try:
        await page.bring_to_front()
    except Exception:
        pass
    try:
        session = await page.context.new_cdp_session(page)
        info = await session.send("Browser.getWindowForTarget")
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": info["windowId"],
                "bounds": {
                    "windowState": "normal",
                    "left": 40,
                    "top": 40,
                    "width": 1280,
                    "height": 860,
                },
            },
        )
        try:
            await session.detach()
        except Exception:
            pass
    except Exception:
        pass
