# -*- coding: utf-8 -*-
"""Current hot lists for 一览.

Lists come from a NewsNow-compatible `/api/s?id=` endpoint (MIT).
Default upstream is the public instance; set NEWSNOW_BASE to a local fork.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

NEWSNOW_BASE = os.getenv("NEWSNOW_BASE", "https://newsnow.busiyi.world").rstrip("/")
CACHE_TTL = max(30, int(os.getenv("NEWSNOW_CACHE_SECONDS", "180")))
FETCH_TIMEOUT = 18.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": f"{NEWSNOW_BASE}/",
}

# NewsNow source metadata, flattened to fetchable ids. Disabled upstream
# sources (linux.do / 果核剥壳 / 什么值得买) are omitted.
_SOURCES: tuple[dict[str, str], ...] = (
    {"id": "weibo", "name": "微博", "title": "实时热搜", "column": "china", "kind": "hottest", "home": "https://weibo.com"},
    {"id": "baidu", "name": "百度热搜", "title": "热搜", "column": "china", "kind": "hottest", "home": "https://www.baidu.com"},
    {"id": "zhihu", "name": "知乎", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.zhihu.com"},
    {"id": "douyin", "name": "抖音", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.douyin.com"},
    {"id": "bilibili-hot-search", "name": "哔哩哔哩", "title": "热搜", "column": "china", "kind": "hottest", "home": "https://www.bilibili.com"},
    {"id": "toutiao", "name": "今日头条", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.toutiao.com"},
    {"id": "tieba", "name": "百度贴吧", "title": "热议", "column": "china", "kind": "hottest", "home": "https://tieba.baidu.com"},
    {"id": "thepaper", "name": "澎湃新闻", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.thepaper.cn"},
    {"id": "ifeng", "name": "凤凰网", "title": "热点资讯", "column": "china", "kind": "hottest", "home": "https://www.ifeng.com"},
    {"id": "kuaishou", "name": "快手", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.kuaishou.com"},
    {"id": "tencent-hot", "name": "腾讯新闻", "title": "综合早报", "column": "china", "kind": "hottest", "home": "https://news.qq.com"},
    {"id": "douban", "name": "豆瓣", "title": "热门电影", "column": "china", "kind": "hottest", "home": "https://www.douban.com"},
    {"id": "bilibili-hot-video", "name": "哔哩哔哩", "title": "热门视频", "column": "china", "kind": "hottest", "home": "https://www.bilibili.com"},
    {"id": "bilibili-ranking", "name": "哔哩哔哩", "title": "排行榜", "column": "china", "kind": "hottest", "home": "https://www.bilibili.com"},
    {"id": "qqvideo-tv-hotsearch", "name": "腾讯视频", "title": "热搜榜", "column": "china", "kind": "hottest", "home": "https://v.qq.com/"},
    {"id": "iqiyi-hot-ranklist", "name": "爱奇艺", "title": "热播榜", "column": "china", "kind": "hottest", "home": "https://www.iqiyi.com"},
    {"id": "freebuf", "name": "Freebuf", "title": "网络安全", "column": "china", "kind": "hottest", "home": "https://www.freebuf.com/"},
    {"id": "nowcoder", "name": "牛客", "title": "热榜", "column": "china", "kind": "hottest", "home": "https://www.nowcoder.com"},
    {"id": "chongbuluo-hot", "name": "虫部落", "title": "最热", "column": "china", "kind": "hottest", "home": "https://www.chongbuluo.com"},
    {"id": "chongbuluo-latest", "name": "虫部落", "title": "最新", "column": "china", "kind": "", "home": "https://www.chongbuluo.com"},
    {"id": "zaobao", "name": "联合早报", "title": "即时", "column": "world", "kind": "realtime", "home": "https://www.zaobao.com"},
    {"id": "cankaoxiaoxi", "name": "参考消息", "title": "要闻", "column": "world", "kind": "", "home": "https://china.cankaoxiaoxi.com"},
    {"id": "sputniknewscn", "name": "卫星通讯社", "title": "要闻", "column": "world", "kind": "", "home": "https://sputniknews.cn"},
    {"id": "kaopu", "name": "靠谱新闻", "title": "汇编", "column": "world", "kind": "", "home": "https://kaopu.news/"},
    {"id": "steam", "name": "Steam", "title": "在线人数", "column": "world", "kind": "hottest", "home": "https://store.steampowered.com"},
    {"id": "ithome", "name": "IT之家", "title": "最新", "column": "tech", "kind": "realtime", "home": "https://www.ithome.com"},
    {"id": "sspai", "name": "少数派", "title": "热榜", "column": "tech", "kind": "hottest", "home": "https://sspai.com"},
    {"id": "juejin", "name": "稀土掘金", "title": "热榜", "column": "tech", "kind": "hottest", "home": "https://juejin.cn"},
    {"id": "36kr-renqi", "name": "36氪", "title": "人气榜", "column": "tech", "kind": "hottest", "home": "https://36kr.com"},
    {"id": "36kr-quick", "name": "36氪", "title": "快讯", "column": "tech", "kind": "realtime", "home": "https://36kr.com"},
    {"id": "v2ex-share", "name": "V2EX", "title": "最新分享", "column": "tech", "kind": "", "home": "https://v2ex.com/"},
    {"id": "coolapk", "name": "酷安", "title": "今日最热", "column": "tech", "kind": "hottest", "home": "https://coolapk.com"},
    {"id": "solidot", "name": "Solidot", "title": "最新", "column": "tech", "kind": "", "home": "https://solidot.org"},
    {"id": "aihot", "name": "AIHOT", "title": "最新", "column": "tech", "kind": "realtime", "home": "https://aihot.virxact.com/all"},
    {"id": "pcbeta-windows11", "name": "远景论坛", "title": "Win11", "column": "tech", "kind": "realtime", "home": "https://bbs.pcbeta.com"},
    {"id": "hackernews", "name": "Hacker News", "title": "热榜", "column": "tech", "kind": "hottest", "home": "https://news.ycombinator.com/"},
    {"id": "github-trending-today", "name": "Github", "title": "Today", "column": "tech", "kind": "hottest", "home": "https://github.com/"},
    {"id": "producthunt", "name": "Product Hunt", "title": "热榜", "column": "tech", "kind": "hottest", "home": "https://www.producthunt.com/"},
    {"id": "cls-hot", "name": "财联社", "title": "热门", "column": "finance", "kind": "hottest", "home": "https://www.cls.cn"},
    {"id": "cls-telegraph", "name": "财联社", "title": "电报", "column": "finance", "kind": "realtime", "home": "https://www.cls.cn"},
    {"id": "cls-depth", "name": "财联社", "title": "深度", "column": "finance", "kind": "", "home": "https://www.cls.cn"},
    {"id": "wallstreetcn-hot", "name": "华尔街见闻", "title": "最热", "column": "finance", "kind": "hottest", "home": "https://wallstreetcn.com/"},
    {"id": "wallstreetcn-quick", "name": "华尔街见闻", "title": "快讯", "column": "finance", "kind": "realtime", "home": "https://wallstreetcn.com/"},
    {"id": "wallstreetcn-news", "name": "华尔街见闻", "title": "最新", "column": "finance", "kind": "", "home": "https://wallstreetcn.com/"},
    {"id": "xueqiu-hotstock", "name": "雪球", "title": "热门股票", "column": "finance", "kind": "hottest", "home": "https://xueqiu.com"},
    {"id": "gelonghui", "name": "格隆汇", "title": "事件", "column": "finance", "kind": "realtime", "home": "https://www.gelonghui.com"},
    {"id": "jin10", "name": "金十数据", "title": "快讯", "column": "finance", "kind": "realtime", "home": "https://www.jin10.com"},
    {"id": "mktnews-flash", "name": "MKTNews", "title": "快讯", "column": "finance", "kind": "", "home": "https://mktnews.net"},
    {"id": "fastbull-express", "name": "法布财经", "title": "快讯", "column": "finance", "kind": "realtime", "home": "https://www.fastbull.cn"},
    {"id": "fastbull-news", "name": "法布财经", "title": "头条", "column": "finance", "kind": "", "home": "https://www.fastbull.cn"},
    {"id": "hupu", "name": "虎扑", "title": "主干道热帖", "column": "sports", "kind": "hottest", "home": "https://hupu.com"},
    {"id": "dongqiudi", "name": "懂球帝", "title": "头条", "column": "sports", "kind": "realtime", "home": "https://www.dongqiudi.com"},
)

# Parent ids in NewsNow redirect to the first sub-list.
ALIASES = {
    "v2ex": "v2ex-share",
    "mktnews": "mktnews-flash",
    "wallstreetcn": "wallstreetcn-quick",
    "36kr": "36kr-quick",
    "pcbeta": "pcbeta-windows11",
    "cls": "cls-telegraph",
    "xueqiu": "xueqiu-hotstock",
    "fastbull": "fastbull-express",
    "github": "github-trending-today",
    "bilibili": "bilibili-hot-search",
    "tencent": "tencent-hot",
    "qqvideo": "qqvideo-tv-hotsearch",
    "iqiyi": "iqiyi-hot-ranklist",
    "chongbuluo": "chongbuluo-latest",
}

# Kept out of the first 最热 board; they stay in 更多 for starring.
HIDDEN_DEFAULTS = frozenset(
    {
        "coolapk",
        "hupu",
        "nowcoder",
        "steam",
        "chongbuluo",
        "chongbuluo-hot",
        "chongbuluo-latest",
    }
)

DEFAULT_FRONT = (
    "weibo",
    "baidu",
    "zhihu",
    "douyin",
    "bilibili-hot-search",
    "toutiao",
    "tieba",
    "thepaper",
    "ifeng",
    "kuaishou",
)

COLUMNS = (
    ("china", "国内"),
    ("world", "国际"),
    ("tech", "科技"),
    ("finance", "财经"),
    ("sports", "体育"),
)

_SOURCE_MAP = {row["id"]: row for row in _SOURCES}
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_JUNK_MARK = re.compile(r"[#＃\[\]【】]+")
_JUNK_WORD = re.compile(r"(实时热搜|热搜榜|今日最热|热搜|热榜|热议)")


def resolve_id(raw: str) -> str:
    text = (raw or "").strip()
    return ALIASES.get(text, text)


def source_of(raw: str) -> dict[str, str] | None:
    sid = resolve_id(raw)
    row = _SOURCE_MAP.get(sid)
    return dict(row) if row else None


def known_ids() -> set[str]:
    return set(_SOURCE_MAP) | set(ALIASES)


def catalog() -> dict[str, Any]:
    return {
        "base": NEWSNOW_BASE,
        "defaults": list(DEFAULT_FRONT),
        "hidden": sorted(HIDDEN_DEFAULTS),
        "columns": [{"id": key, "label": label} for key, label in COLUMNS],
        "sources": [dict(row) for row in _SOURCES],
    }


def fallback_keyword(title: str) -> str:
    text = _JUNK_MARK.sub(" ", title or "")
    text = _JUNK_WORD.sub(" ", text)
    text = " ".join(text.split()).strip(" -_/|")
    return text[:80]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _item(title: Any, url: Any = "", item_id: Any = "") -> dict[str, str] | None:
    text = " ".join(str(title or "").split())
    href = str(url or "").strip()
    if not text:
        return None
    return {
        "id": str(item_id or text)[:180],
        "title": text[:200],
        "url": href[:500],
    }


def _take(rows: list[dict[str, str] | None], limit: int = 30) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not row or row["title"] in seen:
            continue
        seen.add(row["title"])
        items.append(row)
        if len(items) >= limit:
            break
    return items


def parse_weibo(payload: Any) -> list[dict[str, str]]:
    band = ((payload or {}).get("data") or {}).get("band_list") if isinstance(payload, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in band or []:
        if not isinstance(row, dict):
            continue
        title = row.get("word") or row.get("note") or ""
        query = row.get("word_scheme") or title
        rows.append(
            _item(title, f"https://s.weibo.com/weibo?q={quote(str(query))}", row.get("word"))
        )
    return _take(rows)


def parse_baidu(payload: Any) -> list[dict[str, str]]:
    cards = ((payload or {}).get("data") or {}).get("cards") if isinstance(payload, dict) else None
    content: list[Any] = []
    if isinstance(cards, list) and cards and isinstance(cards[0], dict):
        raw = cards[0].get("content")
        if isinstance(raw, list):
            content = raw
    rows: list[dict[str, str] | None] = []
    for row in content:
        if not isinstance(row, dict) or row.get("isTop"):
            continue
        rows.append(_item(row.get("word") or row.get("query"), row.get("rawUrl") or row.get("url"), row.get("rawUrl")))
    return _take(rows)


def parse_zhihu(payload: Any) -> list[dict[str, str]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        title = ((target.get("title_area") or {}) if isinstance(target.get("title_area"), dict) else {}).get("text")
        link = ((target.get("link") or {}) if isinstance(target.get("link"), dict) else {}).get("url")
        rows.append(_item(title, link, link))
    return _take(rows)


def parse_douyin(payload: Any) -> list[dict[str, str]]:
    words = (payload or {}).get("word_list") if isinstance(payload, dict) else None
    if not words and isinstance(payload, dict):
        words = ((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}).get("word_list")
    rows: list[dict[str, str] | None] = []
    for row in words or []:
        if not isinstance(row, dict):
            continue
        title = row.get("word") or row.get("sentence") or ""
        sid = row.get("sentence_id") or title
        href = f"https://www.douyin.com/hot/{sid}" if row.get("sentence_id") else f"https://www.douyin.com/search/{quote(str(title))}"
        rows.append(_item(title, href, sid))
    return _take(rows)


def parse_bilibili(payload: Any) -> list[dict[str, str]]:
    data = (payload or {}).get("list") if isinstance(payload, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        title = row.get("show_name") or row.get("keyword") or ""
        rows.append(_item(title, f"https://search.bilibili.com/all?keyword={quote(str(title))}", row.get("hot_id") or title))
    return _take(rows)


def parse_toutiao(payload: Any) -> list[dict[str, str]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        rows.append(_item(row.get("Title"), row.get("Url"), row.get("ClusterId") or row.get("Title")))
    return _take(rows)


def parse_tieba(payload: Any) -> list[dict[str, str]]:
    topic = ((((payload or {}).get("data") or {}) if isinstance(payload, dict) else {}).get("bang_topic") or {})
    raw = topic.get("topic_list") if isinstance(topic, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        title = row.get("topic_name") or ""
        tid = row.get("topic_id") or title
        href = (
            f"https://tieba.baidu.com/hottopic/browse/hottopic"
            f"?topic_id={quote(str(tid))}&topic_name={quote(str(title))}"
        )
        rows.append(_item(title, href, tid))
    return _take(rows)


def parse_thepaper(payload: Any) -> list[dict[str, str]]:
    news = ((payload or {}).get("data") or {}).get("hotNews") if isinstance(payload, dict) else None
    rows: list[dict[str, str] | None] = []
    for row in news or []:
        if not isinstance(row, dict):
            continue
        cont = row.get("contId") or ""
        href = str(row.get("link") or "").strip()
        if not href and cont:
            href = f"https://www.thepaper.cn/newsDetail_forward_{cont}"
        elif href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.thepaper.cn" + href
        rows.append(_item(row.get("name"), href, cont or row.get("name")))
    return _take(rows)


def parse_ifeng_html(html: str) -> list[dict[str, str]]:
    start = html.find("var allData")
    if start < 0:
        return []
    brace = html.find("{", start)
    if brace < 0:
        return []
    depth = 0
    end = -1
    for index, ch in enumerate(html[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end < 0:
        return []
    try:
        payload = json.loads(html[brace:end])
    except Exception:
        return []
    rows: list[dict[str, str] | None] = []
    for row in payload.get("hotNews1") or []:
        if isinstance(row, dict):
            rows.append(_item(row.get("title"), row.get("url"), row.get("url")))
    return _take(rows)


def _clean_item(row: Any, index: int) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    title = " ".join(str(row.get("title") or "").split())
    url = str(row.get("url") or row.get("mobileUrl") or "").strip()
    if not title:
        return None
    return {
        "id": str(row.get("id") or f"{index}:{title}")[:180],
        "title": title[:200],
        "url": url[:500],
    }


def normalize_list(source_id: str, payload: Any, error: str = "") -> dict[str, Any]:
    meta = source_of(source_id) or {
        "id": resolve_id(source_id),
        "name": source_id,
        "title": "",
        "column": "",
        "kind": "",
        "home": "",
    }
    items: list[dict[str, str]] = []
    updated = 0
    if isinstance(payload, dict):
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            for index, row in enumerate(raw_items[:30], start=1):
                item = _clean_item(row, index)
                if item:
                    items.append(item)
        try:
            updated = int(payload.get("updatedTime") or 0)
        except (TypeError, ValueError):
            updated = 0
    return {
        "ok": bool(items) and not error,
        "id": meta["id"],
        "name": meta["name"],
        "title": meta["title"],
        "column": meta["column"],
        "kind": meta["kind"],
        "home": meta["home"],
        "updated": updated,
        "items": items,
        "error": error,
    }


def _cached(source_id: str) -> dict[str, Any] | None:
    hit = _cache.get(source_id)
    if not hit:
        return None
    stamp, payload = hit
    if time.time() - stamp > CACHE_TTL:
        return None
    return payload


def remember(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _cache[source_id] = (time.time(), payload)
    return payload


async def _http_json(url: str, referer: str) -> Any:
    headers = {**_HEADERS, "Referer": referer}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.json()


async def _http_text(url: str, referer: str) -> str:
    headers = {**_HEADERS, "Referer": referer, "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.text


async def _direct_weibo() -> list[dict[str, str]]:
    return parse_weibo(await _http_json("https://weibo.com/ajax/statuses/hot_band", "https://weibo.com/"))


async def _direct_baidu() -> list[dict[str, str]]:
    return parse_baidu(
        await _http_json("https://top.baidu.com/api/board?tab=realtime", "https://top.baidu.com/board?tab=realtime")
    )


async def _direct_zhihu() -> list[dict[str, str]]:
    return parse_zhihu(
        await _http_json(
            "https://www.zhihu.com/api/v3/feed/topstory/hot-list-web?limit=30&desktop=true",
            "https://www.zhihu.com/hot",
        )
    )


async def _direct_douyin() -> list[dict[str, str]]:
    return parse_douyin(
        await _http_json(
            "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/",
            "https://www.douyin.com/",
        )
    )


async def _direct_bilibili() -> list[dict[str, str]]:
    return parse_bilibili(await _http_json("https://s.search.bilibili.com/main/hotword", "https://www.bilibili.com/"))


async def _direct_toutiao() -> list[dict[str, str]]:
    return parse_toutiao(
        await _http_json("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc", "https://www.toutiao.com/")
    )


async def _direct_tieba() -> list[dict[str, str]]:
    return parse_tieba(
        await _http_json("https://tieba.baidu.com/hottopic/browse/topicList", "https://tieba.baidu.com/")
    )


async def _direct_thepaper() -> list[dict[str, str]]:
    return parse_thepaper(
        await _http_json("https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar", "https://www.thepaper.cn/")
    )


async def _direct_ifeng() -> list[dict[str, str]]:
    return parse_ifeng_html(await _http_text("https://www.ifeng.com/", "https://www.ifeng.com/"))


DIRECT_FETCHERS: dict[str, Callable[[], Awaitable[list[dict[str, str]]]]] = {
    "weibo": _direct_weibo,
    "baidu": _direct_baidu,
    "zhihu": _direct_zhihu,
    "douyin": _direct_douyin,
    "bilibili-hot-search": _direct_bilibili,
    "toutiao": _direct_toutiao,
    "tieba": _direct_tieba,
    "thepaper": _direct_thepaper,
    "ifeng": _direct_ifeng,
}


def _fresh_row(source_id: str, items: list[dict[str, str]]) -> dict[str, Any]:
    return normalize_list(source_id, {"updatedTime": _now_ms(), "items": items})


async def _from_direct(source_id: str) -> dict[str, Any] | None:
    fetcher = DIRECT_FETCHERS.get(source_id)
    if not fetcher:
        return None
    try:
        items = await fetcher()
    except Exception:
        return None
    if not items:
        return None
    return _fresh_row(source_id, items)


async def _from_newsnow(source_id: str, force: bool = False) -> dict[str, Any]:
    params: dict[str, str] = {"id": source_id}
    if force:
        params["latest"] = "1"
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(f"{NEWSNOW_BASE}/api/s", params=params, headers=_HEADERS)
        if response.status_code >= 400:
            return normalize_list(source_id, {}, f"热榜暂时连不上（HTTP {response.status_code}）")
        payload = response.json()
    except Exception as error:
        return normalize_list(source_id, {}, f"热榜暂时连不上：{error}")
    row = normalize_list(source_id, payload)
    if force and row["ok"] and isinstance(payload, dict) and payload.get("status") == "cache":
        # Public NewsNow still refuses latest without login; keep items but don't pretend we just fetched.
        return row
    if force and row["ok"]:
        row = normalize_list(source_id, {"updatedTime": _now_ms(), "items": row["items"]})
    return row


async def fetch_list(source_id: str, force: bool = False) -> dict[str, Any]:
    meta = source_of(source_id)
    if not meta:
        return normalize_list(source_id, {}, "没有这个热搜源")
    sid = meta["id"]
    if not force:
        cached = _cached(sid)
        if cached:
            return cached
    direct = await _from_direct(sid)
    if direct and direct.get("ok"):
        return remember(sid, direct)
    row = await _from_newsnow(sid, force=force)
    if row.get("ok"):
        return remember(sid, row)
    stale = _cache.get(sid)
    if stale:
        return stale[1]
    return row


async def fetch_lists(raw_ids: list[str], force: bool = False) -> list[dict[str, Any]]:
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        meta = source_of(raw)
        if not meta or meta["id"] in seen:
            continue
        seen.add(meta["id"])
        wanted.append(meta["id"])
        if len(wanted) >= 16:
            break
    if not wanted:
        return []
    return list(await asyncio.gather(*[fetch_list(sid, force=force) for sid in wanted]))
