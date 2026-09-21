# -*- coding: utf-8 -*-
"""
回归测试(小红书 xhs):访问受限异常不能击穿到 asyncio.gather。

背景:
IPBlockError / PlatformAccessError 被加入 request() 的 retry_if_not_exception_type 之后,
tenacity 会直接重抛原异常而不再包装成 RetryError。core 层原先只 catch
RetryError / NoteNotFoundError / DataFetchError / KeyError,导致单条笔记被限流
就会让整批 gather 抛出,同批已抓到但未入库的数据全部丢失。

覆盖:
1. 笔记详情任务遇到 IPBlockError / PlatformAccessError 时返回 None 并跳过。
2. 批量 gather 不会因为其中一条被限流而整体失败。
3. 创作者主页被限流时跳过该创作者,而不是中断整个任务。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import config
from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.xhs.exception import IPBlockError, PlatformAccessError


def make_crawler(xhs_client):
    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.xhs_client = xhs_client
    return crawler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        IPBlockError("Network connection error"),
        PlatformAccessError("XHS request blocked with HTTP 403"),
    ],
)
async def test_note_detail_task_skips_access_error(error):
    xhs_client = AsyncMock()
    xhs_client.get_note_by_id.side_effect = error
    xhs_client.get_note_by_id_from_html.side_effect = AssertionError(
        "被限流后不应再走 HTML 兜底"
    )

    result = await make_crawler(xhs_client).get_note_detail_async_task(
        note_id="n1",
        xsec_source="pc_search",
        xsec_token="token",
        semaphore=asyncio.Semaphore(1),
    )

    assert result is None


@pytest.mark.asyncio
async def test_gather_survives_single_blocked_note():
    """一条笔记被限流不能让同批次其他笔记的结果一起丢掉。"""
    xhs_client = AsyncMock()

    async def get_note_by_id(note_id, xsec_source, xsec_token):
        if note_id == "blocked":
            raise PlatformAccessError("XHS account security restriction, code: 300011")
        return {"note_id": note_id}

    xhs_client.get_note_by_id.side_effect = get_note_by_id
    crawler = make_crawler(xhs_client)

    semaphore = asyncio.Semaphore(2)
    results = await asyncio.gather(
        *[
            crawler.get_note_detail_async_task(
                note_id=note_id,
                xsec_source="pc_search",
                xsec_token="token",
                semaphore=semaphore,
            )
            for note_id in ("ok1", "blocked", "ok2")
        ]
    )

    assert [r.get("note_id") if r else None for r in results] == ["ok1", None, "ok2"]


@pytest.mark.asyncio
async def test_creator_flow_skips_blocked_creator(monkeypatch):
    """创作者主页 403 时跳过该创作者,不中断整个采集任务。"""
    monkeypatch.setattr(
        config,
        "XHS_CREATOR_ID_LIST",
        [
            "https://www.xiaohongshu.com/user/profile/blocked?xsec_token=a&xsec_source=pc_feed",
            "https://www.xiaohongshu.com/user/profile/ok?xsec_token=b&xsec_source=pc_feed",
        ],
        raising=False,
    )

    crawled_user_ids = []
    xhs_client = AsyncMock()

    async def get_creator_info(user_id, xsec_token, xsec_source):
        if user_id == "blocked":
            raise PlatformAccessError("XHS request blocked with HTTP 403")
        return {"user_id": user_id}

    async def get_all_notes_by_creator(user_id, **kwargs):
        crawled_user_ids.append(user_id)
        return []

    xhs_client.get_creator_info.side_effect = get_creator_info
    xhs_client.get_all_notes_by_creator.side_effect = get_all_notes_by_creator

    crawler = make_crawler(xhs_client)
    crawler.batch_get_note_comments = AsyncMock()
    monkeypatch.setattr(
        "media_platform.xhs.core.xhs_store.save_creator", AsyncMock(), raising=False
    )

    await crawler.get_creators_and_notes()

    # 被限流的创作者整体跳过,后面的创作者照常采集
    assert crawled_user_ids == ["ok"]


@pytest.mark.asyncio
async def test_settle_unless_verify_runs_even_if_blocked(monkeypatch):
    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.context_page = object()
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.verify_blocked = True
    settle = AsyncMock()
    monkeypatch.setattr("media_platform.xhs.core.settle_if_idle", settle)
    await crawler._settle_unless_verify()
    settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_settles_after_first_saved_item(monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", "python", raising=False)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 1, raising=False)
    monkeypatch.setattr(config, "START_PAGE", 1, raising=False)
    monkeypatch.setattr(config, "SORT_TYPE", "", raising=False)
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 1, raising=False)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0, raising=False)
    monkeypatch.setattr(config, "ENABLE_GET_COMMENTS", False, raising=False)
    monkeypatch.setattr(config, "ENABLE_GET_MEIDAS", False, raising=False)

    xhs_client = AsyncMock()
    xhs_client.verify_blocked = True
    xhs_client.detail_api_blocked = False
    xhs_client.get_note_by_keyword = AsyncMock(
        return_value={
            "items": [
                {
                    "id": "n1",
                    "model_type": "note",
                    "xsec_source": "pc_search",
                    "xsec_token": "tok",
                }
            ]
        }
    )
    xhs_client.get_note_by_id = AsyncMock(return_value={"note_id": "n1", "title": "T"})
    crawler = make_crawler(xhs_client)
    crawler.context_page = object()
    crawler._settle_unless_verify = AsyncMock()
    crawler.get_notice_media = AsyncMock()
    crawler.batch_get_note_comments = AsyncMock()
    monkeypatch.setattr("media_platform.xhs.core.xhs_store.update_xhs_note", AsyncMock())
    monkeypatch.setattr("media_platform.xhs.core.mark_ladder_done", lambda *_a, **_k: None)

    await crawler.search()
    assert crawler._settle_unless_verify.await_count >= 1


def test_note_from_search_item_keeps_list_card():
    from media_platform.xhs.help import note_from_search_item

    note = note_from_search_item(
        {
            "id": "abc123",
            "xsec_token": "tok",
            "xsec_source": "pc_search",
            "note_card": {
                "display_title": "Python入门学习规划",
                "desc": "路线图",
                "type": "normal",
                "user": {"nickname": "甲"},
                "cover": {"url_default": "https://img.example/a.jpg"},
            },
        }
    )
    assert note["note_id"] == "abc123"
    assert note["title"] == "Python入门学习规划"
    assert note["image_list"][0]["url"] == "https://img.example/a.jpg"
    assert note_from_search_item({"id": "x", "note_card": {}}) is None

    sparse = note_from_search_item(
        {
            "id": "def456",
            "note_card": {
                "display_title": "只有封面结构",
                "image_list": [
                    {"url": None, "url_default": None, "info_list": [{"url": "https://img.example/b.jpg"}]}
                ],
            },
        }
    )
    assert sparse["image_list"][0]["url"] == "https://img.example/b.jpg"


def test_update_xhs_note_accepts_null_image_urls():
    import asyncio
    import store.xhs as xs

    captured = {}

    class FakeStore:
        async def store_content(self, content_item):
            captured.update(content_item)

    orig = xs.XhsStoreFactory.create_store
    xs.XhsStoreFactory.create_store = staticmethod(lambda: FakeStore())
    try:
        asyncio.run(
            xs.update_xhs_note(
                {
                    "note_id": "n1",
                    "type": "normal",
                    "title": "列表卡",
                    "desc": "没有详情",
                    "time": 1,
                    "user": {"user_id": "u1", "nickname": "甲"},
                    "interact_info": {},
                    "image_list": [
                        {"url": None, "url_default": None, "info_list": [{"url": "https://img.example/c.jpg"}]},
                    ],
                    "tag_list": [],
                    "xsec_token": "tok",
                }
            )
        )
    finally:
        xs.XhsStoreFactory.create_store = orig
    assert captured["note_id"] == "n1"
    assert captured["image_list"] == "https://img.example/c.jpg"


def test_pick_note_from_state_reads_camel_case_map():
    from media_platform.xhs.extractor import XiaoHongShuExtractor

    note = XiaoHongShuExtractor.pick_note_from_state(
        {
            "note": {
                "noteDetailMap": {
                    "n1": {"note": {"noteId": "n1", "title": "从页面收", "desc": "正文"}}
                }
            }
        },
        "n1",
    )
    assert note["note_id"] == "n1"
    assert note["title"] == "从页面收"
