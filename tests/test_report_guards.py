# -*- coding: utf-8 -*-
from board.server import attach_report, engagement_by_platform, enrich_report, hoist_report


def _section(heading, thesis="判断", body="一段"):
    return {
        "heading": heading,
        "thesis": thesis,
        "body": body,
        "evidence": [],
        "table": {"caption": "", "headers": [], "rows": []},
        "chart": {"title": "", "kind": "bar", "items": []},
    }


def test_hoist_keeps_argument_sections_over_platform_dump():
    board = {
        "sections": [_section("共识：送她缺的、能用的")],
        "map": [
            {
                "name": "2000元礼物",
                "children": [
                    {"name": "实用轻奢", "children": [{"name": "耳机"}, {"name": "香氛"}]}
                ],
            }
        ],
        "picks": [
            {
                "name": "苹果手机/金饰",
                "verdict": "skip",
                "reason": "小红书求助帖明确列出前女友已有这些物品",
                "item_id": "xhs:1",
            },
            {
                "name": "耳机",
                "verdict": "go",
                "reason": "多个送礼指南都点名日常能用的耳机",
                "item_id": "xhs:2",
            },
        ],
        "report": {
            "title": "检索报告",
            "overview": "",
            "sections": [_section("小红书", "这一站目前有 30 条")],
            "map": [{"name": "小红书", "children": ["送女生礼物合集"]}],
            "picks": [],
        },
        "judgment": {"headline_zh": "判断", "one_line_judgment": "先看她缺什么"},
    }
    items = [
        {"id": "xhs:1", "title": "求助", "text": "前女友已经有苹果手机"},
        {"id": "xhs:2", "title": "耳机", "text": "指南推荐耳机"},
    ]
    out = enrich_report("20岁女生生日礼物", items, board)
    headings = [row["heading"] for row in out["report"]["sections"]]
    assert "共识：送她缺的、能用的" in headings
    assert "小红书" not in headings
    names = [row["name"] for row in out["report"]["picks"]]
    assert "耳机" in names
    assert not any("苹果" in name for name in names)
    map_names = [row["name"] for row in out["report"]["map"]]
    assert "小红书" not in map_names
    assert any("实用" in name or "轻奢" in name for name in map_names)


def test_attach_report_does_not_clobber_eleven_writeup():
    board = {
        "sections": [_section("争议：鲜花算不算好礼物")],
        "picks": [{"name": "拍立得", "verdict": "go", "reason": "B站和指南都提到仪式感", "item_id": "bili:1"}],
        "report": {"title": "报告", "overview": ""},
        "judgment": {"headline_zh": "判断", "one_line_judgment": "先看仪式感"},
    }
    items = [{"id": "bili:1", "title": "拍立得", "text": "仪式感"}]
    out = attach_report("生日礼物", items, board)
    headings = [row["heading"] for row in out["report"]["sections"]]
    assert headings[0].startswith("争议")
    assert "小红书" not in headings


def test_hoist_report_prefers_top_level_map():
    report = hoist_report(
        {
            "map": [{"name": "避雷区", "children": [{"name": "口红"}]}],
            "report": {"map": [{"name": "知乎", "children": ["一篇回答"]}]},
        }
    )
    assert report["map"][0]["name"] == "避雷区"


def test_platform_briefs_are_editorial_not_title_lists():
    items = [
        {"id": "xhs:1", "platform": "xhs", "platform_label": "小红书", "title": "2000元实用礼物避雷", "text": "别送口红"},
        {"id": "dy:1", "platform": "dy", "platform_label": "抖音", "title": "仪式感花束", "text": "拆箱"},
    ]
    out = enrich_report(
        "20岁生日礼物",
        items,
        {
            "report": {
                "title": "报告",
                "overview": "总述",
                "sections": [_section("共识：送缺的")],
                "picks": [],
                "map": [{"name": "实用轻奢", "children": ["耳机"]}],
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "先看缺什么"},
        },
    )
    briefs = out["report"]["platform_briefs"]
    names = [row["name"] for row in briefs]
    assert "小红书" in names and "抖音" in names
    xhs = next(row for row in briefs if row["name"] == "小红书")
    assert "这批材料共" not in xhs["answer"]
    assert "围绕「" not in xhs["answer"]
    assert "当可带走" not in xhs["answer"]
    assert "。" in xhs["answer"]
    assert "口红" in xhs["answer"] or "实用" in xhs["answer"] or "避" in xhs["answer"]
    assert xhs.get("direction")
    assert xhs.get("trend")
    assert xhs["worth"]
    assert not xhs["worth"].startswith("适合看")


def test_platform_briefs_fill_stations_eleven_skipped():
    items = [
        {"id": "xhs:1", "platform": "xhs", "platform_label": "小红书", "title": "折叠上手", "text": "折痕浅"},
        {"id": "dy:1", "platform": "dy", "platform_label": "抖音", "title": "铰链手感", "text": "开合顺"},
        {"id": "mp:1", "platform": "mp", "platform_label": "公众号", "title": "公众号长文上手", "text": "细节才是重点", "author": "数智见"},
        {"id": "yt:1", "platform": "yt", "platform_label": "YouTube", "title": "Folding iPhone first look", "text": "hands on", "author": "MKBHD"},
    ]
    out = enrich_report(
        "IPhone Duo上手体验",
        items,
        {
            "report": {
                "title": "报告",
                "overview": "总述",
                "sections": [_section("共识：先看折痕")],
                "picks": [],
                "map": [{"name": "折痕", "children": ["浅"]}],
                "platform_briefs": [
                    {
                        "name": "小红书",
                        "answer": "笔记在讲折痕和上手手感。",
                        "direction": "上手",
                        "trend": "热",
                        "worth": "当侧面看。",
                    },
                    {
                        "name": "抖音",
                        "answer": "视频在讲铰链开合。",
                        "direction": "铰链",
                        "trend": "热",
                        "worth": "广告重。",
                    },
                ],
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "先看折痕"},
        },
    )
    names = [row["name"] for row in out["report"]["platform_briefs"]]
    assert names == ["小红书", "抖音", "公众号", "YouTube"]
    mp = next(row for row in out["report"]["platform_briefs"] if row["name"] == "公众号")
    assert "公众号长文上手" in mp["answer"]
    assert "送礼" not in mp["answer"]


def test_empty_briefs_replaced_when_station_has_items():
    items = [
        {
            "id": "xhs:1",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "折叠上手",
            "text": "折痕浅",
        },
        {
            "id": "mp:1",
            "platform": "mp",
            "platform_label": "公众号",
            "title": "影视飓风测评发烫",
            "text": "苹果回应单机测试片面",
            "author": "中新经纬",
        },
        {
            "id": "gnews:1",
            "platform": "gnews",
            "platform_label": "Google News",
            "title": "iPhone Duo Reality Check",
            "text": "Incredible Specs vs Flaws",
            "author": "Mashable",
        },
    ]
    out = enrich_report(
        "IPhone Duo测评",
        items,
        {
            "report": {
                "title": "报告",
                "overview": "总述",
                "sections": [_section("共识：先看发热")],
                "picks": [],
                "map": [{"name": "发热", "children": ["测评"]}],
                "sources": {
                    "summary": "本批材料共 200 条。",
                    "platforms": [
                        {"name": "小红书", "count": 1, "note": "现场上手"},
                        {"name": "公众号", "count": 0, "note": "本批未返回可读的公众号正文。"},
                        {"name": "Google News", "count": 0, "note": "本批未返回 Google News 条目。"},
                    ],
                    "coverage": "覆盖首批评测",
                },
                "platform_briefs": [
                    {
                        "name": "小红书",
                        "answer": "笔记在讲折痕和上手手感。",
                        "direction": "上手",
                        "trend": "热",
                        "worth": "当侧面看。",
                    },
                    {
                        "name": "公众号",
                        "answer": "本批未返回可读的公众号正文，只有搜狗索引层面的缺失。",
                        "direction": "",
                        "trend": "",
                        "worth": "本批材料在此栏目上没有可用的正文内容。",
                    },
                    {
                        "name": "Google News",
                        "answer": "本批未返回 Google News 条目，因此无法给出该渠道的独立判断。",
                        "direction": "",
                        "trend": "",
                        "worth": "本批材料在该渠道没有可引用内容。",
                    },
                ],
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "先看发热"},
        },
    )
    sources = {row["name"]: row for row in out["report"]["sources"]["platforms"]}
    assert sources["公众号"]["count"] == 1
    assert sources["Google News"]["count"] == 1
    assert "未返回" not in sources["公众号"]["note"]
    briefs = {row["name"]: row for row in out["report"]["platform_briefs"]}
    assert "影视飓风测评发烫" in briefs["公众号"]["answer"]
    assert "未返回" not in briefs["公众号"]["answer"]
    assert "iPhone Duo Reality Check" in briefs["Google News"]["answer"]
    assert "未返回" not in briefs["Google News"]["answer"]


