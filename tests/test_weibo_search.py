# -*- coding: utf-8 -*-
from media_platform.weibo.help import (
    card_type_summary,
    filter_search_result_card,
    mids_from_html,
    notes_from_search_payload,
    query_fallbacks,
    search_keywords,
    should_harvest_search,
    weibo_search_queries,
)


def test_search_keywords_keeps_long_sentence():
    query = "20岁的女孩喜欢收到什么生日礼物，2000块钱左右"
    assert search_keywords(query) == [query]
    assert search_keywords("笔记本, 鼠标, 键盘") == ["笔记本", "鼠标", "键盘"]
    assert search_keywords("hello, world this is long") == ["hello, world this is long"]


def test_query_fallbacks_prefer_compact_topic():
    query = "20岁的女孩喜欢收到什么生日礼物，2000块钱左右"
    fallbacks = query_fallbacks(query)
    assert fallbacks[0] == "生日礼物"
    assert "生日礼物 2000" in fallbacks
    assert query in fallbacks
    assert "2000块钱左右" not in fallbacks


def test_weibo_search_queries_shorten_long_guide():
    query = "豆包手机助手选购指南"
    queries = weibo_search_queries(query)
    assert queries[0] == query
    assert "助手选购指南" in queries
    assert "豆包 手机" in queries


def test_filter_keeps_nested_mblog_without_card_type_9():
    cards = [
        {
            "card_type": 11,
            "card_group": [
                {
                    "card_type": 9,
                    "mblog": {"id": "1111111111111111", "text": "礼物清单"},
                },
                {
                    "mblog": {"id": "2222222222222222", "text": "2000预算"},
                },
            ],
        },
        {
            "id": "3333333333333333",
            "text": "直出微博",
            "user": {"id": 1, "screen_name": "测"},
        },
    ]
    notes = filter_search_result_card(cards)
    assert [item["mblog"]["id"] for item in notes] == [
        "1111111111111111",
        "2222222222222222",
        "3333333333333333",
    ]


def test_notes_from_search_payload_reads_items_and_wrapped_data():
    payload = {
        "ok": 1,
        "data": {
            "items": [
                {"mblog": {"id": "4444444444444444", "text": "热门"}},
            ]
        },
    }
    notes = notes_from_search_payload(payload)
    assert notes[0]["mblog"]["id"] == "4444444444444444"
    assert "n=1" in card_type_summary({"cards": [{"card_type": 11}]})


def test_should_not_harvest_user_topic_only_page():
    payload = {"cards": [{"card_type": 4}, {"card_type": 4}, {"card_type": 11}]}
    assert should_harvest_search(payload) is False
    assert should_harvest_search({"cards": []}) is True
    assert "n=3" in card_type_summary(payload)


def test_mids_from_html_prefers_detail_links():
    html = """
    <a href="/detail/5555555555555555">看</a>
    <script>var x = {"id":"6666666666666666","mid":"6666666666666666"}</script>
    """
    assert mids_from_html(html)[:2] == ["5555555555555555", "6666666666666666"]
