# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/kuaishou/login.py
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


import asyncio
import functools
import sys
from typing import Optional

from playwright.async_api import BrowserContext, Page
from tenacity import (RetryError, retry, retry_if_result, stop_after_attempt,
                      wait_fixed)

import config
from base.base_crawler import AbstractLogin
from tools import utils


class KuaishouLogin(AbstractLogin):
    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext,
                 context_page: Page,
                 login_phone: Optional[str] = "",
                 cookie_str: str = ""
                 ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        """Start login kuaishou"""
        from tools.login_ready import already_logged_in, complete_login_if_needed

        if not await already_logged_in(self.browser_context, self.context_page, "ks"):
            await self.popup_login_dialog()
        await complete_login_if_needed(
            self.browser_context,
            self.context_page,
            "ks",
            cookie_login=self.login_by_cookies,
        )

    async def _login_panel_visible(self) -> bool:
        try:
            return bool(
                await self.context_page.evaluate(
                    """() => {
                      const text = (document.body && document.body.innerText || '').slice(0, 4000);
                      if (/扫码登录|手机号登录|验证码登录|二维码登录/.test(text)) return true;
                      if (document.querySelector('.qrcode-img img, img[alt*="二维码"], iframe[src*="passport"]')) {
                        return true;
                      }
                      return false;
                    }"""
                )
            )
        except Exception:
            return False

    async def popup_login_dialog(self):
        """Open the Kuaishou login panel in the same Chrome. Do not solve captcha."""
        from tools.chrome_launch import restore_crawler_window

        await restore_crawler_window(self.context_page)
        if await self._login_panel_visible():
            utils.logger.info("[KuaishouLogin] 快手登录页已经在窗口里，请扫码或点登录。")
            return
        clicks = (
            self.context_page.get_by_role("button", name="登录", exact=True),
            self.context_page.get_by_role("link", name="登录", exact=True),
            self.context_page.get_by_text("登录", exact=True),
            self.context_page.locator("xpath=//p[text()='登录']"),
            self.context_page.locator("xpath=//span[text()='登录']"),
            self.context_page.locator("xpath=//div[text()='登录']"),
        )
        for locator in clicks:
            try:
                target = locator.first
                if await target.is_visible(timeout=1200):
                    await target.click(timeout=2500)
                    await asyncio.sleep(0.5)
                    if await self._login_panel_visible() or await already_on_login(self.context_page):
                        utils.logger.info("[KuaishouLogin] 已打开快手登录页，请在弹出的 Chrome 里扫码或点登录。")
                        return
            except Exception:
                continue
        try:
            await self.context_page.goto(
                "https://passport.kuaishou.com/pc/account/login/?sid=kuaishou.server.web",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(0.4)
            utils.logger.info("[KuaishouLogin] 已打开快手登录页，请在弹出的 Chrome 里扫码或点登录。")
            return
        except Exception:
            pass
        utils.logger.info("[KuaishouLogin] 登录框没自动弹出，请在 Chrome 窗口里自己点登录。")

    @retry(stop=stop_after_attempt(600), wait=wait_fixed(1), retry=retry_if_result(lambda value: value is False))
    async def check_login_state(self) -> bool:
        """
            Check if the current login status is successful and return True otherwise return False
            retry decorator will retry 20 times if the return value is False, and the retry interval is 1 second
            if max retry times reached, raise RetryError
        """
        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        kuaishou_pass_token = cookie_dict.get("passToken")
        if kuaishou_pass_token:
            return True
        return False

    async def login_by_qrcode(self):
        """login kuaishou website and keep webdriver login state"""
        utils.logger.info("[KuaishouLogin.login_by_qrcode] Begin login kuaishou by qrcode ...")
        await self.popup_login_dialog()
        qrcode_img_selector = "//div[@class='qrcode-img']//img"
        base64_qrcode_img = await utils.find_login_qrcode(
            self.context_page,
            selector=qrcode_img_selector
        )
        if not base64_qrcode_img:
            utils.logger.info("[KuaishouLogin.login_by_qrcode] login failed , have not found qrcode please check ....")
            sys.exit()
        partial_show_qrcode = functools.partial(utils.show_qrcode, base64_qrcode_img)
        asyncio.get_running_loop().run_in_executor(executor=None, func=partial_show_qrcode)
        utils.logger.info("[KuaishouLogin.login_by_qrcode] waiting for scan code login, remaining time is 20s")
        try:
            await self.check_login_state()
        except RetryError:
            utils.logger.info("[KuaishouLogin.login_by_qrcode] Login kuaishou failed by qrcode login method ...")
            sys.exit()
        wait_redirect_seconds = 5
        utils.logger.info(f"[KuaishouLogin.login_by_qrcode] Login successful then wait for {wait_redirect_seconds} seconds redirect ...")
        await asyncio.sleep(wait_redirect_seconds)

    async def login_by_mobile(self):
        pass

    async def login_by_cookies(self):
        utils.logger.info("[KuaishouLogin.login_by_cookies] Begin login kuaishou by cookie ...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".kuaishou.com",
                'path': "/"
            }])


def already_on_login(page) -> bool:
    url = (getattr(page, "url", "") or "").lower()
    return "passport.kuaishou.com" in url or "/account/login" in url
