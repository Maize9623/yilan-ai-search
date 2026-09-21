"""Public foreign search for 智搜: 一览-style HTTP plus optional local feedgrab."""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

FOREIGN_PLATFORMS: list[tuple[str, str]] = [
    ("gnews", "Google News"),
    ("bsky", "Bluesky"),
    ("reddit", "Reddit"),
    ("yt", "YouTube"),
]
FOREIGN_MAP = dict(FOREIGN_PLATFORMS)
FOREIGN_CODES = set(FOREIGN_MAP)
WEIXIN_CODE = "mp"
CONNECTOR_CODES = FOREIGN_CODES | {WEIXIN_CODE}
WEIXIN_SEARCH_CAP = 20

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_YILAN = Path(os.getenv("YILAN_ROOT", "/Users/gene/Desktop/信息看板"))


def youtube_ready() -> bool:
    return bool(os.getenv("YOUTUBE_API_KEY", "").strip())


def feedgrab_ready() -> bool:
    python = _YILAN / ".local/feedgrab-venv/bin/python"
    worker = _YILAN / "scripts/feedgrab-worker.py"
    return python.is_file() and worker.is_file()


def foreign_availability() -> dict[str, dict[str, Any]]:
    return {
        "gnews": {"id": "gnews", "label": "Google News", "ready": True, "note": "英文公开新闻索引"},
        "bsky": {"id": "bsky", "label": "Bluesky", "ready": True, "note": "公开帖搜索"},
        "reddit": {
            "id": "reddit",
            "label": "Reddit",
            "ready": True,
            "note": "公开帖搜索。官网 JSON 常 403，feedgrab 要登录；打不开就走公开归档。",
        },
        "yt": {
            "id": "yt",
            "label": "YouTube",
            "ready": youtube_ready(),
            "note": "需要 YOUTUBE_API_KEY" if not youtube_ready() else "公开视频搜索",
        },
        "mp": {
            "id": "mp",
            "label": "公众号",
            "ready": feedgrab_ready(),
            "note": "搜狗微信公开索引；本机要有 feedgrab" if not feedgrab_ready() else "搜狗微信公开索引，摘要不是全文",
        },
    }


def _text(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return _text(value.get("title") or value.get("text") or value.get("#text") or "")
    text = unescape(re.sub(r"<[^>]+>", " ", str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _link(value: Any) -> str:
    if isinstance(value, dict):
        href = value.get("href") or value.get("@href") or value.get("#text") or ""
        return str(href).strip()
    return str(value or "").strip()


def parse_rss_items(xml: str) -> list[dict[str, str]]:
    if not xml or "<" not in xml:
        return []
    text = re.sub(r"xmlns(?::\w+)?=\"[^\"]+\"", "", xml)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items: list[dict[str, str]] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag != "item":
            continue
        fields: dict[str, str] = {}
        for child in list(node):
            key = child.tag.split("}")[-1]
            if child.text and child.text.strip():
                fields[key] = child.text.strip()
            href = child.attrib.get("href") or child.attrib.get("url")
            if href and key.lower() in {"link", "guid"}:
                fields[key] = href
        title = _text(fields.get("title"), 240)
        url = _link(fields.get("link") or fields.get("guid"))
        if not title or not url:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "desc": _text(fields.get("description"), 800),
                "source": _text(fields.get("source") or fields.get("News:Source"), 120),
                "pub": _text(fields.get("pubDate") or fields.get("pubdate"), 80),
            }
        )
    return items


def _item_id(platform: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    return digest


def _row(
    platform: str,
    raw_id: str,
    *,
    title: str,
    text: str,
    url: str,
    author: str = "",
    liked: str = "",
    comments: str = "",
    shares: str = "",
    image: str = "",
    kind: str = "note",
    published_at: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "id": raw_id,
        "note_id": raw_id,
        "title": title,
        "desc": text or title,
        "content": text or title,
        "url": url,
        "author": author,
        "like_count": liked,
        "comment_count": comments,
        "share_count": shares,
        "cover_url": image,
        "type": kind,
        "published_at": published_at,
        "lang": "en",
        "text_source": "index" if platform == "gnews" else "public",
    }
    if extra:
        row.update(extra)
    return row


async def _get(url: str, *, headers: dict[str, str] | None = None, timeout: float = 12) -> str:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        response = await client.get(url, headers={"User-Agent": _UA, "Accept": "*/*", **(headers or {})})
    response.raise_for_status()
    return response.text


async def search_google_news(query: str, limit: int) -> list[dict[str, Any]]:
    term = query.strip() or "news"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote(term)}&hl=en-US&gl=US&ceid=US:en"
    )
    xml = await _get(url)
    rows: list[dict[str, Any]] = []
    for entry in parse_rss_items(xml)[: max(1, limit)]:
        raw_id = _item_id("gnews", entry["url"])
        rows.append(
            _row(
                "gnews",
                raw_id,
                title=entry["title"],
                text=entry["desc"] or "该新闻索引未提供摘要，请看原文。",
                url=entry["url"],
                author=entry["source"],
                published_at=entry["pub"],
                extra={"text_source": "index"},
            )
        )
    return rows


