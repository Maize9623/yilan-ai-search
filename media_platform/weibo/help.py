# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/weibo/help.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/24 17:37
# @Desc    :

import re
from typing import Any, Dict, List, Optional

_SEARCH_SEPS = ("，", "。", "；", "、", "？", "?", "！", "!", ";", "\n")
_MID_IN_URL = re.compile(r"(?:/detail/|/status/|mblogid=)(\d{8,})")
_MID_IN_JSON = re.compile(r'"(?:id|mid)":"(\d{14,})"')
_WHAT_RE = re.compile(r"什么([^，。？?！!、\s]{2,12})")
_MONEY_RE = re.compile(r"(\d{3,6})")
_WEAK_RE = re.compile(r"^[\d\s块钱元左右大概约人民币kKwW]+$")
_QUESTION_MARKS = ("什么", "怎么", "哪些", "如何", "吗", "？", "?", "要不要")
_TOPIC_TAILS = ("礼物", "推荐", "攻略", "测评", "避雷", "清单", "教程", "指南", "穿搭", "护肤")
_USERISH_CARD_TYPES = {3, 4, 8, 11, 17, 22}


def search_keywords(raw: str) -> List[str]:
    """Board 传入的是整句。只有一串短标签用英文逗号隔开时才拆开。"""
    text = " ".join((raw or "").strip().split())
    if not text:
        return []
    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) > 1 and all(len(part) <= 16 and " " not in part for part in parts):
            return parts
    return [text]


def _is_weak_query(value: str) -> bool:
    text = (value or "").strip()
    return len(text) < 2 or bool(_WEAK_RE.fullmatch(text))


def _compact_topics(text: str) -> List[str]:
    found: List[str] = []

    def add(value: str) -> None:
        clean = " ".join((value or "").split())
        if clean and not _is_weak_query(clean) and clean not in found:
            found.append(clean)

    for match in _WHAT_RE.findall(text):
        add(match)
    for tail in _TOPIC_TAILS:
        start = 0
        while True:
            idx = text.find(tail, start)
            if idx < 0:
                break
            prefix = re.split(r"什么|的", text[max(0, idx - 4):idx])[-1]
            add(prefix + tail)
            start = idx + len(tail)
    return found


def query_fallbacks(keyword: str) -> List[str]:
    """问句先用能搜到帖子的短词，整句放后面；普通关键词仍先整句。"""
    text = " ".join((keyword or "").split())
    if not text:
        return []

    compact = [item for item in _compact_topics(text) if item != text]
    extras: List[str] = []
    seen = {text, *compact}

    def add(bucket: List[str], value: str) -> None:
        clean = " ".join((value or "").split())
        if clean and not _is_weak_query(clean) and clean not in seen:
            seen.add(clean)
            bucket.append(clean)

    money = _MONEY_RE.search(text)
    if money and compact:
        add(compact, f"{compact[0]} {money.group(1)}")
    for sep in _SEARCH_SEPS:
        if sep in text:
            for part in text.split(sep):
                add(extras, part)

    if any(mark in text for mark in _QUESTION_MARKS) and compact:
        return [*compact, text, *extras][:6]
    return [text, *compact, *extras][:6]


