# -*- coding: utf-8 -*-
import json
import tempfile
from pathlib import Path

from board.server import SearchOptions, resume_job_dir, split_resume_platforms, split_supplement_platforms
from board import server as board_server
from board.export_report import board_to_markdown, safe_filename


def test_safe_job_dir_rejects_traversal():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        old = board_server.RUNS_DIR
        board_server.RUNS_DIR = root
        try:
            (root / "20260915-ok").mkdir()
            assert board_server.safe_job_dir("20260915-ok") == (root / "20260915-ok").resolve()
            assert board_server.safe_job_dir("../etc") is None
            assert board_server.safe_job_dir("a/b") is None
        finally:
            board_server.RUNS_DIR = old


def test_resume_requires_same_query():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        old = board_server.RUNS_DIR
        board_server.RUNS_DIR = root
        try:
            job = root / "job-1"
            job.mkdir()
            (job / "meta.json").write_text(
                json.dumps({"query": "2026笔记本", "job_id": "job-1"}),
                encoding="utf-8",
            )
            assert resume_job_dir("job-1", "2026笔记本") == job.resolve()
            assert resume_job_dir("job-1", "别的词") is None
        finally:
            board_server.RUNS_DIR = old


def test_split_resume_skips_written_platforms():
    with tempfile.TemporaryDirectory() as raw:
        job = Path(raw) / "job-2"
        dy = job / "dy" / "jsonl"
        dy.mkdir(parents=True)
        (dy / "search_contents_1.jsonl").write_text(
            json.dumps(
                {
                    "aweme_id": "1",
                    "title": "已有抖音",
                    "desc": "文案",
                    "aweme_url": "https://www.douyin.com/video/1",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        options = SearchOptions(
            max_notes=10,
            comments=False,
            sub_comments=False,
            max_comments=10,
            eleven=True,
            notes={"dy": 10, "bili": 10},
        )
        kept, todo = split_resume_platforms(job, ["dy", "bili"], options)
        assert [code for code, _rows in kept] == ["dy"]
        assert kept[0][1][0]["id"] == "dy:1"
        assert todo == ["bili"]


def test_split_supplement_keeps_other_platforms_and_reruns_selected():
    with tempfile.TemporaryDirectory() as raw:
        job = Path(raw) / "job-3"
        dy = job / "dy" / "jsonl"
        dy.mkdir(parents=True)
        (dy / "search_contents_1.jsonl").write_text(
            json.dumps(
                {
                    "aweme_id": "1",
                    "title": "已有抖音",
                    "desc": "文案",
                    "aweme_url": "https://www.douyin.com/video/1",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        kept, todo = split_supplement_platforms(job, ["bili"])
        assert [code for code, _rows in kept] == ["dy"]
        assert todo == ["bili"]
        kept, todo = split_supplement_platforms(job, ["dy"])
        assert kept == []
        assert todo == ["dy"]


def test_board_to_markdown_has_picks_and_sections():
    text = board_to_markdown(
        {
            "intent": {"mode": "comprehensive", "label": "综合研判"},
            "report": {
                "title": "测试报告",
                "deck": "封面一句话",
                "overview": "总述。",
                "picks": [{"name": "机型A", "verdict": "go", "reason": "材料里点过名"}],
                "sections": [
                    {"heading": "涨价原因", "thesis": "供应链", "body": "DRAM涨了90%。", "evidence": []}
                ],
            },
            "judgment": {"one_line_judgment": "先别急着买", "headline_zh": "判断"},
        },
        items=[],
        asks=[{"q": "还要等吗", "a": "再看一个月。"}],
        query="笔记本",
    )
    assert "# 测试报告" in text
    assert "结论清单" in text
    assert "机型A" in text
    assert "深度阅读" in text
    assert "还要等吗" in text
    assert safe_filename("测试/报告", ".md") == "测试_报告.md"


def test_search_continue_flag_is_consumed_once():
    from tools.login_ready import consume_search_continue, request_search_continue

    with tempfile.TemporaryDirectory() as raw:
        job = Path(raw)
        assert not consume_search_continue("xhs", job)
        assert request_search_continue(job, "xhs") == ["xhs"]
        assert (job / "xhs.continue").is_file()
        assert consume_search_continue("xhs", job)
        assert not (job / "xhs.continue").is_file()
        assert not consume_search_continue("xhs", job)


def test_show_crawler_flag_is_consumed_once():
    from tools.login_ready import consume_show_crawler, request_show_crawler

    with tempfile.TemporaryDirectory() as raw:
        job = Path(raw)
        assert not consume_show_crawler(job)
        request_show_crawler(job)
        assert (job / "show.chrome").is_file()
        assert consume_show_crawler(job)
        assert not (job / "show.chrome").is_file()


class _FakeXhsPage:
    def __init__(self, logged_in=False, qr=False):
        self.url = "https://www.xiaohongshu.com/"
        self.logged_in = logged_in
        self.qr = qr

    async def evaluate(self, script):
        if "user/profile" in script:
            return self.logged_in
        if "qrcode" in script or "login-container" in script:
            return self.qr
        return self.logged_in

    async def is_visible(self, selector, timeout=400):
        return False


class _FakeContext:
    def __init__(self, cookies, page=None):
        self._cookies = cookies
        self.pages = [page] if page is not None else []

    async def cookies(self):
        return self._cookies


def test_accept_manual_login_trusts_session_when_qr_gone():
    import asyncio

    from tools.login_ready import accept_manual_login

    page = _FakeXhsPage(logged_in=False, qr=False)
    context = _FakeContext([{"name": "web_session", "value": "loggedinsession12345"}], page)
    assert asyncio.run(accept_manual_login(context, page, "xhs")) is True


def test_accept_manual_login_keeps_waiting_when_qr_still_up():
    import asyncio

    from tools.login_ready import accept_manual_login

    page = _FakeXhsPage(logged_in=False, qr=True)
    context = _FakeContext([{"name": "web_session", "value": "loggedinsession12345"}], page)
    assert asyncio.run(accept_manual_login(context, page, "xhs")) is False


def test_wait_until_logged_in_follows_continue_click(monkeypatch, tmp_path):
    import asyncio

    from tools.login_ready import request_search_continue, wait_until_logged_in

    page = _FakeXhsPage(logged_in=False, qr=False)
    context = _FakeContext([{"name": "web_session", "value": "loggedinsession12345"}], page)
    monkeypatch.setenv("BOARD_JOB_DIR", str(tmp_path))
    request_search_continue(tmp_path, "xhs")

    async def never_ready():
        return False

    assert asyncio.run(
        wait_until_logged_in(context, page, "xhs", timeout=2, ready=never_ready)
    ) is True


def test_selfinfo_and_pong_accept_page_login():
    import asyncio
    from unittest.mock import AsyncMock

    from media_platform.xhs.client import XiaoHongShuClient, selfinfo_looks_logged_in

    assert selfinfo_looks_logged_in({"data": {"result": {"success": True}}})
    assert selfinfo_looks_logged_in({"success": True, "data": {"user_id": "u1", "nickname": "甲"}})
    assert not selfinfo_looks_logged_in({"success": False, "data": {}})

    page = _FakeXhsPage(logged_in=True, qr=False)
    context = _FakeContext([{"name": "web_session", "value": "loggedinsession12345"}], page)
    page.context = context
    client = XiaoHongShuClient(
        headers={"Cookie": "web_session=old"},
        playwright_page=page,
        cookie_dict={},
    )
    client.query_self = AsyncMock(return_value={"success": False, "data": {}})
    client.update_cookies = AsyncMock()
    assert asyncio.run(client.pong()) is True
    client.update_cookies.assert_awaited()
