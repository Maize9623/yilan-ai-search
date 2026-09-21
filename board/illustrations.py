"""Fill Eleven illustration queries from public image APIs. Never invent URLs."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urlsplit

import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_OK_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_BAD_EXT = (".djvu", ".pdf", ".tif", ".tiff", ".svg", ".stl", ".ogg", ".ogv", ".webm")
_OK_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_DOMESTIC_HOSTS = (
    "baidu.com",
    "bdimg.com",
    "bcebos.com",
    "sogoucdn.com",
    "sogou.com",
    "qhimg.com",
    "so.com",
)
_LOCAL_HINTS = ("成都", "四川", "川西", "重庆", "chengdu", "sichuan", "chongqing")
_OFF_HINTS = ("japan", "tokyo", "kyoto", "osaka", "日本", "京都", "大阪", "东京", "canada", "加拿大", "vancouver")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_credit(value: Any) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", _as_text(value)))
    return re.sub(r"\s+", " ", text).strip()[:80]


def _off_topic(query: str, blob: str) -> bool:
    text = f"{query or ''}"
    hay = f"{blob or ''}"
    if not any(token in text for token in _LOCAL_HINTS):
        return False
    if any(token in hay.lower() or token in hay for token in _OFF_HINTS):
        if not any(token in hay.lower() or token in hay for token in _LOCAL_HINTS):
            return True
    return False


def _looks_program(text: str) -> bool:
    low = (text or "").lower()
    return any(mark in low for mark in _PROGRAM_MARKS)


def _has_mark(text: str, marks: tuple[str, ...] | frozenset[str]) -> bool:
    raw = text or ""
    low = raw.lower()
    return any(mark in low or mark in raw for mark in marks)


def _image_off_topic(query: str, blob: str) -> bool:
    if _off_topic(query, blob):
        return True
    if _looks_program(query) and _has_mark(blob, _MAP_OR_DECOR):
        return True
    return False


def image_host(url: str) -> str:
    return urlsplit(_as_text(url).split("#")[0]).netloc.lower()


def is_domestic_image_url(url: str) -> bool:
    host = image_host(url)
    return any(host == name or host.endswith(f".{name}") for name in _DOMESTIC_HOSTS)


def usable_image_url(url: str) -> bool:
    raw = _as_text(url)
    if not raw.startswith("https://"):
        return False
    path = urlsplit(raw.split("#")[0]).path.lower()
    if any(path.endswith(ext) for ext in _BAD_EXT):
        return False
    if is_domestic_image_url(raw):
        return True
    return any(path.endswith(ext) for ext in _OK_EXT)


def keep_illustration_url(url: str, source: str = "") -> bool:
    return bool(usable_image_url(url) and source and is_domestic_image_url(url))


def normalize_illustration_queries(raw: Any, limit: int = 2) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        if isinstance(row, str):
            query = _as_text(row)
            caption = query
        elif isinstance(row, dict):
            query = _as_text(row.get("query") or row.get("q") or row.get("name"))
            caption = _as_text(row.get("caption") or row.get("note") or query)
        else:
            continue
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        packed = {"query": query[:80], "caption": caption[:160]}
        url = _as_text(row.get("url") if isinstance(row, dict) else "")
        source = _as_text(row.get("source") if isinstance(row, dict) else "")
        if keep_illustration_url(url, source):
            packed["url"] = url
            packed["source"] = source[:180]
        out.append(packed)
        if len(out) >= limit:
            break
    return out


def _pick_commons(payload: dict[str, Any], query: str = "") -> dict[str, str] | None:
    pages = (payload.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict):
        return None
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        infos = page.get("imageinfo") or []
        if not infos or not isinstance(infos[0], dict):
            continue
        info = infos[0]
        mime = _as_text(info.get("mime")).lower()
        title = _as_text(page.get("title"))
        if mime and mime not in _OK_MIME:
            continue
        if "CADAL" in title.upper() or title.lower().endswith(".djvu"):
            continue
        artist_raw = ""
        meta = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
        if isinstance(meta.get("Artist"), dict):
            artist_raw = _clean_credit(meta["Artist"].get("value"))
        if _image_off_topic(query, f"{title} {artist_raw}"):
            continue
        thumb = _as_text(info.get("thumburl"))
        orig = _as_text(info.get("url"))
        url = thumb if usable_image_url(thumb) else orig
        if not usable_image_url(url):
            continue
        meta = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
        artist = ""
        if isinstance(meta.get("Artist"), dict):
            artist = _clean_credit(meta["Artist"].get("value"))
        license_name = ""
        if isinstance(meta.get("LicenseShortName"), dict):
            license_name = _clean_credit(meta["LicenseShortName"].get("value"))
        source = "Wikimedia Commons"
        if artist:
            source = f"{source} · {artist}"
        if license_name:
            source = f"{source} · {license_name}"
        return {"url": url, "source": source[:180]}
    return None


def _pick_openverse(payload: dict[str, Any], query: str = "") -> dict[str, str] | None:
    results = payload.get("results") or []
    if not isinstance(results, list):
        return None
    for row in results:
        if not isinstance(row, dict):
            continue
        url = _as_text(row.get("url") or row.get("thumbnail"))
        if not usable_image_url(url):
            continue
        creator = _as_text(row.get("creator"))
        tags = row.get("tags") or []
        tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else _as_text(tags)
        blob = f"{row.get('title') or ''} {row.get('description') or ''} {tag_text} {creator}"
        if _image_off_topic(query, blob):
            continue
        license_name = _as_text(row.get("license"))
        source = "Openverse"
        if creator:
            source = f"{source} · {creator[:80]}"
        if license_name:
            source = f"{source} · {license_name}"
        return {"url": url, "source": source[:180]}
    return None


def _loads_loose(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for candidate in (raw, re.sub(r"\\'", "'", raw)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _hit_matches(title: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    hay = (title or "").lower()
    return any(token.lower() in hay or token in (title or "") for token in tokens)


def _pick_image_row(
    url: str,
    title: str,
    source: str,
    query: str,
    skip_urls: set[str] | None,
    prefer_tokens: list[str] | None,
) -> dict[str, str] | None:
    if not keep_illustration_url(url, source):
        return None
    if skip_urls and url in skip_urls:
        return None
    if _image_off_topic(query, title):
        return None
    if prefer_tokens and not _hit_matches(title, prefer_tokens):
        return None
    return {"url": url, "source": source[:180], "title": title}


def _pick_baidu(
    payload: dict[str, Any],
    query: str = "",
    skip_urls: set[str] | None = None,
    prefer_tokens: list[str] | None = None,
) -> dict[str, str] | None:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    fallback: dict[str, str] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _as_text(row.get("middleURL") or row.get("thumbURL") or row.get("hoverURL"))
        title = _clean_credit(row.get("fromPageTitle") or row.get("fromPageTitleEnc"))
        host = _as_text(row.get("fromURLHost"))
        source = "百度图片"
        if host:
            source = f"{source} · {host[:60]}"
        elif title:
            source = f"{source} · {title[:60]}"
        hit = _pick_image_row(url, title, source, query, skip_urls, prefer_tokens)
        if hit:
            return {"url": hit["url"], "source": hit["source"]}
        if fallback is None:
            loose = _pick_image_row(url, title, source, query, skip_urls, None)
            if loose and not prefer_tokens:
                fallback = {"url": loose["url"], "source": loose["source"]}
    return fallback


def _pick_so(
    payload: dict[str, Any],
    query: str = "",
    skip_urls: set[str] | None = None,
    prefer_tokens: list[str] | None = None,
) -> dict[str, str] | None:
    rows = payload.get("list") or payload.get("result")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _as_text(row.get("img") or row.get("thumb") or row.get("qhimg_url"))
        title = _clean_credit(row.get("title"))
        source = "360图片"
        if title:
            source = f"{source} · {title[:60]}"
        hit = _pick_image_row(url, title, source, query, skip_urls, prefer_tokens)
        if hit:
            return {"url": hit["url"], "source": hit["source"]}
    return None


def _search_terms(query: str) -> list[str]:
    text = _as_text(query)
    terms = [text]
    places: list[str] = []
    nouns: list[str] = []
    try:
        import jieba.posseg as pseg

        for word, flag in pseg.cut(text):
            token = str(word).strip()
            if len(token) < 2:
                continue
            if flag in {"ns", "nrt"}:
                places.append(token)
            elif flag.startswith("n") or flag in {"vn", "nz"}:
                nouns.append(token)
    except Exception:
        pass
    nouns = [token for token in nouns if token.lower() not in _ABSTRACT_ILLUS]
    if places and nouns:
        terms.append(f"{places[0]} {nouns[0]}")
    elif nouns:
        terms.append(nouns[0])
    en: list[str] = []
    if "成都" in text:
        en.append("Chengdu")
    elif "四川" in text or "川西" in text:
        en.append("Sichuan")
    if "茶馆" in text:
        en.append("teahouse")
    elif "古镇" in text:
        en.append("ancient town")
    elif "石桥" in text:
        en.append("stone bridge")
    if en:
        terms.append(" ".join(en))
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            out.append(term)
    return out


def _browser_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "application/json, text/javascript, */*;q=0.8",
        "Referer": referer,
    }


def _fetch_baidu(
    client: httpx.Client,
    query: str,
    topic: str = "",
    skip_urls: set[str] | None = None,
    prefer_tokens: list[str] | None = None,
) -> dict[str, str] | None:
    response = client.get(
        "https://image.baidu.com/search/acjson",
        params={
            "tn": "resultjson_com",
            "ipn": "rj",
            "ct": "201326592",
            "fp": "result",
            "queryWord": query,
            "word": query,
            "ie": "utf-8",
            "oe": "utf-8",
            "pn": "0",
            "rn": "20",
            "istype": "2",
            "nc": "1",
        },
        headers=_browser_headers("https://image.baidu.com/"),
    )
    if response.status_code >= 400:
        return None
    payload = _loads_loose(response.text)
    return (
        _pick_baidu(payload, topic or query, skip_urls=skip_urls, prefer_tokens=prefer_tokens)
        if payload
        else None
    )


def _fetch_so(
    client: httpx.Client,
    query: str,
    topic: str = "",
    skip_urls: set[str] | None = None,
    prefer_tokens: list[str] | None = None,
) -> dict[str, str] | None:
    response = client.get(
        "https://image.so.com/j",
        params={"q": query, "src": "srp", "correct": "1", "pn": "10", "sn": "0"},
        headers=_browser_headers("https://image.so.com/"),
    )
    if response.status_code >= 400:
        return None
    payload = _loads_loose(response.text)
    return (
        _pick_so(payload, topic or query, skip_urls=skip_urls, prefer_tokens=prefer_tokens)
        if payload
        else None
    )


def _fetch_one(client: httpx.Client, query: str, topic: str = "") -> dict[str, str] | None:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    commons = client.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6,
            "gsrlimit": 8,
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": 1280,
        },
        headers=headers,
    )
    if commons.status_code < 400:
        hit = _pick_commons(commons.json(), topic or query)
        if hit:
            return hit
    openverse = client.get(
        "https://api.openverse.org/v1/images/",
        params={"q": query, "page_size": 8, "license_type": "all"},
        headers=headers,
    )
    if openverse.status_code < 400:
        return _pick_openverse(openverse.json(), topic or query)
    return None


def _accept_hit(hit: dict[str, str] | None) -> dict[str, str] | None:
    if not hit:
        return None
    if keep_illustration_url(hit.get("url") or "", hit.get("source") or ""):
        return hit
    return None


_ILLUS_STOP = {
    "上手",
    "体验",
    "建议",
    "选购",
    "指南",
    "评测",
    "真机",
    "综合",
    "研判",
    "检索",
    "报告",
    "这次",
    "现在",
    "怎么",
    "如何",
}
_ABSTRACT_ILLUS = frozenset(
    {
        "入门",
        "学习",
        "规划",
        "路线",
        "路线图",
        "计划",
        "教程",
        "指南",
        "推荐",
        "攻略",
        "课程",
        "路径",
        "方法",
        "开始",
        "完整",
        "最佳",
        "免费",
        "roadmap",
        "plan",
        "guide",
        "learning",
        "learn",
        "beginner",
        "curriculum",
    }
)
_PROGRAM_MARKS = (
    "python",
    "javascript",
    "typescript",
    "golang",
    "java",
    "linux",
    "c++",
    "编程",
    "代码",
    "程序员",
    "开发",
)
_MAP_OR_DECOR = (
    "地图",
    "map",
    "导航",
    "卫星",
    "地形",
    "户型",
    "装修",
    "玄关",
    "客厅",
    "卧室",
    "家具",
    "室内设计",
    "样板间",
    "floor plan",
)


def illustration_topic_tokens(*texts: str) -> list[str]:
    try:
        from freshness import QUERY_STOP, query_tokens
    except ImportError:
        from board.freshness import QUERY_STOP, query_tokens

    skip = QUERY_STOP | _ILLUS_STOP | _ABSTRACT_ILLUS
    ordered: list[str] = []
    for text in texts:
        for token in query_tokens(text):
            if token in skip or token.lower() in skip or token in ordered:
                continue
            if token.isascii() and len(token) < 3:
                continue
            if len(token) < 2:
                continue
            ordered.append(token)
    return ordered[:6]


def illustration_core_tokens(*texts: str) -> list[str]:
    tokens = illustration_topic_tokens(*texts)
    cores = [token for token in tokens if token.lower() not in _ABSTRACT_ILLUS]
    return cores[:4] or tokens[:2]


def bind_illustration_query(query: str, topic: str = "") -> str:
    q = _as_text(query)
    topic = _as_text(topic)
    cores = illustration_core_tokens(topic, q)
    if _looks_program(f"{topic} {q}"):
        lead = " ".join(cores[:2]) if cores else "编程"
        return f"{lead} 编程 代码".strip()[:80]
    cleaned = q
    for word in ("路线图", "学习路线", "路线", "规划"):
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cores and not any(core.lower() in cleaned.lower() or core in cleaned for core in cores[:3]):
        cleaned = f"{' '.join(cores[:2])} {cleaned}".strip()
    return (cleaned or q)[:80]


def illustration_needs_refetch(row: dict[str, str], topic: str = "") -> bool:
    blob = " ".join(_as_text(row.get(key)) for key in ("query", "caption", "title"))
    topic = _as_text(topic)
    if not blob:
        return False
    if _image_off_topic(topic or blob, blob):
        return True
    if _looks_program(topic) and _has_mark(blob, ("路线", "规划", "入门")) and not _looks_program(blob):
        return True
    cores = illustration_core_tokens(topic)
    if cores and not any(core.lower() in blob.lower() or core in blob for core in cores):
        return True
    return False


def suggest_illustration_queries(query: str, text: str, limit: int = 2) -> list[dict[str, str]]:
    try:
        from freshness import QUERY_STOP, query_places
    except ImportError:
        from board.freshness import QUERY_STOP, query_places

    blob = " ".join(part for part in (query, text) if part)
    if not blob.strip():
        return []
    skip = QUERY_STOP | {
        "活动",
        "信息",
        "材料",
        "内容",
        "问题",
        "争议",
        "限制",
        "标签",
        "叙事",
        "软广",
        "汇总",
        "决策",
        "用户",
        "讨论",
        "专门",
        "大型",
        "值得",
        "主要",
        "人们",
        "跨年",
        "陷阱",
    }
    if _looks_program(blob):
        cores = illustration_core_tokens(query, text)
        lead = cores[0] if cores else "编程"
        return [
            {"query": f"{lead} 编程 代码"[:80], "caption": f"{lead} 编程 代码"[:160]},
            {"query": f"{lead} 教程"[:80], "caption": f"{lead} 教程"[:160]},
        ][: max(1, limit)]
    places = query_places(text) or query_places(query)
    nouns: list[str] = []
    try:
        import jieba.posseg as pseg

        for word, flag in pseg.cut(text or query):
            token = str(word).strip()
            if len(token) < 2 or token in skip or token in places:
                continue
            if flag in {"ns", "nrt", "nz", "n", "vn"} and token.lower() not in _ABSTRACT_ILLUS:
                nouns.append(token)
    except Exception:
        nouns.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", text or query))
    terms: list[str] = []
    if places and nouns:
        terms.append(f"{places[0]}{nouns[0]}")
    for place in places[:2]:
        extra = "中秋" if "中秋" in blob else ("国庆" if "国庆" in blob else "")
        terms.append(f"{place}{extra}" if extra else place)
    terms.extend(nouns[:2])
    if query and query not in terms:
        terms.append(query[:20])
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for term in terms:
        key = term.strip().lower()
        if len(term.strip()) < 2 or key in seen:
            continue
        seen.add(key)
        out.append({"query": term.strip()[:80], "caption": term.strip()[:160]})
        if len(out) >= limit:
            break
    return out


def fetch_public_image(
    query: str,
    topic: str = "",
    skip_urls: set[str] | None = None,
) -> dict[str, str] | None:
    q = bind_illustration_query(query, topic)
    if not q:
        return None
    prefer = illustration_core_tokens(topic, query)
    skip = skip_urls or set()
    try:
        with httpx.Client(timeout=10, follow_redirects=True, trust_env=False) as client:
            terms = _search_terms(q)
            for term in terms:
                hit = _accept_hit(
                    _fetch_baidu(
                        client, term, topic=q, skip_urls=skip, prefer_tokens=prefer
                    )
                )
                if hit:
                    return hit
            for term in terms:
                hit = _accept_hit(
                    _fetch_so(
                        client, term, topic=q, skip_urls=skip, prefer_tokens=prefer
                    )
                )
                if hit:
                    return hit
    except Exception:
        return None
    return None


def fill_illustration_hits(
    rows: list[dict[str, str]],
    topic: str = "",
    used_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    used = used_urls if used_urls is not None else set()
    filled: list[dict[str, str]] = []
    for row in rows:
        query = bind_illustration_query(_as_text(row.get("query")), topic)
        caption = _as_text(row.get("caption") or row.get("query") or query)
        url = _as_text(row.get("url"))
        source = _as_text(row.get("source"))
        packed = {"query": query, "caption": caption}
        keep_old = (
            keep_illustration_url(url, source)
            and url not in used
            and not illustration_needs_refetch(row, topic)
        )
        if keep_old:
            packed["url"] = url
            packed["source"] = source
            used.add(url)
            filled.append(packed)
            continue
        hit = _accept_hit(fetch_public_image(query, topic=topic, skip_urls=used))
        if hit and hit.get("url") and hit["url"] not in used:
            packed["url"] = hit["url"]
            packed["source"] = hit["source"]
            used.add(hit["url"])
        filled.append(packed)
    return filled