def test_empty_section_cases_backfilled():
    items = [
        {
            "id": "dy:heat",
            "platform": "dy",
            "platform_label": "抖音",
            "title": "影视飓风Tim测评发烫",
            "text": "录3分钟4K就烫到握不住",
            "author": "影视飓风",
            "liked": "2.1万",
            "liked_n": 21000,
            "images": ["heat.jpg"],
        },
        {
            "id": "xhs:ray",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "内外屏接力上手",
            "text": "半折后上半屏看内容",
            "author": "小雷",
            "liked": "800",
            "liked_n": 800,
            "images": ["ray.jpg"],
        },
        {
            "id": "xhs:duo",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "iPhone Duo首发上手体验！",
            "text": "折叠屏发布会现场",
            "author": "路人",
            "liked": "9万",
            "liked_n": 90000,
            "images": ["duo.jpg"],
        },
    ]
    out = enrich_report(
        "IPhone Duo测评",
        items,
        {
            "report": {
                "title": "报告",
                "overview": "总述",
                "sections": [
                    {
                        "heading": "发热和折叠结构耐用性，是这轮测评最集中的负面",
                        "thesis": "影视飓风 Tim 把高负载发热摊开了。",
                        "body": "Tim 说持续录 4K 就烫。封面新闻也在转。",
                        "evidence": [],
                        "table": {"caption": "", "headers": [], "rows": []},
                        "chart": {"title": "", "kind": "bar", "items": []},
                    },
                    {
                        "heading": "正向体验不止折痕：内外屏接力和做工",
                        "thesis": "现场上手还在讲开合逻辑。",
                        "body": "小红书「小雷」说打开或合上能感到两块屏属于同一台手机。",
                        "evidence": [],
                        "table": {"caption": "", "headers": [], "rows": []},
                        "chart": {"title": "", "kind": "bar", "items": []},
                    },
                ],
                "picks": [],
                "map": [{"name": "发热", "children": ["测评"]}],
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "先看发热"},
        },
    )
    heat, relay = out["report"]["sections"][:2]
    assert [row["item_id"] for row in heat["evidence"]] == ["dy:heat"]
    assert heat["table"]["rows"][0][2] == "影视飓风Tim测评发烫"
    assert heat["chart"]["items"][0]["value"] == 21000
    assert [row["item_id"] for row in relay["evidence"]] == ["xhs:ray"]
    assert all(row["item_id"] != "xhs:duo" for row in heat["evidence"] + relay["evidence"])


def test_report_intent_follows_rewrite_note():
    from board.intent_frame import detect_query_intent, report_intent

    base = report_intent("IPhone Duo测评")
    assert base["mode"] == detect_query_intent("IPhone Duo测评")["mode"]
    assert "user_note" not in base
    rewritten = report_intent("IPhone Duo测评", "只看发热和维修，值不值得买")
    assert rewritten["mode"] != "comprehensive"
    assert rewritten["topic"] == base["topic"]
    assert rewritten["user_note"] == "只看发热和维修，值不值得买"
    assert "重写完整报告" in rewritten["job"]
    assert "发热" in rewritten["job"]


def test_enrich_keeps_rewrite_intent():
    out = enrich_report(
        "IPhone Duo测评",
        [
            {
                "id": "mp:1",
                "platform": "mp",
                "platform_label": "公众号",
                "title": "影视飓风测评发烫",
                "text": "录3分钟就烫",
                "author": "青年报",
            }
        ],
        {
            "rewrite_note": "只看发热和维修，值不值得买",
            "report": {
                "title": "报告",
                "overview": "总述",
                "sections": [_section("发热是硬伤")],
                "picks": [],
                "map": [{"name": "发热", "children": ["维修"]}],
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "先看发热"},
        },
    )
    assert out["intent"]["mode"] != "comprehensive"
    assert "发热" in out["intent"]["job"]
    assert out.get("rewrite_note") == "只看发热和维修，值不值得买"


def test_rewrite_payload_uses_all_items_and_new_logic():
    from board.server import _eleven_payload, eleven_instructions
    from board.intent_frame import report_intent

    items = [
        {
            "id": "xhs:1",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "折痕上手",
            "text": "几乎看不到折痕",
            "author": "小雷",
            "published": "2026-09-17",
            "url": "https://example.com/1",
            "liked": "800",
            "liked_n": 800,
        },
        {
            "id": "mp:1",
            "platform": "mp",
            "platform_label": "公众号",
            "title": "影视飓风测评发烫",
            "text": "录3分钟就烫",
            "author": "青年报",
            "published": "2026-09-16",
            "url": "https://example.com/2",
            "liked": "0",
            "liked_n": 0,
        },
        {
            "id": "gnews:1",
            "platform": "gnews",
            "platform_label": "Google News",
            "title": "iPhone Duo Reality Check",
            "text": "specs vs flaws",
            "author": "Mashable",
            "published": "2026-09-16",
            "url": "https://example.com/3",
            "liked": "0",
            "liked_n": 0,
        },
    ]
    note = "只看发热和维修，值不值得买"
    payload = _eleven_payload(
        "IPhone Duo测评",
        items,
        extra_note=note,
        prior_notes=["先看折痕"],
    )
    assert payload["rewrite"] is True
    assert payload["user_note"] == note
    assert payload["prior_notes"] == ["先看折痕"]
    assert payload["item_count"] == 3
    assert payload["item_shown"] == 3
    assert {row["id"] for row in payload["items"]} == {"xhs:1", "mp:1", "gnews:1"}
    assert "完整报告" in payload["task"]
    assert "不是小修小补" in payload["task"]
    assert "发热" in payload["task"]
    intent = report_intent("IPhone Duo测评", note)
    text = eleven_instructions(intent)
    assert "不是旧稿打补丁" in text
    assert note in text


def test_select_eleven_keeps_all_items():
    from board.server import _eleven_payload, _select_eleven_items

    items = []
    for plat, label in (("xhs", "小红书"), ("mp", "公众号"), ("gnews", "Google News")):
        for index in range(90):
            items.append(
                {
                    "id": f"{plat}:{index}",
                    "platform": plat,
                    "platform_label": label,
                    "title": f"{label}{index}",
                    "text": "正文",
                    "author": "作者",
                    "published": "2026-09-17",
                    "url": "https://example.com",
                    "liked": "0",
                    "liked_n": 900 if plat == "xhs" else 0,
                    "freshness": "week",
                }
            )
    picked = _select_eleven_items(items)
    assert len(picked) == 270
    assert {item["platform"] for item in picked} == {"xhs", "mp", "gnews"}
    assert sum(1 for item in picked if item["platform"] == "mp") == 90
    assert sum(1 for item in picked if item["platform"] == "gnews") == 90
    payload = _eleven_payload("IPhone Duo测评", items)
    assert payload["item_count"] == 270
    assert payload["item_shown"] == 270
    assert {row["id"] for row in payload["items"]} == {item["id"] for item in items}
    assert "取 " not in (payload.get("note") or "")
    shown = {row["name"]: row["shown"] for row in payload["platforms"]}
    assert shown["公众号"] == 90
    assert shown["Google News"] == 90