def weibo_search_queries(keyword: str) -> List[str]:
    """整句和短话题之后，不够就换成短词、核心词，避免长句只命中一页就停。"""
    from tools.search_ladders import query_steps

    ordered: List[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        clean = " ".join((value or "").split())
        if clean and not _is_weak_query(clean) and clean not in seen:
            seen.add(clean)
            ordered.append(clean)

    for item in query_fallbacks(keyword):
        add(item)
    for _name, term in query_steps(keyword):
        add(term)
    return ordered[:8]


def _as_note(node: Dict) -> Optional[Dict]:
    mblog = node.get("mblog")
    if isinstance(mblog, dict) and mblog.get("id"):
        return node if node.get("mblog") is mblog else {"mblog": mblog}
    if node.get("id") and (node.get("text") or node.get("text_raw")) and isinstance(node.get("user"), dict):
        return {"mblog": node}
    return None


def filter_search_result_card(card_list: List[Dict]) -> List[Dict]:
    """Keep any nested card that carries a weibo mblog, not only card_type 9."""
    note_list: List[Dict] = []
    seen = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        note = _as_note(node)
        if note:
            mid = str((note.get("mblog") or {}).get("id") or "")
            if mid and mid not in seen:
                seen.add(mid)
                note_list.append(note)
        for key in ("card_group", "cards", "card_group_v2", "items", "group"):
            child = node.get(key)
            if child:
                walk(child)

    walk(card_list or [])
    return note_list


def notes_from_search_payload(payload: Any) -> List[Dict]:
    if not isinstance(payload, dict):
        return []
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(body, dict):
        return []
    cards = body.get("cards") or body.get("items") or []
    return filter_search_result_card(cards if isinstance(cards, list) else [])


def should_harvest_search(payload: Any) -> bool:
    """用户/话题卡页没有微博正文，再打开搜索页也截不到。"""
    if notes_from_search_payload(payload):
        return False
    if not isinstance(payload, dict):
        return True
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    cards = (body or {}).get("cards") or (body or {}).get("items") or []
    if not isinstance(cards, list) or not cards:
        return True
    types = [item.get("card_type") for item in cards if isinstance(item, dict)]
    return any(card_type not in _USERISH_CARD_TYPES for card_type in types)


def card_type_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n=0 types=[]"
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    cards = (body or {}).get("cards") or (body or {}).get("items") or []
    if not isinstance(cards, list):
        return "n=0 types=[]"
    types = [item.get("card_type") for item in cards if isinstance(item, dict)]
    return f"n={len(cards)} types={types[:16]}"


def mids_from_html(html: str, limit: int = 40) -> List[str]:
    seen = set()
    out: List[str] = []

    def add(mid: str) -> None:
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)

    text = html or ""
    for mid in _MID_IN_URL.findall(text):
        add(mid)
        if len(out) >= limit:
            return out
    for mid in _MID_IN_JSON.findall(text):
        add(mid)
        if len(out) >= limit:
            return out
    return out


def _pic_url(pic) -> str:
    if isinstance(pic, str):
        if pic.startswith("http"):
            return pic
        if pic:
            return f"https://wx1.sinaimg.cn/large/{pic}.jpg"
        return ""
    if not isinstance(pic, dict):
        return ""
    for key in ("large", "original", "largest", "mw2000", "bmiddle"):
        nested = pic.get(key)
        if isinstance(nested, dict) and str(nested.get("url") or "").startswith("http"):
            return str(nested["url"])
        if isinstance(nested, str) and nested.startswith("http"):
            return nested
    for key in ("url", "url_large"):
        value = pic.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    pid = pic.get("pid")
    if pid:
        return f"https://wx1.sinaimg.cn/large/{pid}.jpg"
    return ""


def weibo_pic_urls(mblog: Dict, *, include_retweet: bool = True) -> List[str]:
    """Public image URLs only. Do not download files."""
    urls: List[str] = []
    seen = set()

    def add(url: str) -> None:
        clean = (url or "").strip()
        if clean.startswith("//"):
            clean = "https:" + clean
        if clean.startswith("http") and clean not in seen:
            seen.add(clean)
            urls.append(clean)

    pics = mblog.get("pics") or []
    if isinstance(pics, dict):
        pics = list(pics.values())
    for pic in pics:
        add(_pic_url(pic))

    pic_infos = mblog.get("pic_infos") or {}
    if isinstance(pic_infos, dict):
        for info in pic_infos.values():
            add(_pic_url(info))

    for pid in mblog.get("pic_ids") or []:
        add(_pic_url(pid))

    page_info = mblog.get("page_info") or {}
    if isinstance(page_info, dict):
        page_pic = page_info.get("page_pic") or {}
        if isinstance(page_pic, dict):
            add(str(page_pic.get("url") or ""))
        elif isinstance(page_pic, str):
            add(page_pic)

    if include_retweet and not urls:
        retweeted = mblog.get("retweeted_status") or {}
        if isinstance(retweeted, dict) and retweeted:
            for url in weibo_pic_urls(retweeted, include_retweet=False):
                add(url)
    return urls[:9]


def weibo_tags(text: str) -> List[str]:
    tags: List[str] = []
    for tag in re.findall(r"#([^#]{1,30})#", text or ""):
        clean = tag.strip()
        if clean and clean not in tags:
            tags.append(clean)
    return tags[:12]
