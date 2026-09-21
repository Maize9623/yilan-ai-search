# -*- coding: utf-8 -*-
import asyncio

from media_platform.bilibili.help import search_video_hits
from tools.video_read import (
    classify_caption_source,
    clip_spoken,
    collect_caption_text,
    parse_clock_duration_ms,
    pick_subtitle_track,
    strip_markup,
    subtitle_body_text,
)


def test_subtitle_body_keeps_full_speech():
    text = subtitle_body_text(
        {
            "body": [
                {"from": 1.0, "to": 2.0, "content": "第一句口播"},
                {"from": 2.0, "to": 3.0, "content": "第二句口播"},
                {"from": 3.0, "to": 4.0, "content": "第二句口播"},
            ]
        }
    )
    assert text == "第一句口播\n第二句口播"


def test_pick_subtitle_prefers_human_chinese():
    track = pick_subtitle_track(
        [
            {"lan": "ai-zh", "subtitle_url": "https://example.com/ai.json"},
            {"lan": "zh-CN", "subtitle_url": "https://example.com/zh.json"},
            {"lan": "en", "subtitle_url": "https://example.com/en.json"},
            {"lan": "zh-CN", "subtitle_url": ""},
        ]
    )
    assert track["subtitle_url"] == "https://example.com/zh.json"


def test_collect_caption_keeps_long_desc():
    desc = "这是一条很长的抖音口播文案，" * 20
    text = collect_caption_text(
        {
            "desc": desc,
            "text_extra": [{"hashtag_name": "十一"}, {"hashtag_name": "测试"}],
        }
    )
    assert desc in text
    assert "十一" in text
    assert classify_caption_source(desc) == "caption"
    assert classify_caption_source("买它") == "caption_short"
    assert classify_caption_source("") == "none"


def test_clip_spoken_marks_cut():
    assert clip_spoken("短", 10) == "短"
    cut = clip_spoken("口播" * 40, 20)
    assert cut.endswith("[原文未完]")
    assert "口播" in cut


def test_search_video_hits_skips_non_video():
    rows = search_video_hits(
        {
            "result": [
                {"result_type": "media_bangumi", "data": [{"aid": 1, "title": "番"}]},
                {
                    "result_type": "video",
                    "data": [{"aid": 11, "title": "A"}, {"aid": 12, "bvid": "BV1"}],
                },
                {"type": "ketang", "aid": 99, "title": "课"},
                {"type": "video", "aid": 21, "title": "<em>春</em>节"},
            ]
        }
    )
    assert [row["aid"] for row in rows] == [11, 12, 21]


def test_strip_markup_and_duration():
    assert strip_markup("春<em class='keyword'>节</em>回家") == "春节回家"
    assert parse_clock_duration_ms("12:34") == (12 * 60 + 34) * 1000
    assert parse_clock_duration_ms(90) == 90000


def test_bilibili_store_keeps_subtitle():
    import store.bilibili as bili

    captured = {}

    class FakeStore:
        async def store_content(self, content_item):
            captured.update(content_item)

    class FakeFactory:
        @staticmethod
        def create_store():
            return FakeStore()

    spoken = "这是官方字幕里的一整段口播。" * 8
    original = bili.BiliStoreFactory
    bili.BiliStoreFactory = FakeFactory
    try:
        asyncio.run(
            bili.update_bilibili_video(
                {
                    "spoken_text": spoken,
                    "text_source": "subtitle",
                    "View": {
                        "aid": 123,
                        "bvid": "BV1xxx",
                        "title": "测试稿",
                        "desc": "简介很短",
                        "pubdate": 1700000000,
                        "duration": 80,
                        "pic": "https://i0.hdslb.com/bfs/a.jpg",
                        "owner": {"mid": 9, "name": "UP主甲"},
                        "stat": {"like": 3, "view": 10},
                    },
                    "Card": {"card": {"fans": 88}},
                }
            )
        )
    finally:
        bili.BiliStoreFactory = original
    assert captured["video_text"] == spoken
    assert captured["text_source"] == "subtitle"
    assert captured["desc"] == "简介很短"
    assert "user_id" not in captured
    assert captured["video_url"] == "https://www.bilibili.com/video/BV1xxx"
