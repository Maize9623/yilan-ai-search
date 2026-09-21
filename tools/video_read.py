"""Grab a few frames and on-screen text from video platforms. No full download."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("MediaCrawler")

VIDEO_PLATFORMS = {"dy", "douyin", "ks", "kuaishou", "bili", "bilibili"}
PLATFORM_FOLDERS = {
    "dy": ("dy", "douyin"),
    "douyin": ("douyin", "dy"),
    "ks": ("ks", "kuaishou"),
    "kuaishou": ("kuaishou", "ks"),
    "bili": ("bili", "bilibili"),
    "bilibili": ("bilibili", "bili"),
}
_captured = 0
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_AMP = (
    ("&nbsp;", " "),
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&emsp;", " "),
)


def video_read_enabled() -> bool:
    return os.getenv("BOARD_VIDEO_READ", "").strip() == "1"


def frames_budget() -> int:
    try:
        return max(0, int(os.getenv("BOARD_VIDEO_FRAMES_MAX", "20")))
    except ValueError:
        return 20


def frames_dir(item_id: str, platform: str = "") -> Path | None:
    root = (os.getenv("SAVE_DATA_PATH") or "").strip()
    if not root:
        try:
            import config

            root = str(getattr(config, "SAVE_DATA_PATH", "") or "").strip()
        except Exception:
            root = ""
    if not root or not item_id:
        return None
    folder = platform or "video"
    return Path(root) / folder / "frames" / str(item_id)


def format_duration(raw: Any) -> str:
    try:
        number = float(raw or 0)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number > 1000:
        number /= 1000
    seconds = int(number)
    return f"{seconds // 60}:{seconds % 60:02d}"


def strip_markup(value: Any) -> str:
    text = str(value or "")
    text = _HTML_TAG.sub("", text)
    for src, dest in _HTML_AMP:
        text = text.replace(src, dest)
    return " ".join(text.split()).strip()


def subtitle_body_text(payload: Any) -> str:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("body") or payload.get("data") or []
    else:
        return ""
    chunks: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            piece = str(row.get("content") or row.get("text") or "").strip()
        else:
            piece = str(row or "").strip()
        if piece and (not chunks or chunks[-1] != piece):
            chunks.append(piece)
    return "\n".join(chunks).strip()


def pick_subtitle_track(tracks: list[Any] | None) -> dict[str, Any] | None:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        url = str(track.get("subtitle_url") or track.get("subtitle_url_v2") or "").strip()
        if not url or url in {"/", "null"}:
            continue
        lan = str(track.get("lan") or track.get("lang") or "").strip()
        if lan in {"zh-CN", "zh-Hans", "zh"}:
            rank = 0
        elif lan == "ai-zh" or lan.startswith("ai-zh"):
            rank = 1
        elif lan.startswith("zh"):
            rank = 2
        else:
            rank = 9
        ranked.append((rank, track))
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1] if ranked else None


def classify_caption_source(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) >= 16:
        return "caption"
    if cleaned:
        return "caption_short"
    return "none"


def clip_spoken(text: str, limit: int) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    cut = max(1, limit - 10)
    return cleaned[:cut].rstrip() + "\n[原文未完]"


def collect_caption_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []

    def add(value: Any, *, limit: int = 0) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if limit and len(text) > limit:
            text = text[:limit]
        if text not in parts:
            parts.append(text)

    desc = payload.get("desc")
    if isinstance(desc, str):
        add(desc)
    caption = payload.get("caption")
    if isinstance(caption, str):
        add(caption)
    elif isinstance(caption, dict):
        add(caption.get("text"))
    if not parts:
        add(payload.get("title"))
    for extra in payload.get("text_extra") or []:
        if isinstance(extra, dict):
            add(extra.get("hashtag_name") or extra.get("search_text"), limit=48)
    for sticker in payload.get("interaction_stickers") or []:
        if not isinstance(sticker, dict):
            continue
        info = sticker.get("text_info")
        if isinstance(info, dict):
            add(info.get("text"), limit=80)
        else:
            add(info or sticker.get("text"), limit=80)
    for chapter in payload.get("chapter_list") or []:
        if isinstance(chapter, dict):
            add(chapter.get("desc") or chapter.get("title"), limit=80)
    return "\n".join(parts).strip()


def collect_video_text(payload: dict[str, Any]) -> str:
    return collect_caption_text(payload)


def parse_clock_duration_ms(raw: Any) -> int:
    if raw in (None, "", 0, "0"):
        return 0
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        number = int(raw)
        if number <= 0:
            return 0
        return number if number >= 1000 else number * 1000
    text = str(raw).strip()
    if ":" in text:
        try:
            nums = [int(part) for part in text.split(":")]
        except ValueError:
            return 0
        seconds = 0
        for num in nums:
            seconds = seconds * 60 + num
        return seconds * 1000
    try:
        return parse_clock_duration_ms(float(text))
    except ValueError:
        return 0


def duration_ms(payload: dict[str, Any]) -> int:
    video = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    for raw in (
        video.get("duration"),
        payload.get("duration"),
        (payload.get("photo") or {}).get("duration") if isinstance(payload.get("photo"), dict) else 0,
        (payload.get("View") or {}).get("duration") if isinstance(payload.get("View"), dict) else 0,
    ):
        try:
            number = int(float(raw or 0))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        if number < 1000:
            return number * 1000
        return number
    return 0


def load_sidecar(job_dir: Path, platform: str, item_id: str) -> dict[str, Any]:
    for name in PLATFORM_FOLDERS.get(platform, (platform,)):
        meta = job_dir / name / "frames" / item_id / "meta.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def sidecar_frames(job_dir: Path, platform: str, item_id: str) -> list[Path]:
    for name in PLATFORM_FOLDERS.get(platform, (platform,)):
        folder = job_dir / name / "frames" / item_id
        if folder.is_dir():
            return sorted(folder.glob("f*.jpg"))
    return []


def write_sidecar_scene(job_dir: Path, platform: str, item_id: str, scene: str) -> None:
    text = str(scene or "").strip()
    if not text or not item_id:
        return
    for name in PLATFORM_FOLDERS.get(platform, (platform,)):
        dest = job_dir / name / "frames" / item_id
        if not dest.is_dir():
            continue
        meta = load_sidecar(job_dir, platform, item_id)
        meta["scene"] = text
        _write_meta(dest, meta)
        return


def _write_meta(dest: Path, payload: dict[str, Any]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "meta.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _grab_frame(url: str, dest: Path, seconds: float, headers: dict[str, str]) -> bool:
    ffmpeg = shutil.which("ffmpeg") or os.getenv("FFMPEG_PATH") or ""
    if not ffmpeg or not url:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    ua = headers.get("User-Agent") or ""
    if ua:
        cmd.extend(["-user_agent", ua])
    extra = ""
    if headers.get("Referer"):
        extra += f"Referer: {headers['Referer']}\r\n"
    if headers.get("Cookie"):
        extra += f"Cookie: {headers['Cookie']}\r\n"
    if extra:
        cmd.extend(["-headers", extra])
    cmd.extend(
        [
            "-ss",
            f"{max(0.0, seconds):.2f}",
            "-t",
            "2",
            "-i",
            url,
            "-frames:v",
            "1",
            "-vf",
            "scale=480:-2",
            "-q:v",
            "5",
            str(dest),
        ]
    )
    try:
        subprocess.run(cmd, timeout=18, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return dest.is_file() and dest.stat().st_size > 800


def _timestamps(duration_ms_value: int) -> list[float]:
    if duration_ms_value >= 3000:
        total = duration_ms_value / 1000
        return [max(0.4, total * 0.12), total * 0.45, min(total * 0.78, total - 0.4)]
    return [0.8, 3.0, 6.5]


async def capture_for_board(
    *,
    platform: str,
    item_id: str,
    play_url: str = "",
    cover_url: str = "",
    duration_ms_value: int = 0,
    video_text: str = "",
    headers: dict[str, str] | None = None,
) -> None:
    global _captured
    if not video_read_enabled() or not item_id:
        return
    dest = frames_dir(item_id, platform)
    if dest is None:
        return
    meta = {
        "duration": format_duration(duration_ms_value),
        "duration_ms": duration_ms_value,
        "video_text": video_text,
        "frames": 0,
        "scene": "",
    }
    if dest.joinpath("meta.json").is_file() and list(dest.glob("f*.jpg")):
        return
    _write_meta(dest, meta)
    if _captured >= frames_budget():
        return
    headers = headers or {}
    source = play_url or cover_url
    if not source:
        return
    stamps = [0.0] if not play_url else _timestamps(duration_ms_value)[:3]
    saved = 0
    for index, stamp in enumerate(stamps):
        frame = dest / f"f{index}.jpg"
        ok = await asyncio.to_thread(_grab_frame, source, frame, stamp, headers)
        if ok:
            saved += 1
    if saved == 0 and cover_url and play_url != cover_url:
        ok = await asyncio.to_thread(_grab_frame, cover_url, dest / "f0.jpg", 0.0, headers)
        if ok:
            saved = 1
    if saved:
        _captured += 1
        meta["frames"] = saved
        _write_meta(dest, meta)
        logger.info(
            f"[video_read] {platform} {item_id} 截了 {saved} 帧，片长 {meta['duration'] or '未知'}"
        )
