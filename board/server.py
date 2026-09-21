#!/usr/bin/env python3
"""Local search board: MediaCrawler plus public foreign search, then 十一 writes the board."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import time
import uuid
from html import unescape
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

BOARD_DIR = Path(__file__).resolve().parent
ROOT = BOARD_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))

from export_report import board_to_html, board_to_markdown, safe_filename
from setup import (
    active_agent,
    add_custom_agent,
    add_key,
    agent_persona,
    agent_preview,
    boot_keys,
    delete_custom_agent,
    delete_key,
    get_agent,
    public_state,
    save_keys,
    set_active_agent,
    set_active_key,
)
from foreign import (
    CONNECTOR_CODES,
    FOREIGN_CODES,
    FOREIGN_MAP,
    FOREIGN_PLATFORMS,
    WEIXIN_CODE,
    bluesky_search_terms,
    foreign_availability,
    feedgrab_ready,
    looks_english,
    search_foreign,
    search_weixin,
    write_foreign_jsonl,
)
from freshness import (
    cluster_cross_platform,
    dated_items,
    fetch_budget,
    freshness_summary,
    item_freshness,
    pick_unique_items,
    select_items,
    stamp_freshness,
)
from intent_frame import (
    NARRATIVE_MODES,
    detect_query_intent,
    pick_frame,
    pick_tag,
    report_intent,
)
from illustrations import (
    bind_illustration_query,
    fill_illustration_hits,
    keep_illustration_url,
    normalize_illustration_queries,
    suggest_illustration_queries,
    usable_image_url,
)
from local_media import (
    LOCAL_IMAGE_PLATFORMS,
    attach_local_images,
    cache_is_real,
    fetch_image_bytes,
    media_cache_path,
    prefetch_item_images,
    sniff_media_type,
)
from weixin_sogou import resolve_weixin_rows, weixin_search_query
from hot import catalog as hot_catalog
from hot import fallback_keyword, fetch_list, fetch_lists, source_of

from tools.video_read import (
    clip_spoken,
    format_duration,
    load_sidecar,
    sidecar_frames,
    write_sidecar_scene,
)

RUNS_DIR = BOARD_DIR / "runs"
STATIC_DIR = BOARD_DIR / "static"
ELEVEN_PATH = BOARD_DIR / "eleven.md"

load_dotenv(BOARD_DIR / ".env")

HOST = os.getenv("BOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("BOARD_PORT", "8765"))
HARD_MAX_NOTES = 200
DEFAULT_NOTES = max(1, min(int(os.getenv("MAX_NOTES_PER_PLATFORM", "30")), HARD_MAX_NOTES))
ELEVEN_VIDEO_FULL = 12
ELEVEN_VIDEO_TEXT_FULL = 5000
ELEVEN_VIDEO_TEXT_BRIEF = 800
HARD_MAX_COMMENTS = 20
COMMENTS_READY = False
PLATFORM_TIMEOUT = max(180, int(os.getenv("PLATFORM_TIMEOUT_SECONDS", "600")))
LOGIN_TYPE = os.getenv("LOGIN_TYPE", "qrcode")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip()
DEEPSEEK_ENDPOINT = os.getenv(
    "DEEPSEEK_ENDPOINT", "https://api.deepseek.com/responses"
).strip()


def reload_runtime_keys() -> None:
    global DEEPSEEK_KEY, DEEPSEEK_MODEL, DEEPSEEK_ENDPOINT
    DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"
    DEEPSEEK_ENDPOINT = os.getenv(
        "DEEPSEEK_ENDPOINT", "https://api.deepseek.com/responses"
    ).strip()
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))

DOMESTIC_PLATFORMS: list[tuple[str, str]] = [
    ("xhs", "小红书"),
    ("dy", "抖音"),
    ("ks", "快手"),
    ("bili", "B站"),
    ("wb", "微博"),
    ("tieba", "贴吧"),
    ("zhihu", "知乎"),
    (WEIXIN_CODE, "公众号"),
]
PLATFORMS: list[tuple[str, str]] = [*DOMESTIC_PLATFORMS, *FOREIGN_PLATFORMS]
PLATFORM_MAP = dict(PLATFORMS)
# crawler --platform codes vs store folder names
PLATFORM_DATA_DIRS = {
    "xhs": ("xhs",),
    "dy": ("dy", "douyin"),
    "ks": ("ks", "kuaishou"),
    "bili": ("bili", "bilibili"),
    "wb": ("wb", "weibo"),
    "tieba": ("tieba",),
    "zhihu": ("zhihu",),
    "mp": ("mp",),
    "gnews": ("gnews",),
    "bsky": ("bsky",),
    "reddit": ("reddit",),
    "yt": ("yt",),
}


@dataclass
class SearchOptions:
    max_notes: int
    comments: bool
    sub_comments: bool
    max_comments: int
    eleven: bool
    agent_search: bool = False
    no_ai: bool = False
    notes: dict[str, int] = field(default_factory=dict)
    platform_queries: dict[str, str] = field(default_factory=dict)
    refine_note: str = ""

    def query_for(self, platform: str, fallback: str) -> str:
        return (self.platform_queries.get(platform) or "").strip() or fallback

    def notes_for(self, platform: str) -> int:
        try:
            wanted = int(self.notes.get(platform, self.max_notes))
        except (TypeError, ValueError):
            wanted = self.max_notes
        return max(1, min(wanted, HARD_MAX_NOTES))

    def fetch_for(self, platform: str) -> int:
        cap = 20 if platform == WEIXIN_CODE else HARD_MAX_NOTES
        return fetch_budget(self.notes_for(platform), cap)

    @property
    def timeout(self) -> int:
        extra = 120 if self.comments else 0
        extra += 60 if self.sub_comments else 0
        biggest = max([self.max_notes, *self.notes.values()], default=self.max_notes)
        extra += min(480, max(0, (biggest - 30) * 2))
        return PLATFORM_TIMEOUT + extra

_CLAIM = ["confirmed", "reported", "speculative", "unknown"]
_ROLE = ["support", "counter", "context", "noise"]
_ACT = ["act", "read", "watch", "know", "ignore"]

BOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["report", "judgment", "references"],
    "properties": {
        "report": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title",
                "deck",
                "overview",
                "sources",
                "sections",
                "map",
                "facts",
                "picks",
                "platform_briefs",
            ],
            "properties": {
                "title": {"type": "string"},
                "deck": {"type": "string"},
                "overview": {"type": "string"},
                "sources": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["summary", "platforms", "coverage"],
                    "properties": {
                        "summary": {"type": "string"},
                        "coverage": {"type": "string"},
                        "platforms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "count", "note"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "note": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "heading",
                            "thesis",
                            "body",
                            "evidence",
                            "table",
                            "chart",
                        ],
                        "properties": {
                            "heading": {"type": "string"},
                            "thesis": {"type": "string"},
                            "body": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["text", "item_id"],
                                    "properties": {
                                        "text": {"type": "string"},
                                        "item_id": {"type": "string"},
                                    },
                                },
                            },
                            "illustrations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["query", "caption"],
                                    "properties": {
                                        "query": {"type": "string"},
                                        "caption": {"type": "string"},
                                    },
                                },
                            },
                            "table": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["caption", "headers", "rows"],
                                "properties": {
                                    "caption": {"type": "string"},
                                    "headers": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "rows": {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            "chart": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["title", "kind", "items"],
                                "properties": {
                                    "title": {"type": "string"},
                                    "kind": {
                                        "type": "string",
                                        "enum": ["bar", "pie"],
                                    },
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["label", "value"],
                                            "properties": {
                                                "label": {"type": "string"},
                                                "value": {"type": "number"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "map": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "children"],
                        "properties": {
                            "name": {"type": "string"},
                            "children": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["label", "value", "item_id"],
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "item_id": {"type": "string"},
                        },
                    },
                },
                "pick_frame": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "lead", "go", "skip", "watch"],
                    "properties": {
                        "title": {"type": "string"},
                        "lead": {"type": "string"},
                        "foot": {"type": "string"},
                        "go": {"type": "string"},
                        "skip": {"type": "string"},
                        "watch": {"type": "string"},
                    },
                },
                "answer": {"type": "string"},
                "conclusion": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "form": {
                            "type": "string",
                            "enum": ["prose", "list", "flow", "data", "mix"],
                        },
                        "answer": {"type": "string"},
                        "flow": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "note"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "note": {"type": "string"},
                                },
                            },
                        },
                        "table": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["caption", "headers", "rows"],
                            "properties": {
                                "caption": {"type": "string"},
                                "headers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "chart": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "kind", "items"],
                            "properties": {
                                "title": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["bar", "pie"],
                                },
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["label", "value"],
                                        "properties": {
                                            "label": {"type": "string"},
                                            "value": {"type": "number"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "picks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "verdict", "reason", "item_id"],
                        "properties": {
                            "name": {"type": "string"},
                            "verdict": {
                                "type": "string",
                                "enum": ["go", "skip", "watch"],
                            },
                            "tag": {"type": "string"},
                            "reason": {"type": "string"},
                            "item_id": {"type": "string"},
                        },
                    },
                },
                "platform_briefs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "answer", "direction", "trend", "worth"],
                        "properties": {
                            "name": {"type": "string"},
                            "answer": {"type": "string"},
                            "direction": {"type": "string"},
                            "trend": {"type": "string"},
                            "worth": {"type": "string"},
                        },
                    },
                },
            },
        },
        "judgment": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "headline_zh",
                "one_line_judgment",
                "plain_language",
                "worth_interrupting",
                "value",
                "caveats",
                "next_watch",
            ],
            "properties": {
                "headline_zh": {"type": "string"},
                "one_line_judgment": {"type": "string"},
                "plain_language": {"type": "string"},
                "worth_interrupting": {"type": "boolean"},
                "value": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["level", "label", "reason"],
                    "properties": {
                        "level": {"type": "integer", "minimum": 1, "maximum": 5},
                        "label": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
                "caveats": {"type": "array", "items": {"type": "string"}},
                "next_watch": {"type": "string"},
                "illustrations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["query", "caption"],
                        "properties": {
                            "query": {"type": "string"},
                            "caption": {"type": "string"},
                        },
                    },
                },
            },
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "item_id",
                    "headline_zh",
                    "summary",
                    "role",
                    "recommendation",
                ],
                "properties": {
                    "item_id": {"type": "string"},
                    "headline_zh": {"type": "string"},
                    "summary": {"type": "string"},
                    "role": {"type": "string", "enum": _ROLE},
                    "recommendation": {"type": "string", "enum": _ACT},
                },
            },
        },
    },
}

app = FastAPI(title="一览", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _boot_key_vault() -> None:
    boot_keys()
    reload_runtime_keys()
_search_lock = asyncio.Lock()
_pdf_lock = asyncio.Lock()
_live_job_id = ""


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def pick(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "nan"}:
            return text
    return ""


def compact(text: str, limit: int = 280) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def parse_count(value: Any) -> float:
    text = str(value or "").strip().replace(",", "").replace(" ", "")
    if not text:
        return 0.0
    extra = 1.0
    if text.endswith("万+"):
        text = text[:-2]
        extra = 10000.0
    elif text.endswith("万"):
        text = text[:-1]
        extra = 10000.0
    elif text.endswith("+"):
        text = text[:-1]
    try:
        return float(text) * extra
    except ValueError:
        return 0.0


def split_urls(raw: str) -> list[str]:
    urls: list[str] = []
    for part in raw.replace(";", ",").split(","):
        url = part.strip()
        if url.startswith("http") and url not in urls:
            urls.append(url)
    return urls


def urls_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        parts = str(value or "").replace(";", ",").split(",")
    urls: list[str] = []
    for part in parts:
        url = part.strip()
        if url.startswith("http") and url not in urls:
            urls.append(url)
    return urls


_ZHIHU_PIC_ID = re.compile(r"(v2-[0-9a-fA-F]+)")


def _fold_zhihu_images(urls: list[str]) -> list[str]:
    def rank(url: str) -> int:
        low = url.lower()
        if "_1440w" in low:
            return 0
        if "_r." in low:
            return 2
        return 1

    best: dict[str, str] = {}
    order: list[str] = []
    for url in urls:
        found = _ZHIHU_PIC_ID.search(url)
        key = found.group(1) if found else url
        prev = best.get(key)
        if prev is None:
            best[key] = url
            order.append(key)
            continue
        if rank(url) < rank(prev):
            best[key] = url
    return [best[key] for key in order]


def item_image_urls(row: dict[str, Any], platform: str = "") -> list[str]:
    if platform in LOCAL_IMAGE_PLATFORMS:
        urls: list[str] = []
        for key in ("cover_url", "image_list", "images", "image", "pic", "thumbnail"):
            urls.extend(urls_from_value(row.get(key)))
        urls = list(dict.fromkeys(urls))
        if platform == "zhihu":
            urls = _fold_zhihu_images(urls)
        return urls[:8]
    aweme_type = str(row.get("aweme_type") or "")
    keys = (
        "cover_url",
        "video_cover_url",
        "image_list",
        "note_download_url",
        "pic",
        "thumbnail",
        "image",
    )
    if aweme_type not in {"0", "4"} and row.get("note_download_url"):
        keys = (
            "image_list",
            "note_download_url",
            "cover_url",
            "video_cover_url",
            "pic",
        )
    for key in keys:
        urls = split_urls(pick(row, key))
        if urls:
            return urls[:8]
    return []


def item_kind(platform: str, row: dict[str, Any]) -> str:
    aweme_type = str(row.get("aweme_type") or "")
    if platform == "dy" or row.get("aweme_id"):
        if aweme_type in {"68", "2", "101"}:
            return "note"
        if aweme_type in {"0", "4"}:
            return "video"
        if row.get("note_download_url"):
            return "note"
        if row.get("cover_url") or row.get("video_download_url"):
            return "video"
    if platform in {"bili", "ks"}:
        return "video"
    if platform in {"xhs", "wb", "tieba"}:
        return "note"
    if platform == "zhihu":
        content_type = pick(row, "content_type", "type")
        if content_type in {"zvideo", "video"}:
            return "video"
        return "note"
    if platform == "yt":
        return "video"
    if platform == WEIXIN_CODE:
        return "note"
    if platform in FOREIGN_CODES:
        return pick(row, "type") or "note"
    return pick(row, "type") or ""


def format_time(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return ""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        text = str(value).strip()
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OverflowError):
            return text if len(text) < 40 else ""
    if number > 10_000_000_000:
        number //= 1000
    if number < 1_000_000_000:
        return ""
    return datetime.fromtimestamp(number, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )


def normalize_item(platform: str, row: dict[str, Any], index: int) -> dict[str, Any]:
    raw_id = pick(
        row,
        "note_id",
        "aweme_id",
        "video_id",
        "content_id",
        "awemeId",
        "id",
    ) or str(index + 1)
    title_zh = pick(row, "title_zh")
    title = title_zh or pick(row, "title", "desc", "content_text", "content", "note_desc")
    text = pick(row, "desc", "content_text", "content", "title", "note_desc")
    url = pick(
        row,
        "note_url",
        "aweme_url",
        "video_url",
        "content_url",
        "url",
        "share_url",
    )
    author = pick(
        row,
        "nickname",
        "user_nickname",
        "user_name",
        "author",
        "name",
    )
    fans = pick(
        row,
        "fans",
        "fans_count",
        "follower_count",
        "followers_count",
        "fan_count",
    )
    liked = pick(
        row,
        "liked_count",
        "digg_count",
        "like_count",
        "likedCount",
        "voteup_count",
    )
    collected = pick(row, "collected_count", "collect_count", "favorite_count")
    comments = pick(row, "comment_count", "comments_count", "total_replay_num")
    shares = pick(row, "share_count", "shared_count", "repost_count")
    tags = [part.strip() for part in pick(row, "tag_list", "topics").split(",") if part.strip()]
    images = item_image_urls(row, platform)
    item = {
        "id": f"{platform}:{raw_id}",
        "platform": platform,
        "platform_label": PLATFORM_MAP.get(platform, platform),
        "title": compact(title or text, 90),
        "text": compact(text or title, 2400 if platform in {"tieba", "zhihu", WEIXIN_CODE} else 720),
        "url": url,
        "author": author,
        "fans": fans,
        "published": format_time(
            row.get("time")
            or row.get("create_time")
            or row.get("publish_time")
            or row.get("createTime")
            or row.get("created_time")
            or row.get("published_at")
        ),
        "liked": liked,
        "collected": collected,
        "comment_count": comments,
        "share_count": shares,
        "liked_n": parse_count(liked),
        "collected_n": parse_count(collected),
        "comment_n": parse_count(comments),
        "share_n": parse_count(shares),
        "tags": tags[:12],
        "kind": item_kind(platform, row),
        "images": images,
        "duration": "",
        "video_text": pick(row, "video_text"),
        "text_source": pick(row, "text_source"),
        "scene": "",
        "play_url": pick(row, "video_download_url", "video_play_url"),
        "text_zh": pick(row, "text_zh"),
        "title_zh": title_zh,
        "title_orig": pick(row, "title_orig"),
        "lang": pick(row, "lang"),
    }
    return stamp_freshness(item)


def attach_video_pack(
    job_dir: Path, platform: str, item: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any]:
    raw_id = item["id"].split(":", 1)[-1]
    meta = load_sidecar(job_dir, platform, raw_id)
    frames = sidecar_frames(job_dir, platform, raw_id)
    if frames:
        framed = [f"frame:{path}" for path in frames]
        item["images"] = list(dict.fromkeys(framed + (item.get("images") or [])))[:8]
    duration = meta.get("duration") or format_duration(
        row.get("duration_ms") or row.get("duration") or 0
    )
    if duration:
        item["duration"] = duration
    sidecar_text = str(meta.get("video_text") or "")
    item_text = str(item.get("video_text") or "")
    item["video_text"] = sidecar_text if len(sidecar_text) > len(item_text) else item_text
    source = pick(row, "text_source") or item.get("text_source") or ""
    if source:
        item["text_source"] = source
    if meta.get("scene"):
        item["scene"] = str(meta["scene"])
    return item


def jsonl_folders(job_dir: Path, platform: str) -> list[Path]:
    names = PLATFORM_DATA_DIRS.get(platform, (platform,))
    folders: list[Path] = []
    for name in names:
        folder = job_dir / name / "jsonl"
        if folder.exists() and folder not in folders:
            folders.append(folder)
    return folders


def read_jsonl(job_dir: Path, platform: str) -> list[dict[str, Any]]:
    files: list[Path] = []
    for folder in jsonl_folders(job_dir, platform):
        files.extend(sorted(folder.glob("search_contents_*.jsonl")))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            item = attach_video_pack(
                job_dir, platform, normalize_item(platform, row, len(items)), row
            )
            if platform in LOCAL_IMAGE_PLATFORMS:
                attach_local_images(job_dir, item)
            if not item["title"] and not item["text"]:
                continue
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
    return items


def usable_items(
    query: str,
    items: list[dict[str, Any]],
    wanted: int | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    kept, dropped = select_items(query, items, wanted)
    for item in kept:
        stamp_freshness(item, query)
    return kept, dropped


def rebuild_unique_items(
    job_dir: Path,
    query: str,
    platforms: list[str],
    options: SearchOptions,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pool: list[dict[str, Any]] = []
    per_code: dict[str, list[dict[str, Any]]] = {}
    for code in platforms:
        kept, _dropped = usable_items(query, read_jsonl(job_dir, code))
        per_code[code] = kept
        pool.extend(kept)
    clusters = cluster_cross_platform(pool)
    clustered_ids = {
        str(item.get("id") or "") for rows in clusters.values() for item in rows
    }
    taken: set[str] = set()
    rebuilt: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for code in platforms:
        wanted = options.notes_for(code)
        kept, _dropped, unique_n = pick_unique_items(
            query, read_jsonl(job_dir, code), wanted, taken
        )
        rebuilt.extend(kept)
        overlap = sum(
            1 for item in per_code.get(code) or [] if item.get("id") in clustered_ids
        )
        if overlap >= 2 and unique_n < wanted:
            tasks.append(
                {
                    "platform": code,
                    "count": min(8, max(6, wanted - unique_n)),
                    "overlap": overlap,
                    "unique": unique_n,
                }
            )
    return rebuilt, tasks[:3]


async def compensate_cross_platform(
    query: str,
    job_dir: Path,
    emit,
    options: SearchOptions,
    started: float,
    platforms: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    items, tasks = rebuild_unique_items(job_dir, query, platforms, options)
    pool: list[dict[str, Any]] = []
    for code in platforms:
        kept, _dropped = usable_items(query, read_jsonl(job_dir, code))
        pool.extend(kept)
    clusters = cluster_cross_platform(pool)
    if clusters:
        await emit(
            "log",
            {
                "message": (
                    f"跨平台重复 {len(clusters)} 组大号同款，各站先留独特内容。"
                    f"{' 独特不够的站再补几条。' if tasks else ''}"
                ),
            },
        )
    if not tasks:
        return items, False
    if time.time() - started > 18 * 60:
        await emit("log", {"message": "已经搜很久了，跨平台重复先按独特内容留下，不再加取。"})
        return items, False
    try:
        from tools.search_ladders import compact_search_query
    except ImportError:
        from search_ladders import compact_search_query
    term = compact_search_query(query) or query
    await emit(
        "status",
        {
            "step": "refill",
            "title": "跨平台同款太多，再补一点独特内容",
            "detail": f"{len(tasks)} 个站大号内容重复，用短词再取几条。",
        },
    )
    for task in tasks:
        code = task["platform"]
        extra = replace(
            options,
            notes={code: task["count"]},
            platform_queries={code: term[:80]},
        )
        await emit(
            "log",
            {
                "platform": code,
                "message": (
                    f"{PLATFORM_MAP.get(code, code)}有 {task['overlap']} 条和其他站同款，"
                    f"已有独特 {task['unique']}，再取 {task['count']} 条。"
                ),
            },
        )
        runner = run_foreign_platform if code in CONNECTOR_CODES else run_platform
        result = await runner(code, query, job_dir, emit, extra, keep_all=True)
        await emit("platform_done", result)
    items, _more = rebuild_unique_items(job_dir, query, platforms, options)
    return items, True


def drop_note(dropped: list[tuple[dict[str, Any], str]]) -> str:
    if not dropped:
        return ""
    title = str(dropped[0][0].get("title") or "一条不对题的")
    return f"丢掉 {len(dropped)} 条明显不对题的，改看后面的。例如「{title}」。"


def drop_summary(dropped: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    samples: list[str] = []
    for item, why in dropped:
        key = str(why).split("：")[0]
        reasons[key] = reasons.get(key, 0) + 1
        title = str((item or {}).get("title") or "")
        if title and title not in samples and len(samples) < 3:
            samples.append(title[:60])
    return {"dropped": len(dropped), "reasons": reasons, "samples": samples}


def coverage_from_result(
    platform: str, raw_n: int, items: list[dict[str, Any]], dropped: list[tuple[dict[str, Any], str]]
) -> dict[str, Any]:
    summary = drop_summary(dropped)
    return {
        "platform": platform,
        "label": PLATFORM_MAP.get(platform, platform),
        "raw": raw_n,
        "kept": len(items),
        "dropped": summary["dropped"],
        "reasons": summary["reasons"],
        "samples": summary["samples"],
    }


def write_coverage(job_dir: Path, rows: list[dict[str, Any]]) -> None:
    (job_dir / "coverage.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_coverage(job_dir: Path) -> list[dict[str, Any]]:
    path = job_dir / "coverage.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def job_coverage(job_dir: Path, query: str) -> list[dict[str, Any]]:
    saved = read_coverage(job_dir)
    if saved:
        return saved
    return build_coverage(job_dir, query)


def build_coverage(job_dir: Path, query: str, platforms: list[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    codes = platforms or [code for code, _label in PLATFORMS]
    for code in codes:
        raw = read_jsonl(job_dir, code)
        if not raw:
            if platforms is not None:
                rows.append(coverage_from_result(code, 0, [], []))
            continue
        kept, dropped = usable_items(query, raw)
        rows.append(coverage_from_result(code, len(raw), kept, dropped))
    return rows


_PROCESS_EVENTS = frozenset(
    {
        "status",
        "log",
        "platform_start",
        "platform_done",
        "refill_start",
        "eleven_start",
        "board",
        "board_error",
        "done",
        "error",
    }
)
_PROCESS_STEP_CAP = 80
_PROCESS_LOG_CAP = 120


def process_path(job_dir: Path) -> Path:
    return job_dir / "process.json"


def empty_process() -> dict[str, Any]:
    return {
        "steps": [],
        "logs": [],
        "search_ms": 0,
        "report_ms": 0,
        "elapsed_ms": 0,
        "started_at": "",
    }


def load_process(job_dir: Path) -> dict[str, Any]:
    path = process_path(job_dir)
    data = empty_process()
    if not path.is_file():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in ("steps", "logs"):
        rows = raw.get(key)
        if isinstance(rows, list):
            data[key] = [str(row)[:400] for row in rows if str(row).strip()]
    for key in ("search_ms", "report_ms", "elapsed_ms"):
        try:
            data[key] = max(0, int(raw.get(key) or 0))
        except (TypeError, ValueError):
            data[key] = 0
    data["started_at"] = str(raw.get("started_at") or "")
    return data


def save_process(job_dir: Path, data: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    process_path(job_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_used_ms(ms: int) -> str:
    sec = max(0, int(round(max(0, int(ms or 0)) / 1000)))
    if sec < 60:
        return f"{sec} 秒"
    minutes, sec = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def process_timing_text(proc: dict[str, Any] | None) -> str:
    data = proc or {}
    try:
        search_ms = int(data.get("search_ms") or 0)
        report_ms = int(data.get("report_ms") or 0)
        elapsed_ms = int(data.get("elapsed_ms") or 0)
    except (TypeError, ValueError):
        return ""
    parts: list[str] = []
    if search_ms > 0:
        parts.append(f"搜索用了 {format_used_ms(search_ms)}")
    if report_ms > 0:
        parts.append(f"写报告用了 {format_used_ms(report_ms)}")
    if elapsed_ms > 0:
        parts.append(f"一共 {format_used_ms(elapsed_ms)}")
    return "，".join(parts) + "。" if parts else ""


def _append_unique(rows: list[str], text: str, cap: int) -> list[str]:
    value = " ".join(str(text or "").split())
    if not value:
        return rows
    if rows and rows[-1] == value:
        return rows
    rows.append(value[:400])
    return rows[-cap:]


def persist_process_event(
    job_dir: Path, event: str, data: dict[str, Any], started: float
) -> dict[str, Any]:
    proc = load_process(job_dir)
    if not proc.get("started_at") and started:
        proc["started_at"] = datetime.fromtimestamp(started).astimezone().isoformat(
            timespec="seconds"
        )
    elapsed_ms = int(max(0, (time.time() - started) * 1000)) if started else 0
    if event == "done":
        try:
            elapsed_ms = int(data.get("elapsed_ms") or elapsed_ms)
        except (TypeError, ValueError):
            pass
    if elapsed_ms:
        proc["elapsed_ms"] = elapsed_ms
    step = ""
    log = ""
    if event == "status":
        step = str(data.get("title") or "")
        log = str(data.get("detail") or "")
    elif event == "log":
        log = str(data.get("message") or "")
    elif event == "platform_start":
        step = f"进入 {data.get('label') or data.get('platform') or '这一站'}"
    elif event == "platform_done":
        label = str(data.get("label") or data.get("platform") or "这一站")
        count = int(data.get("count") or 0)
        dropped = int((data.get("coverage") or {}).get("dropped") or 0) if isinstance(data.get("coverage"), dict) else 0
        if data.get("kept"):
            step = f"{label} 已有 {count} 条，跳过"
        elif data.get("ok"):
            step = f"{label} 核对后留下 {count} 条"
            if dropped:
                step += f"，丢掉 {dropped}"
        else:
            step = f"{label} 没搜到"
            log = str(data.get("error") or "")
    elif event == "refill_start":
        step = str(data.get("reason") or "定向补搜")
    elif event == "eleven_start":
        intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
        label = str(intent.get("label") or "综合研判")
        step = f"十一按「{label}」写报告"
        if not proc.get("search_ms"):
            proc["search_ms"] = elapsed_ms
    elif event == "board":
        step = "十一写好了报告"
        if proc.get("search_ms") and not proc.get("report_ms"):
            proc["report_ms"] = max(0, elapsed_ms - int(proc["search_ms"]))
    elif event == "board_error":
        step = str(data.get("message") or "十一这一步没做成")
        if proc.get("search_ms") and not proc.get("report_ms"):
            proc["report_ms"] = max(0, elapsed_ms - int(proc["search_ms"]))
    elif event == "done":
        step = f"结束。{int(data.get('item_count') or 0)} 条原始结果"
        if not proc.get("search_ms"):
            proc["search_ms"] = elapsed_ms
        if proc.get("search_ms") and not proc.get("report_ms"):
            proc["report_ms"] = max(0, elapsed_ms - int(proc["search_ms"]))
    elif event == "error":
        log = str(data.get("message") or "搜索出错")
        step = "搜索出错"
    proc["steps"] = _append_unique(list(proc.get("steps") or []), step, _PROCESS_STEP_CAP)
    proc["logs"] = _append_unique(list(proc.get("logs") or []), log, _PROCESS_LOG_CAP)
    save_process(job_dir, proc)
    data.setdefault("search_ms", proc.get("search_ms") or 0)
    data.setdefault("report_ms", proc.get("report_ms") or 0)
    data.setdefault("elapsed_ms", proc.get("elapsed_ms") or elapsed_ms)
    return proc


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.exists() else None
    except OSError:
        return None


def reconstruct_process(
    job_dir: Path, query: str = "", coverage: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    proc = load_process(job_dir)
    if not proc.get("steps"):
        steps = [f"搜过「{query}」"] if query else ["搜过这一轮"]
        logs: list[str] = []
        for row in coverage or []:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or row.get("platform") or "这一站")
            kept = int(row.get("kept") or 0)
            dropped = int(row.get("dropped") or 0)
            raw_n = int(row.get("raw") or 0)
            if kept or raw_n:
                line = f"{label} 核对后留下 {kept} 条"
                if dropped:
                    line += f"，丢掉 {dropped}"
                steps.append(line)
            else:
                steps.append(f"{label} 没搜到")
            reasons = row.get("reasons") if isinstance(row.get("reasons"), dict) else {}
            if reasons:
                logs.append(
                    f"{label} 丢掉："
                    + "、".join(f"{key} {value}" for key, value in reasons.items())
                )
        if (job_dir / "board.json").is_file():
            steps.append("十一写好了报告")
        kept_total = sum(int(row.get("kept") or 0) for row in (coverage or []) if isinstance(row, dict))
        if kept_total:
            steps.append(f"结束。{kept_total} 条原始结果")
        proc["steps"] = steps
        proc["logs"] = logs
    if not (proc.get("search_ms") or proc.get("elapsed_ms")):
        start = _file_mtime(job_dir / "meta.json") or _file_mtime(job_dir)
        search_end = _file_mtime(job_dir / "coverage.json")
        if search_end is None:
            jsonl_times = [
                stamp
                for stamp in (_file_mtime(path) for path in job_dir.glob("*/jsonl/*.jsonl"))
                if stamp
            ]
            search_end = max(jsonl_times) if jsonl_times else start
        end = _file_mtime(job_dir / "board.json") or search_end
        if start and search_end and search_end >= start:
            proc["search_ms"] = int((search_end - start) * 1000)
        if start and end and end >= start:
            proc["elapsed_ms"] = int((end - start) * 1000)
        if proc.get("elapsed_ms") and proc.get("search_ms"):
            proc["report_ms"] = max(0, int(proc["elapsed_ms"]) - int(proc["search_ms"]))
    return proc


def extract_output_text(payload: dict[str, Any]) -> str:
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                chunk = part.get("text")
                if isinstance(chunk, str):
                    parts.append(chunk)
    return "".join(parts).strip()


def parse_model_json(text: str) -> dict[str, Any]:
    clean = text.strip().lstrip("\ufeff")
    if clean.startswith("```"):
        lines = clean.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("model output is not JSON")
    blob = clean[start : end + 1]
    parsed = json.loads(_escape_json_controls(blob))
    if not isinstance(parsed, dict):
        raise ValueError("model output is not an object")
    return parsed


def _escape_json_controls(text: str) -> str:
    """Escape raw newlines/tabs inside JSON strings; do not invent missing fields."""
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        code = ord(ch)
        if in_string and escaped:
            out.append(ch)
            escaped = False
            continue
        if in_string and ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = not in_string
            continue
        if in_string and code < 32:
            out.append(f"\\u{code:04x}")
            continue
        out.append(ch)
    return "".join(out)


async def chrome_ready(timeout: float = 1.5) -> dict[str, Any]:
    url = f"http://127.0.0.1:{CDP_PORT}/json/version"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            return {"ok": False, "detail": f"Chrome 调试端口返回 {response.status_code}"}
        body = response.json()
        return {
            "ok": True,
            "detail": body.get("Browser") or "Chrome 远程调试已打开",
        }
    except Exception:
        return {
            "ok": False,
            "detail": f"本机 {CDP_PORT} 端口没有可连接的 Chrome",
        }


def chrome_status() -> dict[str, Any]:
    binary = find_chrome()
    if binary is None:
        return {
            "ok": False,
            "detail": "本机没找到 Google Chrome。先安装它，搜索时才会弹出窗口。",
        }
    return {
        "ok": True,
        "detail": f"将使用本机 Chrome：{binary}",
    }


def find_chrome() -> Path | None:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


async def launch_debug_browser() -> dict[str, Any]:
    ready = await chrome_ready()
    if ready["ok"]:
        return {
            "ok": True,
            "already": True,
            "detail": "搜索专用浏览器已经开着，可以直接搜。",
        }
    binary = find_chrome()
    if binary is None:
        return {
            "ok": False,
            "already": False,
            "detail": "本机没找到 Chrome。先安装 Google Chrome，再点这个按钮。",
        }
    profile = Path.home() / "chrome-mediacrawler"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.create_subprocess_exec(
            str(binary),
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={str(profile)}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        return {"ok": False, "already": False, "detail": f"没能启动浏览器：{error}"}
    for _ in range(30):
        await asyncio.sleep(0.5)
        ready = await chrome_ready(timeout=0.4)
        if ready["ok"]:
            return {
                "ok": True,
                "already": False,
                "detail": "已经打开搜索专用浏览器。平台要登录时，去那个新窗口扫码。",
            }
    return {
        "ok": False,
        "already": False,
        "detail": "启动命令发出去了，但调试口还没通。看 Dock 里有没有多出一个 Chrome，没有的话再说一声。",
    }


def writer_public() -> dict[str, str]:
    agent = active_agent()
    return {"id": agent["id"], "name": agent["name"], "kind": agent.get("kind") or "builtin"}


def eleven_instructions(intent: dict[str, str] | None = None) -> str:
    agent = active_agent()
    persona = agent_persona(agent["id"])
    extra = ""
    if agent["id"] != "eleven":
        extra = (
            "写报告时不要自称十一，不要用十一的口头禅、人格或态度表演。"
            f"你现在是「{agent['name']}」，只根据材料和任务写。"
        )
    intent = intent or detect_query_intent("")
    rewrite = ""
    if intent.get("user_note"):
        rewrite = (
            f"这是按使用者新要求重写的完整报告，不是旧稿打补丁。新要求：{intent['user_note']}"
            "必须重写 title、deck、overview、answer、picks、map、platform_briefs、sections、judgment、facts、sources。"
            "不要沿用上一份的章节标题、导图和结论结构，除非它们仍然直接回答这次新要求。"
            "先读完全部材料，再按新要求组织。新要求没点名的材料也要读，用来对照、限制和排除。"
            "若 agent_search 和本机材料或这次新要求冲突，以本机材料和这次新要求为准。"
        )
    return "\n".join(
        [
            persona,
            extra,
            "",
            "你现在写的是一份正式调研报告。overview 只是封面，不能代替正文。",
            (
                "先读使用者这次的新要求，再对照原搜索词，读完全部材料，最后才写结论。不要先套旧稿再改两句。"
                if intent.get("user_note")
                else "先读搜索词要你交什么答案，再读完全部材料，最后才写结论。不要先套清单再找材料填空。"
            ),
            rewrite,
            f"这次任务是「{intent['label']}」：{intent['job']}",
            intent["brief"],
            "结论必须直接回答这次搜索目的。先写 report.answer：一段完整的话，让人看完就知道现在怎么判断。",
            "再按题目选形式，写入 report.conclusion.form：prose 段落、list 清单、flow 流程、data 数据、mix 混合。",
            "选购/购买/选购指南是购物题，tag 只能用选/不选/再比，不要写成先做、照做或能用。",
            "form=flow 时写 conclusion.flow[{name,note}]；form=data 时写 conclusion.table 或 conclusion.chart；需要点名对象时再写 report.picks。",
            "可以只用一段话，也可以段话+流程+数据+清单都有。不要不管题目一律只交选/避/再看三块。",
            "深度阅读 report.sections 必须围着这个结论展开论点、论证、论据。不要另写一套和结论无关的摘要。",
            "不要写成摘要加参考文献，也不要不管搜索词一律写成叙事地图。",
            "report.sections 按内容论点写：原则、品类、争议、例外。不要一节一个平台，也不要把深度阅读写成六站摘要。",
            "report.map 是知识/决策逻辑，按判断分支展开。节点不能是平台名，子节点不能是帖子标题清单。",
            "各平台摘要单独放 report.platform_briefs：[{name,answer,direction,trend,worth}]。"
            "有材料的站都必须写一条，国内国外都要写，不能只写常见的八站，也不能漏掉公众号、YouTube、Google News。"
            "answer 必须是一段完整的话，让读者直接看懂这一站针对这次内容在说什么：点了哪些对象、主张是什么、往哪收、眼下在热什么。"
            "不要用「当可带走的对象」「反复落到」这类半截话。direction 和 trend 只作内部备忘；worth 只留一句限制。"
            "先呈现内容，再写限制。不要写条数，不要复述搜索词，不要先批判平台，不要列标题。这是判断前的分站侧面，不能替代 picks、导图和深度阅读。",
            "深度阅读每一节和 judgment 可写 illustrations：[{query,caption}]。query 必须带上这次搜索对象（产品名或地名），再加这一节自己的场面词，例如「iPhone Duo 折痕」而不是只写「折叠屏手机」。"
            "编程、语言、课程不要用「路线」「规划」「地图」当检索词，要带上语言名和能入画的词，例如「Python 代码」「Python 屏幕」。不要搜室内装修或交通地图。"
            "每一节换不同的场面，不要三节都配同一类图。地点、物件、场面要让人看见时才配，名单和纯判断不要硬配。看板会先查百度等国内图，不要编图片网址。judgment 不强制两张。",
            "材料里的 text_zh 是外文帖的中文回译，写报告时必须用中文；外文原文只作对照，不要把英文标题当结论。",
            "Google News / Bluesky / Reddit / YouTube 经常只有索引标题。text_source=index 不是全文，写清这是公开索引。",
            "report.picks 只收至少两条独立材料撑得住的对象。单人处境不得抬成结论。",
            "report.sources 必须是对象：summary、platforms[{name,count,note}]、coverage。",
            "report.sections 至少 5 节。每节 heading 是论点标题，thesis 是判断，body 至少 3 段，段与段用空行分开。",
            "每节都要写 table（headers + rows）或 chart（kind + items[{label,value}]），数字只来自输入。",
            "evidence 是对象数组：{text,item_id}，item_id 必须来自输入。",
            "report.picks 是 {name,verdict,tag,reason,item_id}。verdict 只能是 go / skip / watch，只用来上色：正 / 反 / 挂起。"
            "tag 是给读者看的2-6字标签，必须跟着这道题：论证用撑得住/撑不住/还说不准，决策用先做/别做/取决于，产品能力用能用/别指望/再观察，攻略用照做/别照做/缺条件，争议用站这边/站不住/两边都弱，购物用选/不选/再比。不要写成立、不成立、存疑。"
            "搜索词带选购、购买、选购指南时，一律按购物写。禁止被指南、助手带成照做或能用。"
            "禁止不管题目一律写成选/避/再看。"
            "report.pick_frame 写 {title,lead,go,skip,watch}，与这次 tag 一致。",
            "name 必须是材料里出现过的具体对象，由主题决定：产品、地方、人、工具、方法、内容、命题、条件都可以。不能是主题词本身，也不能是“氛围”“热度”。",
            "report.facts 是 {label,value,item_id}。report.map 是数组 [{name,children}]。",
            "judgment 是每份报告的必有项，不能空着，也不能只把搜索词当成标题。",
            "judgment 和 conclusion 不是同一段：conclusion 回答现在怎么选；judgment 回答这批材料值不值得信、限制是什么、值几分。",
            "judgment 用 headline_zh / one_line_judgment / plain_language / value / caveats / next_watch。",
            "one_line_judgment、plain_language、value.reason、caveats、next_watch 都必须写成完整句子。",
            "one_line_judgment 必须直接回答这次任务，不能只说“这是一份叙事地图”。",
            "references 仍可写，但页面不再单独展示文末文献，出处看原始检索。",
            "这些材料来自小红书、抖音、快手、B站、微博、贴吧、知乎、公众号（勾选时），以及勾选时的 Google News、Bluesky、Reddit、YouTube 公开搜索结果。",
            "公众号来自搜狗微信公开索引。text_source=index 只有标题和摘要，不是全文；text_source=public 才是 Chrome 跳到 mp.weixin.qq.com 后读到的正文。不要把索引摘要写成已读完全文。",
            "小红书、微博、贴吧按正文和配图读。知乎按回答/文章正文读；zvideo 只有标题和简介。",
            "抖音、快手和 B站的 video_text 是原文，不是摘要。text_source=subtitle 是 B站官方字幕/口播逐字；caption 是抖音或快手创作者文案；desc 只有简介；none 或 caption_short 表示没有口播原文。",
            "有原文就据此总结观点，并标出博主和链接。没有原文时写「未见口播原文」，只用标题和截图，不要编口播。",
            "不要把短视频写成小红书种草笔记，也不要把长文丢掉。",
            "社交帖是叙事信号，不是事实。没有独立来源时不要写成已核实。",
            "每条材料带 published、fresh_label、fresh_weight、age_days。近况才能支撑“现在该怎么选”；仅背景、半年前、一年以上只当历史对照，不能单独抬成结论。时间不明要降权。"
            "写 picks、深度阅读和 judgment 时点明依据是最近还是旧帖。judgment.caveats 里用一句话交代这批材料的时间结构。",
            "不得编造对象名、链接、作者、数字、发布时间或平台上没有出现的事件。材料没落到可执行对象，就写清，不要编。",
            "只返回一个符合 schema 的 JSON 对象。",
            "如果输入里有 agent_search，那是同一 Agent 用模型知识补的一条参考资料，不是本机检索，也不是时间线条目。"
            "只拿它辅助收口，不要整段复述，也不要把它写成平台摘要。必须写清这是模型补充；不能编成本机帖子，不能当成已核实，不能写进时间线。",
        ]
    )


def _item_title(item_id: str, items: list[dict[str, Any]]) -> str:
    for item in items:
        if item.get("id") == item_id:
            return item.get("title") or "无标题"
    return item_id


def _empty_table() -> dict[str, Any]:
    return {"caption": "", "headers": [], "rows": []}


def _empty_chart() -> dict[str, Any]:
    return {"title": "", "kind": "bar", "items": []}


def local_report(
    query: str, items: list[dict[str, Any]], overview: str = ""
) -> dict[str, Any]:
    by_platform: dict[str, list[str]] = {}
    facts: list[dict[str, str]] = []
    for item in items:
        label = item.get("platform_label") or item.get("platform") or "其他"
        by_platform.setdefault(label, []).append(item["id"])
        if item.get("liked"):
            facts.append(
                {
                    "label": "互动",
                    "value": f"{item.get('liked')} · {item.get('title') or '无标题'}",
                    "item_id": item["id"],
                }
            )
    platforms = [
        {
            "name": name,
            "count": len(ids),
            "note": f"收回 {len(ids)} 条公开搜索结果。",
        }
        for name, ids in by_platform.items()
    ]
    return {
        "title": f"「{query}」检索报告",
        "deck": f"基于 {len(items)} 条公开检索，先摊开来源，再等人判断。",
        "overview": overview
        or f"「{query}」这次收回 {len(items)} 条。数字只来自检索本身，还不是完整调研结论。",
        "sources": {
            "summary": f"共 {len(items)} 条，来自 {len(by_platform)} 个平台的公开搜索。",
            "platforms": platforms,
            "coverage": "目前只有检索到的社交帖，没有官方原文或独立核对。",
        },
        "sections": [
            {
                "heading": "材料还没收成论点",
                "thesis": "这一步只有检索结果，还没有按内容写成判断。",
                "body": "先看来源和原帖，不要把平台摘要当成结论。",
                "evidence": [
                    {
                        "text": _item_title(item["id"], items),
                        "item_id": item["id"],
                    }
                    for item in items[:8]
                ],
                "table": _empty_table(),
                "chart": _empty_chart(),
            }
        ],
        "facts": facts[:12],
        "picks": [],
        "map": [],
        "platform_briefs": local_platform_briefs(query, items),
    }


def local_references(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item["id"],
            "headline_zh": item.get("title") or "无标题",
            "summary": item.get("text") or item.get("title") or "",
            "role": "context",
            "recommendation": "know",
        }
        for item in items
    ]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n\n".join(part for part in (_as_text(row) for row in value) if part)
    if isinstance(value, dict):
        return _as_text(
            value.get("text") or value.get("summary") or value.get("overview") or ""
        )
    return str(value).strip()


_PLATFORM_NODE_NAMES = {label for _code, label in PLATFORMS} | {
    "快手",
    "哔哩哔哩",
    "百度贴吧",
}
_ANECDOTE_RE = re.compile(
    r"前任|前女友|前男友|exgirlfriend|ex-girlfriend|ex girlfriend|"
    r"男朋友已经|女朋友已经|她已经有|他已经有|已经有这些|"
    r"求助帖明确|明确列出.{0,16}已有"
)


def _is_platform_heading(name: str) -> bool:
    return (name or "").strip() in _PLATFORM_NODE_NAMES


def _looks_like_platform_catalog(rows: Any) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    names = []
    for row in rows:
        if isinstance(row, dict):
            names.append(_as_text(row.get("heading") or row.get("name") or row.get("title")))
        elif isinstance(row, str):
            names.append(row.strip())
    names = [name for name in names if name]
    if not names:
        return False
    hits = sum(1 for name in names if _is_platform_heading(name))
    content = len(names) - hits
    return hits >= 1 and (content == 0 or hits >= max(2, len(names) / 2))


def _is_anecdote_pick(row: dict[str, Any]) -> bool:
    if str(row.get("support") or "").strip().lower() == "anecdote":
        return True
    return bool(_ANECDOTE_RE.search(_as_text(row.get("reason") or row.get("why"))))


def _normalize_table(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_table()
    headers = raw.get("headers") or raw.get("columns") or []
    if not isinstance(headers, list):
        headers = []
    rows_in = raw.get("rows") or []
    rows: list[list[str]] = []
    if isinstance(rows_in, list):
        for row in rows_in:
            if isinstance(row, list):
                rows.append([str(cell) for cell in row])
            elif isinstance(row, dict):
                if headers:
                    rows.append([str(row.get(key) or "") for key in headers])
                else:
                    rows.append([str(cell) for cell in row.values()])
    return {
        "caption": _as_text(raw.get("caption") or raw.get("title")),
        "headers": [str(col) for col in headers],
        "rows": rows,
    }


def _normalize_chart(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_chart()
    items_in = raw.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(items_in, list):
        for row in items_in:
            if not isinstance(row, dict):
                continue
            label = _as_text(row.get("label"))
            if not label:
                continue
            try:
                value = float(row.get("value") or 0)
            except (TypeError, ValueError):
                value = 0.0
            items.append({"label": label, "value": value})
    labels = raw.get("labels") or []
    values = raw.get("values") or []
    if not items and isinstance(labels, list) and isinstance(values, list):
        for label, value in zip(labels, values):
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = 0.0
            items.append({"label": str(label), "value": number})
    kind = str(raw.get("kind") or raw.get("type") or "bar")
    if kind not in {"bar", "pie"}:
        kind = "bar"
    return {
        "title": _as_text(raw.get("title")),
        "kind": kind,
        "items": items,
    }


def _item_platform_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for item in items:
        label = item.get("platform_label") or PLATFORM_MAP.get(str(item.get("platform") or ""), "")
        if not label:
            continue
        buckets[label] = buckets.get(label, 0) + 1
    return buckets


def _normalize_sources(raw: Any, items: list[dict[str, Any]]) -> dict[str, Any]:
    platforms: list[dict[str, Any]] = []
    coverage_parts: list[str] = []
    summary = ""
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            try:
                count = int(row.get("count") or row.get("item_count") or 0)
            except (TypeError, ValueError):
                count = 0
            platforms.append(
                {
                    "name": _as_text(row.get("name")) or "来源",
                    "count": count,
                    "note": _as_text(row.get("note") or row.get("coverage")),
                }
            )
            if row.get("missing"):
                coverage_parts.append(_as_text(row.get("missing")))
            if row.get("coverage"):
                coverage_parts.append(_as_text(row.get("coverage")))
    elif isinstance(raw, dict):
        summary = _as_text(raw.get("summary"))
        coverage_parts.append(_as_text(raw.get("coverage")))
        rows = raw.get("platforms") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    count = int(row.get("count") or row.get("item_count") or 0)
                except (TypeError, ValueError):
                    count = 0
                platforms.append(
                    {
                        "name": _as_text(row.get("name")) or "来源",
                        "count": count,
                        "note": _as_text(row.get("note") or row.get("coverage")),
                    }
                )
    real = _item_platform_counts(items)
    if real:
        by_name = {row["name"]: row for row in platforms}
        patched: list[dict[str, Any]] = []
        for _code, label in PLATFORMS:
            count = real.get(label) or 0
            if not count and label not in by_name:
                continue
            old = by_name.get(label) or {}
            note = _as_text(old.get("note"))
            if count and (not note or _EMPTY_BRIEF_RE.search(note)):
                note = f"收回 {count} 条。"
            if not count:
                note = note or "这一站没有留下可用条目。"
            patched.append({"name": label, "count": count, "note": note})
        for row in platforms:
            if row["name"] not in {item["name"] for item in patched}:
                patched.append(row)
        platforms = patched
    if not platforms:
        platforms = [
            {"name": name, "count": count, "note": f"收回 {count} 条。"}
            for name, count in real.items()
        ]
    total = sum(int(row.get("count") or 0) for row in platforms)
    if not summary or _EMPTY_BRIEF_RE.search(summary):
        summary = f"共 {total} 条公开检索，来自 {sum(1 for row in platforms if row.get('count'))} 个平台。"
    return {
        "summary": summary,
        "platforms": platforms,
        "coverage": " ".join(part for part in coverage_parts if part)
        or "目前主要是社交帖，不是官方原文。",
    }


def _normalize_facts(raw: Any) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return facts
    for row in raw:
        if not isinstance(row, dict):
            continue
        item_id = _as_text(row.get("item_id"))
        if not item_id:
            ids = row.get("source_item_ids") or row.get("item_ids") or []
            if isinstance(ids, list) and ids:
                item_id = _as_text(ids[0])
        value = _as_text(row.get("value") or row.get("fact"))
        if not value:
            continue
        facts.append(
            {
                "label": _as_text(row.get("label")) or "材料里的数字",
                "value": value,
                "item_id": item_id,
            }
        )
    return facts


def _map_node_name(node: Any) -> str:
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict):
        return _as_text(node.get("name") or node.get("topic"))
    return ""


def _normalize_map_child(child: Any) -> str:
    if isinstance(child, dict):
        name = _map_node_name(child)
        grand = child.get("children") or []
        labels: list[str] = []
        if isinstance(grand, list):
            for row in grand:
                label = _map_node_name(row)
                if label:
                    labels.append(label)
        if name and labels:
            return f"{name}：{'、'.join(labels)}"
        return name or "、".join(labels)
    return str(child).strip()


def _normalize_map(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        nodes = raw.get("nodes") or raw.get("children") or raw.get("map") or []
        if not nodes and raw.get("name"):
            nodes = [raw]
    elif isinstance(raw, list):
        nodes = raw
    else:
        nodes = []
    if (
        len(nodes) == 1
        and isinstance(nodes[0], dict)
        and any(isinstance(child, dict) for child in (nodes[0].get("children") or []))
    ):
        nodes = nodes[0].get("children") or []
    out: list[dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, str) and node.strip() and not _is_platform_heading(node):
            out.append({"name": node.strip(), "children": []})
            continue
        if not isinstance(node, dict):
            continue
        name = _map_node_name(node)
        if not name or _is_platform_heading(name):
            continue
        children_in = node.get("children") or []
        if not isinstance(children_in, list):
            children_in = []
        children = []
        for child in children_in:
            text = _normalize_map_child(child)
            if text and not _is_platform_heading(text):
                children.append(text)
        out.append({"name": name, "children": children})
    if _looks_like_platform_catalog(out):
        return []
    return out


def _first_sentence(text: str, limit: int = 96) -> str:
    blob = re.sub(r"\s+", " ", _as_text(text)).strip()
    if not blob:
        return ""
    for sep in ("。", "！", "？"):
        if sep in blob:
            bit = blob.split(sep, 1)[0].strip()
            if len(bit) >= 10:
                return bit + sep
    return blob[:limit] + ("…" if len(blob) > limit else "")


def _is_stub_headline(text: str, query: str) -> bool:
    clean = re.sub(r"[「」《》]|检索报告", "", _as_text(text)).strip()
    topic = re.sub(r"[「」《》]", "", query or "").strip()
    return (not clean) or clean == topic or clean == f"{topic}建议"


def judgment_ready(raw: Any, query: str = "") -> bool:
    if not isinstance(raw, dict):
        return False
    one = _as_text(raw.get("one_line_judgment"))
    if len(one) < 12:
        return False
    if _is_stub_headline(one, query):
        return False
    if re.search(r"没法读成报告判断|还没有收成判断|跳过判断|一份叙事地图", one):
        return False
    return True


def ensure_judgment(
    query: str, items: list[dict[str, Any]], board: dict[str, Any]
) -> dict[str, Any]:
    if board.get("harvest"):
        existing = _normalize_judgment(board.get("judgment")) or {}
        caveats = [row for row in (existing.get("caveats") or []) if _as_text(row)]
        clock = freshness_summary(items, query) if items else {}
        clock_note = _as_text((clock or {}).get("note"))
        if clock_note and not any(clock_note[:8] in row for row in caveats):
            caveats.insert(0, clock_note)
        if not any("叙事信号" in row or "已核实" in row for row in caveats):
            caveats.append("社交帖只当叙事信号，不是已核实新闻。")
        if not any("没有调用模型" in row or "没有模型" in row for row in caveats):
            caveats.insert(0, "没有调用模型，页面上的话都不是结论。")
        board["judgment"] = {
            "headline_zh": _as_text(existing.get("headline_zh")) or "这次只摊开材料",
            "one_line_judgment": _as_text(existing.get("one_line_judgment"))
            or "没有调用模型，所以没有结论标签。下面按平台、时间和原帖摊开，都可以点开。",
            "plain_language": _as_text(existing.get("plain_language"))
            or "没有 API Key，或不勾选写报告时，一览只做本机检索。",
            "worth_interrupting": False,
            "value": {
                "level": 2,
                "label": "知道即可",
                "reason": "这次没有模型判断，只摊开检索结果。",
            },
            "caveats": caveats,
            "next_watch": _as_text(existing.get("next_watch"))
            or "配好 API Key 并勾选写报告后，可以不重搜，直接让 Agent 收一次。",
            "illustrations": [],
        }
        return board
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    conclusion = report.get("conclusion") if isinstance(report.get("conclusion"), dict) else {}
    existing = _normalize_judgment(board.get("judgment")) or {}
    answer = _as_text(conclusion.get("answer") or report.get("answer") or existing.get("one_line_judgment"))
    overview = _as_text(report.get("overview"))
    picks = [row for row in (report.get("picks") or []) if isinstance(row, dict)]
    one = _as_text(existing.get("one_line_judgment"))
    if not judgment_ready({"one_line_judgment": one}, query):
        if answer:
            one = _first_sentence(answer) or answer[:96]
        elif picks:
            bits = [
                " ".join(part for part in [str(row.get("tag") or ""), str(row.get("name") or "")] if part).strip()
                for row in picks[:3]
            ]
            one = "；".join(bit for bit in bits if bit) + "。"
        else:
            one = f"这批材料能看「{query}」怎么被讨论，不能当成已核实结论。"
    headline = _as_text(existing.get("headline_zh"))
    same_as_one = headline.rstrip("。！？…") == one.rstrip("。！？…")
    if _is_stub_headline(headline, query) or same_as_one:
        seed = one.split("：", 1)[-1] if "：" in one else one
        seed = re.split(r"[，,]", seed, 1)[0]
        headline = seed.rstrip("。！？…") or "十一的判断"
        if len(headline) > 28:
            headline = headline[:26] + "…"
    plain = _as_text(existing.get("plain_language"))
    if len(plain) < 12:
        rest = ""
        if answer and one and answer != one:
            rest = answer[len(one) :].lstrip("。！？ \n")
        plain = overview or rest or answer or one
    caveats = [row for row in (existing.get("caveats") or []) if _as_text(row)]
    clock = freshness_summary(items, query) if items else {}
    clock_note = _as_text((clock or {}).get("note"))
    if clock_note and not any(clock_note[:8] in row for row in caveats):
        caveats.insert(0, clock_note)
    if not any("叙事信号" in row or "已核实" in row for row in caveats):
        caveats.append("社交帖只当叙事信号，不是已核实新闻。")
    value = existing.get("value") if isinstance(existing.get("value"), dict) else {}
    reason = _as_text(value.get("reason"))
    level = int(value.get("level") or 0)
    if not reason or (level == 2 and not judgment_ready(board.get("judgment"), query)):
        recent = ((clock or {}).get("weights") or {}).get("近况", 0)
        if answer and picks:
            level, label, reason = 4, "认真看", "材料已经收到可执行的对象和限制，值得对照着选。"
        elif recent:
            level, label, reason = 3, "保持观察", "近况材料在，判断可以看，但仍是公开讨论不是官方原文。"
        else:
            level, label, reason = 2, "知道即可", "材料能看讨论，但还撑不起单独下决定。"
    else:
        label = _as_text(value.get("label")) or "保持观察"
        level = level or 3
    watch = _as_text(existing.get("next_watch"))
    if not watch:
        hanging = [
            str(row.get("name") or "")
            for row in picks
            if str(row.get("verdict") or row.get("tag") or "") in {"watch", "再比", "再看"}
        ]
        watch = (
            "接着看：" + "、".join(hanging[:3])
            if hanging
            else "有新的上手参数或价格落地时，再让十一对照这批近况收一次。"
        )
    board["judgment"] = {
        "headline_zh": headline,
        "one_line_judgment": one,
        "plain_language": plain,
        "worth_interrupting": bool(existing.get("worth_interrupting")) or (level >= 4),
        "value": {"level": max(1, min(5, level)), "label": label, "reason": reason},
        "caveats": caveats,
        "next_watch": watch,
        "illustrations": existing.get("illustrations") or [],
    }
    return board


def _normalize_judgment(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("one_line_judgment") or raw.get("headline_zh"):
        value = raw.get("value")
        if not isinstance(value, dict):
            value = {"level": 3, "label": "保持观察", "reason": ""}
        watch = raw.get("next_watch") or raw.get("watch_next") or ""
        if isinstance(watch, list):
            watch = "；".join(_as_text(part) for part in watch if _as_text(part))
        caveats = raw.get("caveats") or raw.get("limitations") or []
        if not isinstance(caveats, list):
            caveats = [caveats] if caveats else []
        return {
            "headline_zh": _as_text(raw.get("headline_zh")),
            "one_line_judgment": _as_text(raw.get("one_line_judgment")),
            "plain_language": _as_text(raw.get("plain_language")),
            "worth_interrupting": bool(raw.get("worth_interrupting")),
            "value": {
                "level": int(value.get("level") or 3),
                "label": _as_text(value.get("label")) or "保持观察",
                "reason": _as_text(value.get("reason")),
            },
            "caveats": [_as_text(row) for row in caveats if _as_text(row)],
            "next_watch": _as_text(watch),
            "illustrations": normalize_illustration_queries(raw.get("illustrations"), 3),
        }
    useful = [_as_text(row) for row in (raw.get("useful") or []) if _as_text(row)]
    unused = [_as_text(row) for row in (raw.get("not_useful") or []) if _as_text(row)]
    limits = [_as_text(row) for row in (raw.get("limitations") or raw.get("caveats") or []) if _as_text(row)]
    watch = raw.get("watch_next") or raw.get("next_watch") or ""
    if isinstance(watch, list):
        watch = "；".join(_as_text(part) for part in watch if _as_text(part))
    if not (useful or unused or limits):
        return None
    parts = []
    if useful:
        parts.append("有用的地方：" + " ".join(useful[:4]))
    if unused:
        parts.append("用处不大：" + " ".join(unused[:3]))
    return {
        "headline_zh": useful[0][:40] if useful else "这批材料的判断",
        "one_line_judgment": useful[0] if useful else "材料能看叙事，不能当成已核实事实。",
        "plain_language": "\n\n".join(parts),
        "worth_interrupting": False,
        "value": {"level": 3, "label": "保持观察", "reason": "社交帖交叉印证，不是官方原文"},
        "caveats": limits,
        "next_watch": _as_text(watch),
        "illustrations": normalize_illustration_queries(raw.get("illustrations"), 3),
    }


def _normalize_sections(raw: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    sections: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        heading = _as_text(row.get("heading") or row.get("title"))
        body = _as_text(row.get("body") or row.get("paragraphs"))
        if not heading and not body:
            continue
        evidence_in = row.get("evidence") or []
        evidence: list[dict[str, str]] = []
        if isinstance(evidence_in, list):
            for ev in evidence_in:
                if isinstance(ev, str) and ev:
                    evidence.append({"text": _item_title(ev, items), "item_id": ev})
                elif isinstance(ev, dict):
                    item_id = _as_text(ev.get("item_id") or ev.get("id"))
                    if item_id:
                        evidence.append(
                            {
                                "text": _as_text(ev.get("text")) or _item_title(item_id, items),
                                "item_id": item_id,
                            }
                        )
        sections.append(
            {
                "heading": heading or "论点",
                "thesis": _as_text(row.get("thesis")),
                "body": body,
                "evidence": evidence,
                "table": _normalize_table(row.get("table")),
                "chart": _normalize_chart(row.get("chart")),
                "illustrations": normalize_illustration_queries(row.get("illustrations"), 2),
            }
        )
    return sections


_APPENDIX_HEADING_RE = re.compile(
    r"互动最高|按点赞|传播排序|数据来源|原帖|标签和叙事|用户讨论最高"
)
_SECTION_FOCUS_RE = re.compile(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9]{2,}")
_SECTION_STOP = {
    "iPhone",
    "iphone",
    "Duo",
    "duo",
    "苹果",
    "测评",
    "评测",
    "这次",
    "这批",
    "这轮",
    "这代",
    "这个",
    "这些",
    "不是",
    "而是",
    "以及",
    "必须",
    "自己",
    "确认",
    "对照",
    "材料",
    "判断",
    "体验",
    "集中",
    "购买",
    "软件",
    "适配",
    "最硬",
    "卖点",
    "门槛",
    "正面",
    "负面",
    "手机",
    "发布",
    "折叠",
}
_GENERIC_TITLE_RE = re.compile(r"iphone\s*duo|苹果折叠", re.I)


def _section_blob(section: dict[str, Any]) -> str:
    return "\n".join(
        [
            _as_text(section.get("heading")),
            _as_text(section.get("thesis")),
            _as_text(section.get("body")),
        ]
    )


def _section_focus_tokens(section: dict[str, Any], query: str) -> list[str]:
    blob = f"{section.get('heading') or ''} {section.get('thesis') or ''}"
    drop = {token.lower() for token in _SECTION_FOCUS_RE.findall(query)} | {
        token.lower() for token in _SECTION_STOP
    }
    out: list[str] = []
    seen: set[str] = set()
    for token in _SECTION_FOCUS_RE.findall(blob):
        key = token.lower()
        if key in drop or key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _section_case_items(
    section: dict[str, Any],
    items: list[dict[str, Any]],
    query: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    blob = _section_blob(section)
    if not blob.strip() or not items:
        return []
    quoted = [name for name in re.findall(r"「([^」]{1,24})」", blob) if name]
    focus = _section_focus_tokens(section, query)
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for item in items:
        author = str(item.get("author") or "").strip()
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "")[:280]
        score = 0
        if author and len(author) >= 2 and author in blob:
            score += 12
        for name in quoted:
            if author and (name == author or name in author or author in name):
                score += 10
            elif len(name) >= 4 and name in title:
                score += 8
        prefix = title[:10]
        if (
            prefix
            and prefix in blob
            and not _GENERIC_TITLE_RE.search(prefix)
            and prefix.lower() not in query.lower()
        ):
            score += 6
        for token in focus:
            if token in title or token in author or token in text:
                score += 3
        if not score:
            continue
        if item.get("images"):
            score += 1
        ranked.append((score, float(item.get("liked_n") or 0), item))
    ranked.sort(key=lambda row: (-row[0], -row[1]))
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    plat_n: dict[str, int] = {}
    for _score, _liked, item in ranked:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen:
            continue
        plat = str(item.get("platform") or "")
        if plat_n.get(plat, 0) >= 2 and len(picked) < limit:
            continue
        seen.add(item_id)
        plat_n[plat] = plat_n.get(plat, 0) + 1
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _fill_section_cases(
    section: dict[str, Any],
    items: list[dict[str, Any]],
    query: str,
) -> dict[str, Any]:
    heading = section.get("heading") or ""
    if _APPENDIX_HEADING_RE.search(heading) or _is_platform_heading(heading):
        return section
    evidence = [
        row
        for row in (section.get("evidence") or [])
        if isinstance(row, dict) and row.get("item_id")
    ]
    cases = []
    if evidence:
        by_id = {item["id"]: item for item in items}
        for row in evidence:
            found = by_id.get(str(row.get("item_id") or ""))
            if found:
                cases.append(found)
    if not cases:
        cases = _section_case_items(section, items, query)
        evidence = [
            {"text": item.get("title") or item["id"], "item_id": item["id"]}
            for item in cases
        ]
        section["evidence"] = evidence
    table = section.get("table") if isinstance(section.get("table"), dict) else {}
    if cases and not (table.get("rows") or []):
        section["table"] = {
            "caption": "这一节对上的材料",
            "headers": ["来源", "作者", "材料", "互动"],
            "rows": [
                [
                    item.get("platform_label") or "",
                    item.get("author") or "",
                    item.get("title") or "",
                    item.get("liked") or "—",
                ]
                for item in cases[:8]
            ],
        }
    chart = section.get("chart") if isinstance(section.get("chart"), dict) else {}
    liked = [
        {
            "label": (item.get("author") or item.get("title") or "材料")[:12],
            "value": float(item.get("liked_n") or 0),
        }
        for item in cases
        if float(item.get("liked_n") or 0) > 0
    ]
    if liked and not (chart.get("items") or []):
        section["chart"] = {
            "title": "这些材料的互动",
            "kind": "bar",
            "items": liked[:8],
        }
    return section


def engagement_table(
    items: list[dict[str, Any]], with_platform: bool = True
) -> dict[str, Any]:
    ranked = sorted(items, key=lambda item: float(item.get("liked_n") or 0), reverse=True)
    rows = []
    for index, item in enumerate(ranked[:12], start=1):
        row = [str(index), item.get("title") or "无标题"]
        if with_platform:
            row.append(item.get("platform_label") or "")
        row.extend(
            [
                item.get("liked") or "0",
                item.get("collected") or "0",
                item.get("comment_count") or "0",
                item.get("share_count") or "0",
                "视频" if item.get("kind") == "video" else "图文",
            ]
        )
        rows.append(row)
    headers = ["排名", "标题"]
    if with_platform:
        headers.append("平台")
    headers.extend(["点赞", "收藏", "评论", "分享", "形态"])
    return {
        "caption": "按点赞排序的公开检索条目（平台展示数，不是官方统计）",
        "headers": headers,
        "rows": rows,
        "item_ids": [item.get("id") or "" for item in ranked[:12]],
    }


def engagement_by_platform(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        label = item.get("platform_label") or PLATFORM_MAP.get(str(item.get("platform") or ""), "")
        if not label:
            continue
        buckets.setdefault(label, []).append(item)
    out: list[dict[str, Any]] = []
    for _code, label in PLATFORMS:
        rows = buckets.get(label) or []
        if not rows:
            continue
        table = engagement_table(rows, with_platform=False)
        table["caption"] = f"{label}按点赞排序（平台展示数，不是官方统计）"
        out.append({"name": label, "count": len(rows), "table": table})
    return out


def tag_table(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        for tag in item.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    top = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:10]
    if not top:
        return _empty_table()
    return {
        "caption": "材料里反复出现的话题标签",
        "headers": ["话题", "出现条数"],
        "rows": [[name, str(count)] for name, count in top],
    }


def report_gallery(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(items, key=lambda item: float(item.get("liked_n") or 0), reverse=True)
    gallery: list[dict[str, Any]] = []
    for item in ranked:
        if not item.get("images"):
            continue
        gallery.append(
            {
                "item_id": item["id"],
                "caption": item.get("title") or "",
                "count": len(item.get("images") or []),
            }
        )
        if len(gallery) >= 16:
            break
    return gallery


def report_timeline(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for stamp, item in dated_items(items):
        out.append(
            {
                "when": stamp.astimezone().strftime("%Y-%m-%d %H:%M"),
                "text": item.get("title") or item.get("id") or "",
                "item_id": item.get("id") or "",
                "platform": item.get("platform_label") or "",
            }
        )
    return out


def topic_map(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = tag_table(items)
    if table["rows"]:
        return [
            {
                "name": "讨论最多的标签",
                "children": [f"{name} · {count} 条" for name, count in table["rows"][:8]],
            }
        ]
    return []


def _normalize_picks(raw: Any, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    known = {item.get("id") for item in items if item.get("id")}
    picks: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return picks
    for row in raw:
        if not isinstance(row, dict):
            continue
        if _is_anecdote_pick(row):
            continue
        name = _as_text(row.get("name") or row.get("title") or row.get("object"))
        if not name:
            continue
        verdict = str(row.get("verdict") or row.get("act") or "watch").strip().lower()
        if verdict in {"推荐", "去", "选", "买", "adopt", "use", "成立", "撑得住", "先做", "能用", "照做", "更强", "站这边"}:
            verdict = "go"
        elif verdict in {"避", "避雷", "别去", "别买", "坑", "drop", "不成立", "撑不住", "别做", "别指望", "别照做", "更弱", "站不住"}:
            verdict = "skip"
        elif verdict not in {"go", "skip", "watch"}:
            verdict = "watch"
        tag = _as_text(row.get("tag"))[:8]
        item_ids = row.get("item_ids") or row.get("source_item_ids") or []
        if not isinstance(item_ids, list):
            item_ids = []
        item_id = _as_text(row.get("item_id"))
        if item_id:
            item_ids = [item_id, *[str(x) for x in item_ids if str(x) and str(x) != item_id]]
        item_ids = [str(x) for x in item_ids if str(x) in known]
        item_id = item_ids[0] if item_ids else ""
        picks.append(
            {
                "name": name,
                "verdict": verdict,
                "tag": tag,
                "reason": _as_text(row.get("reason") or row.get("why")),
                "item_id": item_id,
            }
        )
    return picks


def _normalize_flow(raw: Any) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return steps
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = _as_text(row.get("name") or row.get("title") or row.get("step"))
        note = _as_text(row.get("note") or row.get("text") or row.get("why"))
        if not name and not note:
            continue
        steps.append({"name": name or note, "note": note})
    return steps


def _normalize_conclusion(
    raw: Any,
    report: dict[str, Any],
    judgment: dict[str, Any] | None,
    picks: list[dict[str, str]],
) -> dict[str, Any]:
    block = raw if isinstance(raw, dict) else {}
    answer = _as_text(
        block.get("answer")
        or report.get("answer")
        or (judgment or {}).get("one_line_judgment")
    )
    flow = _normalize_flow(block.get("flow") or report.get("flow"))
    table = _normalize_table(block.get("table"))
    chart = _normalize_chart(block.get("chart"))
    form = str(block.get("form") or "").strip()
    if form not in {"prose", "list", "flow", "data", "mix"}:
        kinds = [
            bool(answer),
            bool(picks),
            bool(flow),
            bool(table.get("rows") or chart.get("items")),
        ]
        form = "mix" if sum(kinds) > 1 else (
            "flow" if flow else "data" if table.get("rows") or chart.get("items") else "list" if picks else "prose"
        )
    return {
        "form": form,
        "answer": answer,
        "flow": flow,
        "table": table,
        "chart": chart,
    }


_BRIEF_OBJECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("耳机/耳夹", ("耳机", "耳夹", "耳钉", "airpods")),
    ("香氛/香水", ("香氛", "香水", "蜡烛")),
    ("拍立得", ("拍立得", "富士mini", "富士")),
    ("口红/化妆品", ("口红", "化妆品", "彩妆")),
    ("花束/鲜花", ("花束", "鲜花")),
    ("轻奢配饰", ("墨镜", "项链", "手链", "西太后", "轻奢", "手表")),
    ("包袋", ("卡包", "链条包", "包包", "小包")),
    ("数码小物", ("数码", "充电宝", "键盘", "电器")),
    ("体验礼物", ("环球影城", "一日游")),
)
_BRIEF_WORTH = {
    "小红书": "单品点名快，跟风也快，当侧面看。",
    "抖音": "好拍的东西会偏热，广告和节日话题重。",
    "快手": "短视频和搬运多，口播不一定完整。",
    "B站": "攻略全，具体型号要回原片。",
    "微博": "收藏和拆箱多，噪声也多。",
    "贴吧": "求助和故事多，很少落到具体型号。",
    "知乎": "公式清楚，商业清单文也不少。",
    "公众号": "搜狗索引不是全文；跳到原文的才是公开正文。",
    "Google News": "这是新闻索引，不是社交帖全文。",
    "Bluesky": "短帖多，公开讨论，不是站内热搜榜。",
    "Reddit": "公开帖和讨论串，登录墙后的全文可能没有。",
    "YouTube": "只有标题和简介，未见口播原文。",
}
_STALE_BRIEF_RE = re.compile(
    r"这批材料共\s*\d+|不是已经核实的答案|围绕「|不当抽样|不适合当完整论证|"
    r"当可带走的对象|反复落到|^在推|很少点名单品，更多是|还没收到稳定对象"
)
_EMPTY_BRIEF_RE = re.compile(
    r"未返回|没有可引用|没有可用|没有可读|本批未返回|该渠道没有|"
    r"栏目上没有|索引层面缺失|没有搜到|没收回"
)


def _item_blob(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "text", "video_text", "desc")
    )


def _brief_hits(blob: str, keys: tuple[str, ...]) -> int:
    return sum(blob.count(key) for key in keys)


def _brief_item_hits(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> int:
    n = 0
    for item in rows:
        blob = (item.get("title") or "") + " " + (item.get("text") or "")[:180]
        if any(key in blob for key in keys):
            n += 1
    return n


def _brief_objects(rows: list[dict[str, Any]]) -> list[str]:
    scored = [
        (_brief_item_hits(rows, keys), name)
        for name, keys in _BRIEF_OBJECTS
        if _brief_item_hits(rows, keys)
    ]
    scored.sort(reverse=True)
    strong = [name for n, name in scored if n >= 2]
    return (strong or [name for _n, name in scored])[:4]


def _brief_direction(blob: str, objects: list[str]) -> str:
    useful = _brief_hits(blob, ("实用", "能用", "日常", "舍不得自己买"))
    ritual = _brief_hits(blob, ("仪式", "拆箱", "开箱", "装箱", "种草"))
    budget = _brief_hits(blob, ("预算", "价位", "全价位"))
    avoid = _brief_hits(blob, ("避雷", "别送", "不建议", "踩雷", "第一个排除"))
    ask = _brief_hits(blob, ("求吧友", "求大家", "给点意见", "求开导"))
    formula = _brief_hits(blob, ("万能公式", "什么关系送什么"))
    parts: list[str] = []
    if ask >= 2 and ask >= useful:
        parts.append("先问人要主意，还没收到单品")
    elif ritual > useful:
        parts.append("往好看、好拍、有开箱感收")
    elif useful:
        parts.append("往日常能用、自己舍不得买的东西收")
    if budget:
        parts.append("按价位切开")
    if avoid >= 2:
        parts.append("化妆品和人人都有的摆设会被拿来避")
    if formula:
        parts.append("先定关系和阶段，再定品类")
    if objects and not parts:
        parts.append("往" + "、".join(objects[:2]) + "上收")
    return "，".join(parts[:3]) or "还没收到统一方向"


def _brief_trend(rows: list[dict[str, Any]], blob: str) -> str:
    hot = sorted(rows, key=lambda item: float(item.get("liked_n") or 0), reverse=True)[:6]
    hot_blob = "".join(_item_blob(item) for item in hot)
    if _brief_hits(hot_blob, ("小钱", "装大货", "省钱")):
        return "眼下热帖在比谁更能小钱办大事"
    if _brief_hits(hot_blob, ("拆箱", "装箱", "开箱")):
        return "眼下热的是装箱、拆箱和仪式感"
    if _brief_hits(hot_blob, ("合集", "清单", "指南")):
        return "眼下热的是能直接抄的清单和价位表"
    if _brief_hits(hot_blob, ("公式", "排行", "top")):
        return "眼下热的是万能公式和排行榜"
    if _brief_hits(hot_blob, ("送什么", "求")):
        return "眼下热的是求助，不是安利"
    if _brief_hits(blob, ("文案", "抄作业")):
        return "眼下清单和文案是绑在一起给的"
    return "眼下还没有一条特别热的线"


def _brief_avoids(blob: str) -> list[str]:
    avoid: list[str] = []
    if _brief_hits(blob, ("口红", "化妆品")) and _brief_hits(
        blob, ("排除", "别送", "避雷", "不建议", "第一个排除")
    ):
        avoid.append("口红和化妆品")
    if _brief_hits(blob, ("摆设",)) and _brief_hits(blob, ("不实用", "不要送")):
        avoid.append("不实用的摆设")
    return avoid


def _brief_paragraph(
    label: str,
    objects: list[str],
    blob: str,
    titles: str,
    rows: list[dict[str, Any]],
) -> str:
    avoid = _brief_avoids(blob)
    skip = set(avoid) | {"口红/化妆品"}
    objects = [name for name in objects if name not in skip]
    ask = _brief_hits(blob, ("求吧友", "求大家", "给点意见", "求开导", "女朋友快过生日"))
    useful = _brief_hits(titles or blob, ("实用", "能用", "日常", "舍不得自己买"))
    ritual = _brief_hits(titles or blob, ("仪式", "拆箱", "开箱", "装箱", "种草"))
    budget = _brief_hits(titles or blob, ("预算", "价位", "全价位"))
    formula = _brief_hits(titles or blob, ("万能公式", "什么关系送什么"))
    avoid_n = _brief_hits(titles or blob, ("避雷", "别送", "不建议", "踩雷", "第一个排除"))
    trend = _brief_trend(rows, titles or blob)
    names = "、".join(objects[:3])

    if ask >= 2 and (not objects or ask >= 3):
        lead = {
            "贴吧": "吧友多半在互相问过生日送什么、恋爱要花多少，以及一堆劝退故事，很少点到具体单品或型号。",
            "知乎": "回答里还停在过生日送什么、恋爱花费这类问法，具体单品还没收拢。",
        }.get(label, "这一站多半在问过生日送什么、恋爱花费和劝退，很少点到具体单品。")
    elif names:
        lead = {
            "小红书": f"笔记在反复点名{names}，当成可以买回去送人的礼物。",
            "抖音": f"视频清单在讲{names}，并配上能跟着买的种草话术。",
            "B站": f"攻略片把{names}摊开讲，常配价位和能抄的型号。",
            "微博": f"收藏帖和拆箱帖在讲{names}，偏好好看、好存的那一类。",
            "知乎": f"回答常落到{names}，并配上能抄的公式或清单。",
            "贴吧": f"帖子里也会提到{names}，但还是求助更多。",
        }.get(label, f"这一站在讲{names}。")
        if avoid:
            lead = lead.rstrip("。") + f"，同时把{'、'.join(avoid)}拿出来避。"
    elif avoid:
        lead = f"这一站先把{'、'.join(avoid)}拿出来避，其余还没有讲到能反复出现的对象。"
    elif _brief_hits(blob, ("存一些", "先截图", "收藏")):
        lead = "这一站在存清单、先截图，选型不如收藏欲强。"
    else:
        lead = _generic_brief_lead(rows)

    heading: list[str] = []
    if ask >= 2 and ask >= useful:
        heading.append("方向还停在先问人要主意")
    elif ritual > useful:
        heading.append("整体往好看、好拍、有开箱感收")
    elif useful:
        heading.append("整体往日常能用、自己舍不得买的东西收")
    if budget:
        heading.append("还会按价位切开")
    if avoid_n >= 2 and not avoid:
        heading.append("化妆品和人人都有的摆设也常被拿来避")
    if formula:
        heading.append("还会先定关系和阶段再定品类")

    parts = [lead]
    if heading:
        parts.append("，".join(heading) + "。")
    if trend:
        parts.append(trend if trend.endswith("。") else trend + "。")
    return "".join(parts)


def _generic_brief_lead(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(rows, key=lambda item: float(item.get("liked_n") or 0), reverse=True)
    titles: list[str] = []
    authors: list[str] = []
    for item in ranked:
        title = compact(str(item.get("title") or ""), 28)
        if title and title not in titles:
            titles.append(title)
        author = str(item.get("author") or "").strip()
        if author and author not in authors:
            authors.append(author)
        if len(titles) >= 3:
            break
    if not titles:
        return "这一站收回了材料，但还没有收成一句清楚的侧面。"
    lead = f"这一站在讲「{titles[0]}」"
    if len(titles) > 1:
        lead += f"，也有「{titles[1]}」"
        if len(titles) > 2:
            lead += f"和「{titles[2]}」"
    lead += "。"
    if authors:
        lead += f"{'、'.join(authors[:3])}这几个账号更常出现。"
    return lead


def local_platform_briefs(query: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        label = item.get("platform_label") or PLATFORM_MAP.get(str(item.get("platform") or ""), "")
        if not label:
            continue
        buckets.setdefault(label, []).append(item)
    briefs: list[dict[str, str]] = []
    for _code, label in PLATFORMS:
        rows = buckets.get(label) or []
        if not rows:
            continue
        blob = "".join(
            (item.get("title") or "") + " " + (item.get("text") or "")[:180]
            for item in rows
        )
        objects = _brief_objects(rows)
        titles = " ".join(item.get("title") or "" for item in rows)
        briefs.append(
            {
                "name": label,
                "answer": _brief_paragraph(label, objects, blob, titles, rows),
                "direction": _brief_direction(titles or blob, objects),
                "trend": _brief_trend(rows, titles or blob),
                "worth": _BRIEF_WORTH.get(label, "当这一站的侧面看，不当抽样。"),
            }
        )
    return briefs


def harvest_report(query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    facts: list[dict[str, str]] = []
    for item in items:
        label = (
            item.get("platform_label")
            or PLATFORM_MAP.get(str(item.get("platform") or ""), "")
            or "其他"
        )
        buckets.setdefault(label, []).append(item)
        if item.get("liked"):
            facts.append(
                {
                    "label": "互动",
                    "value": f"{item.get('liked')} · {item.get('title') or '无标题'}",
                    "item_id": item["id"],
                }
            )
    sections: list[dict[str, Any]] = []
    platforms: list[dict[str, Any]] = []
    for _code, label in PLATFORMS:
        rows = buckets.get(label) or []
        if not rows:
            continue
        platforms.append(
            {
                "name": label,
                "count": len(rows),
                "note": f"本机检索收回 {len(rows)} 条。",
            }
        )
        lines: list[str] = []
        for item in rows[:16]:
            title = item.get("title") or "无标题"
            author = item.get("author") or ""
            fresh = item.get("fresh_label") or ""
            snippet = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:72]
            bit = title
            if author:
                bit += f"。{author}"
            if fresh:
                bit += f" · {fresh}"
            if snippet:
                bit += f"。{snippet}"
            lines.append(bit)
        if len(rows) > 16:
            lines.append(f"这一站还有 {len(rows) - 16} 条，在下面的清单和时间线里。")
        sections.append(
            {
                "heading": f"{label}这一站收回了什么",
                "thesis": f"{label}这次收回 {len(rows)} 条公开检索，还没有模型收口。",
                "body": "\n\n".join(lines) or "这一站还没有可读的标题。",
                "evidence": [
                    {"text": item.get("title") or "无标题", "item_id": item["id"]}
                    for item in rows[:12]
                ],
                "table": _empty_table(),
                "chart": _empty_chart(),
            }
        )
    if not sections:
        sections.append(
            {
                "heading": "这次还没有摊开的材料",
                "thesis": "检索是空的，或者各站都没收回来。",
                "body": "可以换个词再搜，或先去 Chrome 登录对应的站。",
                "evidence": [],
                "table": _empty_table(),
                "chart": _empty_chart(),
            }
        )
    return {
        "title": f"「{query}」材料一览",
        "deck": "本机检索，没有调用模型。下面按平台和时间摊开。",
        "overview": (
            f"「{query}」这次收回 {len(items)} 条，来自 {len(buckets)} 个平台。"
            "没有 Agent 判断，数字和标题都来自原帖。点开一条就能看原文。"
        ),
        "sources": {
            "summary": f"共 {len(items)} 条，来自 {len(buckets)} 个平台的公开搜索。",
            "platforms": platforms,
            "coverage": "目前只有本机检索到的公开结果，没有模型收口，也没有官方核对。",
        },
        "sections": sections,
        "facts": facts[:16],
        "picks": [],
        "map": [],
        "platform_briefs": local_platform_briefs(query, items),
    }


def normalize_agent_search(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    points: list[dict[str, str]] = []
    for row in raw.get("points") or []:
        if not isinstance(row, dict):
            continue
        heading = _as_text(row.get("heading") or row.get("title"))
        body = _as_text(row.get("body") or row.get("text"))
        if not heading and not body:
            continue
        kind = _as_text(row.get("kind")).lower()
        if kind not in {"background", "detail", "caveat", "unknown"}:
            kind = "detail"
        points.append({"heading": heading, "body": body, "kind": kind})
    details = [_as_text(row) for row in (raw.get("details") or []) if _as_text(row)]
    caveats = [_as_text(row) for row in (raw.get("caveats") or []) if _as_text(row)]
    questions = [
        _as_text(row)
        for row in (raw.get("open_questions") or raw.get("questions") or [])
        if _as_text(row)
    ]
    overview = _as_text(raw.get("overview") or raw.get("summary"))
    if not overview and not points and not details:
        return None
    if not any("不是本机检索" in row or "模型补充" in row for row in caveats):
        caveats.append("这是模型补充，不是本机检索，也不进时间线。不能当成已核实新闻。")
    writer = raw.get("writer") if isinstance(raw.get("writer"), dict) else writer_public()
    created = _as_text(raw.get("created_at") or raw.get("generated_at"))
    return {
        "overview": overview,
        "points": points[:12],
        "details": details[:12],
        "caveats": caveats[:8],
        "open_questions": questions[:8],
        "writer": {
            "id": _as_text(writer.get("id")),
            "name": _as_text(writer.get("name")) or "Agent",
            "kind": _as_text(writer.get("kind")) or "builtin",
        },
        "model": _as_text(raw.get("model")) or DEEPSEEK_MODEL,
        "created_at": created,
        "source": "model",
    }


def stamp_agent_search_time(brief: dict[str, Any] | None, job_dir: Path | None = None) -> dict[str, Any] | None:
    packed = normalize_agent_search(brief)
    if not packed:
        return None
    if packed.get("created_at"):
        return packed
    if job_dir is not None:
        for name in ("agent_search.json", "board.json"):
            path = job_dir / name
            if path.is_file():
                packed["created_at"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
                    timespec="seconds"
                )
                break
    return packed


def harvest_board(
    query: str,
    items: list[dict[str, Any]],
    agent_search: dict[str, Any] | None = None,
) -> dict[str, Any]:
    brief = normalize_agent_search(agent_search)
    if brief:
        judgment = {
            "headline_zh": "材料已摊开，模型只作补充",
            "one_line_judgment": "本机检索按平台和时间摊开。Agent 搜索补了一层参考，不进时间线，也不能当成已核实。",
            "plain_language": "没有勾选 Agent 报告，所以没有结论标签。模型补充只用来补背景和细节。",
            "worth_interrupting": False,
            "value": {
                "level": 2,
                "label": "知道即可",
                "reason": "这次没有写成报告，只有本机材料和模型补充。",
            },
            "caveats": [
                "模型补充不是本机检索，也不进时间线。",
                "社交帖只当叙事信号，不是已核实新闻。",
            ],
            "next_watch": "勾选 Agent 报告后，可以不重搜，直接让 Agent 收一次。",
        }
    else:
        judgment = {
            "headline_zh": "这次只摊开材料",
            "one_line_judgment": "没有调用模型，所以没有结论标签。下面按平台、时间和原帖摊开，都可以点开。",
            "plain_language": (
                "无 AI 模式，或没有 API Key、或不勾选 Agent 报告时，一览只做本机检索。"
                "各站摘要是规则粗看，不是模型判断。"
            ),
            "worth_interrupting": False,
            "value": {
                "level": 2,
                "label": "知道即可",
                "reason": "这次没有模型判断，只摊开检索结果。",
            },
            "caveats": [
                "没有调用模型，页面上的话都不是结论。",
                "社交帖只当叙事信号，不是已核实新闻。",
            ],
            "next_watch": "关掉无 AI 并勾选 Agent 报告后，可以不重搜，直接让 Agent 收一次。",
        }
    return attach_report(
        query,
        items,
        {
            "harvest": True,
            "writer": None,
            "agent_search": brief,
            "report": harvest_report(query, items),
            "judgment": judgment,
            "references": local_references(items),
        },
    )


def _normalize_platform_briefs(
    raw: Any, query: str, items: list[dict[str, Any]]
) -> list[dict[str, str]]:
    known = {label for _code, label in PLATFORMS}
    counts = _item_platform_counts(items)
    briefs: list[dict[str, str]] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            name = _as_text(row.get("name") or row.get("platform") or row.get("label"))
            answer = _as_text(row.get("answer") or row.get("take") or row.get("summary"))
            worth = _as_text(row.get("worth") or row.get("use") or row.get("value"))
            direction = _as_text(row.get("direction") or row.get("heading"))
            trend = _as_text(row.get("trend") or row.get("now"))
            if not name or not answer:
                continue
            if name not in known and name not in PLATFORM_MAP:
                name = PLATFORM_MAP.get(name, name)
            if re.search(r"^https?://|^\d+、|^[-•]", answer):
                continue
            if _STALE_BRIEF_RE.search(answer) or _STALE_BRIEF_RE.search(worth):
                continue
            if "。" not in answer:
                continue
            if counts.get(name) and _EMPTY_BRIEF_RE.search(answer + worth):
                continue
            briefs.append(
                {
                    "name": name,
                    "answer": answer,
                    "direction": direction,
                    "trend": trend,
                    "worth": worth,
                }
            )
    local = local_platform_briefs(query, items)
    if len(briefs) < 2:
        return local
    by_name = {row["name"]: row for row in briefs}
    merged: list[dict[str, str]] = []
    for _code, label in PLATFORMS:
        found = by_name.get(label) or next((row for row in local if row["name"] == label), None)
        if found:
            merged.append(found)
    return merged


def hoist_report(board: dict[str, Any]) -> dict[str, Any]:
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    report = dict(report)
    top_sections = board.get("sections")
    if _looks_like_platform_catalog(report.get("sections")) and not _looks_like_platform_catalog(
        top_sections
    ):
        report["sections"] = top_sections
    top_map = board.get("map")
    if _looks_like_platform_catalog(report.get("map")) and not _looks_like_platform_catalog(top_map):
        report["map"] = top_map
    if not _as_text(report.get("overview")):
        report["overview"] = _as_text(board.get("overview"))
    for key in (
        "sources",
        "sections",
        "facts",
        "map",
        "gallery",
        "timeline",
        "picks",
        "pick_frame",
        "platform_briefs",
        "answer",
        "conclusion",
    ):
        if not report.get(key) and board.get(key) is not None:
            report[key] = board[key]
    return report


def enrich_report(
    query: str, items: list[dict[str, Any]], board: dict[str, Any]
) -> dict[str, Any]:
    intent = report_intent(query, str(board.get("rewrite_note") or ""))
    board["intent"] = {
        "mode": intent["mode"],
        "label": intent["label"],
        "topic": intent["topic"],
        "job": intent["job"],
        "pick_title": intent.get("pick_title") or "",
        "pick_lead": intent.get("pick_lead") or "",
        "pick_foot": intent.get("pick_foot") or "",
        "pick_go": intent.get("pick_go") or "",
        "pick_skip": intent.get("pick_skip") or "",
        "pick_watch": intent.get("pick_watch") or "",
    }
    report = hoist_report(board)
    sections = _normalize_sections(report.get("sections"), items)
    if not sections:
        generated = local_report(query, items, _as_text(report.get("overview")))
        sections = generated["sections"]
        report = {**generated, **report, "sections": sections}
    sections = [
        _fill_section_cases(section, items, query)
        for section in sections
        if not any(
            token in (section.get("heading") or "")
            for token in ("互动最高", "按点赞", "传播排序")
        )
    ]
    tags = tag_table(items)
    if (
        tags["rows"]
        and intent["mode"] in NARRATIVE_MODES
        and not any(
            "话题" in (section.get("heading") or "") or "标签" in (section.get("heading") or "")
            for section in sections
        )
    ):
        sections.append(
            {
                "heading": "用户讨论最高的标签和叙事",
                "thesis": "标签不能代表真实用户比例，但能看出这批帖子把自己归到哪些话术里。",
                "body": "出现次数高的标签，说明创作者认为这些词能带来流量。它是叙事信号，不是民意调查。",
                "evidence": [],
                "table": tags,
                "chart": {
                    "title": "高频标签",
                    "kind": "bar",
                    "items": [
                        {"label": row[0], "value": float(row[1])}
                        for row in tags["rows"][:8]
                    ],
                },
            }
        )
    facts = _normalize_facts(report.get("facts"))
    if not facts:
        facts = local_report(query, items).get("facts") or []
    mapping = _normalize_map(report.get("map"))
    if _looks_like_platform_catalog(mapping):
        mapping = []
    if not mapping:
        mapping = _normalize_map(board.get("map"))
    raw_picks = report.get("picks")
    picks = _normalize_picks(raw_picks, items)
    frame = pick_frame(
        intent["mode"],
        report.get("pick_frame") if isinstance(report.get("pick_frame"), dict) else None,
    )
    for pick in picks:
        pick["tag"] = pick_tag(pick, frame, intent["mode"])
    dropped_anecdote = isinstance(raw_picks, list) and any(
        isinstance(row, dict) and _is_anecdote_pick(row) for row in raw_picks
    )
    judgment = _normalize_judgment(board.get("judgment"))
    conclusion = _normalize_conclusion(report.get("conclusion"), report, judgment, picks)
    board["report"] = {
        "title": _as_text(report.get("title")) or f"「{query}」检索报告",
        "deck": _as_text(report.get("deck")),
        "overview": _as_text(report.get("overview")),
        "sources": _normalize_sources(report.get("sources"), items),
        "sections": sections,
        "facts": facts,
        "answer": conclusion["answer"],
        "conclusion": conclusion,
        "pick_frame": frame,
        "picks": picks,
        "map": mapping,
        "platform_briefs": _normalize_platform_briefs(
            report.get("platform_briefs") or board.get("platform_briefs"),
            query,
            items,
        ),
        "engagement": engagement_by_platform(items),
        "gallery": report_gallery(items),
        "timeline": report_timeline(items),
    }
    if board.get("harvest"):
        board["report"]["answer"] = ""
        board["report"]["picks"] = []
        board["report"]["map"] = []
        board["report"]["conclusion"] = {
            "form": "prose",
            "answer": "",
            "flow": [],
            "table": _empty_table(),
            "chart": _empty_chart(),
        }
    if judgment:
        if dropped_anecdote:
            note = "单帖处境没有写进结论清单，只当个例。"
            caveats = list(judgment.get("caveats") or [])
            if note not in caveats:
                caveats.append(note)
            judgment["caveats"] = caveats
        board["judgment"] = judgment
    ensure_judgment(query, items, board)
    refs = board.get("references")
    normalized_refs: list[dict[str, Any]] = []
    if isinstance(refs, list):
        for row in refs:
            if not isinstance(row, dict):
                continue
            item_id = _as_text(row.get("item_id"))
            if not item_id:
                continue
            role = row.get("role") if row.get("role") in _ROLE else "context"
            act = row.get("recommendation") if row.get("recommendation") in _ACT else "know"
            normalized_refs.append(
                {
                    "item_id": item_id,
                    "headline_zh": _as_text(row.get("headline_zh")) or _item_title(item_id, items),
                    "summary": _as_text(row.get("summary")),
                    "role": role,
                    "recommendation": act,
                }
            )
    if not normalized_refs:
        board["references"] = local_references(items)
    else:
        have = {row["item_id"] for row in normalized_refs}
        board["references"] = normalized_refs + [
            row for row in local_references(items) if row["item_id"] not in have
        ]
    fill_board_illustrations(board, query)
    if board.get("harvest"):
        board["harvest"] = True
        board["writer"] = None
    else:
        board["writer"] = writer_public()
    return attach_agent_search(board)


def attach_agent_search(board: dict[str, Any], job_dir: Path | None = None) -> dict[str, Any]:
    brief = stamp_agent_search_time(board.get("agent_search"), job_dir)
    if brief:
        board["agent_search"] = brief
    return board


def illustration_urls(board: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for row in section.get("illustrations") or []:
            if isinstance(row, dict) and row.get("url"):
                urls.append(str(row["url"]))
    judgment = board.get("judgment") if isinstance(board.get("judgment"), dict) else {}
    for row in judgment.get("illustrations") or []:
        if isinstance(row, dict) and row.get("url"):
            urls.append(str(row["url"]))
    return urls


def fill_board_illustrations(board: dict[str, Any], query: str = "") -> dict[str, Any]:
    if board.get("harvest"):
        return board
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    sections = report.get("sections") if isinstance(report.get("sections"), list) else []
    used: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        wanted = normalize_illustration_queries(section.get("illustrations"), 2)
        if not wanted:
            blob = " ".join(
                str(section.get(key) or "")
                for key in ("heading", "thesis", "body")
            )
            wanted = suggest_illustration_queries(query, blob, 2)
        if not wanted:
            continue
        for row in wanted:
            row["query"] = bind_illustration_query(row.get("query") or "", query)
            url = str(row.get("url") or "")
            if url and url in used:
                row.pop("url", None)
                row.pop("source", None)
        section["illustrations"] = fill_illustration_hits(wanted, topic=query, used_urls=used)
    judgment = board.get("judgment") if isinstance(board.get("judgment"), dict) else {}
    wanted = normalize_illustration_queries(judgment.get("illustrations"), 3)
    if wanted:
        for row in wanted:
            row["query"] = bind_illustration_query(row.get("query") or "", query)
            url = str(row.get("url") or "")
            if url and url in used:
                row.pop("url", None)
                row.pop("source", None)
        judgment["illustrations"] = fill_illustration_hits(wanted, topic=query, used_urls=used)
        board["judgment"] = judgment
    return board


def attach_report(
    query: str, items: list[dict[str, Any]], board: dict[str, Any]
) -> dict[str, Any]:
    report = board.get("report")
    if not isinstance(report, dict):
        report = {}
    else:
        report = dict(report)
    harvest = bool(board.get("harvest"))
    if (not harvest and _looks_like_platform_catalog(report.get("sections"))) or not report.get(
        "sections"
    ):
        top_sections = board.get("sections")
        if isinstance(top_sections, list) and top_sections and not _looks_like_platform_catalog(
            top_sections
        ):
            report["sections"] = top_sections
    if _looks_like_platform_catalog(report.get("map")) or not report.get("map"):
        top_map = board.get("map")
        if top_map and not _looks_like_platform_catalog(top_map):
            report["map"] = top_map
    if not _as_text(report.get("overview")):
        report["overview"] = _as_text(board.get("overview"))
    has_writeup = bool(
        _as_text(report.get("overview"))
        or report.get("sections")
        or report.get("picks")
        or board.get("sections")
        or board.get("picks")
    )
    if not has_writeup:
        digest = board.get("digest") if isinstance(board.get("digest"), dict) else {}
        generated = local_report(query, items, str(digest.get("overview") or ""))
        if digest:
            generated["facts"] = digest.get("facts") or generated["facts"]
            generated["map"] = digest.get("map") or generated["map"]
        report = {**generated, **report}
    board["report"] = report
    judgment = board.get("judgment")
    if not isinstance(judgment, dict):
        board["judgment"] = {
            "headline_zh": board.get("headline_zh") or f"「{query}」",
            "one_line_judgment": board.get("one_line_judgment") or "",
            "plain_language": board.get("plain_language") or "",
            "worth_interrupting": bool(board.get("worth_interrupting")),
            "value": board.get("value")
            or {"level": 2, "label": "知道即可", "reason": ""},
            "caveats": board.get("caveats") or [],
            "next_watch": board.get("next_watch") or "",
        }
    refs = board.get("references")
    if not isinstance(refs, list) or not refs:
        board["references"] = local_references(items)
    else:
        have = {row.get("item_id") for row in refs if isinstance(row, dict)}
        extra = [row for row in local_references(items) if row["item_id"] not in have]
        if extra:
            board["references"] = list(refs) + extra
    return enrich_report(query, items, board)


def attach_digest(
    query: str, items: list[dict[str, Any]], board: dict[str, Any]
) -> dict[str, Any]:
    return attach_report(query, items, board)


def fallback_board(query: str, items: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return attach_report(
        query,
        items,
        {
            "report": local_report(
                query,
                items,
                f"「{query}」收回 {len(items)} 条。十一没写成完整报告，先按来源摊开，不要把空白判断当成结论。",
            ),
            "judgment": {
                "headline_zh": f"「{query}」还没有收成判断",
                "one_line_judgment": "模型输出没法读成报告判断。上面的来源和条目仍可看。",
                "plain_language": reason,
                "worth_interrupting": False,
                "value": {
                    "level": 2,
                    "label": "知道即可",
                    "reason": "材料在，判断这一步失败了",
                },
                "caveats": [reason, "社交帖只当叙事信号。"],
                "next_watch": "可以再让十一写一次报告，不必重新搜。",
            },
            "references": local_references(items),
            "fallback": True,
        },
    )


_FRESH_RANK = {
    "week": 0,
    "month": 1,
    "quarter": 2,
    "unknown": 3,
    "half": 4,
    "year": 5,
    "old": 6,
}


def _fresh_rank(item: dict[str, Any]) -> tuple[int, float]:
    band = str(item.get("freshness") or "")
    if band not in _FRESH_RANK:
        band = str(item_freshness(item).get("band") or "unknown")
    return (_FRESH_RANK.get(band, 3), -float(item.get("liked_n") or 0))


def _select_eleven_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=_fresh_rank)


def _eleven_body_limit(n: int, platform: str) -> int:
    long_form = platform in {"tieba", "zhihu", "reddit", WEIXIN_CODE}
    if n > 600:
        return 360 if long_form else 72
    if n > 360:
        return 640 if long_form else 100
    if n > 200:
        return 900 if long_form else 140
    if n > 120:
        return 1200 if long_form else 160
    if n > 60:
        return 1400 if long_form else 280
    return 1600 if long_form else 720


def _compact_eleven_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in ("", [], None)
    }


def _spoken_for_eleven(item: dict[str, Any], full: bool) -> str:
    source = str(item.get("text_source") or "")
    raw = str(item.get("video_text") or "")
    if source in {"subtitle", "caption"}:
        return clip_spoken(raw, ELEVEN_VIDEO_TEXT_FULL if full else ELEVEN_VIDEO_TEXT_BRIEF)
    if source == "desc":
        return clip_spoken(raw, 800)
    return clip_spoken(raw, 400)


def _eleven_payload(
    query: str,
    items: list[dict[str, Any]],
    intent: dict[str, str] | None = None,
    extra_note: str = "",
    prior_notes: list[str] | None = None,
) -> dict[str, Any]:
    extra_note = " ".join((extra_note or "").split())
    priors = [ " ".join(str(row).split()) for row in (prior_notes or []) if str(row).strip() ]
    intent = intent or report_intent(query, extra_note)
    for item in items:
        stamp_freshness(item, query)
    shown = _select_eleven_items(items)
    clock = freshness_summary(shown, query)
    all_counts = _item_platform_counts(items)
    inventory = [
        {
            "name": label,
            "count": all_counts.get(label, 0),
            "shown": all_counts.get(label, 0),
        }
        for _code, label in PLATFORMS
        if all_counts.get(label)
    ]
    full_ids = {
        item["id"]
        for item in sorted(
            (
                row
                for row in shown
                if row.get("kind") == "video"
                and row.get("text_source") in {"subtitle", "caption"}
            ),
            key=lambda row: float(row.get("liked_n") or 0),
            reverse=True,
        )[:ELEVEN_VIDEO_FULL]
    }
    return {
        "query": query,
        "intent": {
            "mode": intent["mode"],
            "label": intent["label"],
            "topic": intent["topic"],
            "job": intent["job"],
        },
        "item_count": len(items),
        "item_shown": len(shown),
        "platforms": inventory,
        "freshness": clock,
        "note": (
            f"共收回 {len(items)} 条，全部写入材料，按近况排在前面。"
            + (" " + clock["note"] if clock.get("note") else "")
        )
        + " platforms 里 count>0 的站已经有材料，platform_briefs 和 sources 不能写成未返回。",
        "rewrite": bool(extra_note),
        "user_note": extra_note,
        "prior_notes": priors,
        "task": (
            (
                f"使用者没有新搜平台，要求用已有全部 {len(items)} 条材料按新逻辑重写完整报告：{extra_note}。"
                f"原搜索词是「{query}」，对象不能换。这不是小修小补，不要只改 overview 或加一段。"
                "必须重写结论、导图、各平台摘要、深度阅读、判断和来源。"
                f"再完成这个任务：{intent['job']}"
            )
            if extra_note
            else f"先读完全部材料，再完成这个任务：{intent['job']}"
        )
        + " 结论必须直接回答这次任务。先写 report.answer，再按题目决定用段落、清单、流程、数据还是混合。"
        " picks 必须是材料里出现过的具体对象。深度阅读围着这个结论写。"
        " 不要不管搜索词一律写成叙事地图。"
        " 若有 agent_search，用它补条理和细节，但本机材料优先；模型补充不能写进时间线。"
        " 有材料的站必须写各平台摘要，不能写成未返回或没有正文。",
        "items": [
            _compact_eleven_item(
                {
                    "id": item["id"],
                    "platform": item["platform_label"],
                    "title": item.get("text_zh") and item.get("title") or item["title"],
                    "text": compact(
                        item.get("text_zh") or item.get("text") or "",
                        _eleven_body_limit(len(shown), str(item.get("platform") or "")),
                    ),
                    "text_orig": compact(item.get("text") or "", 400)
                    if item.get("text_zh")
                    else "",
                    "author": item["author"],
                    "fans": item.get("fans") or "",
                    "published": item["published"],
                    "freshness": item.get("freshness") or "",
                    "fresh_label": item.get("fresh_label") or "",
                    "fresh_weight": item.get("fresh_weight") or "",
                    "fresh_hint": item.get("fresh_hint") or "",
                    "age_days": item.get("age_days"),
                    "url": item["url"],
                    "liked": item["liked"],
                    "collected": item.get("collected") or "",
                    "comment_count": item.get("comment_count") or "",
                    "share_count": item.get("share_count") or "",
                    "kind": item.get("kind") or "",
                    "duration": item.get("duration") or "",
                    "video_text": _spoken_for_eleven(item, item["id"] in full_ids),
                    "text_source": item.get("text_source") or "",
                    "scene": item.get("scene") or "",
                    "tags": item.get("tags") or [],
                    "image_count": len(item.get("images") or []),
                    "comments": item.get("comments") or [],
                }
            )
            for item in shown
        ],
    }


async def _post_eleven(body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(
            DEEPSEEK_ENDPOINT,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"十一请求失败 HTTP {response.status_code}")
    envelope = response.json()
    if not isinstance(envelope, dict):
        raise RuntimeError("十一返回的不是对象")
    if envelope.get("status") == "incomplete":
        reason = ""
        details = envelope.get("incomplete_details") or {}
        if isinstance(details, dict):
            reason = str(details.get("reason") or "")
        raise RuntimeError(f"十一输出不完整{('：' + reason) if reason else ''}")
    text = extract_output_text(envelope)
    if not text:
        raise RuntimeError("十一没有返回可用文本")
    return envelope, text


_VISION_OK = True


async def _vision_frames(paths: list[Path], title: str, video_text: str) -> str:
    global _VISION_OK
    if not _VISION_OK or not DEEPSEEK_KEY:
        return ""
    images = [path for path in paths[:3] if path.is_file() and path.stat().st_size > 800]
    if not images:
        return ""
    content: list[dict[str, str]] = [
        {
            "type": "input_text",
            "text": (
                f"标题：{compact(title, 80)}\n"
                f"视频上的字：{compact(video_text, 160) or '无'}\n"
                "下面是开头/中段/后段截帧。只根据看见的画面写："
                "1) 在干什么 2) 能看清的字幕原文。没看见的写没看见。"
            ),
        }
    ]
    for path in images:
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{b64}",
            }
        )
    try:
        _env, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": (
                    "你在读短视频截帧，不是看完整视频。"
                    "只描述看见的画面和字幕，不要补剧情、不要猜没出现的结论。"
                ),
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": 220,
            }
        )
        return compact(text, 220)
    except Exception:
        _VISION_OK = False
        return ""


async def describe_top_videos(job_dir: Path, items: list[dict[str, Any]]) -> None:
    videos = [
        item
        for item in items
        if item.get("kind") == "video" and not item.get("scene")
    ]
    videos = sorted(
        videos, key=lambda item: float(item.get("liked_n") or 0), reverse=True
    )[:8]
    for item in videos:
        raw_id = item["id"].split(":", 1)[-1]
        frames = sidecar_frames(job_dir, item.get("platform") or "", raw_id)
        if not frames:
            continue
        scene = await _vision_frames(
            frames, item.get("title") or "", item.get("video_text") or ""
        )
        if not scene:
            break
        item["scene"] = scene
        write_sidecar_scene(job_dir, item.get("platform") or "", raw_id, scene)


def _ddg_unwrap(url: str) -> str:
    text = unescape((url or "").strip())
    if text.startswith("//"):
        text = "https:" + text
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    if "duckduckgo.com" in host or host.endswith("duck.com"):
        for key in ("uddg", "u"):
            target = parse_qs(parsed.query).get(key, [""])[0]
            if target:
                return unquote(target)
    return text


def _plain_html(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", text or "")).replace("\xa0", " ")


def _citations_from_hits(raw_cites: Any, hits: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed = {row["url"].rstrip("/"): row for row in hits}
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    rows = raw_cites if isinstance(raw_cites, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _ddg_unwrap(str(row.get("url") or "")).rstrip("/")
        if url not in allowed:
            match = next((hit for hit in hits if hit["url"].rstrip("/") == url), None)
            if not match:
                continue
            url = match["url"]
        if url in seen:
            continue
        seen.add(url)
        hit = allowed.get(url) or {}
        refs.append(
            {
                "title": str(row.get("title") or hit.get("title") or url),
                "url": url,
                "note": str(row.get("note") or hit.get("snippet") or ""),
            }
        )
    return refs


def _web_query(question: str, topic: str = "") -> str:
    return " ".join(part for part in (topic, question) if part).strip()


def _wiki_query(question: str, topic: str = "") -> str:
    extra = re.sub(r"[你您请问啊呀呢吗？?]+", " ", question or "")
    extra = re.sub(r"为什么|怎么会|是什么意思|从哪来|由来|叫", " ", extra)
    extra = compact(extra, 40)
    if topic and extra:
        return compact(f"{topic} {extra}", 80)
    return compact(topic or extra or question, 80)


async def web_lookup(question: str, topic: str = "") -> list[dict[str, str]]:
    query = _web_query(question, topic)
    if not query:
        return []
    wiki_query = _wiki_query(question, topic)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    skip_hosts = ("duckduckgo.com", "duck.com", "google.com", "bing.com")

    def add(title: str, url: str, snippet: str) -> None:
        clean = _ddg_unwrap(url)
        if not clean.startswith("http") or clean in seen:
            return
        host = urlparse(clean).netloc.lower()
        if any(host == item or host.endswith("." + item) for item in skip_hosts):
            return
        seen.add(clean)
        hits.append(
            {
                "title": compact(_plain_html(title), 80),
                "url": clean,
                "snippet": compact(_plain_html(snippet), 180),
            }
        )

    def add_from_html(page: str) -> None:
        titles = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            page,
            flags=re.I | re.S,
        )
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td|span|div)>',
            page,
            flags=re.I | re.S,
        )
        for index, (href, title_html) in enumerate(titles[:8]):
            snippet = snippets[index] if index < len(snippets) else ""
            add(title_html, href, snippet)
        if titles:
            return
        for href, title_html in re.findall(
            r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            page,
            flags=re.I | re.S,
        )[:8]:
            add(title_html, href, "")

    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True, trust_env=False) as client:
            instant = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers=headers,
            )
            if instant.status_code < 400:
                data = instant.json()
                if isinstance(data, dict):
                    if data.get("AbstractText") and data.get("AbstractURL"):
                        add(
                            str(data.get("Heading") or query),
                            str(data.get("AbstractURL")),
                            str(data.get("AbstractText")),
                        )
                    for row in data.get("RelatedTopics") or []:
                        if not isinstance(row, dict):
                            continue
                        add(
                            str(row.get("Text") or "")[:80],
                            str(row.get("FirstURL") or ""),
                            str(row.get("Text") or ""),
                        )
            for endpoint in (
                "https://html.duckduckgo.com/html/",
                "https://lite.duckduckgo.com/lite/",
            ):
                page = await client.get(endpoint, params={"q": query}, headers=headers)
                if page.status_code < 400:
                    add_from_html(page.text)
                if len(hits) >= 4:
                    break
            if wiki_query:
                for wiki_host in ("zh.wikipedia.org", "en.wikipedia.org"):
                    wiki = await client.get(
                        f"https://{wiki_host}/w/api.php",
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": wiki_query,
                            "srlimit": "3",
                            "format": "json",
                            "utf8": "1",
                        },
                        headers=headers,
                    )
                    if wiki.status_code >= 400:
                        continue
                    data = wiki.json()
                    pages = ((data.get("query") or {}).get("search") or []) if isinstance(data, dict) else []
                    for page in pages:
                        if not isinstance(page, dict):
                            continue
                        title = str(page.get("title") or "")
                        if not title:
                            continue
                        add(
                            title,
                            f"https://{wiki_host}/wiki/{quote(title.replace(' ', '_'), safe='()_')}",
                            str(page.get("snippet") or ""),
                        )
    except Exception as error:
        print(f"web_lookup failed: {error}", flush=True)
        return hits[:6]
    return hits[:6]


def _ask_needs_web(parsed: dict[str, Any], answer: str) -> bool:
    if parsed.get("need_web") is True:
        return True
    if parsed.get("used_materials") is False:
        return True
    if parsed.get("used_materials") is True and parsed.get("need_web") is False:
        return False
    marks = (
        "材料里没有",
        "报告中没有任何",
        "检索材料不够",
        "这批材料没有",
        "材料没有提到",
        "没有任何一条解释",
        "无法从材料",
        "材料不足以",
    )
    return any(mark in answer for mark in marks)


async def ask_agent_search(query: str) -> dict[str, Any]:
    if not DEEPSEEK_KEY:
        raise RuntimeError("还没有配置 API Key")
    agent = active_agent()
    extra = ""
    if agent["id"] != "eleven":
        extra = f"不要自称十一。你现在是「{agent['name']}」。"
    body = {
        "model": DEEPSEEK_MODEL,
        "reasoning": {"effort": "none"},
        "instructions": "\n".join(
            [
                agent_persona(agent["id"]),
                extra,
                "你现在不做本机检索，也不写完整看板报告。",
                "把这个话题用你已有的知识整理成一份补充参考，帮助后面把报告写得更有条理、细节更够。",
                "不要假装刚上过网，不要编链接、发布时间或社交帖。",
                "不确定的数字和专名要写清不确定。",
                "不要输出时间线条目，也不要冒充本机检索结果。",
                "只返回 JSON：overview, points[{heading,body,kind}], details[], caveats[], open_questions[]。",
                "kind 只能是 background / detail / caveat / unknown。",
            ]
        ),
        "input": json.dumps(
            {"query": query, "task": "给这个话题一份模型补充参考，不进时间线。"},
            ensure_ascii=False,
        ),
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 4000,
    }
    _envelope, text = await _post_eleven(body)
    parsed = parse_model_json(text)
    out = normalize_agent_search(
        {
            **parsed,
            "writer": writer_public(),
            "model": DEEPSEEK_MODEL,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    if not out:
        raise RuntimeError("Agent 搜索没有给出可用补充")
    return out


async def ask_eleven(
    query: str,
    items: list[dict[str, Any]],
    extra_note: str = "",
    agent_search: dict[str, Any] | None = None,
    prior_notes: list[str] | None = None,
) -> dict[str, Any]:
    if not DEEPSEEK_KEY:
        raise RuntimeError("还没有配置 DEEPSEEK_API_KEY")
    if not items:
        return fallback_board(query, items, "没有原始材料，十一没有东西可判断。")

    intent = report_intent(query, extra_note)
    payload = _eleven_payload(
        query, items, intent, extra_note=extra_note, prior_notes=prior_notes
    )
    brief = normalize_agent_search(agent_search)
    if brief:
        payload["agent_search"] = brief
    attempts = [
        {
            "model": DEEPSEEK_MODEL,
            "reasoning": {"effort": "none"},
            "instructions": eleven_instructions(intent),
            "input": json.dumps(payload, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "search_board",
                    "schema": BOARD_SCHEMA,
                },
            },
            "max_output_tokens": 16000,
        },
        {
            "model": DEEPSEEK_MODEL,
            "reasoning": {"effort": "none"},
            "instructions": eleven_instructions(intent),
            "input": json.dumps(payload, ensure_ascii=False),
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 16000,
        },
    ]
    last_error = "十一没有返回可用 JSON"
    for body in attempts:
        try:
            _envelope, text = await _post_eleven(body)
            parsed = parse_model_json(text)
            if brief:
                parsed["agent_search"] = brief
            if extra_note:
                parsed["rewrite_note"] = extra_note
            return attach_report(query, items, parsed)
        except Exception as error:
            last_error = str(error)
    return fallback_board(
        query,
        items,
        f"十一的模型输出读不出来（{last_error}）。原始检索还在，可以再收一次。",
    )


def human_status(platform: str, label: str, line: str) -> dict[str, str] | None:
    if any(
        token in line
        for token in ("Launching browser", "launch_browser", "Begin create browser")
    ):
        return {
            "step": "chrome",
            "title": f"正在打开{label}的 Chrome",
            "detail": "浏览器在后台跑，画面会转到看板右侧。",
        }
    if "Login state result: False" in line:
        return None
    if "Login state result: True" in line or ("pong" in line and "True" in line):
        return {
            "step": "logged_in",
            "title": f"{label}已经是登录状态",
            "detail": "这次不用再登，接着搜。",
        }
    if "已最小化" in line:
        return {
            "step": "logged_in",
            "title": f"{label}已稳定，窗口已最小化",
            "detail": "右侧预览会跟着刚收回的封面走。",
        }
    if (
        "请到弹出的 Chrome" in line
        or "还没登录" in line
        or "登录抖音" in line
        or "登录快手" in line
        or "已打开快手登录页" in line
        or "登录框没自动弹出" in line
    ):
        return {
            "step": "login",
            "title": f"先去 Chrome 登录{label}",
            "detail": f"已弹出{label}登录窗口。扫码或点登录，登完会自动继续；也可以点「我登录好了，接着搜」。",
        }
    if "收到接着搜" in line:
        return {
            "step": "retry",
            "title": f"正在再取{label}",
            "detail": "已接着搜。登录已经好了的话，还要在 Chrome 里过完安全验证。",
        }
    if "详情被拦，先收下搜索列表" in line:
        return {
            "step": "items",
            "title": f"{label}详情被风控，先收下搜索列表",
            "detail": "标题、封面和链接还能用。正文等接口松开再补。",
        }
    if "从页面收" in line or "已从页面收下" in line:
        return {
            "step": "items",
            "title": f"{label}详情页已打开，正在从页面收",
            "detail": "接口被风控了，但页面上没有验证。你看到的那条详情就是正在收的。",
        }
    if "页面上没有验证弹窗" in line or "接口被风控" in line or "取详情仍被风控" in line:
        return {
            "step": "risk",
            "title": f"{label}取详情被风控挡住了",
            "detail": "你看到的首页正常也正常，不一定会弹滑块。先再试几下，还不行就收下搜索列表。日常开着的 Chrome 不是爬虫那一扇。",
        }
    if "被安全验证挡住了" in line or "接口仍被验证挡住" in line or ("还在等你过" in line and "验证" in line):
        return {
            "step": "verify",
            "title": f"{label}被安全验证挡住了",
            "detail": "登录可能已经好了。挡住这一站的是取详情的接口。首页正常也不一定弹窗；如果爬虫窗口里有滑块，过完会自动再取。",
        }
    if "登录已过期" in line:
        return {
            "step": "login",
            "title": f"{label}登录过期了",
            "detail": "再去 Chrome 登一次，或点「我登录好了，接着搜」。已经收下的列表会留下。",
        }
    if "登录成功" in line or "这次不用再登" in line:
        return {
            "step": "logged_in",
            "title": f"{label}已登录",
            "detail": "登录态会留在这台电脑上。",
        }
    if "打开搜索页" in line:
        return {
            "step": "search_page",
            "title": f"已在 Chrome 打开{label}搜索页",
            "detail": "你应该能看到搜索结果页，同时接口在收回内容。",
        }
    if (
        "search Xiaohongshu keyword" in line
        or "search weibo keyword" in line
        or "Begin search" in line
        or "Current search keyword" in line
    ):
        return {
            "step": "search",
            "title": f"正在向{label}要搜索结果",
            "detail": line[-160:],
        }
    if (
        "本页取" in line
        or "已写下第" in line
        or "douyin aweme id" in line
        or "weibo note id" in line
    ):
        return {
            "step": "items",
            "title": line.split("] ", 1)[-1] if "] " in line else line[-160:],
            "detail": "看板上会陆续出现标题。",
        }
    if (
        ("页空了" in line or "没有短视频，先收下" in line or "改用纠错词" in line or "打开搜索页" in line)
        and "verify_check" not in line
        and "要验证" not in line
    ):
        return {
            "step": "items",
            "title": line.split("] ", 1)[-1] if "] " in line else line[-160:],
            "detail": "空页不一定是没登录。没有短视频时会先收下图文。",
        }
    if "截了" in line and "帧" in line:
        return {
            "step": "frames",
            "title": line.split("] ", 1)[-1] if "] " in line else line[-160:],
            "detail": "短视频抽几帧读画面，不下载整支片子。",
        }
    if "Search notes response:" in line:
        return {
            "step": "results",
            "title": f"{label}已经拿到一批结果",
            "detail": "正在把每条详情写下来，看板上会一条条冒出来。",
        }
    if any(
        token in line
        for token in (
            "请通过验证",
            "滑动验证",
            "CAPTCHA appeared",
            "verify_check",
            "被安全验证挡住了",
            "还在等你过抖音安全验证",
        )
    ):
        return {
            "step": "verify",
            "title": f"{label}被安全验证挡住了",
            "detail": "封面会跟着条数走。要过验证时，把窗口拉到前面自己过，过完会继续。",
        }
    if "No more content" in line or "Crawler finished" in line:
        return {
            "step": "platform_wrap",
            "title": f"{label}这一站搜完了",
            "detail": "",
        }
    return None


def parse_platform_queries(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            return {
                str(code): " ".join(str(keyword).split())[:80]
                for code, keyword in parsed.items()
                if str(code) in PLATFORM_MAP and str(keyword).strip()
            }
    out: dict[str, str] = {}
    for part in text.split("|") if "|" in text else text.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        code, value = part.split(":", 1)
        code = code.strip()
        if code not in PLATFORM_MAP:
            continue
        keyword = " ".join(value.split()).strip()
        if keyword:
            out[code] = keyword[:80]
    return out


def platform_queries_text(queries: dict[str, str]) -> str:
    return ",".join(f"{code}:{keyword}" for code, keyword in queries.items() if keyword)


async def english_search_query(query: str) -> str:
    if looks_english(query) or not DEEPSEEK_KEY:
        return query
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": (
                    "把中文搜索意图收成一句不超过 12 个英文单词的检索词。"
                    "保留专有名词。只返回 JSON：{\"en\":\"...\"}。"
                ),
                "input": json.dumps({"query": query}, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 120,
            }
        )
        parsed = parse_model_json(text)
        en = _as_text(parsed.get("en") or parsed.get("query"))
        return en[:80] if en else query
    except Exception:
        return query


def mostly_cjk(text: str) -> bool:
    chars = [ch for ch in (text or "") if not ch.isspace()]
    if len(chars) < 2:
        return False
    cjk = sum(1 for ch in chars if "\u4e00" <= ch <= "\u9fff")
    return cjk >= 2 and cjk / len(chars) >= 0.4


def foreign_row_text(row: dict[str, Any]) -> str:
    return pick(row, "desc", "content", "text", "title")


def apply_foreign_zh(row: dict[str, Any], title_zh: str, text_zh: str) -> None:
    if title_zh:
        if not _as_text(row.get("title_orig")):
            row["title_orig"] = row.get("title") or ""
        row["title_zh"] = title_zh
        row["title"] = title_zh
    if text_zh:
        row["text_zh"] = text_zh


def seed_foreign_zh(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        if _as_text(row.get("text_zh")):
            continue
        title = _as_text(row.get("title"))
        text = foreign_row_text(row)
        if mostly_cjk(title) or mostly_cjk(text):
            blob = compact(" ".join(part for part in (title, text) if part), 700)
            apply_foreign_zh(row, title if mostly_cjk(title) else "", blob)
    return rows


def foreign_translate_need(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not _as_text(row.get("text_zh"))]


async def _translate_foreign_chunk(chunk: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    payload = [
        {
            "id": row.get("id") or row.get("note_id"),
            "title": row.get("title") or "",
            "text": compact(foreign_row_text(row), 700),
        }
        for row in chunk
    ]
    _envelope, text = await _post_eleven(
        {
            "model": DEEPSEEK_MODEL,
            "reasoning": {"effort": "none"},
            "instructions": (
                "把这些外文检索材料译成通顺中文。不要评价，不要改事实，不要保留英文标题。"
                "返回 JSON：{\"items\":[{\"id\",\"title_zh\",\"text_zh\"}]}。"
            ),
            "input": json.dumps({"items": payload}, ensure_ascii=False),
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 2200,
        }
    )
    parsed = parse_model_json(text)
    return {
        _as_text(row.get("id")): row
        for row in (parsed.get("items") or [])
        if isinstance(row, dict) and _as_text(row.get("id"))
    }


async def translate_foreign_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seed_foreign_zh(rows)
    need = foreign_translate_need(rows)
    if not need or not DEEPSEEK_KEY:
        return rows
    mapped: dict[str, dict[str, Any]] = {}
    for start in range(0, len(need), 8):
        chunk = need[start : start + 8]
        try:
            mapped.update(await _translate_foreign_chunk(chunk))
        except Exception:
            continue
    for row in rows:
        hit = mapped.get(_as_text(row.get("id") or row.get("note_id")))
        if not hit:
            continue
        apply_foreign_zh(
            row,
            _as_text(hit.get("title_zh")),
            _as_text(hit.get("text_zh")),
        )
    return rows


def load_json_file(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return data


def save_json_file(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_refine(job_dir: Path, note: str, plan: dict[str, Any]) -> None:
    row = {"ts": time.time(), "note": note, **plan}
    with (job_dir / "refine.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_refine_rows(job_dir: Path) -> list[dict[str, Any]]:
    path = job_dir / "refine.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def run_foreign_platform(
    platform: str,
    query: str,
    job_dir: Path,
    emit,
    options: SearchOptions,
    keep_all: bool = False,
) -> dict[str, Any]:
    label = PLATFORM_MAP[platform]
    filter_query = options.query_for(platform, query)
    keyword = filter_query
    await emit(
        "status",
        {
            "step": "start",
            "title": f"开始搜{label}",
            "detail": (
                f"搜狗微信公开索引，不登录。这一站最多 {min(20, options.notes_for(platform))} 条。"
                " 打开原文时要等搜狗自己跳，验证码过完窗口会最小化。注销/迁移页会自动点，原文失效或跑题会补搜一条。"
                if platform == WEIXIN_CODE
                else f"公开检索，不登录。这一站最多 {options.notes_for(platform)} 条，过期和跑题会丢掉再补。"
            ),
        },
    )
    search_term = weixin_search_query(keyword) if platform == WEIXIN_CODE else keyword
    await emit(
        "log",
        {
            "platform": platform,
            "message": (
                f"开始搜{label}，检索词：{search_term}"
                + (f"（原句：{keyword}）" if search_term != keyword else "")
            ),
        },
    )
    if platform == "yt" and not os.getenv("YOUTUBE_API_KEY", "").strip():
        return {
            "platform": platform,
            "label": label,
            "ok": False,
            "count": 0,
            "error": "YouTube 还没配置 YOUTUBE_API_KEY",
            "items": [],
        }
    if platform == WEIXIN_CODE and not feedgrab_ready():
        return {
            "platform": platform,
            "label": label,
            "ok": False,
            "count": 0,
            "error": "公众号要本机 feedgrab（一览 worker）才能走搜狗公开索引",
            "items": [],
        }
    search_terms = [keyword]
    if platform in FOREIGN_CODES and not looks_english(keyword):
        translated = await english_search_query(keyword)
        if translated and translated != keyword:
            await emit(
                "log",
                {"platform": platform, "message": f"{label}外文检索词：{translated}"},
            )
            if platform == "bsky":
                search_terms = [translated, keyword]
                await emit(
                    "log",
                    {
                        "platform": platform,
                        "message": f"{label}中文检索词也搜一遍，避免只搜英文漏掉豆包这类帖。",
                    },
                )
            else:
                search_terms = [translated]
    if platform == "bsky":
        widened: list[str] = []
        for term in search_terms:
            for seed in bluesky_search_terms(term):
                if seed not in widened:
                    widened.append(seed)
        if widened != search_terms:
            extra = [term for term in widened if term not in search_terms]
            if extra:
                await emit(
                    "log",
                    {
                        "platform": platform,
                        "message": f"{label}短词再搜：{' / '.join(extra[:3])}",
                    },
                )
        search_terms = widened or search_terms
    try:
        rows: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        ask = options.fetch_for(platform)
        for term in search_terms:
            if len(rows) >= ask:
                break
            for row in await search_foreign(platform, term, ask):
                url = str(row.get("url") or row.get("id") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                rows.append(row)
                if len(rows) >= ask:
                    break
    except Exception as error:
        return {
            "platform": platform,
            "label": label,
            "ok": False,
            "count": 0,
            "error": str(error),
            "items": [],
        }
    if platform in FOREIGN_CODES:
        rows = await translate_foreign_rows(rows)
    if platform == WEIXIN_CODE and rows:
        rows = await resolve_weixin_rows(
            rows,
            job_dir,
            emit,
            query=keyword,
            wanted=options.notes_for(platform),
            fetch_more=search_weixin,
        )
    if rows:
        write_foreign_jsonl(job_dir, platform, rows)
    raw_items = read_jsonl(job_dir, platform)
    if platform in LOCAL_IMAGE_PLATFORMS and raw_items:
        saved = await prefetch_item_images(job_dir, raw_items)
        if saved:
            await emit(
                "log",
                {"platform": platform, "message": f"{label}配图已下载 {saved} 张到本地。"},
            )
            raw_items = read_jsonl(job_dir, platform)
    items, dropped = usable_items(
        filter_query, raw_items, None if keep_all else options.notes_for(platform)
    )
    cover = coverage_from_result(platform, len(raw_items), items, dropped)
    if dropped:
        await emit("log", {"platform": platform, "message": drop_note(dropped)})
    if items:
        await emit(
            "items",
            {
                "platform": platform,
                "label": label,
                "items": items,
                "count": len(items),
            },
        )
        await emit(
            "status",
            {
                "step": "items",
                "title": f"{label}核对后留下 {len(items)} 条",
                "detail": (
                    f"抓回 {len(raw_items)}，丢掉 {len(dropped)}。"
                    if dropped
                    else items[-1].get("title") or ""
                ),
            },
        )
        return {
            "platform": platform,
            "label": label,
            "ok": True,
            "count": len(items),
            "error": None,
            "items": items,
            "coverage": cover,
        }
    return {
        "platform": platform,
        "label": label,
        "ok": False,
        "count": 0,
        "error": "这一站公开检索没有收回内容",
        "items": [],
        "coverage": cover,
    }


def crawl_command(
    platform: str, query: str, job_dir: Path, options: SearchOptions
) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--platform",
        platform,
        "--lt",
        LOGIN_TYPE,
        "--type",
        "search",
        "--keywords",
        query,
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(job_dir),
        "--get_comment",
        "true" if options.comments else "false",
        "--get_sub_comment",
        "true" if options.sub_comments else "false",
        "--enable_ip_proxy",
        "false",
        "--crawler_max_notes_count",
        str(options.fetch_for(platform)),
        "--max_concurrency_num",
        "1",
        "--headless",
        "false",
    ]
    if options.comments:
        cmd.extend(["--max_comments_count_singlenotes", str(options.max_comments)])
    return cmd


def attach_comments(
    job_dir: Path, platform: str, items: list[dict[str, Any]], limit: int
) -> None:
    paths: list[Path] = []
    for folder in jsonl_folders(job_dir, platform):
        paths.extend(sorted(folder.glob("search_comments_*.jsonl")))
    if not paths:
        return
    buckets: dict[str, list[str]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            parent = pick(
                row,
                "note_id",
                "aweme_id",
                "content_id",
                "video_id",
                "parent_comment_id",
            )
            text = pick(row, "content", "text", "comment_text", "content_text")
            if not parent or not text:
                continue
            buckets.setdefault(parent, []).append(compact(text, 120))
    for item in items:
        raw_id = item["id"].split(":", 1)[-1]
        snippets = buckets.get(raw_id, [])[:limit]
        if snippets:
            item["comments"] = snippets


async def run_platform(
    platform: str,
    query: str,
    job_dir: Path,
    emit,
    options: SearchOptions,
    keep_all: bool = False,
) -> dict[str, Any]:
    label = PLATFORM_MAP[platform]
    keyword = options.query_for(platform, query)
    cmd = crawl_command(platform, keyword, job_dir, options)
    comment_note = (
        f"每帖最多 {options.max_comments} 条评论"
        + ("，含楼中楼" if options.sub_comments else "")
        if options.comments
        else "不拉评论"
    )
    await emit(
        "status",
        {
            "step": "start",
            "title": f"开始搜{label}",
            "detail": (
                f"最多 {options.notes_for(platform)} 条，多抓一些备用，过期和跑题会丢掉再补。{comment_note}。"
                + (
                    " 这一站先收正文和封面，热门视频会看一帧画面。"
                    if platform in {"dy", "ks"}
                    else " 这一站先收标题、文案和字幕，不下载整支片子。"
                    if platform == "bili"
                    else " 这一站收帖子正文和配图，不下载原图文件。"
                    if platform in {"xhs", "wb", "tieba"}
                    else " 这一站收回答和文章正文，视频只留标题和简介。"
                    if platform == "zhihu"
                    else " 浏览器画面会转到看板上，窗口不弹到前面。"
                )
            ),
        },
    )
    await emit(
        "log",
        {
            "platform": platform,
            "message": f"开始搜{label}，最多 {options.notes_for(platform)} 条，过期和跑题会丢掉再补。{comment_note}。画面在看板右侧转播。",
        },
    )
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    env["BOARD_PREVIEW_PATH"] = str(job_dir / "preview.jpg")
    env["BOARD_HIDE_BROWSER"] = "1"
    env["BOARD_KEEP_CREATORS"] = "1"
    env["BOARD_JOB_DIR"] = str(job_dir)
    env["SAVE_DATA_PATH"] = str(job_dir)
    if platform in {"dy", "ks", "bili"}:
        env["BOARD_VIDEO_READ"] = "1"
        env["BOARD_VIDEO_FRAMES_MAX"] = os.getenv("BOARD_VIDEO_FRAMES_MAX", "20")
        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if Path(ffmpeg).is_file():
            env["FFMPEG_PATH"] = ffmpeg
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "PLAYWRIGHT_BROWSERS_PATH",
    ):
        env.pop(key, None)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    tail: list[str] = []
    timed_out = False
    assert process.stdout is not None
    log_path = job_dir / f"{platform}.log"
    try:
        async def handle_line(line: str) -> None:
            try:
                with log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(line + "\n")
            except OSError:
                pass
            tail.append(line)
            if len(tail) > 12:
                del tail[:-12]
            if len(line) > 360:
                return
            status = human_status(platform, label, line)
            if status:
                await emit("status", {"platform": platform, "label": label, **status})
                await emit(
                    "log",
                    {
                        "platform": platform,
                        "message": status["title"]
                        + (f"：{status['detail']}" if status.get("detail") else ""),
                    },
                )
            fatal = any(
                token in line
                for token in (
                    "Executable doesn't exist",
                    "context management is not supported",
                    "Chrome for Testing",
                )
            )
            already_ok = any(
                token in line
                for token in ("已登录", "这次不用再登", "登录成功", "已用 cookie")
            )
            need_login = any(
                token in line
                for token in (
                    "请到弹出的 Chrome",
                    "登录抖音",
                    "登录快手",
                    "已打开快手登录页",
                    "请到新窗口",
                    "还没登录",
                    "waiting for scan",
                    "没有权限访问",
                    "请通过验证",
                    "滑动验证",
                    "被安全验证挡住了",
                    "verify_check",
                    "还在等你过抖音安全验证",
                    "还在等你过",
                    "登录已过期",
                    "接口仍被验证挡住",
                    "页面上没有验证弹窗",
                    "接口被风控",
                    "取详情仍被风控",
                )
            )
            if need_login and not fatal and not already_ok and "从页面收" not in line:
                risk = any(
                    token in line
                    for token in ("页面上没有验证弹窗", "接口被风控", "取详情仍被风控")
                )
                verify = any(
                    token in line
                    for token in (
                        "被安全验证挡住了",
                        "还在等你过",
                        "接口仍被验证挡住",
                        "verify_check",
                        "请通过验证",
                        "滑动验证",
                    )
                )
                await emit(
                    "need_user",
                    {
                        "platform": platform,
                        "label": label,
                        "kind": "risk" if risk else ("verify" if verify else "login"),
                        "message": line[-240:],
                    },
                )

        async def pump() -> None:
            buf = b""
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if len(raw) > 8000:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        await handle_line(line)
                if len(buf) > 8000:
                    buf = b""

        async def watch_items() -> None:
            seen: set[str] = set()
            while True:
                kept, _dropped = usable_items(
                    keyword, read_jsonl(job_dir, platform), options.notes_for(platform)
                )
                fresh = [item for item in kept if item["id"] not in seen]
                if fresh:
                    if platform in LOCAL_IMAGE_PLATFORMS:
                        await prefetch_item_images(job_dir, fresh)
                    for item in fresh:
                        seen.add(item["id"])
                    await emit(
                        "items",
                        {
                            "platform": platform,
                            "label": label,
                            "items": fresh,
                            "count": len(seen),
                        },
                    )
                    await emit(
                        "status",
                        {
                            "step": "items",
                            "title": f"{label}已收回 {len(seen)} 条",
                            "detail": fresh[-1]["title"],
                        },
                    )
                if process.returncode is not None:
                    return
                await asyncio.sleep(1.2)

        watcher = asyncio.create_task(watch_items())
        try:
            await asyncio.wait_for(pump(), timeout=options.timeout)
            try:
                await asyncio.wait_for(process.wait(), timeout=20)
            except asyncio.TimeoutError:
                timed_out = True
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            if not watcher.done():
                watcher.cancel()
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    raw_items = read_jsonl(job_dir, platform)
    if platform in LOCAL_IMAGE_PLATFORMS and raw_items:
        saved = await prefetch_item_images(job_dir, raw_items)
        if saved:
            await emit(
                "log",
                {"platform": platform, "message": f"{label}配图已下载 {saved} 张到本地。"},
            )
            raw_items = read_jsonl(job_dir, platform)
    items, dropped = usable_items(
        keyword,
        raw_items,
        None if keep_all else options.notes_for(platform),
    )
    cover = coverage_from_result(platform, len(raw_items), items, dropped)
    if dropped:
        await emit("log", {"platform": platform, "message": drop_note(dropped)})
    if options.comments:
        attach_comments(job_dir, platform, items, options.max_comments)
    if timed_out and items:
        await emit(
            "log",
            {
                "platform": platform,
                "message": f"{label}这一站超时，先用已经写下的 {len(items)} 条。",
            },
        )
    if items:
        return {
            "platform": platform,
            "label": label,
            "ok": True,
            "count": len(items),
            "error": None,
            "items": items,
            "coverage": cover,
        }
    hint = tail[-1] if tail else "没有写出 jsonl"
    return {
        "platform": platform,
        "label": label,
        "ok": False,
        "count": 0,
        "error": (f"超过 {options.timeout} 秒" if timed_out else hint[-180:]),
        "items": [],
        "coverage": cover,
    }


def safe_job_dir(job_id: str) -> Path | None:
    if not job_id or job_id in {".", ".."} or "/" in job_id or "\\" in job_id:
        return None
    path = (RUNS_DIR / job_id).resolve()
    try:
        path.relative_to(RUNS_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_dir() else None


def latest_job_for_query(query: str) -> Path | None:
    if not RUNS_DIR.is_dir() or not query:
        return None
    found: list[Path] = []
    for path in RUNS_DIR.iterdir():
        if not path.is_dir():
            continue
        meta_path = path / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str((meta or {}).get("query") or "").strip() != query:
            continue
        found.append(path)
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def resume_job_dir(resume_job: str, query: str, allow_latest: bool = False) -> Path | None:
    path = safe_job_dir((resume_job or "").strip())
    if path is None and allow_latest:
        path = latest_job_for_query(query)
    if path is None:
        return None
    meta_path = path / "meta.json"
    if not meta_path.is_file():
        return path
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return path
    saved = str((meta or {}).get("query") or "").strip()
    if saved and saved != query:
        return None
    return path


def split_resume_platforms(
    job_dir: Path,
    platforms: list[str],
    options: SearchOptions,
    query: str = "",
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[str]]:
    kept: list[tuple[str, list[dict[str, Any]]]] = []
    todo: list[str] = []
    for code in platforms:
        existing, _dropped = usable_items(
            query, read_jsonl(job_dir, code), options.notes_for(code)
        )
        if existing:
            if options.comments:
                attach_comments(job_dir, code, existing, options.max_comments)
            kept.append((code, existing))
        else:
            todo.append(code)
    return kept, todo


def platform_needs_relogin(result: dict[str, Any]) -> bool:
    text = str(result.get("error") or "")
    return any(
        token in text
        for token in ("登录", "验证", "过期", "还没登录", "Verify", "verify")
    )


def split_supplement_platforms(
    job_dir: Path, rerun: list[str], query: str = ""
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[str]]:
    kept: list[tuple[str, list[dict[str, Any]]]] = []
    todo = [code for code in rerun if code in PLATFORM_MAP]
    rerun_set = set(todo)
    for code, _label in PLATFORMS:
        if code in rerun_set:
            continue
        existing, _dropped = usable_items(query, read_jsonl(job_dir, code))
        if existing:
            kept.append((code, existing))
    return kept, todo


async def plan_material_gaps(
    query: str,
    coverage: list[dict[str, Any]],
    options: SearchOptions,
    allowed: set[str],
) -> dict[str, Any]:
    empty = {"need_refill": False, "reason": "", "tasks": []}
    if not DEEPSEEK_KEY or not coverage:
        return empty
    weak = []
    for row in coverage:
        wanted = options.notes_for(row["platform"])
        raw_n = int(row.get("raw") or 0)
        kept = int(row.get("kept") or 0)
        dropped = int(row.get("dropped") or 0)
        if kept < max(3, wanted // 3) or (raw_n and dropped / raw_n >= 0.4):
            weak.append(row["platform"])
    if not weak:
        return {"need_refill": False, "reason": "核对后各站留下的材料够用，不再加一轮。", "tasks": []}
    payload = {
        "query": query,
        "weak_platforms": weak,
        "coverage": [
            {
                "platform": row["platform"],
                "label": row["label"],
                "wanted": options.notes_for(row["platform"]),
                "raw": row.get("raw") or 0,
                "kept": row.get("kept") or 0,
                "dropped": row.get("dropped") or 0,
                "reasons": row.get("reasons") or {},
                "dropped_examples": row.get("samples") or [],
            }
            for row in coverage
        ],
    }
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": "\n".join(
                    [
                        "你是十一。各站已搜过一轮，并对过期、跑题、地点不对的材料做过核对。",
                        "判断要不要用更精确的词再补一轮。只补真正缺的站，不要重搜已经够用的站。",
                        "补搜词要短，带上地点和今年/当下节庆，避开已经丢掉的旧活动或错节假日。",
                        "need_refill=false 若留下的材料已能回答这次搜索。",
                        "tasks 最多 3 条。platform 用平台 id。count 取 6 到 12。",
                        "返回 JSON：need_refill, reason, tasks[{platform,query,count,why}]。",
                    ]
                ),
                "input": json.dumps(payload, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 700,
            }
        )
        parsed = parse_model_json(text)
    except Exception:
        return empty
    tasks: list[dict[str, Any]] = []
    for row in parsed.get("tasks") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("platform") or "")
        term = " ".join(str(row.get("query") or "").split())
        if code not in allowed or code not in PLATFORM_MAP or not term:
            continue
        try:
            count = int(row.get("count") or 8)
        except (TypeError, ValueError):
            count = 8
        tasks.append(
            {
                "platform": code,
                "query": term[:80],
                "count": max(6, min(12, count)),
                "why": str(row.get("why") or "")[:120],
            }
        )
        if len(tasks) >= 3:
            break
    return {
        "need_refill": bool(parsed.get("need_refill")) and bool(tasks),
        "reason": str(parsed.get("reason") or ""),
        "tasks": tasks,
    }


async def refill_from_plan(
    query: str,
    job_dir: Path,
    emit,
    options: SearchOptions,
    started: float,
    coverage: list[dict[str, Any]],
    allowed: list[str],
) -> bool:
    if time.time() - started > 18 * 60:
        await emit(
            "log",
            {"message": "已经搜很久了，不再加一轮补搜，先用核对留下的写报告。"},
        )
        return False
    plan = await plan_material_gaps(query, coverage, options, set(allowed))
    if not plan.get("need_refill"):
        if plan.get("reason"):
            await emit("log", {"message": plan["reason"]})
        return False
    await emit(
        "refill_start",
        {"reason": plan.get("reason") or "核对后有缺口，按更精确的词补一轮。", "tasks": plan["tasks"]},
    )
    await emit(
        "status",
        {
            "step": "refill",
            "title": "十一要定向补一点材料",
            "detail": plan.get("reason") or "只补缺的站，不再全量重搜。",
        },
    )
    for task in plan["tasks"]:
        code = task["platform"]
        extra = replace(
            options,
            notes={code: task["count"]},
            platform_queries={code: task["query"]},
        )
        await emit(
            "log",
            {
                "platform": code,
                "message": f"补搜{PLATFORM_MAP[code]}：{task['query']}。{task.get('why') or ''}".strip(),
            },
        )
        runner = run_foreign_platform if code in CONNECTOR_CODES else run_platform
        result = await runner(code, query, job_dir, emit, extra, keep_all=True)
        await emit("platform_done", result)
    return True


async def search_stream(
    query: str,
    platforms: list[str],
    options: SearchOptions,
    resume_job: str = "",
    allow_latest: bool = False,
    supplement: bool = False,
) -> AsyncIterator[str]:
    started = time.time()
    job_dir = resume_job_dir(resume_job, query, allow_latest=allow_latest)
    resumed = job_dir is not None
    supplementing = bool(supplement and resumed)
    if job_dir is None:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job_dir = RUNS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
    else:
        job_id = job_dir.name
    previous_platforms: list[str] = []
    meta_path = job_dir / "meta.json"
    if meta_path.is_file():
        try:
            old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(old_meta, dict) and isinstance(old_meta.get("platforms"), list):
                previous_platforms = [str(code) for code in old_meta["platforms"] if code in PLATFORM_MAP]
        except (OSError, json.JSONDecodeError):
            previous_platforms = []
    tracked = list(dict.fromkeys([*previous_platforms, *platforms]))
    (job_dir / "meta.json").write_text(
        json.dumps(
            {
                "query": query,
                "job_id": job_id,
                "platforms": tracked,
                "resumed": resumed,
                "supplemented": supplementing,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    async def emit(event: str, data: Any) -> None:
        if event in _PROCESS_EVENTS and isinstance(data, dict):
            try:
                persist_process_event(job_dir, event, data, started)
            except OSError:
                pass
        await queue.put((event, data))

    async def work() -> None:
        global _live_job_id
        _live_job_id = job_id
        beat: asyncio.Task | None = None
        preview_task: asyncio.Task | None = None

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(2)
                await emit(
                    "tick",
                    {"elapsed_s": int(time.time() - started)},
                )

        async def preview_watch() -> None:
            last = 0.0
            path = job_dir / "preview.jpg"
            while True:
                await asyncio.sleep(0.8)
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
                if mtime == last:
                    continue
                last = mtime
                payload = {
                    "url": f"/api/job/{job_id}/preview?t={int(mtime * 1000)}",
                }
                alert = job_dir / "preview_alert.txt"
                if alert.is_file():
                    payload["alert"] = alert.read_text(encoding="utf-8").strip()
                await emit("preview", payload)

        try:
            beat = asyncio.create_task(heartbeat())
            preview_task = asyncio.create_task(preview_watch())
            chrome = chrome_status()
            await emit(
                "meta",
                {
                    "job_id": job_id,
                    "query": query,
                    "platforms": [
                        {"id": code, "label": PLATFORM_MAP[code]} for code in platforms
                    ],
                    "chrome": chrome,
                    "max_notes": options.max_notes,
                    "notes": {
                        code: options.notes_for(code) for code in platforms
                    },
                    "comments": options.comments,
                    "eleven": options.eleven,
                    "agent_search": options.agent_search,
                    "no_ai": options.no_ai,
                    "resumed": resumed,
                    "supplemented": supplementing,
                },
            )
            if not chrome["ok"]:
                await emit(
                    "log",
                    {
                        "message": chrome["detail"],
                    },
                )
            results: list[dict[str, Any]] = []
            items: list[dict[str, Any]] = []
            if supplementing:
                kept, todo = split_supplement_platforms(job_dir, platforms, query)
            elif resumed:
                kept, todo = split_resume_platforms(job_dir, platforms, options, query)
            else:
                kept, todo = [], list(platforms)
            if supplementing:
                await emit(
                    "status",
                    {
                        "step": "supplement",
                        "title": (
                            f"补充搜索，已有 {sum(len(rows) for _code, rows in kept)} 条留下"
                            if kept
                            else "补充搜索，开始追加材料"
                        ),
                        "detail": (
                            "只跑你勾的平台，新旧材料合并后再重写报告。"
                            if todo
                            else "没有要补的平台，直接按现有材料重写报告。"
                        ),
                    },
                )
            elif resumed:
                await emit(
                    "status",
                    {
                        "step": "resume",
                        "title": (
                            f"接着搜，已有 {sum(len(rows) for _code, rows in kept)} 条留下"
                            if kept
                            else "接着搜还没写下的平台"
                        ),
                        "detail": (
                            "只补还没登录或没结果的平台，前面的不重跑。"
                            if todo
                            else "各站都有结果，直接进十一。"
                        ),
                    },
                )
            for code, existing in kept:
                result = {
                    "platform": code,
                    "label": PLATFORM_MAP[code],
                    "ok": True,
                    "count": len(existing),
                    "error": None,
                    "items": existing,
                    "kept": True,
                }
                results.append(result)
                items.extend(existing)
                await emit(
                    "items",
                    {
                        "platform": code,
                        "label": PLATFORM_MAP[code],
                        "items": existing,
                        "count": len(existing),
                    },
                )
                await emit("platform_done", result)
                await emit(
                    "log",
                    {
                        "platform": code,
                        "message": (
                            f"{PLATFORM_MAP[code]}已有 {len(existing)} 条，留下。"
                            if supplementing
                            else f"{PLATFORM_MAP[code]}已有 {len(existing)} 条，这次跳过。"
                        ),
                    },
                )
            if supplementing:
                for code in todo:
                    existing = read_jsonl(job_dir, code)
                    if not existing:
                        continue
                    items.extend(existing)
                    await emit(
                        "items",
                        {
                            "platform": code,
                            "label": PLATFORM_MAP[code],
                            "items": existing,
                            "count": len(existing),
                        },
                    )
                    await emit(
                        "log",
                        {
                            "platform": code,
                            "message": f"{PLATFORM_MAP[code]}已有 {len(existing)} 条，这一轮再追加。",
                        },
                    )
            for code in todo:
                await emit(
                    "platform_start",
                    {"platform": code, "label": PLATFORM_MAP[code]},
                )
                runner = run_foreign_platform if code in CONNECTOR_CODES else run_platform
                result = await runner(
                    code, query, job_dir, emit, options, keep_all=supplementing
                )
                if not result.get("items"):
                    recovered, _dropped = usable_items(
                        query, read_jsonl(job_dir, code), options.notes_for(code)
                    )
                    if recovered:
                        result = {
                            **result,
                            "ok": True,
                            "count": len(recovered),
                            "error": None,
                            "items": recovered,
                        }
                if not result.get("items") and platform_needs_relogin(result):
                    await emit(
                        "status",
                        {
                            "step": "login",
                            "platform": code,
                            "label": PLATFORM_MAP[code],
                            "title": f"{PLATFORM_MAP[code]}还没取到，登完后重新取",
                            "detail": "窗口先留着。过完会再跑这一站；也可以点「我登录好了，接着搜」。",
                        },
                    )
                    result = await runner(
                        code, query, job_dir, emit, options, keep_all=supplementing
                    )
                    if not result.get("items"):
                        recovered, _dropped = usable_items(
                            query, read_jsonl(job_dir, code), options.notes_for(code)
                        )
                        if recovered:
                            result = {
                                **result,
                                "ok": True,
                                "count": len(recovered),
                                "error": None,
                                "items": recovered,
                            }
                results.append(result)
                items.extend(result["items"])
                await emit("platform_done", result)
            coverage_rows = build_coverage(job_dir, query, tracked)
            write_coverage(job_dir, coverage_rows)
            await emit("coverage", {"rows": coverage_rows})
            topped = False
            refilled = False
            if not supplementing:
                items, topped = await compensate_cross_platform(
                    query, job_dir, emit, options, started, tracked
                )
                refilled = await refill_from_plan(
                    query,
                    job_dir,
                    emit,
                    options,
                    started,
                    coverage_rows,
                    list(platforms),
                )
            if supplementing:
                _query, items = load_job_items(job_id)
            items, _tasks = rebuild_unique_items(job_dir, query, tracked, options)
            if topped or refilled:
                coverage_rows = build_coverage(job_dir, query, tracked)
                write_coverage(job_dir, coverage_rows)
                await emit("coverage", {"rows": coverage_rows})
            use_ai = (not options.no_ai) and bool(DEEPSEEK_KEY)
            want_report = use_ai and options.eleven
            want_search = use_ai and options.agent_search
            if options.no_ai:
                await emit("log", {"message": "无 AI 模式，只摊开本机材料，不调用模型。"})
            elif not DEEPSEEK_KEY and (options.eleven or options.agent_search):
                await emit(
                    "log",
                    {"message": "还没有 API Key，这次只摊开本机材料，不调用模型。"},
                )
            agent_brief = None
            if want_search:
                writer = writer_public()
                await emit(
                    "status",
                    {
                        "step": "agent_search",
                        "title": f"{writer['name']}正在补一层参考",
                        "detail": f"{DEEPSEEK_MODEL}。这份不进时间线，只当背景和细节。",
                    },
                )
                try:
                    agent_brief = await ask_agent_search(query)
                    (job_dir / "agent_search.json").write_text(
                        json.dumps(agent_brief, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    await emit("agent_search", agent_brief)
                except Exception as error:
                    await emit("log", {"message": f"Agent 搜索没做成：{error}"})
            if want_report and items:
                try:
                    need_frames = any(
                        item.get("kind") == "video" and not item.get("scene")
                        for item in items
                    )
                    if need_frames:
                        await emit(
                            "status",
                            {
                                "step": "frames",
                                "title": "正在读热门视频的画面",
                                "detail": "点赞靠前的几条会看一帧，写出画面在干什么。看不了就只用标题和时长。",
                            },
                        )
                        await describe_top_videos(job_dir, items)
                    await emit(
                        "eleven_start",
                        {
                            "item_count": len(items),
                            "intent": detect_query_intent(query),
                        },
                    )
                    board = await ask_eleven(
                        query,
                        items,
                        extra_note=options.refine_note,
                        agent_search=agent_brief,
                    )
                    save_board(job_dir, board)
                    await emit("board", board)
                    if board.get("fallback"):
                        await emit(
                            "board_error",
                            {
                                "message": (board.get("judgment") or {}).get(
                                    "plain_language"
                                )
                                or "Agent 没能写成报告，先看来源。",
                                "item_count": len(items),
                                "can_retry_eleven": True,
                            },
                        )
                except Exception as error:
                    await emit(
                        "board_error",
                        {
                            "message": str(error),
                            "item_count": len(items),
                            "can_retry_eleven": True,
                        },
                    )
            elif want_report:
                await emit(
                    "board_error",
                    {
                        "message": "还没有搜到内容。如果刚在 Chrome 里登录，点「我登录好了，重新搜」。",
                        "item_count": 0,
                        "need_retry": True,
                    },
                )
            else:
                board = harvest_board(query, items, agent_search=agent_brief)
                save_board(job_dir, board)
                await emit("board", board)
                await emit(
                    "log",
                    {
                        "message": "按你的选择，只摊开本机材料。"
                        + (" Agent 搜索已作为补充参考。" if agent_brief else "没有调用模型。")
                    },
                )
            remember_job(job_id)
            await emit(
                "done",
                {
                    "ok": True,
                    "job_id": job_id,
                    "item_count": len(items),
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "failed": [
                        row["label"] for row in results if not row["ok"]
                    ],
                },
            )
        except Exception as error:
            await emit("error", {"message": str(error)})
        finally:
            if _live_job_id == job_id:
                _live_job_id = ""
            if beat is not None:
                beat.cancel()
            if preview_task is not None:
                preview_task.cancel()
            await queue.put(None)

    worker = asyncio.create_task(work())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            event, data = item
            yield sse(event, data)
    finally:
        if not worker.done():
            worker.cancel()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/chrome/launch")
async def chrome_launch() -> JSONResponse:
    result = await launch_debug_browser()
    return JSONResponse(result, status_code=200 if result["ok"] else 500)


HISTORY_LIMIT = 10


def history_file() -> Path:
    return RUNS_DIR / "history.json"


def _is_job_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith(("_", ".")):
        return False
    return (path / "board.json").is_file() or (path / "meta.json").is_file()


def load_chat_rows(job_dir: Path) -> list[dict[str, Any]]:
    path = job_dir / "chat.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("q") and row.get("a"):
            rows.append(row)
    return rows


def history_entry(job_id: str) -> dict[str, Any] | None:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return None
    try:
        query, items = load_job_items(job_id)
    except FileNotFoundError:
        return None
    board = load_saved_board(job_dir) or {}
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    return {
        "job_id": job_id,
        "query": query,
        "title": report.get("title") or query,
        "deck": report.get("deck") or "",
        "item_count": len(items),
        "ask_count": len(load_chat_rows(job_dir)),
        "has_board": bool(board),
        "updated_at": datetime.fromtimestamp(
            job_dir.stat().st_mtime
        ).astimezone().strftime("%-m月%-d日 %H:%M"),
    }


def _read_history_file() -> list[dict[str, Any]]:
    path = history_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and row.get("job_id")]


def _write_history(rows: list[dict[str, Any]]) -> None:
    history_file().write_text(
        json.dumps(rows[:HISTORY_LIMIT], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remember_job(job_id: str) -> None:
    entry = history_entry(job_id)
    if not entry:
        return
    entry["updated_at"] = datetime.now().astimezone().strftime("%-m月%-d日 %H:%M")
    rows = [row for row in _read_history_file() if row.get("job_id") != job_id]
    rows.insert(0, entry)
    _write_history(rows)


def seed_history() -> None:
    if _read_history_file():
        return
    jobs = [
        path
        for path in RUNS_DIR.iterdir()
        if _is_job_dir(path)
    ]
    jobs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    entries = []
    for path in jobs[:HISTORY_LIMIT]:
        entry = history_entry(path.name)
        if entry:
            entries.append(entry)
    if entries:
        _write_history(entries)


def read_history() -> list[dict[str, Any]]:
    seed_history()
    listed = {str(row.get("job_id") or "") for row in _read_history_file()}
    rows = [row for row in _read_history_file() if (RUNS_DIR / str(row.get("job_id") or "")).is_dir()]
    extras: list[Path] = []
    if RUNS_DIR.is_dir():
        for path in RUNS_DIR.iterdir():
            if path.name in listed or not _is_job_dir(path):
                continue
            extras.append(path)
        extras.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in extras:
            entry = history_entry(path.name)
            if entry:
                rows.append(entry)
            if len(rows) >= HISTORY_LIMIT:
                break
    return rows[:HISTORY_LIMIT]


def forget_job(job_id: str) -> tuple[bool, str]:
    if not job_id or job_id in {".", ".."} or "/" in job_id or "\\" in job_id:
        return False, "记录不对"
    if _live_job_id == job_id and _search_lock.locked():
        return False, "这次还在搜，先等它结束。"
    path = (RUNS_DIR / job_id).resolve()
    try:
        path.relative_to(RUNS_DIR.resolve())
    except ValueError:
        return False, "记录不对"
    if path.is_dir():
        shutil.rmtree(path)
    _write_history([row for row in _read_history_file() if row.get("job_id") != job_id])
    return True, ""


def format_bytes(n: int) -> str:
    size = max(0, int(n or 0))
    if size <= 0:
        return "空的"
    if size < 1024:
        return "不到 1 KB"
    kb = size / 1024
    if kb < 1024:
        return f"{kb:.0f} KB" if kb >= 10 else f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"
    return f"{mb / 1024:.1f} GB"


def folder_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def kept_run_ids() -> set[str]:
    ids = {str(row.get("job_id") or "") for row in read_history()}
    ids.discard("")
    if _live_job_id:
        ids.add(_live_job_id)
    return ids


def leftover_run_dirs() -> list[Path]:
    if not RUNS_DIR.is_dir():
        return []
    keep = kept_run_ids()
    leftover: list[Path] = []
    for path in RUNS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if path.name == "_illus" or path.name in keep:
            continue
        leftover.append(path)
    leftover.sort(key=lambda path: path.name)
    return leftover


def _safe_runs_child(path: Path) -> bool:
    if not path.name or path.name in {".", "..", "_illus"} or "/" in path.name or "\\" in path.name:
        return False
    try:
        path.resolve().relative_to(RUNS_DIR.resolve())
    except ValueError:
        return False
    return True


def storage_report() -> dict[str, Any]:
    keep = kept_run_ids()
    jobs_bytes = 0
    leftover_bytes = 0
    leftover_count = 0
    if RUNS_DIR.is_dir():
        for path in RUNS_DIR.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name == "_illus":
                continue
            size = folder_bytes(path)
            if path.name in keep:
                jobs_bytes += size
            else:
                leftover_bytes += size
                leftover_count += 1
    cache_bytes = folder_bytes(RUNS_DIR / "_illus")
    total_bytes = folder_bytes(RUNS_DIR)
    reclaimable = leftover_bytes + cache_bytes
    jobs_count = sum(1 for job_id in keep if (RUNS_DIR / job_id).is_dir())
    return {
        "jobs_bytes": jobs_bytes,
        "jobs_label": format_bytes(jobs_bytes),
        "jobs_count": jobs_count,
        "leftover_bytes": leftover_bytes,
        "leftover_label": format_bytes(leftover_bytes),
        "leftover_count": leftover_count,
        "cache_bytes": cache_bytes,
        "cache_label": format_bytes(cache_bytes),
        "total_bytes": total_bytes,
        "total_label": format_bytes(total_bytes),
        "reclaimable_bytes": reclaimable,
        "reclaimable_label": format_bytes(reclaimable),
        "can_clear": reclaimable > 0,
    }


def clear_run_cache() -> dict[str, Any]:
    freed = 0
    illus = RUNS_DIR / "_illus"
    if illus.exists():
        freed += folder_bytes(illus)
        if illus.is_dir():
            shutil.rmtree(illus)
        else:
            illus.unlink()
    for path in leftover_run_dirs():
        if path.name == _live_job_id and _search_lock.locked():
            continue
        if not _safe_runs_child(path):
            continue
        if not path.is_dir():
            continue
        freed += folder_bytes(path)
        shutil.rmtree(path)
    report = storage_report()
    report["freed_bytes"] = freed
    report["freed_label"] = format_bytes(freed)
    return report


def save_board(job_dir: Path, board: dict[str, Any]) -> None:
    (job_dir / "board.json").write_text(
        json.dumps(board, ensure_ascii=False), encoding="utf-8"
    )
    remember_job(job_dir.name)


def load_saved_board(job_dir: Path) -> dict[str, Any] | None:
    path = job_dir / "board.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_chat(job_dir: Path) -> list[dict[str, str]]:
    return [
        {"q": str(row["q"]), "a": str(row["a"])} for row in load_chat_rows(job_dir)[-6:]
    ]


def append_chat(
    job_dir: Path,
    question: str,
    answer: str,
    extra: dict[str, Any] | None = None,
) -> None:
    row = {"q": question, "a": answer}
    if extra:
        row.update(extra)
    with (job_dir / "chat.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    remember_job(job_dir.name)


def board_cited_ids(board: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    if not isinstance(board, dict):
        return ids
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for row in section.get("evidence") or []:
            if isinstance(row, dict) and row.get("item_id"):
                ids.add(str(row["item_id"]))
            elif isinstance(row, str) and row:
                ids.add(row)
    for row in report.get("picks") or []:
        if isinstance(row, dict) and row.get("item_id"):
            ids.add(str(row["item_id"]))
    for row in report.get("facts") or []:
        if isinstance(row, dict) and row.get("item_id"):
            ids.add(str(row["item_id"]))
    return ids


def _report_guard_sig(board: dict[str, Any]) -> tuple[Any, ...]:
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    sources = ((report or {}).get("sources") or {}).get("platforms") or []
    briefs = (report or {}).get("platform_briefs") or []
    sections = (report or {}).get("sections") or []
    return (
        tuple((row.get("name"), row.get("count")) for row in sources if isinstance(row, dict)),
        tuple((row.get("name"), (row.get("answer") or "")[:48]) for row in briefs if isinstance(row, dict)),
        tuple(
            (
                row.get("heading"),
                len(row.get("evidence") or []),
                len(((row.get("table") or {}) if isinstance(row.get("table"), dict) else {}).get("rows") or []),
            )
            for row in sections
            if isinstance(row, dict)
        ),
    )


def load_job_items(
    job_id: str, extra_ids: set[str] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    query = job_id
    meta_path = job_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("query"):
                query = str(meta["query"])
        except json.JSONDecodeError:
            pass
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, _label in PLATFORMS:
        kept, _dropped = usable_items(query, read_jsonl(job_dir, code))
        for item in kept:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
    need = {item_id for item_id in (extra_ids or set()) if item_id and item_id not in seen}
    if need:
        for code, _label in PLATFORMS:
            for item in read_jsonl(job_dir, code):
                if item["id"] in need and item["id"] not in seen:
                    seen.add(item["id"])
                    items.append(item)
    for item in items:
        stamp_freshness(item, query)
    return query, items


@app.post("/api/eleven")
async def eleven_retry(
    job_id: str = Query(...),
    note: str = Query(""),
) -> JSONResponse:
    try:
        query, items = load_job_items(job_id)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    if not items:
        return JSONResponse({"ok": False, "error": "这次搜索没有原始结果"}, status_code=400)
    if not DEEPSEEK_KEY:
        return JSONResponse({"ok": False, "error": "还没有 API Key，先在本机配置里加上。"}, status_code=400)
    try:
        await describe_top_videos(RUNS_DIR / job_id, items)
        cached = RUNS_DIR / job_id / "agent_search.json"
        agent_brief = None
        if cached.is_file():
            try:
                loaded = json.loads(cached.read_text(encoding="utf-8"))
                agent_brief = normalize_agent_search(loaded)
            except (OSError, json.JSONDecodeError):
                agent_brief = None
        note = note.strip()
        prior_notes = [
            str(row.get("note") or "").strip()
            for row in load_refine_rows(RUNS_DIR / job_id)
            if str(row.get("note") or "").strip() and str(row.get("note") or "").strip() != note
        ]
        if note:
            append_refine(
                RUNS_DIR / job_id,
                note,
                {
                    "need_search": False,
                    "rewrite_only": True,
                    "reason": "按补充重写完整报告",
                    "platforms": [],
                    "queries": {},
                },
            )
        board = await ask_eleven(
            query,
            items,
            extra_note=note,
            agent_search=agent_brief,
            prior_notes=prior_notes,
        )
        save_board(RUNS_DIR / job_id, board)
    except Exception as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
    return JSONResponse({"ok": True, "board": board, "item_count": len(items)})


@app.post("/api/refine")
async def refine_search(
    job_id: str = Query(...),
    note: str = Query(..., min_length=1, max_length=800),
    platforms: str = Query(""),
) -> JSONResponse:
    try:
        query, items = load_job_items(job_id)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    note = " ".join(note.split())
    checked = [code.strip() for code in platforms.split(",") if code.strip() in PLATFORM_MAP]
    counts = {}
    for item in items:
        label = item.get("platform_label") or item.get("platform")
        counts[label] = counts.get(label, 0) + 1
    if not DEEPSEEK_KEY:
        return JSONResponse({"ok": False, "error": "还没有配置 DEEPSEEK_API_KEY"}, status_code=400)
    payload = {
        "query": query,
        "note": note,
        "checked_platforms": checked,
        "counts": counts,
        "sample": [
            {
                "id": item["id"],
                "platform": item.get("platform_label"),
                "title": item.get("title"),
                "text": compact(item.get("text_zh") or item.get("text") or "", 180),
            }
            for item in items[:24]
        ],
        "platforms": [{"id": code, "label": label} for code, label in PLATFORMS],
    }
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": "\n".join(
                    [
                        "你是十一。使用者在已有智搜结果上补充真实需求。",
                        "判断要不要重新去平台搜，以及每站该用什么更精确的词。",
                        "need_search=true 时，platforms 只填有必要的站，queries 是 {平台id:检索词}。",
                        "材料已经够回答补充、只是方向不对时，need_search=false，用已有全部材料按补充重写完整报告，不要只改两句。",
                        "不要为了凑平台而搜。国内站用中文词，国外站用对应外文词。",
                        "返回 JSON：need_search, reason, platforms, queries, rewrite_only。",
                    ]
                ),
                "input": json.dumps(payload, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 800,
            }
        )
        parsed = parse_model_json(text)
    except Exception as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
    need_search = bool(parsed.get("need_search"))
    selected = [
        code
        for code in (parsed.get("platforms") or checked or [])
        if str(code) in PLATFORM_MAP
    ]
    raw_queries = parsed.get("queries") if isinstance(parsed.get("queries"), dict) else {}
    queries = {
        str(code): str(keyword).strip()[:80]
        for code, keyword in raw_queries.items()
        if str(code) in PLATFORM_MAP and str(keyword).strip()
    }
    if need_search and not selected:
        selected = checked or [code for code, _label in DOMESTIC_PLATFORMS]
    plan = {
        "need_search": need_search,
        "reason": _as_text(parsed.get("reason")) or "按你的补充收一下。",
        "platforms": selected,
        "queries": queries,
        "rewrite_only": (not need_search) or bool(parsed.get("rewrite_only")),
        "note": note,
    }
    append_refine(RUNS_DIR / job_id, note, plan)
    return JSONResponse({"ok": True, **plan})


@app.post("/api/job/{job_id}/digest")
async def item_digest(
    job_id: str,
    item_id: str = Query(..., min_length=1, max_length=180),
) -> JSONResponse:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    cache = load_json_file(job_dir / "digests.json", {})
    if not isinstance(cache, dict):
        cache = {}
    cached = cache.get(item_id)
    if isinstance(cached, dict) and cached.get("summary"):
        foreign = item_id.split(":", 1)[0] in FOREIGN_CODES
        if not (foreign and looks_english(str(cached.get("summary") or ""))):
            return JSONResponse({"ok": True, "cached": True, **cached})
    try:
        _query, items = load_job_items(job_id, extra_ids={item_id})
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    item = next((row for row in items if row.get("id") == item_id), None)
    if not item:
        return JSONResponse({"ok": False, "error": "没有这条材料"}, status_code=404)
    if (
        item.get("platform") in FOREIGN_CODES
        or item.get("lang") == "en"
        or looks_english(str(item.get("title") or ""))
    ) and not _as_text(item.get("text_zh")):
        packed = [
            {
                "id": item.get("id"),
                "title": item.get("title") or "",
                "desc": item.get("text") or "",
                "content": item.get("text") or "",
            }
        ]
        await translate_foreign_rows(packed)
        if packed[0].get("text_zh"):
            item["text_zh"] = packed[0]["text_zh"]
        if packed[0].get("title_zh") or packed[0].get("title"):
            item["title"] = packed[0].get("title_zh") or packed[0].get("title")
    fallback = {
        "summary": compact(
            item.get("text_zh") or item.get("title_zh") or "", 360
        )
        or "这条只有外文索引标题，十一还没写成中文总结。",
        "points": [],
        "judgment": False,
    }
    if not DEEPSEEK_KEY:
        cache[item_id] = fallback
        save_json_file(job_dir / "digests.json", cache)
        return JSONResponse({"ok": True, "cached": False, **fallback})
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": "\n".join(
                    [
                        "你是十一。只总结这一条原帖在说什么：点了哪些对象、列了什么、图或视频里写了什么。",
                        "不要评判值不值得、该不该选、可不可信。不要升格成结论清单。",
                        "一律用中文写 summary 和 points。不要把英文标题原样当总结。",
                        "有 text_zh 用它。text_source=index 只有公开索引，不是全文，写清这一点。",
                        "未见口播就写未见口播。",
                        "返回 JSON：summary, points。points 最多 4 条短句。",
                    ]
                ),
                "input": json.dumps(
                    {
                        "id": item.get("id"),
                        "platform": item.get("platform_label"),
                        "title": item.get("title_zh") or item.get("title"),
                        "title_orig": item.get("title_orig") or "",
                        "text": item.get("text_zh") or item.get("text"),
                        "text_orig": item.get("text") if item.get("text_zh") else "",
                        "video_text": compact(item.get("video_text") or "", 800),
                        "text_source": item.get("text_source") or "",
                        "author": item.get("author"),
                        "kind": item.get("kind"),
                    },
                    ensure_ascii=False,
                ),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 700,
            }
        )
        parsed = parse_model_json(text)
        digest = {
            "summary": _as_text(parsed.get("summary")) or fallback["summary"],
            "points": [
                _as_text(row)
                for row in (parsed.get("points") or [])
                if _as_text(row)
            ][:4],
            "judgment": False,
        }
    except Exception:
        digest = fallback
    cache[item_id] = digest
    save_json_file(job_dir / "digests.json", cache)
    return JSONResponse({"ok": True, "cached": False, **digest})


@app.get("/api/job/{job_id}/refine")
async def job_refine(job_id: str) -> JSONResponse:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    return JSONResponse({"ok": True, "items": load_refine_rows(job_dir)})


@app.get("/api/public-image")
async def public_image(u: str = Query(..., min_length=12, max_length=2000)):
    if not usable_image_url(u):
        return JSONResponse({"ok": False, "error": "不是可用的配图地址"}, status_code=400)
    host = urlparse(u).netloc.lower()
    allowed = (
        "baidu.com",
        "bdimg.com",
        "bcebos.com",
        "sogoucdn.com",
        "sogou.com",
        "qhimg.com",
        "so.com",
        "upload.wikimedia.org",
        "thumb.wikimedia.org",
        "commons.wikimedia.org",
        "live.staticflickr.com",
        "images.openverse.org",
        "api.openverse.org",
    )
    if host not in allowed and not any(host.endswith(f".{name}") for name in allowed):
        return JSONResponse({"ok": False, "error": "配图来源不在公域白名单"}, status_code=400)
    cache_dir = RUNS_DIR / "_illus"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(u.encode("utf-8")).hexdigest()[:20]
    suffix = ".jpg"
    lower = urlsplit(u).path.lower()
    for ext in (".png", ".gif", ".webp", ".jpeg", ".jpg"):
        if lower.endswith(ext):
            suffix = ".jpg" if ext == ".jpeg" else ext
            break
    cache = cache_dir / f"{digest}{suffix}"
    if not cache.is_file():
        if "baidu" in host or "bcebos" in host or "bdimg" in host:
            referer = "https://image.baidu.com/"
        elif "qhimg" in host or host.endswith("so.com"):
            referer = "https://image.so.com/"
        elif "sogou" in host:
            referer = "https://pic.sogou.com/"
        elif "wikimedia" in host or "wikipedia" in host:
            referer = "https://commons.wikimedia.org/"
        else:
            referer = f"https://{host}/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, trust_env=False) as client:
                response = await client.get(u, headers=headers)
            if response.status_code >= 400 or not response.content:
                return JSONResponse({"ok": False, "error": "配图暂时拉不下来"}, status_code=502)
            cache.write_bytes(response.content)
        except Exception:
            return JSONResponse({"ok": False, "error": "配图暂时拉不下来"}, status_code=502)
    media = {
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    return FileResponse(
        cache,
        media_type=media,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/job/{job_id}/preview")
async def job_preview(job_id: str):
    path = RUNS_DIR / job_id / "preview.jpg"
    if not path.is_file():
        return JSONResponse({"ok": False, "error": "还没有画面"}, status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/search/continue")
async def search_continue(
    job_id: str = Query(""),
    platform: str = Query(""),
) -> JSONResponse:
    from tools.login_ready import request_search_continue

    path = safe_job_dir((job_id or "").strip())
    if path is None and _live_job_id:
        path = safe_job_dir(_live_job_id)
    if path is None:
        return JSONResponse({"ok": False, "error": "没有这次搜索。", "live": False}, status_code=404)
    written = request_search_continue(path, platform)
    live = bool(_live_job_id and path.name == _live_job_id and _search_lock.locked())
    return JSONResponse(
        {"ok": True, "job_id": path.name, "live": live, "platforms": written}
    )


@app.post("/api/chrome/show")
async def chrome_show() -> JSONResponse:
    from tools.login_ready import request_show_crawler

    path = safe_job_dir(_live_job_id) if _live_job_id else None
    if path is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "现在没有爬虫窗口。日常开着的 Chrome 不是这一扇，拉到前面也看不到验证。",
            },
            status_code=404,
        )
    request_show_crawler(path)
    return JSONResponse({"ok": True, "crawler": True, "job_id": path.name})


@app.post("/api/ask")
async def ask_followup(
    job_id: str = Query(...),
    q: str = Query(..., min_length=1, max_length=400),
) -> JSONResponse:
    question = " ".join(q.split())
    if not question:
        return JSONResponse({"ok": False, "error": "问题是空的"}, status_code=400)
    try:
        query, items = load_job_items(job_id)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    if not items:
        return JSONResponse({"ok": False, "error": "这次搜索没有原始结果"}, status_code=400)
    job_dir = RUNS_DIR / job_id
    board = load_saved_board(job_dir) or {}
    history = load_chat(job_dir)
    intent = detect_query_intent(query)
    payload = {
        "query": query,
        "question": question,
        "history": history,
        "intent": {
            "mode": intent["mode"],
            "label": intent["label"],
            "job": intent["job"],
        },
        "report": board.get("report"),
        "judgment": board.get("judgment"),
        "items": _eleven_payload(query, items, intent)["items"],
    }
    if not DEEPSEEK_KEY:
        return JSONResponse({"ok": False, "error": "还没有配置 DEEPSEEK_API_KEY"}, status_code=400)
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": "\n".join(
                    [
                        "你是十一。使用者在追问刚刚写好的调研报告。",
                        f"原搜索任务是「{intent['label']}」：{intent['job']}",
                        "先看检索材料和报告。能直接构成答案时，必须优先引用这些材料。",
                        "引用时点名标题或原文短句，并填 item_ids。不要空口转述。",
                        "材料不够、答不上来源或命名这类材料没写的事实时，used_materials=false，need_web=true。",
                        "社交帖是叙事信号，不是已核实事实。不要编出处。",
                        "返回 JSON：answer, points, item_ids, open_questions, used_materials, need_web, illustrations。",
                        "item_ids 必须来自输入里已有的 id。",
                        "illustrations 看情况写，最多两张：[{query,caption}]。地点、物件、场面要让人看见时才配；名单、数字、纯判断不要硬配。不要编图片网址。",
                    ]
                ),
                "input": json.dumps(payload, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 2500,
            }
        )
        parsed = parse_model_json(text)
    except Exception as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
    answer = str(parsed.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"ok": False, "error": "十一没有回答"}, status_code=502)
    web_refs: list[dict[str, str]] = []
    used_web = False
    if _ask_needs_web(parsed, answer):
        hits = await web_lookup(question, query)
        if hits:
            payload["web_hits"] = hits
            payload["note"] = "检索材料不够。下面是联网公开搜索结果，据此补答，并列出 citations。"
            try:
                _envelope, text = await _post_eleven(
                    {
                        "model": DEEPSEEK_MODEL,
                        "reasoning": {"effort": "none"},
                        "instructions": "\n".join(
                            [
                                "你是十一。材料里答不全，现在用联网结果补答。",
                                "先写材料为什么不够，再写联网能补上什么。",
                                "citations 只许用 web_hits 里出现过的 title 和 url，不要编链接。",
                                "返回 JSON：answer, points, item_ids, citations, open_questions, illustrations。",
                                "item_ids 若仍能对照材料就填，必须来自输入里已有的 id。",
                                "illustrations 看情况写，最多两张：[{query,caption}]。地点、物件、场面要让人看见时才配；不要编图片网址。",
                            ]
                        ),
                        "input": json.dumps(payload, ensure_ascii=False),
                        "text": {"format": {"type": "json_object"}},
                        "max_output_tokens": 3500,
                    }
                )
                parsed = parse_model_json(text)
                answer = str(parsed.get("answer") or answer).strip()
                used_web = True
                web_refs = _citations_from_hits(parsed.get("citations"), hits)
                if not web_refs:
                    web_refs = hits
            except Exception:
                used_web = True
                web_refs = hits
                if hits:
                    answer = (
                        answer
                        + "\n\n材料不够，联网查到这些公开来源，可以点开看："
                        + "；".join(f"{row['title']}（{row['url']}）" for row in hits[:4])
                    )
        else:
            answer = answer + "\n\n材料不够，这次联网也没搜到能引用的公开结果。"
    illustrations = fill_illustration_hits(
        normalize_illustration_queries(parsed.get("illustrations"), 2)
    )
    extra = {
        "points": parsed.get("points") or [],
        "item_ids": parsed.get("item_ids") or [],
        "open_questions": parsed.get("open_questions") or [],
        "web_refs": web_refs,
        "used_web": used_web,
        "illustrations": illustrations,
    }
    append_chat(job_dir, question, answer, extra)
    return JSONResponse(
        {
            "ok": True,
            "answer": answer,
            **extra,
        }
    )


@app.get("/api/history")
async def history() -> JSONResponse:
    return JSONResponse({"ok": True, "items": read_history()})


@app.delete("/api/history/{job_id}")
async def history_delete(job_id: str) -> JSONResponse:
    ok, error = forget_job(job_id)
    if not ok:
        code = 409 if "还在搜" in error else 400
        return JSONResponse({"ok": False, "error": error}, status_code=code)
    return JSONResponse({"ok": True, "items": read_history(), "storage": storage_report()})


@app.get("/api/storage")
async def storage_get() -> JSONResponse:
    return JSONResponse({"ok": True, **storage_report()})


@app.post("/api/storage/clear")
async def storage_clear() -> JSONResponse:
    return JSONResponse({"ok": True, **clear_run_cache(), "items": read_history()})


@app.get("/api/job/{job_id}/chat")
async def job_chat(job_id: str) -> JSONResponse:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    rows = []
    for row in load_chat_rows(job_dir):
        rows.append(
            {
                "q": str(row.get("q") or ""),
                "a": str(row.get("a") or ""),
                "points": row.get("points") or [],
                "item_ids": row.get("item_ids") or [],
                "open_questions": row.get("open_questions") or [],
                "web_refs": row.get("web_refs") or [],
                "used_web": bool(row.get("used_web")),
                "illustrations": row.get("illustrations") or [],
            }
        )
    return JSONResponse({"ok": True, "items": rows})


def _saved_board_ready(board: dict[str, Any] | None) -> bool:
    if not isinstance(board, dict):
        return False
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    sections = [
        row
        for row in (report.get("sections") or [])
        if isinstance(row, dict) and (row.get("heading") or row.get("body"))
    ]
    if not sections:
        return False
    if board.get("harvest"):
        return True
    briefs = [
        row
        for row in (report.get("platform_briefs") or [])
        if isinstance(row, dict) and row.get("answer")
    ]
    sources = (
        ((report.get("sources") or {}) if isinstance(report.get("sources"), dict) else {}).get(
            "platforms"
        )
        or []
    )
    if len(briefs) < 2 or not sources or not report.get("engagement"):
        return False
    blob = " ".join(str(row.get("answer") or "") for row in briefs)
    return "未返回" not in blob and "没有可引用" not in blob


@app.get("/api/job/{job_id}/board")
async def job_board(job_id: str) -> JSONResponse:
    job_dir = RUNS_DIR / job_id
    board = load_saved_board(job_dir)
    if not board:
        return JSONResponse({"ok": False, "error": "还没有报告"}, status_code=404)
    if _saved_board_ready(board):
        return JSONResponse({"ok": True, "board": attach_agent_search(board, job_dir)})
    try:
        query, items = load_job_items(job_id)
        before = illustration_urls(board)
        before_judge = ((board.get("judgment") or {}) if isinstance(board.get("judgment"), dict) else {}).get(
            "one_line_judgment"
        )
        before_guard = _report_guard_sig(board)
        board = attach_agent_search(enrich_report(query, items, board), job_dir)
        after_judge = ((board.get("judgment") or {}) if isinstance(board.get("judgment"), dict) else {}).get(
            "one_line_judgment"
        )
        if (
            illustration_urls(board) != before
            or after_judge != before_judge
            or _report_guard_sig(board) != before_guard
        ):
            save_board(job_dir, board)
    except FileNotFoundError:
        pass
    return JSONResponse({"ok": True, "board": board})


def packed_export(job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], Path, str]:
    job_dir = RUNS_DIR / job_id
    board = load_saved_board(job_dir)
    if not board:
        raise FileNotFoundError("还没有报告")
    query, items = load_job_items(job_id)
    board = attach_agent_search(enrich_report(query, items, board), job_dir)
    return board, items, load_chat_rows(job_dir), job_dir, query


async def html_to_pdf(html: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.emulate_media(media="print")
            await page.set_content(html, wait_until="load")
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            return await page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "12mm", "right": "11mm", "bottom": "16mm", "left": "11mm"},
            )
        finally:
            await browser.close()


@app.get("/api/job/{job_id}/export.md")
async def export_markdown(job_id: str):
    try:
        board, items, asks, _job_dir, query = packed_export(job_id)
    except FileNotFoundError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=404)
    title = ((board.get("report") or {}) if isinstance(board.get("report"), dict) else {}).get("title") or query
    body = board_to_markdown(board, items, asks, query)
    filename = quote(safe_filename(str(title), ".md"))
    return Response(
        content=body.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/job/{job_id}/export.pdf")
async def export_pdf(job_id: str):
    try:
        board, items, asks, job_dir, query = packed_export(job_id)
    except FileNotFoundError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=404)
    title = ((board.get("report") or {}) if isinstance(board.get("report"), dict) else {}).get("title") or query
    await prefetch_item_images(job_dir, items)
    html = board_to_html(board, items, asks, query, job_dir)
    async with _pdf_lock:
        try:
            pdf = await html_to_pdf(html)
        except Exception as error:
            return JSONResponse({"ok": False, "error": f"PDF 没做成：{error}"}, status_code=502)
    filename = quote(safe_filename(str(title), ".pdf"))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/media/{job_id}")
async def job_media(
    job_id: str,
    item: str = Query(...),
    i: int = Query(0, ge=0, le=12),
):
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    found = None
    for code, _label in PLATFORMS:
        for row in read_jsonl(job_dir, code):
            if row.get("id") == item:
                found = row
                break
        if found:
            break
    images = (found or {}).get("images") or []
    if i >= len(images):
        return JSONResponse({"ok": False, "error": "这条没有这张图"}, status_code=404)
    url = str(images[i])
    if url.startswith("frame:"):
        path = Path(url[6:])
        try:
            resolved = path.resolve()
            resolved.relative_to(job_dir.resolve())
        except (OSError, ValueError):
            return JSONResponse({"ok": False, "error": "截帧不在这次搜索里"}, status_code=404)
        if not resolved.is_file():
            return JSONResponse({"ok": False, "error": "这帧还没截下来"}, status_code=404)
        return FileResponse(
            resolved,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    cache = media_cache_path(job_dir, item, i, url)
    if not cache_is_real(cache, url):
        cache.parent.mkdir(parents=True, exist_ok=True)
        data = await fetch_image_bytes(
            url,
            platform=str((found or {}).get("platform") or ""),
            page_url=str((found or {}).get("url") or ""),
        )
        if not data:
            return JSONResponse({"ok": False, "error": "原图暂时拉不下来"}, status_code=502)
        cache.write_bytes(data)
    raw = cache.read_bytes()
    return FileResponse(
        cache,
        media_type=sniff_media_type(raw) or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/job/{job_id}")
async def job_snapshot(job_id: str) -> JSONResponse:
    job_dir = RUNS_DIR / job_id
    if not job_dir.is_dir():
        return JSONResponse({"ok": False, "error": "没有这次搜索"}, status_code=404)
    preview = job_dir / "preview.jpg"
    query, items = load_job_items(job_id, extra_ids=board_cited_ids(load_saved_board(job_dir)))
    coverage = job_coverage(job_dir, query)
    process = reconstruct_process(job_dir, query, coverage)
    return JSONResponse(
        {
            "ok": True,
            "job_id": job_id,
            "query": query,
            "item_count": len(items),
            "coverage": coverage,
            "process": process,
            "items": items[:HARD_MAX_NOTES * len(PLATFORMS)],
            "preview_url": (
                f"/api/job/{job_id}/preview?t={int(preview.stat().st_mtime * 1000)}"
                if preview.is_file()
                else ""
            ),
        }
    )


@app.get("/api/hot/sources")
async def api_hot_sources() -> JSONResponse:
    return JSONResponse(hot_catalog())


@app.get("/api/hot/s")
async def api_hot_one(
    id: str = Query(..., min_length=1, max_length=40),
    force: bool = Query(False),
) -> JSONResponse:
    if not source_of(id):
        return JSONResponse({"ok": False, "error": "没有这个热搜源", "id": id, "items": []}, status_code=404)
    return JSONResponse(await fetch_list(id, force=force))


@app.get("/api/hot/lists")
async def api_hot_lists(
    ids: str = Query(""),
    force: bool = Query(False),
) -> JSONResponse:
    wanted = [part.strip() for part in ids.split(",") if part.strip()]
    rows = await fetch_lists(wanted, force=force)
    return JSONResponse({"ok": True, "lists": rows})


@app.post("/api/hot/keyword")
async def api_hot_keyword(
    title: str = Query(..., min_length=1, max_length=200),
    source: str = Query("", max_length=40),
) -> JSONResponse:
    cleaned = fallback_keyword(title)
    if not DEEPSEEK_KEY:
        return JSONResponse({"ok": True, "query": cleaned or title.strip()[:80], "via": "fallback"})
    meta = source_of(source) or {}
    try:
        _envelope, text = await _post_eleven(
            {
                "model": DEEPSEEK_MODEL,
                "reasoning": {"effort": "none"},
                "instructions": "\n".join(
                    [
                        "你是十一。把一条热搜标题收成一览搜索栏能直接用的关键词。",
                        "只返回 JSON：{\"query\":\"...\"}",
                        "去掉平台名、热搜、话题符号、营销后缀；保留专名、事件、产品。",
                        "不超过 24 个汉字。不要解释。",
                    ]
                ),
                "input": json.dumps(
                    {
                        "title": title.strip(),
                        "source": meta.get("name") or source,
                        "fallback": cleaned,
                    },
                    ensure_ascii=False,
                ),
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 200,
            }
        )
        parsed = parse_model_json(text)
        query = fallback_keyword(str(parsed.get("query") or cleaned))
    except Exception:
        query = cleaned
    if not query:
        query = title.strip()[:80]
    return JSONResponse({"ok": True, "query": query[:80], "via": "eleven"})


@app.get("/api/intent")
async def api_intent(q: str = Query("", max_length=80)) -> JSONResponse:
    return JSONResponse(detect_query_intent(q))


def _setup_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


@app.get("/api/setup")
async def setup_get() -> JSONResponse:
    reload_runtime_keys()
    return JSONResponse({"ok": True, **public_state()})


@app.get("/api/setup/agents/{agent_id}")
async def setup_agent_get(agent_id: str) -> JSONResponse:
    agent = get_agent(agent_id)
    if not agent:
        return _setup_error(ValueError("没有这个 Agent"), 404)
    return JSONResponse(
        {
            "ok": True,
            "agent": {
                **agent,
                "preview": agent_preview(agent["id"]),
                "persona": agent_persona(agent["id"]) if agent["id"] != "eleven" else agent_preview(agent["id"], 4000),
            },
        }
    )


@app.post("/api/setup/agents")
async def setup_agent_create(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    try:
        agent = add_custom_agent(
            str(payload.get("name") or ""),
            str(payload.get("persona") or payload.get("skill") or ""),
        )
    except ValueError as exc:
        return _setup_error(exc)
    return JSONResponse({"ok": True, "agent": agent, **public_state()})


@app.post("/api/setup/agent")
async def setup_agent_activate(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    try:
        set_active_agent(str(payload.get("id") or ""))
    except ValueError as exc:
        return _setup_error(exc)
    return JSONResponse({"ok": True, **public_state()})


@app.delete("/api/setup/agents/{agent_id}")
async def setup_agent_delete(agent_id: str) -> JSONResponse:
    try:
        delete_custom_agent(agent_id)
    except ValueError as exc:
        return _setup_error(exc)
    return JSONResponse({"ok": True, **public_state()})


@app.post("/api/setup/keys")
async def setup_keys_save(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    try:
        if payload.get("id") and (payload.get("activate") or payload.get("active")):
            set_active_key(str(payload.get("id") or ""))
        elif payload.get("kind"):
            add_key(
                str(payload.get("kind") or ""),
                str(payload.get("name") or ""),
                str(payload.get("value") or payload.get("key") or ""),
                str(payload.get("model") or ""),
            )
        else:
            save_keys(payload)
    except ValueError as exc:
        return _setup_error(exc)
    reload_runtime_keys()
    return JSONResponse({"ok": True, **public_state()})


@app.post("/api/setup/keys/active")
async def setup_keys_activate(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    try:
        set_active_key(str(payload.get("id") or ""))
    except ValueError as exc:
        return _setup_error(exc)
    reload_runtime_keys()
    return JSONResponse({"ok": True, **public_state()})


@app.delete("/api/setup/keys/{key_id}")
async def setup_keys_delete(key_id: str) -> JSONResponse:
    try:
        delete_key(key_id)
    except ValueError as exc:
        return _setup_error(exc)
    reload_runtime_keys()
    return JSONResponse({"ok": True, **public_state()})


@app.get("/api/health")
async def health() -> JSONResponse:
    chrome = chrome_status()
    return JSONResponse(
        {
            "ok": True,
            "chrome": chrome,
            "model": {
                "configured": bool(DEEPSEEK_KEY),
                "model": DEEPSEEK_MODEL if DEEPSEEK_KEY else "",
            },
            "setup": public_state(),
            "platforms": [
                {"id": code, "label": label, "foreign": code in FOREIGN_CODES}
                for code, label in PLATFORMS
            ],
            "foreign": foreign_availability(),
            "feedgrab": {"ready": feedgrab_ready(), "mp": feedgrab_ready()},
            "max_notes": DEFAULT_NOTES,
            "max_notes_limit": HARD_MAX_NOTES,
            "timeout": PLATFORM_TIMEOUT,
            "bind": f"{HOST}:{PORT}",
        }
    )


def parse_notes_spec(raw: str, default: int) -> dict[str, int]:
    notes: dict[str, int] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if ":" not in part:
            continue
        code, value = part.split(":", 1)
        code = code.strip()
        if code not in PLATFORM_MAP:
            continue
        try:
            count = int(value)
        except ValueError:
            continue
        notes[code] = max(1, min(count, HARD_MAX_NOTES))
    if not notes:
        notes = {code: default for code, _label in PLATFORMS}
    return notes


def parse_options(
    max_notes: int,
    comments: bool,
    sub_comments: bool,
    max_comments: int,
    eleven: bool,
    notes_spec: str = "",
    platform_queries_spec: str = "",
    refine_note: str = "",
    agent_search: bool = False,
    no_ai: bool = False,
) -> SearchOptions:
    default = max(1, min(max_notes, HARD_MAX_NOTES))
    if no_ai:
        eleven = False
        agent_search = False
    return SearchOptions(
        max_notes=default,
        comments=bool(comments and COMMENTS_READY),
        sub_comments=bool(comments and sub_comments and COMMENTS_READY),
        max_comments=max(1, min(max_comments, HARD_MAX_COMMENTS)),
        eleven=eleven,
        agent_search=agent_search,
        no_ai=no_ai,
        notes=parse_notes_spec(notes_spec, default),
        platform_queries=parse_platform_queries(platform_queries_spec),
        refine_note=refine_note,
    )


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=80),
    platforms: str = Query(""),
    max_notes: int = Query(DEFAULT_NOTES, ge=1, le=HARD_MAX_NOTES),
    notes: str = Query(""),
    comments: bool = Query(False),
    sub_comments: bool = Query(False),
    max_comments: int = Query(10, ge=1, le=HARD_MAX_COMMENTS),
    eleven: bool = Query(True),
    agent_search: bool = Query(False),
    no_ai: bool = Query(False),
    resume_job: str = Query(""),
    resume: bool = Query(False),
    supplement: bool = Query(False),
    platform_queries: str = Query(""),
    refine_note: str = Query(""),
) -> StreamingResponse:
    query = " ".join(q.split())
    if not query:
        return JSONResponse({"error": "搜索词是空的"}, status_code=400)
    selected = [
        code.strip()
        for code in platforms.split(",")
        if code.strip() in PLATFORM_MAP
    ] or [code for code, _ in PLATFORMS]
    options = parse_options(
        max_notes,
        comments,
        sub_comments,
        max_comments,
        eleven,
        notes,
        platform_queries,
        refine_note,
        agent_search=agent_search,
        no_ai=no_ai,
    )
    if supplement:
        job_path = resume_job_dir(resume_job, query, allow_latest=True)
        if job_path is None:
            return JSONResponse({"error": "没有这次搜索，没法在原结果上补充。"}, status_code=404)
    if _search_lock.locked():
        return JSONResponse({"error": "已经有一次搜索在跑，先等它结束。"}, status_code=409)

    async def locked() -> AsyncIterator[str]:
        async with _search_lock:
            async for chunk in search_stream(
                query,
                selected,
                options,
                resume_job=resume_job,
                allow_latest=resume or supplement,
                supplement=supplement,
            ):
                yield chunk

    return StreamingResponse(
        locked(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def main() -> None:
    import uvicorn

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"一览 AI智搜: http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