async def search_bing_news(query: str, limit: int) -> list[dict[str, Any]]:
    url = f"https://www.bing.com/news/search?q={quote(query.strip() or 'news')}&format=rss&setlang=en-us"
    xml = await _get(url)
    rows: list[dict[str, Any]] = []
    for entry in parse_rss_items(xml)[: max(1, limit)]:
        raw_id = _item_id("gnews", entry["url"])
        rows.append(
            _row(
                "gnews",
                raw_id,
                title=entry["title"],
                text=entry["desc"] or "该新闻索引未提供摘要，请看原文。",
                url=entry["url"],
                author=entry["source"],
                published_at=entry["pub"],
                extra={"text_source": "index"},
            )
        )
    return rows


def _bluesky_url(uri: str, handle: str) -> str:
    if handle and uri:
        rkey = uri.rsplit("/", 1)[-1]
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    return uri


_BSKY_TASK = re.compile(
    r"选购建议|购买建议|选购指南|购买指南|怎么选|怎么买|选哪个|买哪个|选购|购买|建议|指南|推荐|评测|攻略|"
    r"buying guide|purchase guide|how to buy|\bbuying\b|\bpurchase\b",
    re.I,
)


def bluesky_latin_span(query: str) -> str:
    return " ".join(re.findall(r"[A-Za-z][A-Za-z0-9+.\-]*", query or "")).strip()


def bluesky_search_terms(query: str) -> list[str]:
    text = re.sub(r"\s+", " ", (query or "").strip())
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = re.sub(r"\s+", " ", term).strip()
        if len(cleaned) < 2:
            return
        if any(cleaned.lower() == row.lower() for row in terms):
            return
        terms.append(cleaned)

    add(text)
    add(_BSKY_TASK.sub(" ", text))
    add(bluesky_latin_span(text))
    return terms or ["news"]


def bluesky_search_urls(query: str, limit: int) -> list[str]:
    term = quote(query.strip() or "news")
    cap = min(100, max(10, limit))
    hosts = ("https://api.bsky.app", "https://public.api.bsky.app")
    paths = (
        f"/xrpc/app.bsky.feed.searchPostsV2?query={term}&limit={cap}&sort=top",
        f"/xrpc/app.bsky.feed.searchPostsV2?query={term}&limit={cap}&sort=top&allTime=true",
        f"/xrpc/app.bsky.feed.searchPosts?q={term}&limit={cap}",
    )
    return [f"{host}{path}" for path in paths for host in hosts]


def _bluesky_posts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    posts = payload.get("posts") or payload.get("feed") or []
    if not isinstance(posts, list):
        return []
    return [row for row in posts if isinstance(row, dict)]


