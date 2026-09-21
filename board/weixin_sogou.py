"""Resolve 搜狗微信 /link to mp.weixin.qq.com in the board Chrome.

Sogou does not HTTP-redirect. After captcha it runs page JS. Waiting for 302,
or stripping type/query/token before the jump, is why 一览填完验证码也不跳。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from html import unescape
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "browser_data" / "mp_user_data_dir"
_WEIXIN = re.compile(r"https://mp\.weixin\.qq\.com/s[^\s\"'<>\\]{0,400}")
_URL_PLUS = re.compile(r"""url\s*\+=\s*['"]([^'"]+)['"]""")
_GATE_MARKS = (
    "已迁移",
    "账号已迁移",
    "该账号已迁移",
    "已注销",
    "账号已注销",
    "已被发布者删除",
    "该内容已被删除",
    "此内容已被删除",
    "无法查看",
    "原账号已回收",
)
_GATE_CLICKS = ("访问文章", "继续访问", "查看原文", "前往新账号", "打开原文", "继续阅读")
_WEIXIN_IMG = re.compile(
    r'(?:data-src|data-original|data-actualsrc|data-lazy-src|src)=["\']([^"\']+)["\']',
    re.I,
)
_WEIXIN_IMG_SKIP = (
    "emoji",
    "icon",
    "qrcode",
    "avatar",
    "wx_icon",
    "placeholder",
    "res.wx.qq.com/mmad",
)
_STOP = frozenset(
    {
        "推荐",
        "攻略",
        "怎么",
        "如何",
        "哪些",
        "什么",
        "一个",
        "一下",
        "可以",
        "适合",
        "相关",
        "内容",
        "文章",
        "视频",
        "分享",
        "最新",
        "希望",
        "需要",
        "还有",
        "或者",
        "以及",
        "这个",
        "那个",
        "我们",
        "建议",
        "问题",
        "看看",
        "求问",
        "谢谢",
        "就是",
        "还是",
        "不是",
    }
)
_PLACE_FALLBACK = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "成都",
    "重庆",
    "杭州",
    "南京",
    "武汉",
    "西安",
    "苏州",
    "天津",
    "长沙",
    "郑州",
    "青岛",
    "厦门",
    "昆明",
    "大连",
    "哈尔滨",
    "沈阳",
    "济南",
    "福州",
    "合肥",
    "南昌",
    "南宁",
    "海口",
    "贵阳",
    "太原",
    "石家庄",
    "兰州",
    "乌鲁木齐",
    "拉萨",
    "银川",
    "西宁",
    "呼和浩特",
    "香港",
    "澳门",
    "台北",
    "四川",
    "云南",
    "西藏",
    "新疆",
    "海南",
    "广西",
    "贵州",
    "日本",
    "泰国",
    "韩国",
    "新加坡",
    "马来西亚",
    "越南",
    "柬埔寨",
    "加拿大",
    "美国",
    "澳洲",
    "澳大利亚",
    "英国",
    "法国",
    "意大利",
    "德国",
    "西班牙",
    "瑞士",
    "新西兰",
)
_DEAD_MARKS = (
    "已被发布者删除",
    "该内容已被删除",
    "此内容已被删除",
    "该内容无法查看",
    "此内容因违规无法查看",
    "该内容因违规无法查看",
    "页面不存在",
    "内容不存在",
    "此内容已过期",
    "该内容已过期",
    "文章已删除",
    "该账号已注销",
    "公众号已注销",
    "账号已注销",
    "原账号已回收",
    "该内容已被投诉",
)
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
FetchMore = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
WEIXIN_PUBLIC_CAP = 8
WEIXIN_RESOLVE_S = 210
WEIXIN_FIRST_WAIT_S = 180
WEIXIN_ARTICLE_S = 45
WEIXIN_FALLBACK_READS = 8
WEIXIN_STUCK_GIVE_UP = 3


def weixin_should_stop(
    *,
    wanted: int,
    public_n: int,
    elapsed_s: float,
    work_s: float | None,
) -> str:
    """Stop opening more 原文: enough public bodies, or Chrome has sat too long."""
    if public_n >= min(WEIXIN_PUBLIC_CAP, max(1, wanted)):
        return "enough"
    if elapsed_s >= WEIXIN_FIRST_WAIT_S + WEIXIN_RESOLVE_S:
        return "time"
    if work_s is not None and work_s >= WEIXIN_RESOLVE_S:
        return "time"
    return ""


def is_sogou_link(url: str) -> bool:
    parsed = urlsplit(url or "")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "weixin.sogou.com"
        and parsed.path == "/link"
        and bool(parse_qs(parsed.query).get("url"))
    )


def is_weixin_article(url: str) -> bool:
    parsed = urlsplit(url or "")
    return parsed.hostname == "mp.weixin.qq.com" and parsed.path.startswith("/s")


def sogou_jump_url(url: str) -> str:
    """Keep the search-session fields the jump script needs. Do not drop token."""
    parsed = urlsplit(url or "")
    if parsed.hostname != "weixin.sogou.com" or parsed.path != "/link":
        return url
    query = parse_qs(parsed.query, keep_blank_values=True)
    kept = []
    for key in ("url", "k", "h", "type", "query", "token"):
        values = query.get(key) or []
        if values:
            kept.append((key, values[0]))
    if not kept:
        return url
    return urlunsplit(("https", "weixin.sogou.com", "/link", urlencode(kept), ""))


def extract_weixin_url(html: str) -> str:
    text = unescape(html or "")
    parts = _URL_PLUS.findall(text)
    if parts:
        joined = "".join(parts)
        if "mp.weixin.qq.com/s" in joined:
            return joined.split("#")[0].split("&amp;")[0]
    found = _WEIXIN.findall(text)
    for item in found:
        cleaned = item.rstrip(").,;\"'")
        if "/s" in cleaned:
            return cleaned
    return ""


def weixin_pic_urls(html: str) -> list[str]:
    urls: list[str] = []
    for pattern in (
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
    ):
        cover = re.search(pattern, html or "", re.I)
        if cover:
            candidate = unescape(cover.group(1)).strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            if candidate.startswith("http") and candidate not in urls:
                urls.append(candidate[:800])
            break
    for src in _WEIXIN_IMG.findall(html or ""):
        clean = unescape(src).strip()
        if clean.startswith("//"):
            clean = "https:" + clean
        if not clean.startswith("http"):
            continue
        low = clean.lower()
        if any(token in low for token in _WEIXIN_IMG_SKIP):
            continue
        if not any(host in low for host in ("qpic.cn", "qlogo.cn")):
            continue
        if clean not in urls:
            urls.append(clean[:800])
        if len(urls) >= 8:
            break
    return urls[:8]


def article_from_html(html: str, url: str) -> dict[str, Any] | None:
    heading = re.search(
        r'id="(?:activity-name|js_name)"[^>]*>(.*?)</(?:h1|a|strong|span)>',
        html or "",
        re.I | re.S,
    )
    title = re.search(
        r'id="activity-name"[^>]*>(.*?)</',
        html or "",
        re.I | re.S,
    )
    body = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', html or "", re.I | re.S)
    if not body:
        body = re.search(r'id="js_content"[^>]*>(.*)$', html or "", re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", body.group(1) if body else "")
    text = unescape(re.sub(r"\s+", " ", raw)).strip()
    if len(text) < 30:
        return None
    name = ""
    if title:
        name = unescape(re.sub(r"<[^>]+>", " ", title.group(1))).strip()
    elif heading:
        name = unescape(re.sub(r"<[^>]+>", " ", heading.group(1))).strip()
    author = ""
    meta = re.search(r'<meta[^>]+name="author"[^>]+content="([^"]+)"', html or "", re.I)
    if meta:
        author = unescape(meta.group(1)).strip()
    images = weixin_pic_urls(html)
    return {
        "title": name[:240],
        "text": text[:4000],
        "url": url,
        "author": author[:80],
        "image": (images[0] if images else "")[:800],
        "images": images,
    }


def apply_weixin_article(row: dict[str, Any], article: dict[str, Any]) -> None:
    text = str(article.get("text") or "").strip()
    if text:
        row["desc"] = text
        row["content"] = text
        row["text_source"] = "public"
    if article.get("url"):
        row["url"] = article["url"]
    if article.get("title"):
        row["title"] = article["title"]
    if article.get("author") and not row.get("author"):
        row["author"] = article["author"]
    images = [str(url) for url in (article.get("images") or []) if str(url).startswith("http")]
    cover = str(article.get("image") or "").strip()
    if cover.startswith("http") and cover not in images:
        images = [cover] + images
    if images:
        row["cover_url"] = images[0]
        row["image_list"] = ",".join(images[:8])
        row["images"] = images[:8]


def merge_weixin_topup(
    existing: list[dict[str, Any]],
    extras: list[dict[str, Any]],
    wanted: int,
    seen: set[str],
) -> list[dict[str, Any]]:
    """Keep what we already have, then append unseen replacements up to wanted."""
    out = list(existing)
    for row in extras or []:
        if len(out) >= max(1, wanted):
            break
        key = weixin_row_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def weixin_needs_topup(rows: list[dict[str, Any]], wanted: int) -> bool:
    public_n = sum(1 for row in rows if row.get("text_source") == "public")
    return len(rows) < max(1, wanted) or public_n < min(WEIXIN_PUBLIC_CAP, max(1, wanted))


def is_weixin_gate(html: str) -> bool:
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    return any(mark in text for mark in _GATE_MARKS)


def is_weixin_expired(html: str) -> bool:
    if article_from_html(html or "", "https://mp.weixin.qq.com/s"):
        return False
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    if any(mark in text for mark in _DEAD_MARKS):
        return True
    return is_weixin_gate(html)


def weixin_row_key(row: dict[str, Any] | str) -> str:
    url = str(row.get("url") if isinstance(row, dict) else row or "")
    parsed = urlsplit(url)
    if parsed.hostname == "weixin.sogou.com":
        inner = (parse_qs(parsed.query).get("url") or [""])[0]
        return weixin_row_key(inner) if inner else url
    if parsed.hostname == "mp.weixin.qq.com":
        query = parse_qs(parsed.query)
        biz = (query.get("__biz") or [""])[0]
        mid = (query.get("mid") or query.get("appmsgid") or [""])[0]
        idx = (query.get("idx") or [""])[0]
        if biz or mid:
            return f"{biz}:{mid}:{idx}"
        if parsed.path.startswith("/s/"):
            return parsed.path.rstrip("/")
        return url.split("#")[0].split("&signature=")[0]
    if isinstance(row, dict):
        return url or str(row.get("id") or row.get("title") or "")
    return url


def weixin_place_terms(query: str) -> list[str]:
    from freshness import query_places

    return query_places(query)


def weixin_search_query(query: str) -> str:
    """搜狗微信是散词匹配。整句会把「国庆」对到加拿大国庆，地点被丢掉。"""
    text = " ".join((query or "").split())
    if not text:
        return text
    places = weixin_place_terms(text)
    tokens: list[str] = []
    try:
        import jieba

        tokens = [
            token.strip()
            for token in jieba.cut(text)
            if len(token.strip()) >= 2 and token.strip() not in _STOP
        ]
    except Exception:
        tokens = [
            token
            for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,}", text)
            if token not in _STOP
        ]
    ordered: list[str] = []
    for token in [*places, *tokens]:
        if token and token not in ordered:
            ordered.append(token)
    compact = " ".join(ordered[:4])
    return compact or text[:20]


def weixin_row_relevant(query: str, row: dict[str, Any]) -> bool:
    if not (query or "").strip():
        return True
    from freshness import reject_reason

    return reject_reason(query, row) is None


def filter_weixin_rows(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if weixin_row_relevant(query, row)]


def pick_weixin_replacement(
    seen: set[str],
    candidates: list[dict[str, Any]],
    query: str = "",
) -> dict[str, Any] | None:
    for cand in candidates:
        key = weixin_row_key(cand)
        if not key or key in seen:
            continue
        if query and not weixin_row_relevant(query, cand):
            continue
        return cand
    return None


def _looks_like_captcha(url: str, html: str = "") -> bool:
    blob = f"{url}\n{html[:4000]}"
    return any(
        mark in blob
        for mark in ("antispider", "seccode", "安全验证", "请输入验证码", "verify.sogou")
    )


async def _bring_chrome() -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            'tell application "Google Chrome" to activate',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
    except Exception:
        pass


async def _wait_weixin(page, emit: Emit | None, first: bool) -> str:
    url = page.url or ""
    if is_weixin_article(url):
        return url
    if _looks_like_captcha(url) and emit:
        await emit(
            "status",
            {
                "step": "verify",
                "title": "搜狗要过验证才能打开公众号原文",
                "detail": "直接在弹出的 Chrome 里点图。验证期间不截图、不刷新，避免验证码抽搐点不中。填完后等它自己跳到文章。",
            },
        )
        await _bring_chrome()
    timeout_ms = 180_000 if first else 35_000
    try:
        await page.wait_for_url(
            re.compile(r"https://mp\.weixin\.qq\.com/s"),
            timeout=timeout_ms,
        )
        return page.url or ""
    except Exception:
        pass
    url = page.url or ""
    if is_weixin_article(url) or _looks_like_captcha(url):
        return url
    try:
        html = await page.content()
    except Exception:
        return url
    extracted = extract_weixin_url(html)
    if not extracted:
        return url
    try:
        await page.goto(extracted, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        return extracted
    return page.url or extracted


async def pass_weixin_gate(page) -> bool:
    try:
        html = await page.content()
    except Exception:
        return False
    if not is_weixin_gate(html):
        return False
    for name in _GATE_CLICKS:
        for finder in (
            lambda n: page.get_by_role("button", name=n),
            lambda n: page.get_by_role("link", name=n),
            lambda n: page.get_by_text(n, exact=True),
        ):
            try:
                loc = finder(name)
                if await loc.count():
                    await loc.first.click(timeout=4000)
                    return True
            except Exception:
                continue
    return False


async def open_weixin_article(page) -> str:
    url = page.url or ""
    if not is_weixin_article(url):
        return url
    clicked = await pass_weixin_gate(page)
    if not clicked:
        return url
    try:
        await page.wait_for_selector("#js_content, #activity-name", timeout=12000)
    except Exception:
        await asyncio.sleep(1.0)
        await pass_weixin_gate(page)
        try:
            await page.wait_for_selector("#js_content, #activity-name", timeout=8000)
        except Exception:
            pass
    return page.url or url


async def _page_html(page) -> str:
    try:
        return await asyncio.wait_for(page.content(), timeout=8)
    except Exception:
        return ""


async def _close_context(context) -> None:
    if context is None:
        return
    try:
        await asyncio.wait_for(context.close(), timeout=6)
    except Exception:
        pass


async def take_weixin_replacement(
    *,
    query: str,
    seen: set[str],
    spare: list[dict[str, Any]],
    fetch_more: FetchMore | None,
    wanted: int,
    emit: Emit | None,
    why: str = "原文失效",
) -> dict[str, Any] | None:
    while spare:
        cand = spare.pop(0)
        key = weixin_row_key(cand)
        if key and key not in seen:
            seen.add(key)
            if emit:
                await emit(
                    "log",
                    {
                        "platform": "mp",
                        "message": f"{why}，改用候补「{cand.get('title') or '另一篇'}」。",
                    },
                )
            return cand
    if not query or fetch_more is None:
        return None
    try:
        more = await fetch_more(query, min(20, max(wanted + 1, len(seen) + 1)))
    except Exception:
        more = []
    cand = pick_weixin_replacement(seen, more or [], query=query)
    if not cand:
        return None
    seen.add(weixin_row_key(cand))
    if emit:
        await emit(
            "log",
            {
                "platform": "mp",
                "message": f"{why}，补搜到「{cand.get('title') or '另一篇'}」。",
            },
        )
    return cand


async def fill_weixin_body(row: dict[str, Any], emit: Emit | None = None) -> bool:
    """If Chrome could not open 原文, try feedgrab public read so the snippet is not all we have."""
    if row.get("text_source") == "public" and len(str(row.get("content") or row.get("desc") or "")) >= 80:
        return True
    url = str(row.get("url") or "")
    if not url:
        return False
    try:
        try:
            from foreign import _feedgrab_read
        except ImportError:
            from board.foreign import _feedgrab_read
        article = await asyncio.wait_for(_feedgrab_read(url), timeout=30)
    except Exception:
        return False
    if not article or len(str(article.get("text") or "")) < 30:
        return False
    apply_weixin_article(row, article)
    if emit:
        await emit(
            "log",
            {
                "platform": "mp",
                "message": f"「{row.get('title') or '这篇'}」窗口卡住，已用公开读取补上正文。",
            },
        )
    return True


async def top_up_weixin_rows(
    rows: list[dict[str, Any]],
    *,
    query: str,
    wanted: int,
    seen: set[str],
    fetch_more: FetchMore | None,
    emit: Emit | None,
) -> list[dict[str, Any]]:
    """After Chrome stops or hangs, fill missing bodies and missing count."""
    need = max(1, wanted)
    out = list(rows)
    if not weixin_needs_topup(out, need):
        return out
    if emit:
        await emit(
            "status",
            {
                "step": "items",
                "title": "公众号这一站卡住了，正在补材料",
                "detail": "先公开读已有篇的正文，不够再补搜新篇，不把缺口留到报告里。",
            },
        )
    reads = 0
    for row in out:
        if not weixin_needs_topup(out, need):
            break
        if row.get("text_source") == "public":
            continue
        if reads >= WEIXIN_FALLBACK_READS:
            break
        if await fill_weixin_body(row, emit):
            reads += 1
    if len(out) < need and fetch_more and query:
        try:
            more = await fetch_more(query, min(20, need + 4))
        except Exception:
            more = []
        before = len(out)
        out = merge_weixin_topup(out, more or [], need, seen)
        if emit and len(out) > before:
            await emit(
                "log",
                {
                    "platform": "mp",
                    "message": f"公众号数量不够，补搜进 {len(out) - before} 篇。",
                },
            )
        for row in out:
            if not weixin_needs_topup(out, need):
                break
            if row.get("text_source") == "public":
                continue
            if reads >= WEIXIN_FALLBACK_READS:
                break
            if await fill_weixin_body(row, emit):
                reads += 1
    if emit:
        public_n = sum(1 for row in out if row.get("text_source") == "public")
        await emit(
            "log",
            {
                "platform": "mp",
                "message": f"公众号补齐后 {len(out)} 条，其中 {public_n} 条有正文。",
            },
        )
    return out


async def resolve_weixin_rows(
    rows: list[dict[str, Any]],
    job_dir: Path,
    emit: Emit | None = None,
    *,
    query: str = "",
    wanted: int = 0,
    fetch_more: FetchMore | None = None,
) -> list[dict[str, Any]]:
    others = [row for row in rows if not is_sogou_link(str(row.get("url") or ""))]
    targets = [row for row in rows if is_sogou_link(str(row.get("url") or ""))]
    if not targets:
        return rows
    need = wanted or len(targets)
    spare = targets[need:]
    queue = deque(targets[:need])
    kept: list[dict[str, Any]] = []
    seen = {key for key in (weixin_row_key(row) for row in rows) if key}
    from playwright.async_api import async_playwright

    from tools.board_preview import take_still
    from tools.chrome_launch import (
        adopt_page,
        launch_persistent_chrome,
        restore_crawler_window,
        settle_crawler_window,
    )

    PROFILE.mkdir(parents=True, exist_ok=True)
    preview = job_dir / "preview.jpg"
    os.environ["BOARD_PREVIEW_PATH"] = str(preview)
    os.environ.pop("BOARD_HIDE_BROWSER", None)
    if emit:
        await emit(
            "status",
            {
                "step": "search_page",
                "title": "正在用 Chrome 解开搜狗跳转",
                "detail": "第一次可能要过搜狗验证。请在弹出的 Chrome 里点，看板不再连拍。填完后等它自己跳。注销/迁移页会自动点「访问文章」。",
            },
        )
    context = None
    leftover: list[dict[str, Any]] = []
    started = time.monotonic()
    work_started: float | None = None
    try:
        async with async_playwright() as playwright:
            context = await launch_persistent_chrome(
                playwright.chromium,
                str(PROFILE),
                headless=False,
            )
            page = await adopt_page(context)
            page.set_default_timeout(20_000)
            await page.set_extra_http_headers({"Referer": "https://weixin.sogou.com/"})
            first = True
            minimized = False
            stuck_n = 0
            while queue and len(kept) < need:
                public_n = sum(1 for item in kept if item.get("text_source") == "public")
                reason = weixin_should_stop(
                    wanted=need,
                    public_n=public_n,
                    elapsed_s=time.monotonic() - started,
                    work_s=(time.monotonic() - work_started) if work_started is not None else None,
                )
                if reason:
                    leftover = list(queue)
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": (
                                    "已经读够几篇原文，其余先留下再补正文。"
                                    if reason == "enough"
                                    else "打开原文超过时限，先收住已有的，再补搜和公开读取。"
                                ),
                            },
                        )
                    break
                if _looks_like_captcha(page.url or ""):
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": "还停在搜狗验证页，不再打开下一篇，避免验证码被刷新。",
                            },
                        )
                    leftover = list(queue)
                    break
                row = queue.popleft()
                if emit:
                    await emit(
                        "status",
                        {
                            "step": "items",
                            "title": f"正在打开公众号原文 {len(kept) + 1}/{need}：{row.get('title') or ''}"[:80],
                            "detail": "搜狗跳到微信后读正文。卡住会换一篇或公开补读，不把缺口留空。",
                        },
                    )
                jump = sogou_jump_url(str(row.get("url") or ""))
                try:
                    await asyncio.wait_for(
                        page.goto(jump, wait_until="domcontentloaded", timeout=20000),
                        timeout=22,
                    )
                except Exception:
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": f"「{row.get('title') or '这篇'}」搜狗跳转卡住，这篇先留下，再换一篇。",
                            },
                        )
                    kept.append(row)
                    first = False
                    work_started = work_started or time.monotonic()
                    stuck_n += 1
                    nxt = await take_weixin_replacement(
                        query=query,
                        seen=seen,
                        spare=spare,
                        fetch_more=fetch_more,
                        wanted=need,
                        emit=emit,
                        why="跳转卡住",
                    )
                    if nxt:
                        queue.append(nxt)
                    if stuck_n >= WEIXIN_STUCK_GIVE_UP:
                        leftover = list(queue)
                        if emit:
                            await emit(
                                "log",
                                {
                                    "platform": "mp",
                                    "message": "连续几篇都卡住，不再跟这个窗口耗着，改公开补读和补搜。",
                                },
                            )
                        break
                    try:
                        page = await asyncio.wait_for(context.new_page(), timeout=8)
                        page.set_default_timeout(20_000)
                        await page.set_extra_http_headers({"Referer": "https://weixin.sogou.com/"})
                    except Exception:
                        leftover = list(queue)
                        break
                    continue
                if _looks_like_captcha(page.url or "") and minimized:
                    await restore_crawler_window(page)
                    minimized = False
                try:
                    final = await asyncio.wait_for(
                        _wait_weixin(page, emit, first),
                        timeout=(WEIXIN_FIRST_WAIT_S if first else 35) + 2,
                    )
                except Exception:
                    final = page.url or ""
                first = False
                work_started = work_started or time.monotonic()
                if _looks_like_captcha(final or page.url or ""):
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": "搜狗验证还没过完。窗口留下，后面的原文这一轮先不拆。",
                            },
                        )
                    leftover = [row, *queue]
                    break
                if not is_weixin_article(final):
                    if query and not weixin_row_relevant(query, row):
                        if emit:
                            await emit(
                                "log",
                                {
                                    "platform": "mp",
                                    "message": f"「{row.get('title') or '这篇'}」对不上检索地，丢掉补一条。",
                                },
                            )
                        nxt = await take_weixin_replacement(
                            query=query,
                            seen=seen,
                            spare=spare,
                            fetch_more=fetch_more,
                            wanted=need,
                            emit=emit,
                        )
                        if nxt:
                            queue.append(nxt)
                        continue
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": f"「{row.get('title') or '这篇'}」搜狗还没跳到原文，这篇先留下，再换一篇。",
                            },
                        )
                    kept.append(row)
                    stuck_n += 1
                    nxt = await take_weixin_replacement(
                        query=query,
                        seen=seen,
                        spare=spare,
                        fetch_more=fetch_more,
                        wanted=need,
                        emit=emit,
                        why="没跳到原文",
                    )
                    if nxt:
                        queue.append(nxt)
                    if stuck_n >= WEIXIN_STUCK_GIVE_UP:
                        leftover = list(queue)
                        if emit:
                            await emit(
                                "log",
                                {
                                    "platform": "mp",
                                    "message": "连续几篇都没打开，改公开补读和补搜。",
                                },
                            )
                        break
                    continue
                try:
                    final = await asyncio.wait_for(open_weixin_article(page), timeout=20)
                except Exception:
                    final = page.url or final
                if not minimized:
                    try:
                        await asyncio.wait_for(settle_crawler_window(page, "公众号"), timeout=10)
                    except Exception:
                        pass
                    minimized = True
                    if emit:
                        await emit(
                            "status",
                            {
                                "step": "logged_in",
                                "title": "公众号窗口已最小化",
                                "detail": "搜狗已跳到微信。后面在后台点迁移/注销页的「访问文章」，不用再盯窗口。",
                            },
                        )
                try:
                    await page.wait_for_selector("#js_content, #activity-name", timeout=8000)
                except Exception:
                    pass
                html = await _page_html(page)
                article = article_from_html(html, final)
                if not article or is_weixin_expired(html):
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": f"「{row.get('title') or '这篇'}」原文已失效，补搜一条。",
                            },
                        )
                    nxt = await take_weixin_replacement(
                        query=query,
                        seen=seen,
                        spare=spare,
                        fetch_more=fetch_more,
                        wanted=need,
                        emit=emit,
                    )
                    if nxt:
                        queue.append(nxt)
                    continue
                try:
                    await asyncio.wait_for(take_still(context), timeout=8)
                except Exception:
                    pass
                apply_weixin_article(row, article)
                try:
                    from local_media import prefetch_row_images
                except ImportError:
                    from board.local_media import prefetch_row_images
                try:
                    await prefetch_row_images(job_dir, "mp", row)
                except Exception:
                    pass
                stuck_n = 0
                if query and not weixin_row_relevant(query, row):
                    if emit:
                        await emit(
                            "log",
                            {
                                "platform": "mp",
                                "message": f"「{row.get('title') or '这篇'}」原文不是这个地方的，丢掉补一条。",
                            },
                        )
                    nxt = await take_weixin_replacement(
                        query=query,
                        seen=seen,
                        spare=spare,
                        fetch_more=fetch_more,
                        wanted=need,
                        emit=emit,
                    )
                    if nxt:
                        queue.append(nxt)
                    continue
                kept.append(row)
                if emit:
                    await emit(
                        "status",
                        {
                            "step": "items",
                            "title": f"已打开公众号原文：{row.get('title') or ''}"[:80],
                            "detail": "搜狗跳转已经走到 mp.weixin.qq.com。",
                        },
                    )
    except Exception as error:
        leftover.extend(list(queue))
        if emit:
            await emit(
                "log",
                {"platform": "mp", "message": f"搜狗跳转没走完：{error}"},
            )
    finally:
        await _close_context(context)
    leftover.extend([row for row in queue if row not in leftover and row not in kept])
    filled = await top_up_weixin_rows(
        kept + leftover,
        query=query,
        wanted=need,
        seen=seen,
        fetch_more=fetch_more,
        emit=emit,
    )
    return others + filled
