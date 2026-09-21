# -*- coding: utf-8 -*-
from media_platform.tieba.help import search_keywords as tieba_keywords
from media_platform.tieba.help import tieba_pic_urls
from media_platform.zhihu.help import ZhihuExtractor
from media_platform.zhihu.help import search_keywords as zhihu_keywords
from board.server import item_kind, normalize_item, PLATFORMS


def test_board_platforms_include_kuaishou():
    codes = [code for code, _label in PLATFORMS]
    assert codes[:8] == ["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu", "mp"]
    assert "gnews" in codes and "bsky" in codes and "reddit" in codes


def test_keywords_keep_long_sentence():
    query = "20岁的女孩喜欢收到什么生日礼物，2000块钱左右"
    assert tieba_keywords(query) == [query]
    assert zhihu_keywords(query) == [query]


def test_tieba_pic_urls_from_api_items():
    content = [
        {"type": "0", "text": "生日礼物推荐"},
        {"type": "3", "src": "https://imgsa.baidu.com/forum/pic/item/abc.jpg"},
        {"type": "3", "src": "https://gsp0.bdstatic.com/tb/editor/images/client/image_emoticon22.png"},
    ]
    assert tieba_pic_urls(content) == ["https://imgsa.baidu.com/forum/pic/item/abc.jpg"]


def test_zhihu_answer_uses_question_title_and_images():
    extractor = ZhihuExtractor()
    note = extractor._extract_answer_content(
        {
            "id": "99",
            "type": "answer",
            "content": '<p>先买实用的</p><img src="https://pic1.zhimg.com/v2-gift.jpg">',
            "excerpt": "先买实用的",
            "question": {"id": "1", "title": "20岁生日送什么"},
            "author": {"id": "u1", "name": "答主"},
            "voteup_count": 12,
            "comment_count": 3,
            "created_time": 1710000000,
        }
    )
    assert note.title == "20岁生日送什么"
    assert "先买实用的" in note.content_text
    assert "zhimg.com" in note.image_list


def test_zhihu_folds_retina_and_wide_copies():
    from media_platform.zhihu.help import fold_related_images

    urls = fold_related_images(
        [
            "https://pic1.zhimg.com/v2-aaaa_r.jpg",
            "https://pic1.zhimg.com/v2-aaaa_1440w.jpg",
            "https://pic2.zhimg.com/v2-bbbb_r.jpg",
        ]
    )
    assert urls == [
        "https://pic1.zhimg.com/v2-aaaa_1440w.jpg",
        "https://pic2.zhimg.com/v2-bbbb_r.jpg",
    ]


def test_board_reads_tieba_and_zhihu_text():
    tieba = normalize_item(
        "tieba",
        {
            "note_id": "111",
            "title": "吧里讨论礼物",
            "desc": "楼主写了很长的第一层正文。",
            "note_url": "https://tieba.baidu.com/p/111",
            "user_nickname": "吧友",
            "total_replay_num": 8,
            "image_list": "https://imgsa.baidu.com/forum/pic/item/abc.jpg",
        },
        0,
    )
    zhihu = normalize_item(
        "zhihu",
        {
            "content_id": "99",
            "content_type": "answer",
            "title": "20岁生日送什么",
            "content_text": "先买实用的，再看预算。",
            "content_url": "https://www.zhihu.com/question/1/answer/99",
            "user_nickname": "答主",
            "voteup_count": 12,
            "created_time": 1710000000,
        },
        0,
    )
    assert item_kind("ks", {}) == "video"
    assert item_kind("tieba", {}) == "note"
    assert item_kind("zhihu", {"content_type": "answer"}) == "note"
    assert item_kind("zhihu", {"content_type": "zvideo"}) == "video"
    assert "很长的第一层正文" in tieba["text"]
    assert str(tieba["comment_count"]) == "8"
    assert tieba["images"]
    assert "先买实用的" in zhihu["text"]
    assert zhihu["title"] == "20岁生日送什么"
    pictured = normalize_item(
        "zhihu",
        {
            "content_id": "100",
            "content_type": "answer",
            "title": "带图回答",
            "content_text": "先看铰链。",
            "content_url": "https://www.zhihu.com/question/1/answer/100",
            "image_list": "https://pic1.zhimg.com/v2-a.jpg,https://pic2.zhimg.com/v2-b.jpg",
        },
        1,
    )
    assert pictured["images"][:2] == [
        "https://pic1.zhimg.com/v2-a.jpg",
        "https://pic2.zhimg.com/v2-b.jpg",
    ]