def _bluesky_row(post: dict[str, Any]) -> dict[str, Any] | None:
    body = post.get("post") if isinstance(post.get("post"), dict) else post
    author = body.get("author") if isinstance(body.get("author"), dict) else {}
    record = body.get("record") if isinstance(body.get("record"), dict) else {}
    handle = _text(author.get("handle"), 80)
    text = _text(record.get("text"), 700)
    uri = _text(body.get("uri"), 400)
    url = _bluesky_url(uri, handle)
    if not text or not url:
        return None
    title = text if len(text) <= 90 else text[:88] + "…"
    raw_id = _text(body.get("cid"), 40) or _item_id("bsky", uri or url)
    embed = body.get("embed") if isinstance(body.get("embed"), dict) else {}
    image = ""
    images = embed.get("images") if isinstance(embed.get("images"), list) else []
    if images and isinstance(images[0], dict):
        image = _text(images[0].get("fullsize") or images[0].get("thumb"), 500)
    return _row(
        "bsky",
        raw_id,
        title=title,
        text=text,
        url=url,
        author=_text(author.get("displayName"), 80) or (f"@{handle}" if handle else ""),
        liked=str(body.get("likeCount") or ""),
        comments=str(body.get("replyCount") or ""),
        shares=str(body.get("repostCount") or ""),
        image=image,
        published_at=_text(record.get("createdAt"), 40),
    )


