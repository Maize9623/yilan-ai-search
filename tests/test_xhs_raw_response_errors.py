# -*- coding: utf-8 -*-

import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from tenacity import RetryError

from media_platform.xhs.client import XiaoHongShuClient
from media_platform.xhs.exception import DataFetchError, IPBlockError, PlatformAccessError, VerifyBlocked


class FakeAsyncClient:
    def __init__(self, request_impl):
        self.request_impl = request_impl

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, *args, **kwargs):
        return await self.request_impl(*args, **kwargs)


def make_client():
    client = XiaoHongShuClient(
        headers={"Cookie": "web_session=test"},
        playwright_page=object(),
        cookie_dict={},
    )
    client._refresh_proxy_if_expired = AsyncMock()
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429])
async def test_raw_response_rejects_access_http_status(monkeypatch, status_code):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            text="blocked",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )

    with pytest.raises(PlatformAccessError):
        await make_client().request(
            "GET", "https://www.xiaohongshu.com/user/profile/test", return_response=True
        )

    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_exception", "return_response"),
    [
        (300011, PlatformAccessError, True),
        ("300011", PlatformAccessError, False),
        (300012, IPBlockError, True),
        ("300012", IPBlockError, False),
    ],
)
async def test_raw_response_rejects_known_business_block(
    monkeypatch, code, expected_exception, return_response
):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"success": False, "code": code, "msg": "blocked"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )

    with pytest.raises(expected_exception):
        await make_client().request(
            "GET",
            "https://www.xiaohongshu.com/explore/test",
            return_response=return_response,
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_raw_response_keeps_successful_html(monkeypatch):
    async def request_impl(method, url, **kwargs):
        return httpx.Response(
            200,
            text="<html>ok</html>",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )

    result = await make_client().request(
        "GET", "https://www.xiaohongshu.com/explore/test", return_response=True
    )

    assert result == "<html>ok</html>"


@pytest.mark.asyncio
async def test_successful_json_keeps_raw_and_parsed_return_modes(monkeypatch):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"success": True, "data": {"id": "test"}},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )
    client = make_client()

    raw_result = await client.request(
        "GET", "https://www.xiaohongshu.com/explore/test", return_response=True
    )
    parsed_result = await client.request(
        "GET", "https://edith.xiaohongshu.com/api/test"
    )

    assert json.loads(raw_result) == {"success": True, "data": {"id": "test"}}
    assert parsed_result == {"id": "test"}
    assert calls == 2


@pytest.mark.asyncio
async def test_html_detail_does_not_multiply_transport_retries(monkeypatch):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=httpx.Request(method, url))

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )

    with pytest.raises(RetryError):
        await make_client().get_note_by_id_from_html(
            "test", xsec_source="pc_search", xsec_token="token"
        )

    assert calls == 3


@pytest.mark.asyncio
async def test_html_detail_still_retries_parse_failures():
    client = make_client()
    client.request = AsyncMock(return_value="<html>incomplete</html>")
    client._extractor.extract_note_detail_from_html = Mock(
        side_effect=ValueError("incomplete initial state")
    )

    with pytest.raises(RetryError):
        await client.get_note_by_id_from_html(
            "test", xsec_source="pc_search", xsec_token="token"
        )

    assert client.request.await_count == 3
    assert client._extractor.extract_note_detail_from_html.call_count == 3