def test_engagement_tables_stay_inside_one_platform():
    groups = engagement_by_platform(
        [
            {"id": "dy:1", "platform": "dy", "platform_label": "抖音", "title": "抖音热帖", "liked": "100", "liked_n": 100, "kind": "video"},
            {"id": "zhihu:1", "platform": "zhihu", "platform_label": "知乎", "title": "知乎回答", "liked": "8", "liked_n": 8, "kind": "note"},
        ]
    )
    names = [row["name"] for row in groups]
    assert names == ["抖音", "知乎"]
    assert "平台" not in groups[0]["table"]["headers"]
    assert groups[0]["table"]["rows"][0][1] == "抖音热帖"
    assert groups[0]["table"]["item_ids"][0] == "dy:1"


def test_parse_platform_queries_keeps_known_codes():
    from board.server import parse_platform_queries
    out = parse_platform_queries("xhs:室内脱口秀,zhihu:话剧 推荐,nope:xx")
    assert out["xhs"] == "室内脱口秀"
    assert out["zhihu"] == "话剧 推荐"
    assert "nope" not in out


def test_illustration_queries_drop_empty():
    from board.illustrations import normalize_illustration_queries
    rows = normalize_illustration_queries(
        [{"query": "hotpot", "caption": "火锅"}, {"query": "", "caption": "x"}, "ramen"],
        2,
    )
    assert rows[0]["query"] == "hotpot"
    assert rows[1]["query"] == "ramen"


def test_weixin_article_keeps_body_images():
    from board.weixin_sogou import apply_weixin_article, article_from_html, weixin_pic_urls

    html = """
    <meta property="og:image" content="https://mmbiz.qpic.cn/mmbiz_jpg/cover/0?wx_fmt=jpeg">
    <h1 id="activity-name">iPhone Duo 上手</h1>
    <div id="js_content">
      折叠只是开始，这几个细节才是重点。上手之后铰链手感和外屏亮度都要比想象中更能用。
      <img data-src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/body1/640?wx_fmt=jpeg">
      <img src="https://res.wx.qq.com/mmad/emoji/smile.png">
      <img data-original="https://mmbiz.qpic.cn/mmbiz_png/body2/640?wx_fmt=png">
    </div>
    <script></script>
    """
    urls = weixin_pic_urls(html)
    assert urls[0].startswith("https://mmbiz.qpic.cn/mmbiz_jpg/cover")
    assert any("body1" in url for url in urls)
    assert any("body2" in url for url in urls)
    assert all("emoji" not in url for url in urls)
    article = article_from_html(html, "https://mp.weixin.qq.com/s?__biz=demo")
    assert article is not None
    assert "折叠只是开始" in article["text"]
    row = {"title": "旧标题"}
    apply_weixin_article(row, article)
    assert "body1" in row["image_list"]
    assert row["cover_url"].startswith("https://mmbiz.qpic.cn/mmbiz_jpg/cover")
    assert len(row["images"]) >= 2


def test_local_media_rejects_weixin_stub_and_uses_mp_referer():
    from board.local_media import image_referers, looks_like_real_image
    from board.server import item_image_urls, normalize_item

    stub = b"\xff\xd8\xff\xe0" + b"\x00" * 2000
    real = b"\xff\xd8\xff\xe0" + b"\x00" * 8000
    qpic = "https://mmbiz.qpic.cn/mmbiz_jpg/demo/0?wx_fmt=jpeg"
    assert not looks_like_real_image(stub, qpic)
    assert looks_like_real_image(real, qpic)
    refs = image_referers(qpic, "mp", "https://mp.weixin.qq.com/s?__biz=demo")
    assert refs[0].startswith("https://mp.weixin.qq.com/")
    assert "weixin.sogou.com" not in "".join(refs)
    item = normalize_item(
        "mp",
        {
            "note_id": "wx1",
            "title": "公众号图",
            "desc": "正文",
            "cover_url": qpic,
            "image_list": qpic + ",https://mmbiz.qpic.cn/mmbiz_jpg/demo2/0?wx_fmt=jpeg",
        },
        0,
    )
    assert len(item_image_urls(
        {
            "cover_url": qpic,
            "image_list": "https://mmbiz.qpic.cn/mmbiz_jpg/demo2/0?wx_fmt=jpeg",
        },
        "mp",
    )) == 2
    assert item["images"][0] == qpic


def test_attach_local_images_rewrites_cached_files(tmp_path):
    from board.local_media import attach_local_images, media_cache_path

    url = "https://pic1.zhimg.com/v2-demo.jpg"
    item = {"id": "zhihu:99", "platform": "zhihu", "images": [url]}
    path = media_cache_path(tmp_path, "zhihu:99", 0, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8000)
    attach_local_images(tmp_path, item)
    assert item["images"][0].startswith("frame:")
    assert path.as_posix() in item["images"][0]


def test_weixin_should_stop_caps_public_reads_and_time():
    from board.weixin_sogou import weixin_should_stop

    assert weixin_should_stop(wanted=20, public_n=8, elapsed_s=30, work_s=20) == "enough"
    assert weixin_should_stop(wanted=20, public_n=2, elapsed_s=30, work_s=20) == ""
    assert weixin_should_stop(wanted=20, public_n=2, elapsed_s=400, work_s=20) == "time"
    assert weixin_should_stop(wanted=20, public_n=2, elapsed_s=40, work_s=211) == "time"


def test_sogou_jump_keeps_token_and_reads_js_url():
    from board.weixin_sogou import extract_weixin_url, sogou_jump_url

    raw = "https://weixin.sogou.com/link?url=abc&type=2&query=成都&token=DEADBEEF"
    jump = sogou_jump_url(raw)
    assert "token=DEADBEEF" in jump
    assert "type=2" in jump
    html = """
    <script>
    var url = '';
    url += 'https://mp.weixin.qq.com/s?src=3';
    url += '&timestamp=1';
    window.location.replace(url);
    </script>
    """
    assert extract_weixin_url(html).startswith("https://mp.weixin.qq.com/s?src=3")


def test_weixin_feedgrab_candidates_become_index_rows():
    from board.foreign import rows_from_feedgrab_search
    rows = rows_from_feedgrab_search(
        "mp",
        {
            "ok": True,
            "candidates": [
                {
                    "title": "成都室内话剧周末场",
                    "url": "https://weixin.sogou.com/link?url=http://mp.weixin.qq.com/s?__biz=demo",
                    "snippet": "周末两场室内话剧，适合带老人。",
                    "author": "成都文化日历",
                }
            ],
        },
    )
    assert rows[0]["title"] == "成都室内话剧周末场"
    assert rows[0]["text_source"] == "index"
    assert rows[0]["lang"] == "zh"
    assert "sogou" in rows[0]["url"]


def test_weixin_gate_click_and_expired_replacement():
    from board.weixin_sogou import (
        is_weixin_expired,
        is_weixin_gate,
        pick_weixin_replacement,
        weixin_row_key,
    )

    moved = """
    <p>该账号已迁移至新的账号，原账号已回收。</p>
    <a class="weui-btn">访问文章</a>
    """
    dead = "<p>此内容已被发布者删除</p>"
    live = '<h1 id="activity-name">周末话剧</h1><div id="js_content">室内话剧周末两场，适合带老人看，现场有字幕讲解。</div><script>'
    assert is_weixin_gate(moved)
    assert is_weixin_expired(moved)
    assert is_weixin_expired(dead)
    assert not is_weixin_expired(live)
    seen = {
        weixin_row_key(
            "https://weixin.sogou.com/link?url=https://mp.weixin.qq.com/s?__biz=aaa&mid=1&idx=1"
        )
    }
    extra = pick_weixin_replacement(
        seen,
        [
            {"title": "旧的", "url": "https://mp.weixin.qq.com/s?__biz=aaa&mid=1&idx=1"},
            {"title": "补上的", "url": "https://mp.weixin.qq.com/s?__biz=bbb&mid=2&idx=1"},
        ],
    )
    assert extra is not None
    assert extra["title"] == "补上的"


