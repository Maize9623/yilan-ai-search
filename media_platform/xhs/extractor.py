# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/extractor.py
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

import json
import re
from typing import Dict, Optional

import humps


class XiaoHongShuExtractor:
    def __init__(self):
        pass

    @staticmethod
    def pick_note_from_state(state, note_id: str) -> Optional[Dict]:
        if not isinstance(state, dict) or not note_id:
            return None
        note = state.get("note")
        if not isinstance(note, dict):
            return None
        detail_map = note.get("noteDetailMap") or note.get("note_detail_map") or {}
        if not isinstance(detail_map, dict):
            return None
        entry = detail_map.get(note_id)
        if not isinstance(entry, dict):
            return None
        raw = entry.get("note")
        if not isinstance(raw, dict):
            return None
        return humps.decamelize(raw)

    def extract_note_detail_from_html(self, note_id: str, html: str) -> Optional[Dict]:
        """Extract note details from HTML

        Args:
            html (str): HTML string

        Returns:
            Dict: Note details dictionary
        """
        if "noteDetailMap" not in html and "note_detail_map" not in html:
            return None

        found = re.findall(r"window.__INITIAL_STATE__=({.*})</script>", html)
        if not found:
            return None
        state = found[0].replace("undefined", '""')
        if state == "{}":
            return None
        return self.pick_note_from_state(json.loads(state), note_id)

    def extract_creator_info_from_html(self, html: str) -> Optional[Dict]:
        """Extract user information from HTML

        Args:
            html (str): HTML string

        Returns:
            Dict: User information dictionary
        """
        match = re.search(
            r"<script>window.__INITIAL_STATE__=(.+)<\/script>", html, re.M
        )
        if match is None:
            return None
        info = json.loads(match.group(1).replace(":undefined", ":null"), strict=False)
        if info is None:
            return None
        return info.get("user").get("userPageData")
