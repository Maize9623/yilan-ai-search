"""Detect an existing browser login so we do not ask the user to scan again."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from playwright.async_api import BrowserContext, Page

from tools import utils

LOGIN_WAIT_SECONDS = 480
SILENT_VERIFY_SECONDS = 24
CONTINUE_PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")


def board_job_dir() -> Path | None:
    raw = (os.getenv("BOARD_JOB_DIR") or os.getenv("SAVE_DATA_PATH") or "").strip()
    if not raw:
        try:
            import config

            raw = str(getattr(config, "SAVE_DATA_PATH", "") or "").strip()
        except Exception:
            raw = ""
    return Path(raw) if raw else None


def continue_flag_path(job_dir: Path | str, platform: str) -> Path:
    return Path(job_dir) / f"{platform}.continue"


def request_search_continue(job_dir: Path | str, platform: str = "") -> list[str]:
    codes = [platform] if platform in CONTINUE_PLATFORMS else list(CONTINUE_PLATFORMS)
    written: list[str] = []
    for code in codes:
        path = continue_flag_path(job_dir, code)
        path.write_text("1", encoding="utf-8")
        written.append(code)
    return written


def consume_search_continue(platform: str, job_dir: Path | str | None = None) -> bool:
    root = Path(job_dir) if job_dir else board_job_dir()
    if root is None:
        return False
    path = continue_flag_path(root, platform)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return True
    return True


def show_crawler_flag_path(job_dir: Path | str) -> Path:
    return Path(job_dir) / "show.chrome"


def request_show_crawler(job_dir: Path | str) -> Path:
    path = show_crawler_flag_path(job_dir)
    path.write_text("1", encoding="utf-8")
    return path


def consume_show_crawler(job_dir: Path | str | None = None) -> bool:
    root = Path(job_dir) if job_dir else board_job_dir()
    if root is None:
        return False
    path = show_crawler_flag_path(root)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return True
    return True

PLATFORM_LABELS = {
    "xhs": "小红书",
    "dy": "抖音",
    "ks": "快手",
    "bili": "B站",
    "wb": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}

COOKIE_MARKERS = {
    "xhs": ("web_session",),
    "dy": ("LOGIN_STATUS", "sessionid_ss", "sid_guard"),
    "ks": ("passToken",),
    "bili": ("SESSDATA", "DedeUserID"),
    "wb": ("SSOLoginState", "SUB"),
    "tieba": ("STOKEN", "PTOKEN", "BDUSS"),
    "zhihu": ("z_c0",),
}

UI_MARKERS = {
    "xhs": (
        "xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']",
    ),
    "dy": ("[class*='avatar']",),
    "ks": (".user-info", "[class*='avatar']", "[class*='UserInfo']"),
    "bili": (".header-avatar-wrap", ".bili-avatar"),
    "wb": (".gn_name",),
    "zhihu": (".AppHeader-profile",),
}


def _cookie_map(cookies: Iterable[dict]) -> dict[str, str]:
    return {
        str(item.get("name", "")): str(item.get("value") or "")
        for item in cookies
        if item.get("name")
    }


def cookies_look_logged_in(cookies: Iterable[dict], platform: str) -> bool:
    found = _cookie_map(cookies)
    if platform == "dy" and found.get("LOGIN_STATUS") == "1":
        return True
    if platform == "xhs":
        # Guest pages also have web_session. Only the visible profile counts.
        return False
    return any(found.get(name) for name in COOKIE_MARKERS.get(platform, ()))


def _xhs_session_cookie(cookies: Iterable[dict]) -> str:
    return str(_cookie_map(cookies).get("web_session") or "")


async def _xhs_login_dialog_visible(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const visible = (el) => {
                    if (!el) return false;
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) {
                      return false;
                    }
                    const r = el.getBoundingClientRect();
                    return r.width > 40 && r.height > 40;
                  };
                  const qr = document.querySelector(
                    'img.qrcode-img, img[class*="qrcode"], [class*="login-container"]'
                  );
                  if (qr && visible(qr)) return true;
                  const marks = /扫码登录|手机号登录|验证码登录/;
                  for (const el of document.querySelectorAll('h1, h2, button, div')) {
                    const text = (el.innerText || '').trim();
                    if (text && text.length <= 12 && marks.test(text) && visible(el)) return true;
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


async def _xhs_page_logged_in(page: Page) -> bool:
    """Current Xiaohongshu sidebar uses 我 / 通知 / 发布, not the old profile span."""
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const visible = (el) => {
                    if (!el) return false;
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) {
                      return false;
                    }
                    const r = el.getBoundingClientRect();
                    return r.width > 8 && r.height > 8;
                  };
                  const qr = document.querySelector(
                    'img.qrcode-img, img[class*="qrcode"], [class*="login-container"]'
                  );
                  if (qr && visible(qr)) return false;
                  const loginBtn = Array.from(document.querySelectorAll('button, a, span')).some((el) => {
                    return (el.innerText || '').trim() === '登录' && visible(el);
                  });
                  if (document.querySelector('a[href*="/user/profile/"]') && !loginBtn) {
                    return true;
                  }
                  const labels = new Set();
                  for (const el of document.querySelectorAll('a, button, span, div, li')) {
                    const text = (el.innerText || '').trim();
                    if (text && text.length <= 4) labels.add(text);
                  }
                  return labels.has('我') && (
                    labels.has('通知') || labels.has('消息') || labels.has('发布')
                  );
                }"""
            )
        )
    except Exception:
        return False