@pytest.mark.asyncio
async def test_offer_visible_verify_does_not_restore_without_captcha(monkeypatch):
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    page.goto = AsyncMock()
    client = XiaoHongShuClient(
        headers={"Cookie": "web_session=test"},
        playwright_page=page,
        cookie_dict={},
    )
    client._page_has_visible_verify = AsyncMock(return_value=False)
    restore = AsyncMock()
    monkeypatch.setattr("tools.chrome_launch.restore_crawler_window", restore)
    assert await client._offer_visible_verify("https://edith.xiaohongshu.com/api") is False
    restore.assert_not_called()
    page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_offer_visible_verify_restores_when_captcha_visible(monkeypatch):
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=True)
    client = XiaoHongShuClient(
        headers={"Cookie": "web_session=test"},
        playwright_page=page,
        cookie_dict={},
    )
    client._page_has_visible_verify = AsyncMock(return_value=True)
    restore = AsyncMock()
    monkeypatch.setattr("tools.chrome_launch.restore_crawler_window", restore)
    assert await client._offer_visible_verify("https://edith.xiaohongshu.com/api") is True
    restore.assert_awaited()


@pytest.mark.asyncio
async def test_get_note_by_id_from_page_keeps_window_minimized(monkeypatch):
    page = AsyncMock()
    page.url = "https://www.xiaohongshu.com/explore/n1?xsec_token=tok"
    page.goto = AsyncMock()
    page.evaluate = AsyncMock(
        return_value={
            "note": {
                "noteDetailMap": {
                    "n1": {"note": {"noteId": "n1", "title": "从页面收", "desc": "正文"}}
                }
            }
        }
    )
    client = XiaoHongShuClient(
        headers={"Cookie": "web_session=test"},
        playwright_page=page,
        cookie_dict={},
    )
    client._page_has_visible_verify = AsyncMock(return_value=False)
    restore = AsyncMock()
    monkeypatch.setattr("tools.chrome_launch.restore_crawler_window", restore)
    note = await client.get_note_by_id_from_page("n1", "pc_search", "tok")
    assert note["note_id"] == "n1"
    restore.assert_not_called()
    page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_settle_if_idle_skips_visible_verify(monkeypatch):
    from types import SimpleNamespace

    from tools.chrome_launch import settle_if_idle

    page = SimpleNamespace(context=object())
    settle = AsyncMock()
    monkeypatch.setattr(
        "tools.login_ready.page_needs_verify", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("tools.chrome_launch.settle_crawler_window", settle)
    await settle_if_idle(page, "小红书")
    settle.assert_not_called()


@pytest.mark.asyncio
async def test_settle_if_idle_minimizes_when_page_is_idle(monkeypatch):
    from types import SimpleNamespace

    from tools.chrome_launch import settle_if_idle

    page = SimpleNamespace(context=object())
    settle = AsyncMock()
    monkeypatch.setattr(
        "tools.login_ready.page_needs_verify", AsyncMock(return_value=False)
    )
    monkeypatch.setattr("tools.chrome_launch.settle_crawler_window", settle)
    await settle_if_idle(page, "小红书")
    settle.assert_awaited()


@pytest.mark.asyncio
async def test_xhs_461_waits_then_retries_after_user_clears(monkeypatch):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                461,
                text="verify",
                headers={"Verifytype": "124", "Verifyuuid": "abc"},
                request=httpx.Request(method, url),
            )
        return httpx.Response(
            200,
            json={"success": True, "data": {"items": [{"id": "n1"}]}},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )
    client = make_client()
    data = await client.request("POST", "https://edith.xiaohongshu.com/api/sns/web/v1/feed")
    assert data == {"items": [{"id": "n1"}]}
    assert client.verify_blocked is False
    assert calls == 2


@pytest.mark.asyncio
async def test_xhs_461_times_out_as_verify_blocked(monkeypatch):
    calls = 0

    async def request_impl(method, url, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            461,
            text="verify",
            headers={"Verifytype": "124", "Verifyuuid": "abc"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )
    monkeypatch.setattr("media_platform.xhs.client.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("tools.login_ready.LOGIN_WAIT_SECONDS", 0)
    client = make_client()
    with pytest.raises((DataFetchError, VerifyBlocked), match="验证挡住"):
        await client.request("POST", "https://edith.xiaohongshu.com/api/sns/web/v1/feed")
    assert client.verify_blocked is True
    assert calls >= 2
