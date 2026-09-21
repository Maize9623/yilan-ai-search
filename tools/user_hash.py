# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# 本文件为 MediaCrawler 教学版的一部分。
# 出于教学与防骚扰定位，爬取结果中不保留任何可定位到真人的用户个人信息
# （用户 ID、IP 归属地、头像、主页链接、签名、性别等一律不采集；
# 昵称保留但做中间脱敏）。本模块提供匿名化与脱敏工具。
# 本地看板可设 BOARD_KEEP_CREATORS=1，只在本机智搜里留下原名和粉丝数。
import hashlib
import os


def anonymize_user_id(user_id) -> str:
    """把原始用户 ID 转成匿名哈希，用于内容/评论记录的创作者分组，
    不暴露真实身份。返回 sha256 截断 16 位的十六进制串。"""
    if user_id is None:
        return ""
    s = str(user_id).strip()
    if not s:
        return ""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def mask_nickname(name) -> str:
    """昵称中间脱敏：首尾各保留 1 字，中间替换为星号。
    - 长度 <= 1：返回 "*"
    - 长度 == 2：首字 + "*"
    - 长度 >= 3：首字 + "***" + 尾字
    这样既保留教学分析所需的内容归属语义，又无法据昵称定位到真人。
    """
    if name is None:
        return ""
    s = str(name)
    if len(s) <= 1:
        return "*"
    if len(s) == 2:
        return s[0] + "*"
    return s[0] + "***" + s[-1]


def keep_creators() -> bool:
    return os.getenv("BOARD_KEEP_CREATORS", "").strip() == "1"


def store_nickname(name) -> str:
    if keep_creators():
        return "" if name is None else str(name).strip()
    return mask_nickname(name)


def pick_fans(*objs) -> str:
    keys = (
        "fans",
        "fans_count",
        "fan_count",
        "fansCount",
        "fanCount",
        "follower_count",
        "followers_count",
        "followerCount",
        "followers",
        "fans_num",
        "follower",
    )
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for key in keys:
            value = obj.get(key)
            if value in (None, "", 0, "0"):
                continue
            text = str(value).strip()
            if text:
                return text
        for nested_key in ("interact_info", "statistics", "stat", "card", "basicInfo"):
            nested = obj.get(nested_key)
            if not isinstance(nested, dict):
                continue
            for key in keys:
                value = nested.get(key)
                if value in (None, "", 0, "0"):
                    continue
                text = str(value).strip()
                if text:
                    return text
        for row in obj.get("interactions") or []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type") or row.get("name") or "")
            if kind in {"fans", "fan", "follower", "粉丝"}:
                text = str(row.get("count") or row.get("value") or "").strip()
                if text:
                    return text
    return ""