async def _ui_logged_in(page: Page, platform: str) -> bool:
    if platform == "xhs" and await _xhs_page_logged_in(page):
        return True
    for selector in UI_MARKERS.get(platform, ()):
        try:
            if await page.is_visible(selector, timeout=400):
                return True
        except Exception:
            continue
    return False


def _context_pages(context: BrowserContext, page: Page | None) -> list[Page]:
    pages: list[Page] = []
    try:
        pages.extend(context.pages)
    except Exception:
        pass
    if page is not None and page not in pages:
        pages.insert(0, page)
    return pages


async def page_needs_verify(page: Page | None) -> bool:
    if page is None:
        return False
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const visible = (el) => {
                    if (!el) return false;
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) {
                      return false;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 40 || r.height < 18) return false;
                    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) {
                      return false;
                    }
                    return true;
                  };
                  const title = (document.title || '').trim();
                  if (/^(安全验证|请通过验证|滑动验证|验证码)/.test(title)) return true;
                  const srcRe = /captcha|verify|secsdk|slidecaptcha|captchax/i;
                  for (const iframe of document.querySelectorAll('iframe')) {
                    const src = iframe.getAttribute('src') || iframe.getAttribute('data-src') || '';
                    if (!srcRe.test(src) || !visible(iframe)) continue;
                    const r = iframe.getBoundingClientRect();
                    if (r.width > 160 && r.height > 90) return true;
                  }
                  const marks = /请通过验证|请完成安全验证|拖动滑块|滑动验证/;
                  const boxes = document.querySelectorAll(
                    '[class*="captcha" i], [id*="captcha" i], [class*="secsdk" i], [id*="secsdk" i], [role="dialog"]'
                  );
                  for (const el of boxes) {
                    if (!visible(el)) continue;
                    const r = el.getBoundingClientRect();
                    const text = (el.innerText || '').trim().slice(0, 80);
                    if (r.width > 160 && r.height > 80 && marks.test(text)) return true;
                  }
                  for (const el of document.querySelectorAll('h1, h2, button')) {
                    const text = (el.innerText || '').trim();
                    if (!text || text.length > 20 || !marks.test(text) || !visible(el)) continue;
                    return true;
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


_last_show_chrome = 0.0


async def show_chrome_window() -> None:
    """Do not resize Chrome. set bounds hits the front window and makes pages twitch."""
    return


async def wait_for_manual_verify(
    context: BrowserContext | None,
    page: Page | None,
    platform: str,
    timeout: int = LOGIN_WAIT_SECONDS,
    force: bool = False,
) -> bool:
    """Wait until the user clears a visible 安全验证. Do not solve it."""
    label = PLATFORM_LABELS.get(platform, platform)

    def current_pages() -> list[Page]:
        if context is not None:
            return _context_pages(context, page)
        return [page] if page is not None else []

    async def showing() -> bool:
        return any(await page_needs_verify(item) for item in current_pages() if item is not None)

    if not await showing() and not force:
        return True
    if not await showing():
        return False
    utils.logger.info(
        f"[{platform}] {label}被安全验证挡住了。"
        "请把 Chrome 窗口拉到前面自己过，过完会继续搜。"
    )
    try:
        from tools.chrome_launch import restore_crawler_window

        await restore_crawler_window(page)
    except Exception:
        await show_chrome_window()
    started = time.monotonic()
    deadline = started + timeout
    last_beat = 0.0
    while time.monotonic() < deadline:
        if not await showing():
            utils.logger.info(f"[{platform}] {label}安全验证已过，继续。")
            return True
        elapsed = time.monotonic() - started
        if elapsed - last_beat >= 12:
            last_beat = elapsed
            utils.logger.info(
                f"[{platform}] 还在等你过{label}安全验证。"
                "窗口已拉到前面，自己点完即可，不要关这个搜索。"
            )
        await asyncio.sleep(1)
    utils.logger.info(f"[{platform}] {label}安全验证还没过。过完后点重新搜。")
    return False


async def already_logged_in(
    context: BrowserContext, page: Page | None, platform: str
) -> bool:
    try:
        cookies = await context.cookies()
    except Exception:
        cookies = []
    if cookies_look_logged_in(cookies, platform):
        return True
    for candidate in _context_pages(context, page):
        if await _ui_logged_in(candidate, platform):
            return True
    return False


async def accept_manual_login(
    context: BrowserContext, page: Page | None, platform: str
) -> bool:
    """User clicked 我登录好了. Trust the open Chrome more than a stale API check."""
    if await already_logged_in(context, page, platform):
        return True
    try:
        cookies = await context.cookies()
    except Exception:
        cookies = []
    if platform != "xhs":
        return cookies_look_logged_in(cookies, platform)
    if len(_xhs_session_cookie(cookies)) < 16:
        return False
    if page is not None and await _xhs_login_dialog_visible(page):
        return False
    return True


async def wait_until_logged_in(
    context: BrowserContext,
    page: Page | None,
    platform: str,
    timeout: int = LOGIN_WAIT_SECONDS,
    ready: Optional[Callable[[], Awaitable[bool]]] = None,
) -> bool:
    """Wait in the open Chrome. Do not poke the page — that flashes captchas."""
    label = PLATFORM_LABELS.get(platform, platform)
    deadline = time.monotonic() + timeout
    last_beat = 0.0
    while time.monotonic() < deadline:
        poked = consume_search_continue(platform)
        if poked:
            utils.logger.info(f"[{platform}] 收到接着搜，马上再核{label}登录。")
        if ready is not None:
            try:
                if await ready():
                    utils.logger.info(f"[{platform}] {label}登录成功，登录态会留在这台电脑上。")
                    return True
            except Exception:
                pass
        if await already_logged_in(context, page, platform):
            utils.logger.info(f"[{platform}] {label}登录成功，登录态会留在这台电脑上。")
            return True
        if poked and await accept_manual_login(context, page, platform):
            utils.logger.info(
                f"[{platform}] {label}登录成功，登录态会留在这台电脑上。"
                "按你点的接着搜继续。"
            )
            return True
        if poked:
            utils.logger.info(
                f"[{platform}] 收到接着搜，但{label}页面上还是登录框。"
                "先在弹出的 Chrome 里登完，不要关这次搜索。"
            )
        elapsed = timeout - (deadline - time.monotonic())
        if elapsed - last_beat >= 15:
            last_beat = elapsed
            utils.logger.info(
                f"[{platform}] 还在等你在 Chrome 里登录或过{label}验证。"
                "窗口先留着，不要关；过完会自动继续，然后窗口会自己最小化。"
            )
        await asyncio.sleep(1)
    utils.logger.info(f"[{platform}] {label}还没登录。在弹出的 Chrome 里登录后，回到页面点重新搜。")
    return False


async def complete_login_if_needed(
    context: BrowserContext,
    page: Page | None,
    platform: str,
    *,
    cookie_login: Optional[Callable[[], Awaitable[None]]] = None,
    ready: Optional[Callable[[], Awaitable[bool]]] = None,
    timeout: int = LOGIN_WAIT_SECONDS,
) -> None:
    """Skip if this machine already has a login; otherwise wait in the open Chrome."""
    import config

    label = PLATFORM_LABELS.get(platform, platform)
    if ready is not None:
        try:
            if await ready():
                utils.logger.info(f"[{platform}] {label}已登录，这次不用再登。")
                return
        except Exception:
            pass
    elif await already_logged_in(context, page, platform):
        utils.logger.info(f"[{platform}] {label}已登录，这次不用再登。")
        return
    if getattr(config, "LOGIN_TYPE", "") == "cookie" and cookie_login is not None:
        await cookie_login()
        if ready is not None:
            try:
                if await ready():
                    utils.logger.info(f"[{platform}] {label}已用 cookie 登录。")
                    return
            except Exception:
                pass
        elif await already_logged_in(context, page, platform):
            utils.logger.info(f"[{platform}] {label}已用 cookie 登录。")
            return
        raise RuntimeError(f"{label}的 cookie 登录没有生效。")
    utils.logger.info(
        f"请到弹出的 Chrome 窗口登录{label}，有验证码也在这个窗口自己过。"
        "过完会自动继续，窗口随即最小化；也可以回到看板点「我登录好了，重新搜」。"
    )
    try:
        from tools.chrome_launch import restore_crawler_window

        await restore_crawler_window(page)
    except Exception:
        pass
    if await wait_until_logged_in(
        context, page, platform, timeout=timeout, ready=ready
    ):
        return
    raise RuntimeError(f"{label}还没登录。在弹出的 Chrome 里登录后，回到页面点重新搜。")
