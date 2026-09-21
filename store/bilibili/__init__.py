# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/store/bilibili/__init__.py
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
# @Time    : 2024/1/14 19:34
# @Desc    :

from typing import Dict, List

import config
from var import source_keyword_var
from tools import utils
from tools.user_hash import anonymize_user_id, pick_fans, store_nickname
from tools.video_read import parse_clock_duration_ms, strip_markup

from ._store_impl import *
from .bilibilli_store_media import *


class BiliStoreFactory:
    STORES = {
        "csv": BiliCsvStoreImplement,
        "db": BiliDbStoreImplement,
        "postgres": BiliDbStoreImplement,
        "json": BiliJsonStoreImplement,
        "jsonl": BiliJsonlStoreImplement,
        "sqlite": BiliSqliteStoreImplement,
        "mongodb": BiliMongoStoreImplement,
        "excel": BiliExcelStoreImplement,
    }

    @staticmethod
    def create_store() -> AbstractStore:
        store_class = BiliStoreFactory.STORES.get(config.SAVE_DATA_OPTION)
        if not store_class:
            raise ValueError("[BiliStoreFactory.create_store] Invalid save option only supported csv or db or json or sqlite or mongodb or excel ...")
        return store_class()


async def update_bilibili_video(video_item: Dict):
    video_item_view: Dict = video_item.get("View") or {}
    video_user_info: Dict = video_item_view.get("owner") or {}
    video_item_stat: Dict = video_item_view.get("stat") or {}
    video_id = str(video_item_view.get("aid") or "")
    if not video_id:
        return
    desc = str(video_item_view.get("desc") or "")
    spoken = str(video_item.get("spoken_text") or "").strip()
    source = str(video_item.get("text_source") or "").strip()
    if not spoken:
        spoken = desc.strip()
        source = "desc" if spoken else "none"
    elif not source:
        source = "subtitle"
    bvid = str(video_item_view.get("bvid") or "").strip()
    save_content_item = {
        "video_id": video_id,
        "video_type": "video",
        "title": video_item_view.get("title", ""),
        "desc": desc,
        "create_time": video_item_view.get("pubdate"),
        "creator_hash": anonymize_user_id(video_user_info.get("mid")),  # 创作者匿名哈希(不存原始 mid)
        "nickname": store_nickname(video_user_info.get("name")),
        "fans": pick_fans(
            video_user_info,
            video_item.get("Card"),
            (video_item.get("Card") or {}).get("card"),
        ),
        "liked_count": str(video_item_stat.get("like", "")),
        "disliked_count": str(video_item_stat.get("dislike", "")),
        "video_play_count": str(video_item_stat.get("view", "")),
        "video_favorite_count": str(video_item_stat.get("favorite", "")),
        "video_share_count": str(video_item_stat.get("share", "")),
        "video_coin_count": str(video_item_stat.get("coin", "")),
        "video_danmaku": str(video_item_stat.get("danmaku", "")),
        "video_comment": str(video_item_stat.get("reply", "")),
        "last_modify_ts": utils.get_current_timestamp(),
        "video_url": (
            f"https://www.bilibili.com/video/{bvid}"
            if bvid
            else f"https://www.bilibili.com/video/av{video_id}"
        ),
        "video_cover_url": video_item_view.get("pic", ""),
        "duration_ms": int(video_item_view.get("duration") or 0) * 1000,
        "video_text": spoken,
        "text_source": source if spoken else "none",
        "source_keyword": source_keyword_var.get(),
    }
    utils.logger.info(f"[store.bilibili.update_bilibili_video] bilibili video id:{video_id}, title:{save_content_item.get('title')}")
    await BiliStoreFactory.create_store().store_content(content_item=save_content_item)


async def update_bilibili_search_hit(hit: Dict):
    aid = str((hit or {}).get("aid") or (hit or {}).get("id") or "")
    if not aid:
        return
    bvid = str(hit.get("bvid") or "").strip()
    pic = str(hit.get("pic") or "")
    if pic.startswith("//"):
        pic = "https:" + pic
    desc = strip_markup(hit.get("description") or hit.get("desc") or "")
    title = strip_markup(hit.get("title") or "")
    save_content_item = {
        "video_id": aid,
        "video_type": "video",
        "title": title or desc,
        "desc": desc,
        "create_time": hit.get("pubdate") or hit.get("pubtime"),
        "creator_hash": anonymize_user_id(hit.get("mid")),
        "nickname": store_nickname(hit.get("author") or hit.get("uname")),
        "fans": pick_fans(hit),
        "liked_count": str(hit.get("like") or hit.get("like_count") or ""),
        "disliked_count": "",
        "video_play_count": str(hit.get("play") or hit.get("video_review") or ""),
        "video_favorite_count": str(hit.get("favorites") or ""),
        "video_share_count": "",
        "video_coin_count": "",
        "video_danmaku": str(hit.get("review") or hit.get("danmaku") or ""),
        "video_comment": str(hit.get("review") or ""),
        "last_modify_ts": utils.get_current_timestamp(),
        "video_url": (
            f"https://www.bilibili.com/video/{bvid}"
            if bvid
            else f"https://www.bilibili.com/video/av{aid}"
        ),
        "video_cover_url": pic,
        "duration_ms": parse_clock_duration_ms(hit.get("duration") or hit.get("length")),
        "video_text": desc,
        "text_source": "desc" if desc else "none",
        "source_keyword": source_keyword_var.get(),
    }
    utils.logger.info(
        f"[store.bilibili.update_bilibili_search_hit] fallback video id:{aid}, title:{title}"
    )
    await BiliStoreFactory.create_store().store_content(content_item=save_content_item)