async def search_bluesky(query: str, limit: int) -> list[dict[str, Any]]:
    last_error = ""
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    ask = max(1, limit)
    for term in bluesky_search_terms(query):
        if len(rows) >= ask:
            break
        for path in bluesky_search_urls(term, ask):
            try:
                raw = await _get(path)
            except Exception as error:
                last_error = str(error)
                continue
            if not raw.lstrip().startswith(("{", "[")):
                last_error = raw[:120]
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                last_error = raw[:120]
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("error") and not _bluesky_posts(payload):
                last_error = str(payload.get("message") or payload.get("error"))
                continue
            for post in _bluesky_posts(payload):
                row = _bluesky_row(post)
                if not row:
                    continue
                key = str(row.get("url") or row.get("id") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                if len(rows) >= ask:
                    return rows
            if rows:
                return rows
    if not rows and last_error:
        raise RuntimeError(f"Bluesky 公开搜索没开：{last_error[:160]}")
    return rows


def reddit_row_from_post(data: dict[str, Any]) -> dict[str, Any] | None:
    title = _text(data.get("title"), 240)
    permalink = _text(data.get("permalink"), 400)
    url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else _text(data.get("url"), 500)
    if not title or not url:
        return None
    text = _text(data.get("selftext"), 1600) or title
    raw_id = _text(data.get("id"), 20) or _item_id("reddit", url)
    image = ""
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
    images = preview.get("images") if isinstance(preview.get("images"), list) else []
    if images and isinstance(images[0], dict):
        source = images[0].get("source") if isinstance(images[0].get("source"), dict) else {}
        image = unquote(_text(source.get("url"), 500).replace("&amp;", "&"))
    return _row(
        "reddit",
        raw_id,
        title=title,
        text=text,
        url=url,
        author=_text(data.get("author"), 80),
        liked=str(data.get("ups") or data.get("score") or ""),
        comments=str(data.get("num_comments") or ""),
        image=image,
        published_at=str(data.get("created_utc") or ""),
    )


def reddit_rows_from_posts(posts: list[Any], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in posts[: max(1, limit)]:
        data = post
        if isinstance(post, dict) and isinstance(post.get("data"), dict) and "title" not in post:
            data = post.get("data")
        if not isinstance(data, dict):
            continue
        row = reddit_row_from_post(data)
        if row:
            rows.append(row)
    return rows


async def _search_reddit_http(query: str, limit: int) -> list[dict[str, Any]]:
    url = (
        "https://www.reddit.com/search.json?"
        f"q={quote(query.strip() or 'news')}&sort=relevance&limit={min(25, max(5, limit))}&raw_json=1"
    )
    raw = await _get(url, headers={"Accept": "application/json"})
    payload = json.loads(raw)
    children = ((payload.get("data") or {}).get("children") or []) if isinstance(payload, dict) else []
    return reddit_rows_from_posts(children, limit)


async def _search_reddit_pullpush(query: str, limit: int) -> list[dict[str, Any]]:
    url = (
        "https://api.pullpush.io/reddit/search/submission/?"
        f"q={quote(query.strip() or 'news')}&size={min(25, max(5, limit))}"
    )
    raw = await _get(url, headers={"Accept": "application/json"})
    payload = json.loads(raw)
    posts = payload.get("data") if isinstance(payload, dict) else payload
    return reddit_rows_from_posts(posts if isinstance(posts, list) else [], limit)


def _feedgrab_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("candidates") or result.get("items") or result.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


async def _run_feedgrab(payload: dict[str, Any], timeout: float = 65) -> dict[str, Any] | None:
    import asyncio

    if not feedgrab_ready():
        return None
    python = _YILAN / ".local/feedgrab-venv/bin/python"
    worker = _YILAN / "scripts/feedgrab-worker.py"
    work = _YILAN / ".local/feedgrab-work"
    work.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        str(python),
        str(worker),
        cwd=str(work),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            "NODE_ENV": "production",
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "FEEDGRAB_DATA_DIR": str(work / "sessions"),
        },
    )
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        stdout, _err = await asyncio.wait_for(process.communicate(raw), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        return None
    try:
        result = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result


def rows_from_feedgrab_search(platform: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _feedgrab_candidates(result):
        title = _text(item.get("title"), 240)
        url = _text(item.get("url") or item.get("sogou_url"), 4096)
        if not title or not url:
            continue
        source = "index" if platform == WEIXIN_CODE else "feedgrab"
        rows.append(
            _row(
                platform,
                _item_id(platform, url),
                title=title,
                text=_text(item.get("snippet") or item.get("content") or item.get("summary"), 1600) or title,
                url=url,
                author=_text(item.get("author"), 80),
                image=_text(item.get("image") or item.get("thumbnail"), 500),
                published_at=_text(item.get("publishedAt") or item.get("publishedText") or item.get("publish_date"), 80),
                extra={"text_source": source, "lang": "zh" if platform == WEIXIN_CODE else "en"},
            )
        )
    return rows


async def _search_reddit_feedgrab(query: str, limit: int) -> list[dict[str, Any]]:
    result = await _run_feedgrab(
        {"operation": "search", "platform": "reddit", "query": query[:180], "limit": min(20, max(1, limit))}
    )
    if not result:
        return []
    return rows_from_feedgrab_search("reddit", result)


async def _feedgrab_read(url: str) -> dict[str, Any] | None:
    result = await _run_feedgrab({"url": url, "operation": "detail"}, timeout=28)
    if not result:
        return None
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    text = _text(content.get("content"), 4000)
    if len(text) < 30:
        return None
    extra = content.get("extra") if isinstance(content.get("extra"), dict) else {}
    raw_html = str(content.get("content") or "")
    images: list[str] = []
    try:
        from weixin_sogou import weixin_pic_urls

        images = weixin_pic_urls(raw_html)
    except Exception:
        images = []
    cover = _text(extra.get("cover_image") or extra.get("thumbnail"), 500)
    if cover and cover not in images:
        images = [cover] + images
    return {
        "title": _text(content.get("title"), 240),
        "text": text,
        "url": _text(content.get("url"), 500) or url,
        "author": _text(extra.get("author") or extra.get("author_name"), 80),
        "image": cover or (images[0] if images else ""),
        "images": images[:8],
    }


async def search_weixin(query: str, limit: int) -> list[dict[str, Any]]:
    if not feedgrab_ready():
        return []
    from weixin_sogou import filter_weixin_rows, weixin_row_key, weixin_search_query

    wanted = min(WEIXIN_SEARCH_CAP, max(1, limit))
    ask = min(WEIXIN_SEARCH_CAP, wanted + min(6, max(2, (wanted + 2) // 3)))
    compact = weixin_search_query(query)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    async def take(term: str) -> None:
        nonlocal rows
        result = await _run_feedgrab(
            {
                "operation": "search",
                "platform": "mpweixin",
                "query": term[:180],
                "limit": ask,
            }
        )
        if not result:
            return
        for row in filter_weixin_rows(query, rows_from_feedgrab_search(WEIXIN_CODE, result)):
            key = weixin_row_key(row)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(row)

    if compact:
        await take(compact)
    if len(rows) < wanted and compact != (query or "").strip() and (query or "").strip():
        await take(query.strip())
    return rows[:ask]


async def search_reddit(query: str, limit: int) -> list[dict[str, Any]]:
    errors: list[str] = []
    if feedgrab_ready():
        try:
            rows = await _search_reddit_feedgrab(query, limit)
            if rows:
                return rows
            errors.append("本机 feedgrab 要先登录 Reddit")
        except Exception as error:
            errors.append(f"feedgrab：{error}")
    try:
        rows = await _search_reddit_http(query, limit)
        if rows:
            return rows
        errors.append("reddit.com 没有搜到帖")
    except Exception as error:
        errors.append(f"reddit.com：{error}")
    try:
        rows = await _search_reddit_pullpush(query, limit)
        if rows:
            return rows
        errors.append("公开归档没有搜到帖")
    except Exception as error:
        errors.append(f"公开归档：{error}")
    raise RuntimeError("；".join(errors) or "Reddit 公开检索没有收回内容")


async def search_youtube(query: str, limit: int) -> list[dict[str, Any]]:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return []
    params = (
        "part=snippet&type=video&order=relevance"
        f"&q={quote(query.strip() or 'video')}&maxResults={min(50, max(5, limit))}"
    )
    raw = await _get(
        f"https://www.googleapis.com/youtube/v3/search?key={quote(key)}&{params}",
        headers={"Accept": "application/json"},
    )
    payload = json.loads(raw)
    rows: list[dict[str, Any]] = []
    for video in payload.get("items") or []:
        if not isinstance(video, dict):
            continue
        vid = _text((video.get("id") or {}).get("videoId") if isinstance(video.get("id"), dict) else "", 20)
        snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
        title = _text(snippet.get("title"), 240)
        if not vid or not title:
            continue
        thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        image = ""
        for key_name in ("high", "medium", "default"):
            pack = thumbs.get(key_name) if isinstance(thumbs.get(key_name), dict) else {}
            if pack.get("url"):
                image = str(pack["url"])
                break
        rows.append(
            _row(
                "yt",
                vid,
                title=title,
                text=_text(snippet.get("description"), 500) or "视频未提供可用简介，请打开原视频。未见口播原文。",
                url=f"https://www.youtube.com/watch?v={vid}",
                author=_text(snippet.get("channelTitle"), 80),
                image=image,
                kind="video",
                published_at=_text(snippet.get("publishedAt"), 40),
                extra={"text_source": "desc"},
            )
        )
    return rows[: max(1, limit)]


async def search_foreign(platform: str, query: str, limit: int) -> list[dict[str, Any]]:
    if platform == "gnews":
        try:
            rows = await search_google_news(query, limit)
        except Exception:
            rows = []
        if len(rows) < max(3, limit // 3):
            try:
                extra = await search_bing_news(query, limit)
                seen = {row.get("url") for row in rows}
                for row in extra:
                    if row.get("url") not in seen:
                        rows.append(row)
            except Exception:
                pass
        return rows[:limit]
    if platform == "bsky":
        return await search_bluesky(query, limit)
    if platform == "reddit":
        return await search_reddit(query, limit)
    if platform == "yt":
        return await search_youtube(query, limit)
    if platform == WEIXIN_CODE:
        return await search_weixin(query, limit)
    return []


def write_foreign_jsonl(job_dir: Path, platform: str, rows: list[dict[str, Any]]) -> Path:
    folder = job_dir / platform / "jsonl"
    folder.mkdir(parents=True, exist_ok=True)
    from datetime import date

    path = folder / f"search_contents_{date.today().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def looks_english(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    ascii_n = sum(1 for ch in compact if ord(ch) < 128)
    return ascii_n / len(compact) >= 0.85
