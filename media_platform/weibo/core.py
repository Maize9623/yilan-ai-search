# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/weibo/core.py
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
# @Time    : 2023/12/23 15:41
# @Desc    : Weibo crawler main workflow code

import asyncio
import os
# import random  # Removed as we now use fixed config.CRAWLER_MAX_SLEEP_SEC intervals
from asyncio import Task
from typing import Dict, List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)
from tenacity import RetryError

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import weibo as weibo_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from tools.chrome_launch import (
    adopt_page,
    launch_persistent_chrome,
    restore_crawler_window,
    settle_crawler_window,
)
from tools.search_ladders import mark_ladder_done, need_more
from var import crawler_type_var, source_keyword_var

from .client import WeiboClient
from .exception import DataFetchError
from .field import SearchType
from .help import (
    card_type_summary,
    mids_from_html,
    notes_from_search_payload,
    search_keywords,
    should_harvest_search,
    weibo_search_queries,
)
from .login import WeiboLogin


class WeiboCrawler(AbstractCrawler):
    context_page: Page
    wb_client: WeiboClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self):
        self.index_url = "https://www.weibo.com"
        self.mobile_index_url = "https://m.weibo.cn"
        self.cookie_urls = [self.mobile_index_url]
        self.user_agent = utils.get_user_agent()
        self.mobile_user_agent = utils.get_mobile_user_agent()
        self.cdp_manager = None
        self.ip_proxy_pool = None  # Proxy IP pool for automatic proxy refresh

    async def start(self):
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # Select launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[WeiboCrawler] Launching browser with CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.mobile_user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[WeiboCrawler] Launching browser with standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium, None, self.user_agent, headless=config.HEADLESS
                )


            self.context_page = await adopt_page(self.browser_context)
            try:
                await self.context_page.goto(
                    self.index_url, wait_until="domcontentloaded", timeout=20000
                )
            except Exception as error:
                utils.logger.info(
                    f"[WeiboCrawler.start] 微博首页没完全加载完：{error}，窗口先留着。"
                )

            self.wb_client = await self.create_weibo_client(httpx_proxy_format)
            if not await self.wb_client.pong():
                await self._sync_mobile_cookies()
            if not await self.wb_client.pong():
                login_obj = WeiboLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin(ready=self.wb_client.pong)
                await self._sync_mobile_cookies()
            await settle_crawler_window(self.context_page, "微博")

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for video and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_notes()
            elif config.CRAWLER_TYPE == "creator":
                # Get creator's information and their notes and comments
                await self.get_creators_and_notes()
            else:
                pass
            utils.logger.info("[WeiboCrawler.start] Weibo Crawler finished ...")

    async def _sync_mobile_cookies(self) -> None:
        utils.logger.info("[WeiboCrawler.start] redirect weibo mobile homepage and update cookies on mobile platform")
        try:
            await self.context_page.goto(
                self.mobile_index_url, wait_until="domcontentloaded", timeout=20000
            )
            await asyncio.sleep(2)
        except Exception as error:
            utils.logger.info(
                f"[WeiboCrawler.start] 微博移动首页没完全加载完：{error}，仍用现有 cookie。"
            )
        await self.wb_client.update_cookies(
            browser_context=self.browser_context,
            urls=self.cookie_urls,
        )

    def _search_type(self) -> Optional[SearchType]:
        mapping = {
            "default": SearchType.DEFAULT,
            "real_time": SearchType.REAL_TIME,
            "popular": SearchType.POPULAR,
            "video": SearchType.VIDEO,
        }
        search_type = mapping.get(config.WEIBO_SEARCH_TYPE)
        if search_type is None:
            utils.logger.error(f"[WeiboCrawler.search] Invalid WEIBO_SEARCH_TYPE: {config.WEIBO_SEARCH_TYPE}")
        return search_type

    async def search(self):
        """
        search weibo note with keywords
        :return:
        """
        utils.logger.info("[WeiboCrawler.search] Begin search weibo keywords")
        wanted_notes = max(1, int(config.CRAWLER_MAX_NOTES_COUNT))
        page_size = 10
        start_page = config.START_PAGE
        max_page = start_page + max(8, (wanted_notes + page_size - 1) // page_size + 4)
        search_type = self._search_type()
        if search_type is None:
            return
        if search_type is SearchType.VIDEO:
            utils.logger.info("[WeiboCrawler.search] 微博这一站按图文收，综合搜索即可，不走视频频道。")
            search_type = SearchType.DEFAULT

        type_order = [search_type]
        for extra in (SearchType.REAL_TIME, SearchType.POPULAR):
            if extra not in type_order:
                type_order.append(extra)

        for keyword in search_keywords(config.KEYWORDS):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[WeiboCrawler.search] Current search keyword: {keyword}")
            written = 0
            seen_ids: set[str] = set()
            for step_i, query in enumerate(weibo_search_queries(keyword)):
                if written >= wanted_notes:
                    break
                if step_i and not need_more(written, wanted_notes):
                    break
                source_keyword_var.set(query)
                if query != keyword:
                    utils.logger.info(
                        f"[WeiboCrawler.search] 整句只收到 {written} 条，改用短词再搜：{query}"
                    )
                for stype in type_order:
                    if written >= wanted_notes:
                        break
                    page = 1
                    harvested = False
                    while written < wanted_notes and page <= max_page:
                        if page < start_page:
                            utils.logger.info(f"[WeiboCrawler.search] Skip page: {page}")
                            page += 1
                            continue
                        utils.logger.info(
                            f"[WeiboCrawler.search] search weibo keyword: {query}, type={stype.value}, page: {page}"
                        )
                        search_res = await self._fetch_search_page(query, page, stype)
                        fresh = self._take_fresh(notes_from_search_payload(search_res), seen_ids)
                        if (
                            not fresh
                            and not harvested
                            and page <= start_page
                            and should_harvest_search(search_res)
                        ):
                            harvested = True
                            utils.logger.info(
                                f"[WeiboCrawler.search] 接口空页 {card_type_summary(search_res)}，改从搜索页截。"
                            )
                            fresh = self._take_fresh(
                                await self._harvest_search_page(
                                    query, stype, wanted_notes - written
                                ),
                                seen_ids,
                            )
                        if not fresh:
                            utils.logger.info(
                                f"[WeiboCrawler.search] 本页没有新微博 {card_type_summary(search_res)}，目标 {wanted_notes}，已有 {written}"
                            )
                            break
                        written = await self._commit_search_notes(fresh, wanted_notes, written)
                        page += 1
                        await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                        utils.logger.info(
                            f"[WeiboCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}"
                        )
        mark_ladder_done("wb")

    async def _fetch_search_page(self, query: str, page: int, stype: SearchType) -> Dict:
        try:
            return await self.wb_client.get_note_by_keyword(
                keyword=query, page=page, search_type=stype
            )
        except DataFetchError as error:
            utils.logger.info(f"[WeiboCrawler.search] 微博这一页没取到：{error}")
            if self._search_looks_blocked(str(error)):
                return await self._recover_search(query, page, stype)
            return {}
        except RetryError as error:
            utils.logger.info(f"[WeiboCrawler.search] 微博这一页重试后仍空：{error}")
            return {}

    @staticmethod
    def _note_id(note_item: Dict) -> str:
        return str(((note_item or {}).get("mblog") or {}).get("id") or "")

    def _take_fresh(self, note_list: List[Dict], seen_ids: set[str]) -> List[Dict]:
        fresh: List[Dict] = []
        for note_item in note_list or []:
            note_id = self._note_id(note_item)
            if not note_id or note_id in seen_ids:
                continue
            seen_ids.add(note_id)
            fresh.append(note_item)
        return fresh

    async def _commit_search_notes(
        self, fresh: List[Dict], wanted_notes: int, written: int
    ) -> int:
        remain = wanted_notes - written
        fresh = await self.batch_get_notes_full_text(fresh[:remain])
        note_id_list: List[str] = []
        utils.logger.info(
            f"[WeiboCrawler.search] 本页取 {len(fresh)} 条，目标 {wanted_notes}，已有 {written}"
        )
        for note_item in fresh:
            note_id = self._note_id(note_item)
            await weibo_store.update_weibo_note(note_item)
            written += 1
            if note_id:
                note_id_list.append(note_id)
            utils.logger.info(f"[WeiboCrawler.search] 已写下第 {written} 条")
            if written >= wanted_notes:
                break
        await self.batch_get_notes_comments(note_id_list)
        return written

    @staticmethod
    def _search_looks_blocked(message: str) -> bool:
        text = message or ""
        return any(token in text for token in ("登录", "验证", "验证码", "forbidden", "432"))

    async def _harvest_search_page(
        self, keyword: str, search_type: SearchType, limit: int
    ) -> List[Dict]:
        from urllib.parse import quote

        payloads: List[Dict] = []

        async def on_response(response) -> None:
            url = response.url or ""
            if "container/getIndex" not in url or response.status != 200:
                return
            try:
                payloads.append(await response.json())
            except Exception:
                return

        type_val = search_type.value if search_type else "1"
        url = f"https://m.weibo.cn/search?containerid=100103type%3D{type_val}%26q%3D{quote(keyword)}"
        utils.logger.info("[WeiboCrawler.search] 打开搜索页。")
        self.context_page.on("response", on_response)
        try:
            await self.context_page.goto(url, wait_until="domcontentloaded", timeout=18000)
            await asyncio.sleep(1.6)
            try:
                await self.context_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.0)
            except Exception:
                pass
        except Exception as error:
            utils.logger.info(f"[WeiboCrawler.search] 搜索页没打开完：{error}，仍走接口。")
        finally:
            self.context_page.remove_listener("response", on_response)
        await self.wb_client.update_cookies(
            browser_context=self.browser_context,
            urls=self.cookie_urls,
        )

        notes: List[Dict] = []
        seen: set[str] = set()
        for payload in payloads:
            for note_item in notes_from_search_payload(payload):
                note_id = self._note_id(note_item)
                if not note_id or note_id in seen:
                    continue
                seen.add(note_id)
                notes.append(note_item)
                if len(notes) >= limit:
                    return notes

        if len(notes) >= limit:
            return notes
        try:
            html = await self.context_page.content()
        except Exception:
            html = ""
        for mid in mids_from_html(html, limit=max(limit, 12)):
            if mid in seen or len(notes) >= limit:
                continue
            try:
                item = await self.wb_client.get_note_info_by_id(mid)
            except Exception as error:
                utils.logger.info(f"[WeiboCrawler.search] 详情没取到 {mid}：{error}")
                continue
            if item and item.get("mblog"):
                seen.add(mid)
                notes.append(item)
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
        if notes:
            utils.logger.info(f"[WeiboCrawler.search] 搜索页截到 {len(notes)} 条")
        return notes

    async def _recover_search(self, keyword: str, page: int, search_type: SearchType) -> Dict:
        from tools.login_ready import wait_for_manual_verify, wait_until_logged_in

        utils.logger.info("微博被安全验证挡住了。请把 Chrome 窗口拉到前面自己过，过完会继续搜。")
        await restore_crawler_window(self.context_page)
        await wait_for_manual_verify(
            self.browser_context, self.context_page, "wb", timeout=180
        )
        if not await self.wb_client.pong():
            await wait_until_logged_in(
                self.browser_context,
                self.context_page,
                "wb",
                timeout=180,
                ready=self.wb_client.pong,
            )
        await self.wb_client.update_cookies(
            browser_context=self.browser_context,
            urls=self.cookie_urls,
        )
        try:
            data = await self.wb_client.get_note_by_keyword(
                keyword=keyword, page=page, search_type=search_type
            )
        except (DataFetchError, RetryError) as error:
            utils.logger.info(f"[WeiboCrawler.search] 微博恢复后还是没取到：{error}")
            data = {}
        await settle_crawler_window(self.context_page, "微博")
        return data

    async def get_specified_notes(self):
        """
        get specified notes info
        :return:
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_note_info_task(note_id=note_id, semaphore=semaphore) for note_id in config.WEIBO_SPECIFIED_ID_LIST]
        video_details = await asyncio.gather(*task_list)
        for note_item in video_details:
            if note_item:
                await weibo_store.update_weibo_note(note_item)
        await self.batch_get_notes_comments(config.WEIBO_SPECIFIED_ID_LIST)

    async def get_note_info_task(self, note_id: str, semaphore: asyncio.Semaphore) -> Optional[Dict]:
        """
        Get note detail task
        :param note_id:
        :param semaphore:
        :return:
        """
        async with semaphore:
            try:
                result = await self.wb_client.get_note_info_by_id(note_id)

                # Sleep after fetching note details
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[WeiboCrawler.get_note_info_task] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching note details {note_id}")

                return result
            except DataFetchError as ex:
                utils.logger.error(f"[WeiboCrawler.get_note_info_task] Get note detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[WeiboCrawler.get_note_info_task] have not fund note detail note_id:{note_id}, err: {ex}")
                return None

    async def batch_get_notes_comments(self, note_id_list: List[str]):
        """
        batch get notes comments
        :param note_id_list:
        :return:
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[WeiboCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        utils.logger.info(f"[WeiboCrawler.batch_get_notes_comments] note ids:{note_id_list}")
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list: List[Task] = []
        for note_id in note_id_list:
            task = asyncio.create_task(self.get_note_comments(note_id, semaphore), name=note_id)
            task_list.append(task)
        await asyncio.gather(*task_list)

    async def get_note_comments(self, note_id: str, semaphore: asyncio.Semaphore):
        """
        get comment for note id
        :param note_id:
        :param semaphore:
        :return:
        """
        async with semaphore:
            try:
                utils.logger.info(f"[WeiboCrawler.get_note_comments] begin get note_id: {note_id} comments ...")

                # Sleep before fetching comments
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[WeiboCrawler.get_note_comments] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds before fetching comments for note {note_id}")

                await self.wb_client.get_note_all_comments(
                    note_id=note_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,  # Use fixed interval instead of random
                    callback=weibo_store.batch_update_weibo_note_comments,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
            except DataFetchError as ex:
                utils.logger.error(f"[WeiboCrawler.get_note_comments] get note_id: {note_id} comment error: {ex}")
            except Exception as e:
                utils.logger.error(f"[WeiboCrawler.get_note_comments] may be been blocked, err:{e}")

    async def get_note_images(self, mblog: Dict):
        """
        get note images
        :param mblog:
        :return:
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[WeiboCrawler.get_note_images] Crawling image mode is not enabled")
            return

        pics: List = mblog.get("pics")
        if not pics:
            return
        for pic in pics:
            if isinstance(pic, str):
                url = pic
                pid = url.split("/")[-1].split(".")[0]
            elif isinstance(pic, dict):
                url = pic.get("url")
                pid = pic.get("pid", "")
            else:
                continue
            if not url:
                continue
            content = await self.wb_client.get_note_image(url)
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
            utils.logger.info(f"[WeiboCrawler.get_note_images] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching image")
            if content != None:
                extension_file_name = url.split(".")[-1]
                await weibo_store.update_weibo_note_image(pid, content, extension_file_name)

    async def get_creators_and_notes(self) -> None:
        """
        Get creator's information and their notes and comments
        Returns:

        """
        utils.logger.info("[WeiboCrawler.get_creators_and_notes] Begin get weibo creators")
        for user_id in config.WEIBO_CREATOR_ID_LIST:
            createor_info_res: Dict = await self.wb_client.get_creator_info_by_id(creator_id=user_id)
            if createor_info_res:
                createor_info: Dict = createor_info_res.get("userInfo", {})
                utils.logger.info(f"[WeiboCrawler.get_creators_and_notes] creator info: {createor_info}")
                if not createor_info:
                    raise DataFetchError("Get creator info error")
                await weibo_store.save_creator(user_id, user_info=createor_info)

                # Create a wrapper callback to get full text before saving data
                async def save_notes_with_full_text(note_list: List[Dict]):
                    # If full text fetching is enabled, batch get full text first
                    updated_note_list = await self.batch_get_notes_full_text(note_list)
                    await weibo_store.batch_update_weibo_notes(updated_note_list)

                # Get all note information of the creator
                all_notes_list = await self.wb_client.get_all_notes_by_creator_id(
                    creator_id=user_id,
                    container_id=f"107603{user_id}",
                    crawl_interval=0,
                    callback=save_notes_with_full_text,
                )

                note_ids = [note_item.get("mblog", {}).get("id") for note_item in all_notes_list if note_item.get("mblog", {}).get("id")]
                await self.batch_get_notes_comments(note_ids)

            else:
                utils.logger.error(f"[WeiboCrawler.get_creators_and_notes] get creator info error, creator_id:{user_id}")

    async def create_weibo_client(self, httpx_proxy: Optional[str]) -> WeiboClient:
        """Create xhs client"""
        utils.logger.info("[WeiboCrawler.create_weibo_client] Begin create weibo API client ...")
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )
        weibo_client_obj = WeiboClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": utils.get_mobile_user_agent(),
                "Cookie": cookie_str,
                "Origin": "https://m.weibo.cn",
                "Referer": "https://m.weibo.cn",
                "Content-Type": "application/json;charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "MWeibo-Pwa": "1",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # Pass proxy pool for automatic refresh
        )
        return weibo_client_obj

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        utils.logger.info("[WeiboCrawler.launch_browser] Begin create browser context ...")
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM)  # type: ignore
            browser_context = await launch_persistent_chrome(
                chromium,
                user_data_dir,
                headless=headless,
                proxy=playwright_proxy,
                user_agent=user_agent,
            )
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy, channel="chrome")  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        Launch browser with CDP mode
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Display browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[WeiboCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[WeiboCrawler] CDP mode startup failed, falling back to standard mode: {e}")
            # Fallback to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def get_note_full_text(self, note_item: Dict) -> Dict:
        """
        Get full text content of a post
        If the post content is truncated (isLongText=True), request the detail API to get complete content
        :param note_item: Post data, contains mblog field
        :return: Updated post data
        """
        if not config.ENABLE_WEIBO_FULL_TEXT:
            return note_item

        mblog = note_item.get("mblog", {})
        if not mblog:
            return note_item

        # Check if it's a long text
        is_long_text = mblog.get("isLongText", False)
        if not is_long_text:
            return note_item

        note_id = mblog.get("id")
        if not note_id:
            return note_item

        try:
            utils.logger.info(f"[WeiboCrawler.get_note_full_text] Fetching full text for note: {note_id}")
            full_note = await self.wb_client.get_note_info_by_id(note_id)
            if full_note and full_note.get("mblog"):
                # Replace original content with complete content
                note_item["mblog"] = full_note["mblog"]
                utils.logger.info(f"[WeiboCrawler.get_note_full_text] Successfully fetched full text for note: {note_id}")

            # Sleep after request to avoid rate limiting
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
        except DataFetchError as ex:
            utils.logger.error(f"[WeiboCrawler.get_note_full_text] Failed to fetch full text for note {note_id}: {ex}")
        except Exception as ex:
            utils.logger.error(f"[WeiboCrawler.get_note_full_text] Unexpected error for note {note_id}: {ex}")

        return note_item

    async def batch_get_notes_full_text(self, note_list: List[Dict]) -> List[Dict]:
        """
        Batch get full text content of posts
        :param note_list: List of posts
        :return: Updated list of posts
        """
        if not config.ENABLE_WEIBO_FULL_TEXT:
            return note_list

        result = []
        for note_item in note_list:
            updated_note = await self.get_note_full_text(note_item)
            result.append(updated_note)
        return result

    async def close(self):
        """Close browser context"""
        # Special handling if using CDP mode
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[WeiboCrawler.close] Browser context closed ...")
