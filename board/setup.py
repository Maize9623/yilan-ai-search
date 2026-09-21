#!/usr/bin/env python3
"""Local agent roster and API keys for the 一览 board."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

BOARD_DIR = Path(__file__).resolve().parent
SETUP_PATH = BOARD_DIR / "setup.json"
KEYS_PATH = BOARD_DIR / "keys.json"
AGENTS_DIR = BOARD_DIR / "agents"
ENV_PATH = BOARD_DIR / ".env"
ELEVEN_PATH = BOARD_DIR / "eleven.md"
MAX_CUSTOM_AGENTS = 5
MAX_KEYS = 8
MAX_PERSONA_CHARS = 80_000
KEY_FIELDS = {
    "ai": "DEEPSEEK_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "youtube": "YOUTUBE_API_KEY",
}

GENERIC_PERSONA = """# 通用信息处理 Agent

你是通用信息处理编辑。没有固定人格、口头禅或角色扮演。

只根据当前材料和搜索任务写报告。不要自称十一，不要带林时一的判断口吻或态度表演。
用清楚、克制的中文。证据够就下判断，不够就写清未知。不要编造材料里没有的对象、数字或事件。
"""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_setup() -> dict[str, Any]:
    data = _read_json(SETUP_PATH)
    agents = data.get("custom_agents")
    if not isinstance(agents, list):
        agents = []
    cleaned: list[dict[str, Any]] = []
    for row in agents:
        if not isinstance(row, dict):
            continue
        agent_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not agent_id.startswith("c-") or not name:
            continue
        cleaned.append(
            {
                "id": agent_id[:16],
                "name": name[:24],
                "kind": "custom",
                "path": str(row.get("path") or f"agents/{agent_id}.md"),
            }
        )
    active = str(data.get("active_agent") or "eleven").strip() or "eleven"
    return {"active_agent": active, "custom_agents": cleaned[:MAX_CUSTOM_AGENTS]}


def save_setup(data: dict[str, Any]) -> None:
    SETUP_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def builtin_agents() -> list[dict[str, Any]]:
    return [
        {
            "id": "eleven",
            "name": "十一",
            "kind": "builtin",
            "title": "林时一",
            "blurb": "默认信息处理人格。有自己的判断口吻，会替你决定值不值得花时间。",
        },
        {
            "id": "generic",
            "name": "通用编辑",
            "kind": "builtin",
            "title": "通用信息处理 Agent",
            "blurb": "没有十一的人格。只按材料和任务写，不带口头禅或角色扮演。",
        },
    ]


def list_agents() -> list[dict[str, Any]]:
    custom = []
    for row in load_setup()["custom_agents"]:
        custom.append(
            {
                **row,
                "title": row["name"],
                "blurb": "你保存的人格或 skill。",
            }
        )
    return [*builtin_agents(), *custom]


def get_agent(agent_id: str) -> dict[str, Any] | None:
    wanted = (agent_id or "").strip()
    for row in list_agents():
        if row["id"] == wanted:
            return row
    return None


def active_agent() -> dict[str, Any]:
    setup = load_setup()
    agent = get_agent(setup["active_agent"])
    return agent or get_agent("eleven") or builtin_agents()[0]


def set_active_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("没有这个 Agent")
    data = load_setup()
    data["active_agent"] = agent["id"]
    save_setup(data)
    return agent


def _persona_path(row: dict[str, Any]) -> Path:
    rel = str(row.get("path") or "")
    path = (BOARD_DIR / rel).resolve()
    if AGENTS_DIR.resolve() not in path.parents and path != AGENTS_DIR.resolve():
        raise ValueError("人格文件不在本机 Agent 目录里")
    return path


def agent_persona(agent_id: str = "") -> str:
    agent = get_agent(agent_id) if agent_id else active_agent()
    if not agent:
        return GENERIC_PERSONA
    if agent["id"] == "eleven":
        if ELEVEN_PATH.exists():
            return ELEVEN_PATH.read_text(encoding="utf-8")
        return "你是十一，本地实时信息判断编辑。\n"
    if agent["id"] == "generic":
        return GENERIC_PERSONA
    path = _persona_path(agent)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return GENERIC_PERSONA


def agent_preview(agent_id: str, limit: int = 2400) -> str:
    text = agent_persona(agent_id).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n……后面还有，保存后写报告时会用全文。"


def _clean_name(raw: str) -> str:
    name = re.sub(r"[\r\n\\/]+", "", str(raw or "")).strip()
    if not name or len(name) > 24:
        raise ValueError("Agent 名称要在 1 到 24 个字之间")
    return name


def add_custom_agent(name: str, persona: str) -> dict[str, Any]:
    data = load_setup()
    if len(data["custom_agents"]) >= MAX_CUSTOM_AGENTS:
        raise ValueError(f"自定义 Agent 最多 {MAX_CUSTOM_AGENTS} 个，先删一个再加")
    cleaned = _clean_name(name)
    text = str(persona or "").strip()
    if len(text) < 20:
        raise ValueError("人格说明太短。写清楚这个 Agent 怎么说话、怎么判断，或上传 skill 文件")
    if len(text) > MAX_PERSONA_CHARS:
        raise ValueError("人格文件太大，先删到八万字以内")
    agent_id = f"c-{uuid.uuid4().hex[:8]}"
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    rel = f"agents/{agent_id}.md"
    (BOARD_DIR / rel).write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    row = {"id": agent_id, "name": cleaned, "kind": "custom", "path": rel}
    data["custom_agents"].append(row)
    data["active_agent"] = agent_id
    save_setup(data)
    return {**row, "title": cleaned, "blurb": "你保存的人格或 skill。"}


def delete_custom_agent(agent_id: str) -> None:
    agent = get_agent(agent_id)
    if not agent or agent.get("kind") != "custom":
        raise ValueError("只能删除自定义 Agent")
    data = load_setup()
    data["custom_agents"] = [row for row in data["custom_agents"] if row["id"] != agent["id"]]
    if data["active_agent"] == agent["id"]:
        data["active_agent"] = "eleven"
    try:
        path = _persona_path(agent)
        if path.exists():
            path.unlink()
    except ValueError:
        pass
    save_setup(data)


def mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    return f"{text[:4]}{'•' * min(12, len(text) - 8)}{text[-4:]}"


def upsert_dotenv(path: Path, updates: dict[str, str | None]) -> None:
    raw_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                seen.add(key)
                value = updates[key]
                if value is None:
                    continue
                out.append(f"{key}={value}")
                continue
        out.append(line)
    for key, value in updates.items():
        if key in seen or value is None:
            continue
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def apply_env_updates(updates: dict[str, str | None]) -> None:
    upsert_dotenv(ENV_PATH, updates)
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _key_kind(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if text in {"ai", "deepseek", "model"}:
        return "ai"
    if text in {"youtube", "yt"}:
        return "youtube"
    raise ValueError("新增时先选这是 AI 的 Key，还是 YouTube 的 Key")


def _key_name(raw: str, kind: str) -> str:
    name = re.sub(r"[\r\n\\/]+", "", str(raw or "")).strip()
    if not name:
        name = "DeepSeek" if kind == "ai" else "YouTube"
    if len(name) > 24:
        raise ValueError("Key 名称要在 24 个字以内")
    return name


def _clean_vault_rows(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return rows
    for row in raw:
        if not isinstance(row, dict):
            continue
        key_id = str(row.get("id") or "").strip()
        try:
            kind = _key_kind(str(row.get("kind") or ""))
        except ValueError:
            continue
        value = str(row.get("value") or "").strip()
        if not key_id.startswith("k-") or len(value) < 8:
            continue
        rows.append(
            {
                "id": key_id[:16],
                "kind": kind,
                "name": _key_name(str(row.get("name") or ""), kind),
                "value": value,
                "model": str(row.get("model") or "").strip() if kind == "ai" else "",
            }
        )
    return rows[:MAX_KEYS]


def _fix_active(keys: list[dict[str, Any]], active: dict[str, str]) -> dict[str, str]:
    ids = {row["id"] for row in keys}
    out = {"ai": "", "youtube": ""}
    for kind in out:
        current = str(active.get(kind) or "").strip()
        if current in ids:
            out[kind] = current
            continue
        found = next((row["id"] for row in keys if row["kind"] == kind), "")
        out[kind] = found
    return out


def _import_env_keys() -> dict[str, Any]:
    keys: list[dict[str, Any]] = []
    active = {"ai": "", "youtube": ""}
    deepseek = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if len(deepseek) >= 8:
        key_id = f"k-{uuid.uuid4().hex[:8]}"
        keys.append(
            {
                "id": key_id,
                "kind": "ai",
                "name": "DeepSeek",
                "value": deepseek,
                "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash",
            }
        )
        active["ai"] = key_id
    youtube = os.getenv("YOUTUBE_API_KEY", "").strip()
    if len(youtube) >= 8:
        key_id = f"k-{uuid.uuid4().hex[:8]}"
        keys.append(
            {
                "id": key_id,
                "kind": "youtube",
                "name": "YouTube",
                "value": youtube,
                "model": "",
            }
        )
        active["youtube"] = key_id
    return {"keys": keys, "active": active}


def save_vault(vault: dict[str, Any]) -> None:
    KEYS_PATH.write_text(
        json.dumps(vault, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_vault() -> dict[str, Any]:
    existed = KEYS_PATH.exists()
    data = _read_json(KEYS_PATH) if existed else {}
    keys = _clean_vault_rows(data.get("keys"))
    active_in = data.get("active") if isinstance(data.get("active"), dict) else {}
    if not keys and not existed:
        imported = _import_env_keys()
        if imported["keys"]:
            save_vault(imported)
            return imported
        return {"keys": [], "active": {"ai": "", "youtube": ""}}
    if not keys:
        return {"keys": [], "active": {"ai": "", "youtube": ""}}
    return {"keys": keys, "active": _fix_active(keys, {k: str(v or "") for k, v in active_in.items()})}


def vault_row(vault: dict[str, Any], kind: str) -> dict[str, Any] | None:
    wanted = vault["active"].get(kind) or ""
    return next((row for row in vault["keys"] if row["id"] == wanted), None)


def sync_active_to_env(vault: dict[str, Any] | None = None) -> None:
    vault = vault or load_vault()
    ai = vault_row(vault, "ai")
    youtube = vault_row(vault, "youtube")
    apply_env_updates(
        {
            "DEEPSEEK_API_KEY": ai["value"] if ai else None,
            "DEEPSEEK_MODEL": (ai.get("model") or "deepseek-flash") if ai else None,
            "YOUTUBE_API_KEY": youtube["value"] if youtube else None,
        }
    )


def boot_keys() -> None:
    sync_active_to_env(load_vault())


def add_key(kind: str, name: str, value: str, model: str = "") -> dict[str, Any]:
    vault = load_vault()
    if len(vault["keys"]) >= MAX_KEYS:
        raise ValueError(f"本机最多存 {MAX_KEYS} 个 Key，先删一个再加")
    kind = _key_kind(kind)
    secret = str(value or "").strip()
    if len(secret) < 8:
        raise ValueError("Key 太短")
    row = {
        "id": f"k-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "name": _key_name(name, kind),
        "value": secret,
        "model": (str(model or "").strip() or "deepseek-flash") if kind == "ai" else "",
    }
    vault["keys"].append(row)
    vault["active"][kind] = row["id"]
    save_vault(vault)
    sync_active_to_env(vault)
    return row


def set_active_key(key_id: str) -> dict[str, Any]:
    vault = load_vault()
    row = next((item for item in vault["keys"] if item["id"] == key_id), None)
    if not row:
        raise ValueError("没有这个 Key")
    vault["active"][row["kind"]] = row["id"]
    save_vault(vault)
    sync_active_to_env(vault)
    return row


def delete_key(key_id: str) -> None:
    vault = load_vault()
    row = next((item for item in vault["keys"] if item["id"] == key_id), None)
    if not row:
        raise ValueError("没有这个 Key")
    vault["keys"] = [item for item in vault["keys"] if item["id"] != key_id]
    if vault["active"].get(row["kind"]) == row["id"]:
        nxt = next((item for item in vault["keys"] if item["kind"] == row["kind"]), None)
        vault["active"][row["kind"]] = nxt["id"] if nxt else ""
    save_vault(vault)
    sync_active_to_env(vault)


def save_keys(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("activate") or payload.get("active"):
        set_active_key(str(payload.get("id") or payload.get("activate") or payload.get("active") or ""))
        return public_keys()
    if payload.get("kind") and (payload.get("value") or payload.get("key")):
        add_key(
            str(payload.get("kind") or ""),
            str(payload.get("name") or ""),
            str(payload.get("value") or payload.get("key") or ""),
            str(payload.get("model") or ""),
        )
        return public_keys()
    remove = payload.get("remove") if isinstance(payload.get("remove"), list) else []
    vault = load_vault()
    for item in remove:
        token = str(item or "").strip()
        if token.startswith("k-"):
            delete_key(token)
            continue
        kind = "ai" if token in {"ai", "deepseek"} else "youtube" if token in {"youtube", "yt"} else ""
        current = vault_row(vault, kind) if kind else None
        if current:
            delete_key(current["id"])
            vault = load_vault()
    if remove:
        return public_keys()
    for alias, _env_key in KEY_FIELDS.items():
        if alias not in payload:
            continue
        text = str(payload.get(alias) or "").strip()
        if not text:
            continue
        add_key("ai" if alias in {"ai", "deepseek"} else "youtube", "", text, str(payload.get("model") or ""))
        return public_keys()
    raise ValueError("没有要改的 Key")


def public_keys() -> dict[str, Any]:
    vault = load_vault()
    listed: list[dict[str, Any]] = []
    for row in vault["keys"]:
        listed.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "label": "AI" if row["kind"] == "ai" else "YouTube",
                "name": row["name"],
                "masked": mask_secret(row["value"]),
                "model": row.get("model") or ("deepseek-flash" if row["kind"] == "ai" else ""),
                "active": vault["active"].get(row["kind"]) == row["id"],
            }
        )
    ai = next((row for row in listed if row["kind"] == "ai" and row["active"]), None)
    youtube = next((row for row in listed if row["kind"] == "youtube" and row["active"]), None)
    return {
        "vault": listed,
        "max": MAX_KEYS,
        "deepseek": {
            "configured": bool(ai),
            "masked": (ai or {}).get("masked") or "",
            "model": (ai or {}).get("model") or "deepseek-flash",
            "name": (ai or {}).get("name") or "",
            "label": "AI",
            "hint": "当前用来让 Agent 写报告和做 Agent 搜索。",
        },
        "youtube": {
            "configured": bool(youtube),
            "masked": (youtube or {}).get("masked") or "",
            "name": (youtube or {}).get("name") or "",
            "label": "YouTube",
            "hint": "当前用来搜 YouTube。",
        },
    }


def public_agent(row: dict[str, Any] | None = None) -> dict[str, Any]:
    agent = row or active_agent()
    return {
        "id": agent["id"],
        "name": agent["name"],
        "kind": agent.get("kind") or "builtin",
        "title": agent.get("title") or agent["name"],
        "blurb": agent.get("blurb") or "",
    }


def public_state() -> dict[str, Any]:
    keys = public_keys()
    agent = public_agent()
    return {
        "agent": agent,
        "agents": [public_agent(row) for row in list_agents()],
        "keys": keys,
        "can_write": bool(keys["deepseek"]["configured"]),
        "max_custom": MAX_CUSTOM_AGENTS,
        "custom_count": sum(1 for row in list_agents() if row.get("kind") == "custom"),
    }
