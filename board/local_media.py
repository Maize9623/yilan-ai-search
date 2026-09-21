"""Download 公众号 / 知乎 images to the job folder, then serve them locally."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

LOCAL_IMAGE_PLATFORMS = frozenset({"mp", "zhihu"})
MIN_IMAGE_BYTES = 3500
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def sniff_media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def looks_like_real_image(data: bytes, url: str = "") -> bool:
    if not data or not sniff_media_type(data):
        return False
    host = urlparse(url).netloc.lower() if url else ""
    if "qpic.cn" in host or "qlogo.cn" in host or "weixin" in host:
        return len(data) >= MIN_IMAGE_BYTES
    return len(data) >= 800


def cache_is_real(path: Path, url: str = "") -> bool:
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
        if size < 800:
            return False
        with path.open("rb") as handle:
            head = handle.read(16)
        if not sniff_media_type(head):
            return False
        host = urlparse(url).netloc.lower() if url else ""
        if "qpic.cn" in host or "qlogo.cn" in host or "weixin" in host:
            return size >= MIN_IMAGE_BYTES
        return True
    except OSError:
        return False


def image_referers(url: str, platform: str = "", page_url: str = "") -> list[str]:
    host = urlparse(url).netloc.lower()
    page = (page_url or "").split("#")[0].strip()
    if "qpic.cn" in host or "qlogo.cn" in host or "weixin" in host or "sogou" in host:
        refs = []
        if page.startswith("https://mp.weixin.qq.com/"):
            refs.append(page)
        refs.append("https://mp.weixin.qq.com/")
        refs.append("")
        return refs
    if "zhimg.com" in host or "zhihu.com" in host:
        refs = []
        if page.startswith("https://www.zhihu.com/") or page.startswith("https://zhuanlan.zhihu.com/"):
            refs.append(page)
        refs.append("https://www.zhihu.com/")
        refs.append("https://zhuanlan.zhihu.com/")
        return refs
    if "douyin" in host or "byteimg" in host:
        return ["https://www.douyin.com/"]
    if "kuaishou" in host or "kwimgs" in host:
        return ["https://www.kuaishou.com/"]
    if "hdslb" in host or "bilibili" in host:
        return ["https://www.bilibili.com/"]
    if "xiaohongshu" in host or "xhscdn" in host:
        return ["https://www.xiaohongshu.com/"]
    if "sinaimg" in host or "weibo" in host or "weibocdn" in host:
        return ["https://m.weibo.cn/"]
    if "wikimedia" in host or "wikipedia" in host:
        return ["https://commons.wikimedia.org/"]
    if "reddit" in host or "redd.it" in host:
        return ["https://www.reddit.com/"]
    if "bsky" in host or "bluesky" in host:
        return ["https://bsky.app/"]
    fallback = {
        "xhs": "https://www.xiaohongshu.com/",
        "dy": "https://www.douyin.com/",
        "ks": "https://www.kuaishou.com/",
        "bili": "https://www.bilibili.com/",
        "wb": "https://m.weibo.cn/",
        "tieba": "https://tieba.baidu.com/",
        "zhihu": "https://www.zhihu.com/",
        "mp": "https://mp.weixin.qq.com/",
    }
    return [fallback.get(platform or "", "https://www.xiaohongshu.com/")]


def media_cache_path(job_dir: Path, item_id: str, index: int, url: str) -> Path:
    digest = hashlib.sha1(f"{item_id}:{index}:{url}".encode("utf-8")).hexdigest()[:20]
    return job_dir / "media" / f"{digest}.jpg"


def row_item_id(platform: str, row: dict[str, Any]) -> str:
    raw = str(row.get("id") or row.get("note_id") or row.get("content_id") or "").strip()
    if raw.startswith(f"{platform}:"):
        return raw
    return f"{platform}:{raw}" if raw else ""


def row_image_urls(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("images", "cover_url", "image_list", "image"):
        value = row.get(key)
        parts = value if isinstance(value, list) else str(value or "").replace(";", ",").split(",")
        for part in parts:
            url = str(part).strip()
            if url.startswith("http") and url not in urls:
                urls.append(url)
    return urls[:8]


def _headers(referer: str) -> dict[str, str]:
    headers = {
        "User-Agent": _UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


async def fetch_image_bytes(
    url: str,
    *,
    platform: str = "",
    page_url: str = "",
    page: Any = None,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    if page is not None:
        try:
            response = await page.request.get(url, timeout=15000)
            data = await response.body() if response.ok else b""
            if looks_like_real_image(data, url):
                return data
        except Exception:
            pass
    own = client is None
    session = client or httpx.AsyncClient(timeout=20, follow_redirects=True, trust_env=False)
    try:
        for referer in image_referers(url, platform, page_url):
            try:
                response = await session.get(url, headers=_headers(referer))
            except Exception:
                continue
            if response.status_code >= 400 or not response.content:
                continue
            if looks_like_real_image(response.content, url):
                return response.content
    finally:
        if own:
            await session.aclose()
    return None


def attach_local_images(job_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    images = item.get("images") or []
    if not images:
        return item
    item_id = str(item.get("id") or "")
    out: list[str] = []
    for index, url in enumerate(images):
        text = str(url or "")
        if text.startswith("frame:"):
            out.append(text)
            continue
        path = media_cache_path(job_dir, item_id, index, text)
        out.append(f"frame:{path}" if cache_is_real(path, text) else text)
    item["images"] = out
    return item


async def prefetch_row_images(
    job_dir: Path,
    platform: str,
    row: dict[str, Any],
    *,
    page: Any = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    item_id = row_item_id(platform, row)
    images = list(row.get("images") or []) or row_image_urls(row)
    if not item_id or not images:
        return 0
    page_url = str(row.get("url") or row.get("content_url") or "")
    saved = 0
    cache_dir = job_dir / "media"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(images[:8]):
        text = str(url or "")
        if text.startswith("frame:"):
            saved += 1
            continue
        if not text.startswith("http"):
            continue
        path = media_cache_path(job_dir, item_id, index, text)
        if cache_is_real(path, text):
            saved += 1
            continue
        data = await fetch_image_bytes(
            text, platform=platform, page_url=page_url, page=page, client=client
        )
        if not data:
            continue
        path.write_bytes(data)
        saved += 1
    return saved


async def prefetch_item_images(
    job_dir: Path,
    items: list[dict[str, Any]],
    *,
    platforms: set[str] | None = None,
) -> int:
    wanted = platforms or LOCAL_IMAGE_PLATFORMS
    targets = [
        item
        for item in items
        if item.get("platform") in wanted and row_image_urls(item)
    ]
    if not targets:
        return 0
    sem = asyncio.Semaphore(6)

    async def one(item: dict[str, Any], client: httpx.AsyncClient) -> int:
        async with sem:
            count = await prefetch_row_images(
                job_dir, str(item.get("platform") or ""), item, client=client
            )
            attach_local_images(job_dir, item)
            return count

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, trust_env=False) as client:
        counts = await asyncio.gather(*(one(item, client) for item in targets))
    return sum(counts)
