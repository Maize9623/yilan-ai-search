"""Progressive search steps shared by board and crawlers.

A = 整句, B = 短词, C = 核心词. Escalate only when the current step
returns nothing or stays below half the asked count.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from board.freshness import QUERY_STOP, query_places, query_tokens
except ImportError:  # crawler process may load board as a loose folder
    from freshness import QUERY_STOP, query_places, query_tokens


def need_more(have: int, wanted: int) -> bool:
    wanted = max(1, int(wanted or 1))
    floor = max(5, (wanted + 1) // 2) if wanted >= 8 else max(1, (wanted + 1) // 2)
    return have < min(wanted, floor)


def fill_budget(have: int, wanted: int) -> int:
    wanted = max(1, int(wanted or 1))
    floor = max(5, (wanted + 1) // 2) if wanted >= 8 else wanted
    return max(8, min(wanted, floor) - max(0, have) + 3)


def compact_search_query(query: str) -> str:
    text = " ".join((query or "").split())
    if not text:
        return text
    tokens = query_tokens(text)
    compact = " ".join(tokens[:4])
    return compact or text[:20]


def core_search_query(query: str) -> str:
    text = " ".join((query or "").split())
    if not text:
        return text
    places = query_places(text)
    tokens = [token for token in query_tokens(text) if token not in places]
    core = " ".join([*places[:1], *tokens[:2]]).strip()
    if len(core) >= 2:
        return core
    return compact_search_query(text) or text[:12]


def query_steps(query: str) -> list[tuple[str, str]]:
    full = " ".join((query or "").split())
    if not full:
        return []
    steps: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, term in (
        ("整句", full),
        ("短词", compact_search_query(full)),
        ("核心词", core_search_query(full)),
    ):
        clean = " ".join((term or "").split())
        if len(clean) < 2 or clean in seen or clean in QUERY_STOP:
            continue
        if re.fullmatch(r"[\d\s]+", clean):
            continue
        seen.add(clean)
        steps.append((name, clean))
    return steps or [("整句", full)]


def job_dir_from_env() -> Path | None:
    preview = (os.getenv("BOARD_PREVIEW_PATH") or "").strip()
    if preview:
        return Path(preview).parent
    save = (os.getenv("SAVE_DATA_PATH") or "").strip()
    if save:
        return Path(save)
    return None


def ladder_mark_path(job_dir: Path, platform: str) -> Path:
    return Path(job_dir) / f"{platform}.ladder_done"


def mark_ladder_done(platform: str) -> None:
    job_dir = job_dir_from_env()
    if job_dir is None:
        return
    try:
        ladder_mark_path(job_dir, platform).write_text("1", encoding="utf-8")
    except OSError:
        pass


def ladder_finished(job_dir: Path, platform: str) -> bool:
    return ladder_mark_path(job_dir, platform).is_file()


PLATFORM_LADDERS = {
    "xhs": ("整句综合搜索", "短词再搜", "核心词再搜"),
    "dy": ("整句综合搜索", "短词再搜", "核心词再搜"),
    "ks": ("整句综合搜索", "短词再搜", "核心词再搜"),
    "bili": ("整句综合搜索", "短词再搜", "核心词再搜"),
    "wb": ("整句综合/短话题", "实时", "热门"),
    "tieba": ("整句相关贴", "短词再搜", "核心词再搜"),
    "zhihu": ("整句综合搜索", "短词再搜", "核心词再搜"),
    "mp": ("搜狗+打开原文", "短词再搜并补读", "只留索引摘要"),
    "gnews": ("Google News", "Bing News", "短词再搜"),
    "bsky": ("searchPostsV2", "searchPosts", "短词再搜"),
    "reddit": ("本机 feedgrab", "reddit.com JSON", "公开归档"),
    "yt": ("YouTube Search", "短词再搜", "核心词再搜"),
}