def test_weixin_search_keeps_chengdu_drops_canada():
    from board.weixin_sogou import (
        filter_weixin_rows,
        weixin_row_relevant,
        weixin_search_query,
    )

    compact = weixin_search_query("国庆成都老年人小众旅游景点推荐")
    assert "成都" in compact
    assert weixin_row_relevant(
        "成都国庆旅游",
        {"title": "国庆带爸妈逛成都小众点", "desc": "熊猫基地人少"},
    )
    assert not weixin_row_relevant(
        "成都国庆旅游",
        {"title": "加拿大国庆旅游攻略", "desc": "温哥华烟花和多伦多游行"},
    )
    kept = filter_weixin_rows(
        "成都国庆旅游",
        [
            {"title": "加拿大国庆出游", "desc": "温哥华", "url": "a"},
            {"title": "成都国庆避峰路线", "desc": "宽窄巷子", "url": "b"},
        ],
    )
    assert [row["url"] for row in kept] == ["b"]


def test_search_check_drops_mismatch_and_keeps_fit():
    from datetime import datetime, timezone

    from board.freshness import fetch_budget, reject_reason, select_items

    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    query = "成都国庆旅游"
    stale_may = {
        "title": "携程发布五一国内游最热门目的地昆明第三丽江第四",
        "text": "五一出行趋势：中短途游为主",
        "published": "2016-04-26 14:20:01",
    }
    canada = {
        "title": "加拿大国庆旅游攻略",
        "text": "温哥华烟花和多伦多游行",
        "published": "2025-07-01 10:00",
    }
    fit = {
        "title": "成都国庆避峰：熊猫基地和宽窄巷子怎么排",
        "text": "国庆带爸妈逛成都，避开网红店",
        "published": "2025-09-20 09:00",
    }
    gift_off = {
        "title": "五一跟团云南",
        "text": "昆明大理丽江行程",
        "published": "2024-04-20 09:00",
    }
    gift_on = {
        "title": "20岁生日送实用耳机就够了",
        "text": "预算两千内的礼物先看她缺什么",
        "published": "2022-03-01 09:00",
    }
    assert reject_reason(query, stale_may, now=now)
    assert reject_reason(query, canada, now=now)
    assert reject_reason(query, fit, now=now) is None
    assert reject_reason("20岁女生生日礼物", gift_off, now=now)
    assert reject_reason("20岁女生生日礼物", gift_on, now=now) is None
    kept, dropped = select_items(query, [stale_may, canada, fit], wanted=1, now=now)
    assert [row["title"] for row in kept] == [fit["title"]]
    assert len(dropped) == 2
    assert 10 <= fetch_budget(10) <= 18
    assert fetch_budget(30) < 45


def test_search_check_drops_last_year_festival_event():
    from datetime import datetime, timezone

    from board.freshness import reject_reason

    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    last_year = {
        "title": "成都文殊院乙巳年中秋游园会 时间：2025年10月06日11:00~16:00",
        "text": "公益活动，无需报名，DIY月饼、陶艺、手绘。",
        "published": "2025-10-06 07:57",
    }
    upcoming = {
        "title": "成都文殊院中秋游园会 时间：2026年10月06日11:00~16:00",
        "text": "公益活动，无需报名，DIY月饼。",
        "published": "2026-09-10 10:00",
    }
    custom = {
        "title": "中秋家宴怎么摆",
        "text": "月饼和团圆饭摆盘，不办对外活动。",
        "published": "2025-09-12 10:00",
    }
    assert reject_reason("成都中秋节活动", last_year, now=now)
    assert reject_reason("成都中秋节活动", upcoming, now=now) is None
    assert reject_reason("中秋家宴怎么做", custom, now=now) is None


def test_query_places_does_not_treat_rumen_as_city():
    from board.freshness import query_places, reject_reason, select_items

    assert "入门" not in query_places("Python入门学习规划")
    assert "入门" not in query_places("零基础入门编程")
    assert "上门" not in query_places("上门维修")
    assert query_places("成都国庆旅游") == ["成都"]
    assert "东京" in query_places("东京自由行")
    assert "九寨沟" in query_places("九寨沟门票")

    news = {
        "title": "2026年终极Python学习路线图：从初学者到专家",
        "text": "The Ultimate 2026 Python Learning Roadmap: From Beginner to Expert",
        "published": "2026-09-10",
    }
    video = {
        "title": "完整的Python学习路线图（从A到Z）",
        "text": "How to start learning Python Language",
        "published": "2026-09-01",
    }
    assert reject_reason("Python入门学习规划", news) is None
    assert reject_reason("Python入门学习规划", video) is None
    kept, dropped = select_items("Python入门学习规划", [news, video], wanted=2)
    assert [row["title"] for row in kept] == [news["title"], video["title"]]
    assert dropped == []


