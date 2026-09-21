# -*- coding: utf-8 -*-
from board import setup as setup_mod
from board.server import COMMENTS_READY, harvest_board, normalize_agent_search, parse_options


def test_mask_and_env_upsert(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=old-key\nBOARD_PORT=8765\n", encoding="utf-8")
    monkeypatch.setattr(setup_mod, "ENV_PATH", env)
    setup_mod.upsert_dotenv(env, {"DEEPSEEK_API_KEY": "sk-newkey123456", "YOUTUBE_API_KEY": "yt-1"})
    text = env.read_text(encoding="utf-8")
    assert "sk-newkey123456" in text
    assert "BOARD_PORT=8765" in text
    assert "YOUTUBE_API_KEY=yt-1" in text
    setup_mod.upsert_dotenv(env, {"DEEPSEEK_API_KEY": None})
    text = env.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" not in text
    assert setup_mod.mask_secret("sk-newkey123456").startswith("sk-n")
    assert "3456" in setup_mod.mask_secret("sk-newkey123456")


def test_custom_agents_cap_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_mod, "SETUP_PATH", tmp_path / "setup.json")
    monkeypatch.setattr(setup_mod, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(setup_mod, "BOARD_DIR", tmp_path)
    persona = "你是冷静的编辑。只根据材料下判断，不编造事实。"
    for i in range(5):
        setup_mod.add_custom_agent(f"编辑{i}", persona)
    try:
        setup_mod.add_custom_agent("第六个", persona)
        raise AssertionError("should cap at 5")
    except ValueError as exc:
        assert "最多" in str(exc)
    active = setup_mod.active_agent()
    setup_mod.delete_custom_agent(active["id"])
    assert setup_mod.active_agent()["id"] == "eleven"
    setup_mod.set_active_agent("generic")
    assert setup_mod.active_agent()["id"] == "generic"
    persona = setup_mod.agent_persona("generic")
    assert persona.startswith("# 通用信息处理 Agent")
    assert "没有固定人格" in persona


def test_harvest_board_has_no_fake_eleven_verdict():
    items = [
        {
            "id": "xhs:1",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "本地检索标题",
            "author": "甲",
            "text": "这是一条公开帖",
            "liked": 12,
        },
        {
            "id": "wb:1",
            "platform": "wb",
            "platform_label": "微博",
            "title": "另一条",
            "author": "乙",
            "text": "微博上也在谈",
        },
    ]
    board = harvest_board("折叠屏", items)
    assert board["harvest"] is True
    assert board.get("writer") is None
    assert "材料一览" in (board["report"]["title"] or "")
    headings = [row["heading"] for row in board["report"]["sections"]]
    assert any("小红书" in heading for heading in headings)
    assert board["judgment"]["headline_zh"] == "这次只摊开材料"
    assert "十一" not in (board["judgment"]["headline_zh"] or "")
    assert not board["report"]["picks"]
    assert not board["report"]["answer"]


def test_comments_stay_off():
    assert COMMENTS_READY is False
    options = parse_options(30, True, True, 10, False)
    assert options.comments is False
    assert options.sub_comments is False


def test_no_ai_turns_off_agent_flags():
    options = parse_options(30, False, False, 10, True, agent_search=True, no_ai=True)
    assert options.no_ai is True
    assert options.eleven is False
    assert options.agent_search is False


def test_key_vault_add_switch_and_delete(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setup_mod, "KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(setup_mod, "ENV_PATH", tmp_path / ".env")
    (tmp_path / "keys.json").write_text('{"keys":[],"active":{"ai":"","youtube":""}}\n', encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    first = setup_mod.add_key("ai", "主用", "sk-mainkey123456", "deepseek-flash")
    setup_mod.add_key("ai", "备用", "sk-sparekey654321", "deepseek-flash")
    setup_mod.add_key("youtube", "视频", "AIza-youtube-key-1")
    public = setup_mod.public_keys()
    assert len(public["vault"]) == 3
    assert public["deepseek"]["name"] == "备用"
    assert public["youtube"]["configured"] is True
    setup_mod.set_active_key(first["id"])
    public = setup_mod.public_keys()
    assert public["deepseek"]["name"] == "主用"
    assert "sk-mainkey123456" in (tmp_path / ".env").read_text(encoding="utf-8")
    try:
        setup_mod.add_key("", "无名", "sk-otherkey000000")
        raise AssertionError("kind required")
    except ValueError as exc:
        assert "AI" in str(exc) or "YouTube" in str(exc)
    setup_mod.delete_key(first["id"])
    public = setup_mod.public_keys()
    assert public["deepseek"]["name"] == "备用"
    assert all(row["id"] != first["id"] for row in public["vault"])


def test_agent_search_stays_off_timeline():
    items = [
        {
            "id": "xhs:1",
            "platform": "xhs",
            "platform_label": "小红书",
            "title": "本地检索标题",
            "author": "甲",
            "text": "这是一条公开帖",
        }
    ]
    brief = normalize_agent_search(
        {
            "overview": "模型说的补充",
            "points": [{"heading": "背景", "body": "只当参考"}],
            "model": "deepseek-flash",
            "writer": {"id": "eleven", "name": "十一", "kind": "builtin"},
        }
    )
    board = harvest_board("折叠屏", items, agent_search=brief)
    assert board["agent_search"]["source"] == "model"
    assert "模型说的补充" in board["agent_search"]["overview"]
    timeline = " ".join(row.get("text") or "" for row in board["report"].get("timeline") or [])
    assert "模型说的补充" not in timeline