async def update_up_info(video_item: Dict):
    # 教学版：UP 主个人资料(昵称/性别/签名/头像/粉丝数等)不再落库，防骚扰。
    return


async def batch_update_bilibili_video_comments(video_id: str, comments: List[Dict]):
    if not comments:
        return
    for comment_item in comments:
        await update_bilibili_video_comment(video_id, comment_item)


async def update_bilibili_video_comment(video_id: str, comment_item: Dict):
    comment_id = str(comment_item.get("rpid"))
    parent_comment_id = str(comment_item.get("parent", 0))
    content: Dict = comment_item.get("content")
    user_info: Dict = comment_item.get("member")
    like_count: int = comment_item.get("like", 0)
    save_comment_item = {
        "comment_id": comment_id,
        "parent_comment_id": parent_comment_id,
        "create_time": comment_item.get("ctime"),
        "video_id": str(video_id),
        "content": content.get("message"),
        "creator_hash": anonymize_user_id(user_info.get("mid")),  # 创作者匿名哈希(不存原始 mid)
        "nickname": store_nickname(user_info.get("uname")),
        "sub_comment_count": str(comment_item.get("rcount", 0)),
        "like_count": like_count,
        "last_modify_ts": utils.get_current_timestamp(),
    }
    utils.logger.info(f"[store.bilibili.update_bilibili_video_comment] Bilibili video comment: {comment_id}, content: {save_comment_item.get('content')}")
    await BiliStoreFactory.create_store().store_comment(comment_item=save_comment_item)


async def store_video(aid, video_content, extension_file_name):
    """
    video video storage implementation
    Args:
        aid:
        video_content:
        extension_file_name:
    """
    await BilibiliVideo().store_video({
        "aid": aid,
        "video_content": video_content,
        "extension_file_name": extension_file_name,
    })


async def batch_update_bilibili_creator_fans(creator_info: Dict, fans_list: List[Dict]):
    # 教学版：不再采集/存储粉丝列表(其他用户的个人信息)，防骚扰。
    return


async def batch_update_bilibili_creator_followings(creator_info: Dict, followings_list: List[Dict]):
    # 教学版：不再采集/存储关注列表(其他用户的个人信息)，防骚扰。
    return


async def batch_update_bilibili_creator_dynamics(creator_info: Dict, dynamics_list: List[Dict]):
    if not dynamics_list:
        return
    for dynamic_item in dynamics_list:
        dynamic_id: str = dynamic_item["id_str"]
        dynamic_text: str = ""
        if dynamic_item["modules"]["module_dynamic"].get("desc"):
            dynamic_text = dynamic_item["modules"]["module_dynamic"]["desc"]["text"]
        dynamic_type: str = dynamic_item["type"].split("_")[-1]
        dynamic_pub_ts: str = dynamic_item["modules"]["module_author"]["pub_ts"]
        dynamic_stat: Dict = dynamic_item["modules"]["module_stat"]
        dynamic_comment: int = dynamic_stat["comment"]["count"]
        dynamic_forward: int = dynamic_stat["forward"]["count"]
        dynamic_like: int = dynamic_stat["like"]["count"]
        dynamic_info: Dict = {
            "dynamic_id": dynamic_id,
            "text": dynamic_text,
            "type": dynamic_type,
            "pub_ts": dynamic_pub_ts,
            "total_comments": dynamic_comment,
            "total_forwards": dynamic_forward,
            "total_liked": dynamic_like,
        }
        await update_bilibili_creator_dynamic(creator_info=creator_info, dynamic_info=dynamic_info)


async def update_bilibili_creator_contact(creator_info: Dict, fan_info: Dict):
    # 教学版：UP-粉丝关系表已移除，不再存储联系人信息。
    return


async def update_bilibili_creator_dynamic(creator_info: Dict, dynamic_info: Dict):
    save_dynamic_item = {
        "dynamic_id": dynamic_info["dynamic_id"],
        "creator_hash": anonymize_user_id(creator_info.get("id")),  # 创作者匿名哈希(不存原始 ID)
        "user_name": store_nickname(creator_info.get("name")),
        "text": dynamic_info["text"],
        "type": dynamic_info["type"],
        "pub_ts": dynamic_info["pub_ts"],
        "total_comments": dynamic_info["total_comments"],
        "total_forwards": dynamic_info["total_forwards"],
        "total_liked": dynamic_info["total_liked"],
        "last_modify_ts": utils.get_current_timestamp(),
    }

    await BiliStoreFactory.create_store().store_dynamic(dynamic_item=save_dynamic_item)
