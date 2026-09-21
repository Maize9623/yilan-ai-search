# -*- coding: utf-8 -*-
from board.hot import (
    DEFAULT_FRONT,
    HIDDEN_DEFAULTS,
    catalog,
    fallback_keyword,
    normalize_list,
    parse_baidu,
    parse_bilibili,
    parse_douyin,
    parse_thepaper,
    parse_tieba,
    parse_toutiao,
    parse_weibo,
    parse_zhihu,
    resolve_id,
    source_of,
)


def test_default_front_skips_niche_boards():
    hidden = set(HIDDEN_DEFAULTS)
    assert not hidden.intersection(DEFAULT_FRONT)
    for sid in ("coolapk", "hupu", "nowcoder", "steam", "chongbuluo-hot"):
        assert source_of(sid)
        assert sid not in DEFAULT_FRONT


def test_catalog_exposes_more_and_hottest():
    data = catalog()
    ids = {row["id"] for row in data["sources"]}
    assert set(DEFAULT_FRONT) <= ids
    assert data["defaults"] == list(DEFAULT_FRONT)
    assert "coolapk" in ids
    labels = [col["label"] for col in data["columns"]]
    assert labels == ["国内", "国际", "科技", "财经", "体育"]


def test_resolve_parent_aliases():
    assert resolve_id("bilibili") == "bilibili-hot-search"
    assert resolve_id("weibo") == "weibo"
    assert source_of("bilibili")["id"] == "bilibili-hot-search"
    assert source_of("no-such-board") is None


def test_fallback_keyword_strips_hot_noise():
    assert fallback_keyword("#iPhone Duo# 热搜") == "iPhone Duo"
    assert fallback_keyword("【实时热搜】南医大坠楼案") == "南医大坠楼案"
    assert len(fallback_keyword("词" * 100)) == 80


def test_normalize_list_keeps_title_and_url():
    row = normalize_list(
        "weibo",
        {
            "updatedTime": 1700000000000,
            "items": [
                {"id": "1", "title": "  举手式睡姿  ", "url": "https://s.weibo.com/weibo?q=1"},
                {"title": "", "url": "https://example.com"},
            ],
        },
    )
    assert row["ok"] is True
    assert row["name"] == "微博"
    assert row["items"] == [
        {"id": "1", "title": "举手式睡姿", "url": "https://s.weibo.com/weibo?q=1"}
    ]


def test_direct_parsers_read_public_shapes():
    weibo = parse_weibo({"data": {"band_list": [{"word": "举手式睡姿", "word_scheme": "举手式睡姿"}]}})
    assert weibo[0]["title"] == "举手式睡姿"
    assert "weibo.com" in weibo[0]["url"]
    baidu = parse_baidu({"data": {"cards": [{"content": [{"isTop": True, "word": "广告"}, {"word": "高市内阁", "rawUrl": "https://www.baidu.com/s?wd=1"}]}]}})
    assert [row["title"] for row in baidu] == ["高市内阁"]
    zhihu = parse_zhihu({"data": [{"target": {"title_area": {"text": "加息"}, "link": {"url": "https://www.zhihu.com/question/1"}}}]})
    assert zhihu[0]["url"].endswith("/1")
    douyin = parse_douyin({"word_list": [{"word": "美联储加息", "sentence_id": "abc"}]})
    assert douyin[0]["title"] == "美联储加息"
    bili = parse_bilibili({"list": [{"show_name": "热搜词", "hot_id": 9}]})
    assert "bilibili.com" in bili[0]["url"]
    toutiao = parse_toutiao({"data": [{"Title": "机顶盒", "Url": "https://www.toutiao.com/x", "ClusterId": 1}]})
    assert toutiao[0]["title"] == "机顶盒"
    tieba = parse_tieba({"data": {"bang_topic": {"topic_list": [{"topic_id": "2", "topic_name": "热议"}]}}})
    assert tieba[0]["title"] == "热议"
    paper = parse_thepaper({"data": {"hotNews": [{"name": "澎湃一条", "contId": "123"}]}})
    assert "123" in paper[0]["url"]


def test_unknown_source_is_rejected():
    row = normalize_list("not-a-source", {}, "没有这个热搜源")
    assert row["ok"] is False
    assert row["items"] == []
    assert "没有" in row["error"]