def test_rss_items_parse_title_and_link():
    from board.foreign import looks_english, parse_rss_items
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Hidden theater</title><link>https://news.example/a</link><description>Indoor show</description></item>
    </channel></rss>"""
    rows = parse_rss_items(xml)
    assert rows[0]["title"] == "Hidden theater"
    assert rows[0]["url"] == "https://news.example/a"
    assert looks_english("indoor standup comedy")
    assert not looks_english("室内脱口秀话剧")


def test_bluesky_search_v2_uses_query_param():
    from board.foreign import bluesky_search_terms, bluesky_search_urls

    urls = bluesky_search_urls("Doubao phone assistant", 30)
    assert any("searchPostsV2?query=Doubao" in url for url in urls)
    assert any("searchPosts?q=Doubao" in url for url in urls)
    assert bluesky_search_terms("IPhone Duo选购建议") == ["IPhone Duo选购建议", "IPhone Duo"]
    assert "iPhone Duo" in bluesky_search_terms("iPhone Duo buying guide")


def test_parse_platform_queries_accepts_json():
    from board.server import parse_platform_queries
    out = parse_platform_queries('{"xhs":"室内脱口秀","reddit":"indoor standup"}')
    assert out["xhs"] == "室内脱口秀"
    assert out["reddit"] == "indoor standup"


def test_fill_illustration_hits_drop_url_without_source():
    from board import illustrations

    def fake_fetch(query, topic="", skip_urls=None):
        return {"url": "https://img2.baidu.com/it/u=9&fm=253", "source": "百度图片"}

    original = illustrations.fetch_public_image
    illustrations.fetch_public_image = fake_fetch
    try:
        rows = illustrations.fill_illustration_hits(
            [
                {"query": "hotpot", "caption": "火锅", "url": "https://evil.example/x.jpg"},
                {
                    "query": "keep",
                    "caption": "留下",
                    "url": "https://img2.baidu.com/it/u=1&fm=253",
                    "source": "百度图片",
                },
            ]
        )
    finally:
        illustrations.fetch_public_image = original
    assert rows[0]["url"].startswith("https://img2.baidu.com")
    assert rows[1]["url"].startswith("https://img2.baidu.com")
    assert "wikimedia" not in (rows[0]["url"] + rows[1]["url"])


def test_fill_drops_wikimedia_hits():
    from board import illustrations

    def fake_fetch(query, topic="", skip_urls=None):
        return {
            "url": "https://upload.wikimedia.org/wikipedia/commons/x.jpg",
            "source": "Wikimedia Commons",
        }

    original = illustrations.fetch_public_image
    illustrations.fetch_public_image = fake_fetch
    try:
        rows = illustrations.fill_illustration_hits(
            [{"query": "成都中秋", "caption": "夜景", "url": "https://upload.wikimedia.org/x.jpg", "source": "Wikimedia Commons"}]
        )
    finally:
        illustrations.fetch_public_image = original
    assert rows[0].get("query") == "成都中秋"
    assert not rows[0].get("url")


def test_baidu_image_pick_and_domestic_host():
    from board.illustrations import (
        _pick_baidu,
        is_domestic_image_url,
        keep_illustration_url,
        usable_image_url,
    )

    baidu = "https://img2.baidu.com/it/u=123&fm=253"
    assert usable_image_url(baidu)
    assert is_domestic_image_url(baidu)
    assert keep_illustration_url(baidu, "百度图片")
    assert not keep_illustration_url(
        "https://live.staticflickr.com/3938/15437879317_ddd94e2afe_b.jpg",
        "Openverse",
    )
    hit = _pick_baidu(
        {
            "data": [
                {},
                {
                    "middleURL": baidu,
                    "fromURLHost": "example.com",
                    "fromPageTitle": "成都茶馆",
                },
            ]
        },
        "成都茶馆",
    )
    assert hit is not None
    assert hit["url"] == baidu
    assert hit["source"].startswith("百度图片")


def test_commons_skips_djvu_and_keeps_jpeg():
    from board.illustrations import _off_topic, _pick_commons, usable_image_url

    assert not usable_image_url(
        "https://upload.wikimedia.org/wikipedia/commons/c/c2/CADAL09014119_book.djvu"
    )
    assert usable_image_url(
        "https://upload.wikimedia.org/wikipedia/commons/d/da/teahouse.jpg"
    )
    assert _off_topic(
        "川西古镇石桥流水",
        "Japanese countryside river peaceful-jp-scenery",
    )
    assert not _off_topic(
        "川西古镇石桥流水",
        "Sichuan ancient town river Chengdu",
    )
    hit = _pick_commons(
        {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:CADAL09014119 川西北步行記.djvu",
                        "imageinfo": [
                            {
                                "mime": "image/vnd.djvu",
                                "url": "https://upload.wikimedia.org/wikipedia/commons/c/c2/book.djvu",
                                "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/book.djvu/1280px-book.djvu.jpg",
                            }
                        ],
                    },
                    "2": {
                        "title": "File:Chengdu teahouse.jpg",
                        "imageinfo": [
                            {
                                "mime": "image/jpeg",
                                "url": "https://upload.wikimedia.org/wikipedia/commons/d/da/teahouse.jpg",
                                "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/da/teahouse.jpg/1280px-teahouse.jpg",
                                "extmetadata": {
                                    "Artist": {"value": "<a href='/wiki/User:Foo'>茶馆摄影师</a>"},
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                },
                            }
                        ],
                    },
                }
            }
        }
    )
    assert hit["url"].endswith("1280px-teahouse.jpg")
    assert "茶馆摄影师" in hit["source"]
    assert "CADAL" not in hit["url"]


def test_illustration_search_terms_shorten_teahouse_query():
    from board.illustrations import _search_terms

    terms = _search_terms("成都古镇老茶馆盖碗茶")
    assert terms[0] == "成都古镇老茶馆盖碗茶"
    assert any(term == "成都 茶馆" or "茶馆" in term for term in terms)


def test_python_illustration_query_stays_on_code_not_map():
    from board.illustrations import (
        _image_off_topic,
        _pick_image_row,
        bind_illustration_query,
        illustration_core_tokens,
        illustration_needs_refetch,
        suggest_illustration_queries,
    )

    topic = "Python入门学习规划"
    assert "Python" in illustration_core_tokens(topic)
    assert "入门" not in illustration_core_tokens(topic)
    bound = bind_illustration_query("学习路线图", topic)
    assert "Python" in bound
    assert "代码" in bound or "编程" in bound
    assert "路线" not in bound
    assert suggest_illustration_queries(topic, "先看路线再写代码", 2)
    assert all("路线" not in row["query"] for row in suggest_illustration_queries(topic, "先看路线再写代码", 2))
    assert _image_off_topic(topic, "中国交通地图 高德导航")
    assert _pick_image_row(
        "https://img2.baidu.com/it/u=1&fm=253",
        "中国交通地图",
        "百度图片",
        topic,
        None,
        ["Python"],
    ) is None
    assert illustration_needs_refetch({"query": "学习路线", "caption": "路线"}, topic)
    assert illustration_needs_refetch(
        {"query": "print", "caption": "第1天要看见的不是全套课程，而是一个真的能跑起来的 print。"},
        topic,
    )


def test_human_status_treats_kuaishou_login_page_as_login():
    from board.server import human_status

    status = human_status("ks", "快手", "[KuaishouLogin] 已打开快手登录页，请在弹出的 Chrome 里扫码或点登录。")
    assert status["step"] == "login"
    assert "快手" in status["title"]


def test_human_status_keeps_xhs_verify_and_expired_login():
    from board.server import human_status, platform_needs_relogin
    from tools.login_ready import LOGIN_WAIT_SECONDS

    blocked = human_status(
        "xhs",
        "小红书",
        "[XiaoHongShuClient.request] 小红书被安全验证挡住了（HTTP 461）。",
    )
    assert blocked["step"] == "verify"
    waiting = human_status("xhs", "小红书", "还在等你过小红书安全验证。窗口先留着。")
    assert waiting["step"] == "verify"
    silent = human_status(
        "xhs",
        "小红书",
        "取详情的接口被风控挡住了。页面上没有验证弹窗。",
    )
    assert silent["step"] == "risk"
    poked = human_status("xhs", "小红书", "收到接着搜，马上再取小红书。")
    assert poked["step"] == "retry"
    listed = human_status("xhs", "小红书", "详情被拦，先收下搜索列表第 1 条")
    assert listed["step"] == "items"
    from_page = human_status("xhs", "小红书", "详情页已经打开，页面上没有安全验证。改从页面收正文。")
    assert from_page["step"] == "items"
    expired = human_status("xhs", "小红书", "media_platform.xhs.exception.DataFetchError: 登录已过期")
    assert expired["step"] == "login"
    assert human_status("xhs", "小红书", "[XiaoHongShuClient.pong] Login state result: False") is None
    logged = human_status("xhs", "小红书", "[XiaoHongShuClient.pong] Login state result: True")
    assert logged["step"] == "logged_in"
    success = human_status("xhs", "小红书", "[xhs] 小红书登录成功，登录态会留在这台电脑上。")
    assert success["step"] == "logged_in"
    assert LOGIN_WAIT_SECONDS >= 480
    assert platform_needs_relogin({"error": "登录已过期"})
    assert not platform_needs_relogin({"error": "本页没有新内容"})


def test_kuaishou_login_url_is_passport():
    from media_platform.kuaishou.login import already_on_login

    class Fake:
        def __init__(self, url):
            self.url = url

    assert already_on_login(Fake("https://passport.kuaishou.com/pc/account/login/?sid=kuaishou.server.web"))
    assert not already_on_login(Fake("https://www.kuaishou.com/?isHome=1"))


def test_ensure_judgment_fills_empty_after_conclusion():
    from board.server import enrich_report

    board = {
        "report": {
            "title": "选购结论",
            "overview": "",
            "answer": "生态用户加大屏刚需就选 iPhone Duo，影像优先就选 18 Pro。",
            "conclusion": {
                "answer": "生态用户加大屏刚需就选 iPhone Duo，影像优先就选 18 Pro。"
            },
            "picks": [
                {"name": "iPhone Duo", "verdict": "go", "tag": "选", "reason": "大屏刚需"},
                {"name": "华为 Pura X Max", "verdict": "watch", "tag": "再比", "reason": "更轻"},
            ],
            "sections": [{"heading": "形态", "thesis": "先定形态", "body": "先看要不要折叠。"}],
            "sources": {"summary": "共 3 条。", "platforms": [{"name": "抖音", "count": 3}]},
        },
        "judgment": {
            "headline_zh": "「IPhone Duo选购建议」",
            "one_line_judgment": "",
            "plain_language": "",
            "worth_interrupting": False,
            "value": {"level": 2, "label": "知道即可", "reason": ""},
            "caveats": [],
            "next_watch": "",
        },
    }
    items = [
        {
            "id": "dy:1",
            "title": "上手",
            "platform_label": "抖音",
            "published": "2026-09-14",
        }
    ]
    out = enrich_report("IPhone Duo选购建议", items, board)
    judgment = out["judgment"]
    assert judgment["one_line_judgment"]
    assert "Duo" in judgment["one_line_judgment"] or "18" in judgment["one_line_judgment"]
    assert judgment["headline_zh"] != "「IPhone Duo选购建议」"
    assert judgment["caveats"]
    assert judgment["value"]["reason"]


def test_pdf_html_follows_web_report_layout():
    from board.export_report import board_to_html

    html = board_to_html(
        {
            "intent": {"label": "选购", "job": "怎么选"},
            "report": {
                "title": "选购结论",
                "deck": "先看怎么选",
                "overview": "总述一段。",
                "answer": "优先看折叠形态。",
                "conclusion": {
                    "answer": "优先看折叠形态。",
                    "flow": [{"name": "先定形态", "note": "要不要折叠。"}],
                    "table": {"headers": ["方案", "适合"], "rows": [["折叠", "尝鲜"]]},
                },
                "picks": [{"name": "方案A", "verdict": "go", "reason": "屏幕更大", "tag": "选"}],
                "map": [{"name": "形态", "children": ["折叠", "直板"]}],
                "platform_briefs": [{"name": "YouTube", "answer": "在讲折叠。", "worth": "只有简介"}],
                "sections": [
                    {
                        "heading": "形态怎么选",
                        "thesis": "先定要不要折叠。",
                        "body": "正文一段。",
                        "evidence": [{"item_id": "yt:1", "text": "折叠发布"}],
                    }
                ],
                "sources": {"summary": "共 1 条。", "platforms": [{"name": "YouTube", "count": 1}]},
            },
            "judgment": {},
        },
        [
            {
                "id": "yt:1",
                "title": "折叠发布",
                "platform_label": "YouTube",
                "author": "MKBHD",
                "url": "https://youtu.be/x",
            }
        ],
        [],
        "IPhone Duo选购建议",
    )
    assert 'class="picks-wrap"' in html
    assert 'class="concl-flow"' in html
    assert 'class="deep-grid"' in html
    assert 'class="deep-copy"' in html
    assert 'class="deep-right"' in html
    assert 'class="briefs"' in html
    assert "先定形态" in html
    assert "对照着读" in html


def test_raw_export_has_author_fans_and_no_endnotes():
    from board.export_report import author_label, board_to_markdown

    item = {
        "id": "xhs:1",
        "platform_label": "小红书",
        "title": "室内脱口秀",
        "author": "阿乐",
        "fans": "12000",
        "liked": "88",
        "collected": "12",
        "comment_count": "3",
        "share_count": "1",
        "url": "https://example.com/a",
    }
    assert author_label(item) == "阿乐（12000粉）"
    md = board_to_markdown(
        {
            "report": {"title": "报告", "overview": "总述", "sections": [], "picks": [], "map": []},
            "judgment": {},
        },
        [item],
        [],
        "室内表演",
    )
    assert "文末文献" not in md
    assert "原始检索结果" in md
    assert "阿乐（12000粉）" in md
    assert "赞 88" in md


def test_ask_export_keeps_illustration():
    from board.export_report import board_to_markdown

    md = board_to_markdown(
        {
            "report": {"title": "报告", "overview": "", "sections": [], "picks": [], "map": []},
            "judgment": {},
        },
        [],
        [
            {
                "q": "彭镇茶馆长什么样",
                "a": "老街两边都是盖碗茶。",
                "illustrations": [
                    {
                        "url": "https://img2.baidu.com/it/u=1&fm=253",
                        "caption": "彭镇老茶馆",
                        "source": "百度图片",
                    }
                ],
            }
        ],
        "成都",
    )
    assert "彭镇老茶馆" in md
    assert "img2.baidu.com" in md


def test_bilibili_detail_task_swallows_connect_error():
    import asyncio
    from unittest.mock import AsyncMock

    import httpx

    from media_platform.bilibili.core import BilibiliCrawler

    crawler = BilibiliCrawler.__new__(BilibiliCrawler)
    crawler.bili_client = AsyncMock()
    crawler.bili_client.get_video_info.side_effect = httpx.ConnectError("tls")
    result = asyncio.run(crawler.get_video_info_task(1, "BVxxx", asyncio.Semaphore(1)))
    assert result is None


def test_reddit_rows_from_listing_and_archive():
    from board.foreign import reddit_rows_from_posts

    listing = [
        {
            "data": {
                "id": "abc",
                "title": "Doubao phone",
                "permalink": "/r/android/comments/abc/hi/",
                "selftext": "body",
                "author": "u1",
            }
        }
    ]
    archive = [
        {
            "id": "def",
            "title": "SAEP protocol",
            "permalink": "/r/singularity/comments/def/t/",
            "selftext": "",
            "author": "u2",
        }
    ]
    rows = reddit_rows_from_posts(listing, 5)
    extra = reddit_rows_from_posts(archive, 5)
    assert rows[0]["title"] == "Doubao phone"
    assert "/r/android/comments/abc/hi/" in rows[0]["url"]
    assert extra[0]["title"] == "SAEP protocol"


def test_report_timeline_keeps_every_dated_item():
    from board.server import report_timeline

    items = [
        {"id": f"x:{i}", "published": f"2026-09-{(i % 10) + 1:02d} 12:00", "title": f"t{i}", "platform_label": "小红书"}
        for i in range(30)
    ]
    items.append({"id": "x:none", "title": "no time"})
    out = report_timeline(items)
    assert len(out) == 30
    assert out[0]["item_id"] == "x:0" or out[0]["when"] <= out[-1]["when"]


def test_axis_html_now_anchor_and_stale_outliers():
    from board.export_report import _axis_html

    items = [{"id": "old", "published": "2019-04-16 11:17", "title": "galaxy fold"}]
    items += [
        {"id": f"n{i}", "published": f"2026-09-{10 + (i % 7):02d} 12:00", "title": f"n{i}"}
        for i in range(20)
    ]
    items.append({"id": "none", "title": "no time"})
    html = _axis_html(items)
    assert "现在" in html
    assert "过旧" in html
    assert "参考价值有限" in html
    assert html.count("<i style=") == 20


def test_pick_frame_follows_query_grammar():
    from board.intent_frame import detect_query_intent, pick_frame, pick_tag

    assess = detect_query_intent("豆包手机助手")
    assert assess["mode"] == "assess"
    assert pick_frame(assess["mode"])["go"] == "能用"
    assert pick_tag({"verdict": "skip"}, pick_frame("assess")) == "别指望"
    assert detect_query_intent("豆包手机助手靠不靠谱")["mode"] == "argue"
    assert detect_query_intent("该不该换豆包手机")["mode"] == "decide"
    assert detect_query_intent("成都美食攻略")["mode"] == "howto"
    assert detect_query_intent("成都美食推荐")["mode"] == "recommend"
    assert pick_tag({"verdict": "go", "tag": "先做"}, pick_frame("decide")) == "先做"
    shop = detect_query_intent("豆包手机助手选购指南")
    assert shop["mode"] == "recommend"
    assert pick_frame(shop["mode"])["go"] == "选"
    assert pick_tag({"tag": "先做"}, pick_frame("recommend"), "recommend") == "选"
    assert pick_tag({"tag": "照做"}, None, "recommend") == "选"
    assert pick_tag({"tag": "成立"}, pick_frame("comprehensive")) == "撑得住"
    assert pick_tag({"tag": "不成立"}, pick_frame("comprehensive")) == "撑不住"
    assert pick_tag({"tag": "存疑"}, pick_frame("comprehensive")) == "还说不准"
    old = pick_frame(
        "comprehensive",
        {
            "title": "哪些结论成立，哪些先别信",
            "lead": "标签跟着这道题走，不是固定的选、避、再看。",
            "go": "成立",
            "skip": "不成立",
            "watch": "存疑",
        },
    )
    assert old["go"] == "撑得住"
    assert old["skip"] == "撑不住"
    assert "成立" not in old["title"]
    assert "对得上" in old["lead"]
    stolen = pick_frame(
        "recommend",
        {"title": "能落地的做法", "go": "先做", "skip": "别做", "watch": "取决于"},
    )
    assert stolen["title"] == "现在更该选"
    assert stolen["go"] == "选"
    assert stolen["skip"] == "不选"


def test_enrich_shopping_conclusion_drops_howto_tags():
    items = [
        {"id": "xhs:1", "title": "消费者版", "text": "近7天实测能操作手机"},
        {"id": "xhs:2", "title": "预览版", "text": "半年前开箱"},
    ]
    out = enrich_report(
        "豆包手机助手选购指南",
        items,
        {
            "sections": [_section("该买消费者版还是预览版")],
            "picks": [
                {
                    "name": "消费者版",
                    "verdict": "go",
                    "tag": "先做",
                    "reason": "近7天多条实测都说能操作手机",
                    "item_id": "xhs:1",
                },
                {
                    "name": "预览版",
                    "verdict": "skip",
                    "tag": "别做",
                    "reason": "半年前体验，不能当现在的买点",
                    "item_id": "xhs:2",
                },
            ],
            "report": {
                "title": "选购",
                "overview": "",
                "answer": "现在该买消费者版，不要拿预览版当现货。",
                "conclusion": {
                    "form": "mix",
                    "answer": "现在该买消费者版，不要拿预览版当现货。",
                    "flow": [
                        {"name": "先分清版本", "note": "消费者版才是能买的。"},
                        {"name": "再看机型", "note": "材料里反复点到 NaviX Ultra。"},
                    ],
                },
                "pick_frame": {
                    "title": "能落地的做法",
                    "lead": "照着做",
                    "go": "先做",
                    "skip": "别做",
                    "watch": "取决于",
                },
            },
            "judgment": {"headline_zh": "判断", "one_line_judgment": "买消费者版"},
        },
    )
    assert out["intent"]["mode"] == "recommend"
    assert [row["tag"] for row in out["report"]["picks"]] == ["选", "不选"]
    assert out["report"]["pick_frame"]["go"] == "选"
    assert out["report"]["pick_frame"]["title"] == "现在更该选"
    assert out["report"]["answer"].startswith("现在该买")
    assert out["report"]["conclusion"]["form"] == "mix"
    assert len(out["report"]["conclusion"]["flow"]) == 2


def test_agent_search_keeps_model_and_time():
    from board.server import normalize_agent_search, stamp_agent_search_time

    packed = normalize_agent_search(
        {
            "overview": "先写能跑的 print。",
            "points": [{"heading": "先跑起来", "body": "不要先囤课。"}],
            "model": "deepseek-flash",
            "created_at": "2026-09-17T16:22:00+08:00",
        }
    )
    assert packed["model"] == "deepseek-flash"
    assert packed["created_at"].startswith("2026-09-17")
    assert stamp_agent_search_time(packed)["created_at"].startswith("2026-09-17")


def test_foreign_zh_seeds_cjk_and_keeps_english_for_translate():
    from board.server import (
        apply_foreign_zh,
        foreign_translate_need,
        format_time,
        mostly_cjk,
        seed_foreign_zh,
    )

    assert mostly_cjk("豆包手机助手正式上线")
    assert not mostly_cjk("Luo Yonghao-Strongly-Supported Doubao Phone")
    cjk = {"id": "1", "title": "豆包手机助手上线", "desc": "36氪报道消费者版"}
    en = {
        "id": "2",
        "title": "Luo Yonghao-Strongly-Supported Doubao Phone",
        "desc": "Android ecosystem finale",
    }
    seed_foreign_zh([cjk, en])
    assert "豆包" in cjk["text_zh"]
    assert not en.get("text_zh")
    assert foreign_translate_need([cjk, en]) == [en]
    apply_foreign_zh(en, "罗永浩力挺豆包手机", "文章谈安卓生态终局。")
    assert en["title"] == "罗永浩力挺豆包手机"
    assert en["title_orig"].startswith("Luo")
    assert "2025-12-07" in format_time("Sunday, 07 Dec 2025 08:00:00 GMT")


def test_load_job_items_can_recover_dropped_foreign():
    import json
    import tempfile
    from pathlib import Path

    from board import server

    raw = {
        "id": "38ed9a177fbe409b",
        "note_id": "38ed9a177fbe409b",
        "title": "Luo Yonghao-Strongly-Supported Doubao Phone: Holds the Ultimate Finale",
        "desc": "36Kr English index",
        "content": "36Kr English index",
        "url": "https://example.com/luo",
        "lang": "en",
        "text_source": "index",
        "published_at": "Sunday, 07 Dec 2025 08:00:00 GMT",
    }
    with tempfile.TemporaryDirectory() as folder:
        job = Path(folder) / "job"
        (job / "gnews" / "jsonl").mkdir(parents=True)
        (job / "meta.json").write_text(
            json.dumps({"query": "豆包手机助手"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (job / "gnews" / "jsonl" / "search_contents_2026-09-16.jsonl").write_text(
            json.dumps(raw, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        old = server.RUNS_DIR
        server.RUNS_DIR = Path(folder)
        try:
            _query, kept = server.load_job_items("job")
            assert kept == []
            _query, found = server.load_job_items("job", extra_ids={"gnews:38ed9a177fbe409b"})
        finally:
            server.RUNS_DIR = old
    assert found[0]["id"] == "gnews:38ed9a177fbe409b"
    assert "Luo" in found[0]["title"]


def test_fold_zhihu_images_keeps_wide_still():
    from board.server import _fold_zhihu_images

    urls = [
        "https://picx.zhimg.com/v2-abc123_r.jpg",
        "https://picx.zhimg.com/v2-abc123_1440w.jpg",
        "https://picx.zhimg.com/v2-def456.jpg",
    ]
    out = _fold_zhihu_images(urls)
    assert out == [
        "https://picx.zhimg.com/v2-abc123_1440w.jpg",
        "https://picx.zhimg.com/v2-def456.jpg",
    ]


def test_saved_board_ready_skips_empty_or_missing_briefs():
    from board.server import _saved_board_ready

    assert not _saved_board_ready({})
    assert _saved_board_ready(
        {
            "harvest": True,
            "report": {"sections": [{"heading": "小红书", "body": "有材料"}]},
        }
    )
    warm = {
        "report": {
            "sections": [{"heading": "发热", "body": "烫"}],
            "platform_briefs": [
                {"name": "小红书", "answer": "上手在讲折痕。"},
                {"name": "公众号", "answer": "转述了发烫。"},
            ],
            "sources": {"platforms": [{"name": "小红书", "count": 2}]},
            "engagement": [{"name": "小红书"}],
        }
    }
    assert _saved_board_ready(warm)
    warm["report"]["platform_briefs"][1]["answer"] = "本批未返回可读的公众号正文。"
    assert not _saved_board_ready(warm)


def test_job_coverage_prefers_saved_file(tmp_path):
    from board.server import job_coverage

    (tmp_path / "coverage.json").write_text(
        '[{"platform":"xhs","label":"小红书","raw":2,"kept":2}]',
        encoding="utf-8",
    )
    rows = job_coverage(tmp_path, "IPhone Duo测评")
    assert rows[0]["platform"] == "xhs"
    assert rows[0]["kept"] == 2


def test_cache_is_real_does_not_need_full_bytes(tmp_path):
    from board.local_media import cache_is_real

    path = tmp_path / "pic.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 4000)
    assert cache_is_real(path, "https://pic1.zhimg.com/v2.jpg")
    tiny = tmp_path / "tiny.jpg"
    tiny.write_bytes(b"\xff\xd8\xff\xe0")
    assert not cache_is_real(tiny, "https://pic1.zhimg.com/v2.jpg")


def test_build_coverage_lists_empty_requested_station():
    import tempfile
    from pathlib import Path

    from board.server import build_coverage

    with tempfile.TemporaryDirectory() as raw:
        rows = build_coverage(Path(raw), "豆包手机助手", ["bili", "reddit"])
    assert [row["platform"] for row in rows] == ["bili", "reddit"]
    assert rows[0]["raw"] == 0 and rows[1]["kept"] == 0


def test_forget_job_removes_dir_and_history():
    import tempfile
    from pathlib import Path

    import board.server as server

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        job = root / "20260916-test-aaaaaa"
        job.mkdir()
        (job / "meta.json").write_text(
            '{"query":"测试话题","job_id":"20260916-test-aaaaaa"}',
            encoding="utf-8",
        )
        (job / "board.json").write_text('{"report":{"title":"测试"}}', encoding="utf-8")
        old = server.RUNS_DIR
        server.RUNS_DIR = root
        try:
            server.remember_job(job.name)
            assert any(row["job_id"] == job.name for row in server.read_history())
            ok, err = server.forget_job("../secret")
            assert ok is False
            ok, err = server.forget_job(job.name)
            assert ok and not err
            assert not job.exists()
            assert all(row["job_id"] != job.name for row in server.read_history())
        finally:
            server.RUNS_DIR = old


def test_clear_run_cache_keeps_recent_and_drops_leftovers():
    import tempfile
    from pathlib import Path

    import board.server as server

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        recent = root / "20260917-keep-aaaaaa"
        recent.mkdir()
        (recent / "meta.json").write_text(
            '{"query":"还在最近","job_id":"20260917-keep-aaaaaa"}',
            encoding="utf-8",
        )
        (recent / "media").mkdir()
        (recent / "media" / "cover.jpg").write_bytes(b"keep-me" * 200)
        leftover = root / "20260915-old-bbbbbb"
        leftover.mkdir()
        (leftover / "media").mkdir()
        (leftover / "media" / "gone.jpg").write_bytes(b"drop-me" * 200)
        illus = root / "_illus"
        illus.mkdir()
        (illus / "pic.jpg").write_bytes(b"cache" * 200)
        old = server.RUNS_DIR
        server.RUNS_DIR = root
        try:
            server.remember_job(recent.name)
            report = server.storage_report()
            assert report["jobs_count"] == 1
            assert report["can_clear"] is True
            assert report["cache_bytes"] > 0
            assert report["leftover_count"] >= 1
            cleared = server.clear_run_cache()
            assert recent.is_dir()
            assert (recent / "media" / "cover.jpg").is_file()
            assert not leftover.exists()
            assert not illus.exists()
            assert cleared["freed_bytes"] > 0
            assert cleared["can_clear"] is False
            assert any(row["job_id"] == recent.name for row in server.read_history())
        finally:
            server.RUNS_DIR = old


def test_item_freshness_bands_and_eleven_weight():
    from datetime import datetime, timezone

    from board.freshness import freshness_summary, item_freshness, stamp_freshness
    from board.server import _eleven_payload, _select_eleven_items

    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    week = item_freshness({"published": "2026-09-12 10:00"}, now=now)
    month = item_freshness({"published": "2026-08-20 10:00"}, now=now)
    half = item_freshness({"published": "2026-04-20 10:00"}, now=now)
    year = item_freshness({"published": "2026-01-10 10:00"}, now=now)
    old = item_freshness({"published": "2024-03-01 10:00"}, now=now)
    unknown = item_freshness({}, now=now)
    tight = item_freshness(
        {"published": "2026-06-01 10:00"}, now=now, query="成都国庆旅游"
    )
    assert week["band"] == "week" and week["weight"] == "近况"
    assert month["band"] == "month" and month["label"] == "近一个月"
    assert half["band"] == "half" and half["weight"] == "仅背景"
    assert year["band"] == "year" and year["label"] == "半年前"
    assert old["band"] == "old" and old["weight"] == "慎用"
    assert unknown["band"] == "unknown"
    assert tight["band"] == "half" and tight["weight"] == "慎用"

    def row(iid, published, liked, title):
        return stamp_freshness(
            {
                "id": iid,
                "platform": "xhs",
                "platform_label": "小红书",
                "title": title,
                "text": title,
                "author": "博主",
                "published": published,
                "url": "https://example.com",
                "liked": str(liked),
                "liked_n": liked,
            },
            now=now,
        )

    recent = row("xhs:new", "2026-09-12 10:00", 2, "近帖")
    stale = row("xhs:old", "2024-03-01 10:00", 9999, "旧帖")
    picked = _select_eleven_items([stale, recent])
    assert [item["id"] for item in picked] == ["xhs:new", "xhs:old"]
    payload = _eleven_payload("豆包手机助手选购指南", [stale, recent])
    assert payload["items"][0]["id"] == "xhs:new"
    assert payload["items"][0]["fresh_label"] == "近7天"
    assert payload["items"][0]["fresh_weight"] == "近况"
    assert payload["items"][1]["fresh_label"] == "2年前"
    assert "偏旧" in payload["freshness"]["note"]
    clock = freshness_summary([recent, stale], now=now)
    assert clock["weights"]["近况"] == 1
    assert clock["weights"]["慎用"] == 1


def test_process_timing_and_persist(tmp_path):
    import time

    from board.server import (
        format_used_ms,
        persist_process_event,
        process_timing_text,
        reconstruct_process,
    )

    assert format_used_ms(5000) == "5 秒"
    assert format_used_ms(125000) == "2 分 5 秒"
    started = time.time() - 12
    persist_process_event(tmp_path, "status", {"title": "开始搜小红书", "detail": "最多 30 条"}, started)
    persist_process_event(
        tmp_path,
        "platform_done",
        {"label": "小红书", "ok": True, "count": 30, "coverage": {"dropped": 2}},
        started,
    )
    persist_process_event(
        tmp_path, "eleven_start", {"intent": {"label": "综合研判"}, "item_count": 30}, started
    )
    persist_process_event(tmp_path, "board", {}, started)
    persist_process_event(tmp_path, "done", {"item_count": 30, "elapsed_ms": 180000}, started)
    proc = reconstruct_process(tmp_path, "测试", [])
    assert "开始搜小红书" in proc["steps"]
    assert any("小红书 核对后留下 30 条" in step for step in proc["steps"])
    assert "十一写好了报告" in proc["steps"]
    assert proc["elapsed_ms"] == 180000
    assert proc["search_ms"] > 0
    text = process_timing_text(proc)
    assert "搜索用了" in text
    assert "写报告用了" in text
    assert "一共" in text


def test_reconstruct_process_from_coverage(tmp_path):
    import json
    import time

    from board.server import reconstruct_process

    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
    time.sleep(0.05)
    (tmp_path / "coverage.json").write_text("[]", encoding="utf-8")
    time.sleep(0.05)
    (tmp_path / "board.json").write_text("{}", encoding="utf-8")
    coverage = [
        {"platform": "xhs", "label": "小红书", "raw": 36, "kept": 30, "dropped": 6, "reasons": {"太旧": 6}},
        {"platform": "dy", "label": "抖音", "raw": 0, "kept": 0, "dropped": 0, "reasons": {}},
    ]
    proc = reconstruct_process(tmp_path, "IPhone Duo上手体验", coverage)
    assert proc["steps"][0] == "搜过「IPhone Duo上手体验」"
    assert "小红书 核对后留下 30 条，丢掉 6" in proc["steps"]
    assert "抖音 没搜到" in proc["steps"]
    assert "十一写好了报告" in proc["steps"]
    assert any("太旧" in row for row in proc["logs"])
    assert proc["elapsed_ms"] > 0
    assert proc["search_ms"] > 0
