"""Search-task grammar: what the conclusion list is allowed to say."""

from __future__ import annotations

import re
from typing import Any

INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "avoid",
        (
            "避雷",
            "踩雷",
            "翻车",
            "名不副实",
            "别去",
            "别买",
            "别信",
            "劝退",
            "踩坑",
            "雷区",
            "不值",
            "智商税",
            "骗局",
            "坑人",
        ),
    ),
    (
        "argue",
        (
            "是否",
            "是不是",
            "成不成立",
            "站得住",
            "论证",
            "证明",
            "真的假",
            "真的吗",
            "是真是假",
            "靠不靠谱",
            "有没有这回事",
            "说得通",
        ),
    ),
    (
        "decide",
        (
            "该不该",
            "要不要上",
            "换不换",
            "值不值换",
            "如何决策",
            "怎么决策",
            "决策",
            "现在做什么",
            "先做什么",
        ),
    ),
    (
        "recommend",
        (
            "推荐",
            "安利",
            "必买",
            "必去",
            "去哪",
            "哪家",
            "哪款",
            "哪个好",
            "选哪个",
            "买哪个",
            "怎么选",
            "选购",
            "购买",
            "购入",
            "入手",
            "买什么",
            "买哪款",
            "怎么买",
            "选购指南",
            "购买指南",
            "求推",
            "值得买",
            "打卡",
            "探店",
            "清单",
            "买不买",
            "要不要",
            "值不值",
        ),
    ),
    ("controversy", ("争议", "吵架", "骂战", "对立", "撕", "舆论战", "对线", "两极")),
    ("narrative", ("叙事", "舆情", "话语", "话术", "舆论", "传播结构", "风向")),
    ("compare", ("对比", "vs", "VS", "pk", "PK", "哪个更", "差别", "区别", "还是选")),
    ("howto", ("怎么做", "如何", "教程", "攻略", "步骤", "入门", "指南", "怎么用")),
    (
        "assess",
        (
            "手机助手",
            "助手",
            "评测",
            "好用吗",
            "好不好用",
            "怎么样",
            "靠谱吗",
            "消费者版",
            "能力判断",
        ),
    ),
)

INTENT_COPY = {
    "recommend": (
        "给出推荐",
        "用户要带走的是「选谁 / 买哪条」。选购、购买、选购指南都是购物题。"
        "即使词里还有指南、助手、评测，也不要写成照做、先做或能用。"
        "先读完全部材料，再点名该选的对象。对象可以是产品、地方、人、工具、方法、内容。"
        "picks 以 go 为主，材料够就至少 3 条。不要写成舆情叙事地图。"
        "judgment.one_line_judgment 必须点名 1-3 个对象。",
        "围绕「{topic}」，现在该选哪些对象，为什么。",
    ),
    "avoid": (
        "标出避雷",
        "用户要带走的是「别选谁」。点名材料里被说翻车、名不副实、营销大于实质的对象。"
        "picks 以 skip 为主。不要只写“存在避雷叙事”。"
        "judgment.one_line_judgment 必须点名该避的对象。",
        "围绕「{topic}」，哪些对象该避开，依据是什么。",
    ),
    "argue": (
        "论证收口",
        "用户要的是判断站不站得住，不是购物清单。不要写成选/避/再看。"
        "picks 的 tag 用撑得住 / 撑不住 / 还说不准。不要写成立、不成立、存疑。"
        "judgment.one_line_judgment 必须回答这句话在材料里站不站得住。",
        "「{topic}」在材料里成不成立，缺的是哪一层证据。",
    ),
    "decide": (
        "决策收口",
        "用户要的是现在能定什么、必须挂什么条件。不要写成探店式选/避。"
        "picks 的 tag 用先做 / 别做 / 取决于。把条件写进 reason。"
        "judgment.one_line_judgment 必须给出可执行的下一步。",
        "就「{query}」，现在先做什么、什么先别做、什么取决于条件。",
    ),
    "controversy": (
        "拆开争议",
        "先拆对立双方各主张什么、证据各来自哪类帖，再判断材料够不够站队。"
        "picks 的 tag 用站这边 / 站不住 / 两边都弱，不要退化成选/避。"
        "最后仍要有判断，不要假装中立到没有结论。",
        "「{topic}」上对立的是什么，材料支持站到哪一边，还是两边都不够。",
    ),
    "narrative": (
        "画叙事",
        "用户要看人们怎么讲这件事。可以画叙事结构，但 picks 仍要点名被反复提到的对象，不能只剩抽象类型。"
        "picks 的 tag 用主叙事 / 杂音 / 待核。",
        "人们在怎么讲「{topic}」，哪几种讲法在抢注意力。",
    ),
    "compare": (
        "对比收口",
        "把材料里的选项摊开对比，最后给出更值得选的一边，或说清为什么打平。"
        "picks 的 tag 用更强 / 更弱 / 打平。",
        "就「{query}」两边各强在哪，现在更该选谁。",
    ),
    "howto": (
        "给出做法",
        "用户要能照着做。从材料抽出步骤、路径、工具和失败边界。"
        "picks 的 tag 用照做 / 别照做 / 缺条件。",
        "围绕「{topic}」，材料里哪条路径能落地，先做什么。",
    ),
    "assess": (
        "能力判断",
        "用户在看一个产品、功能或版本现在能干什么，不是在点菜。"
        "不要把能力写成「选」，也不要把限制写成「避」。"
        "picks 的 tag 用能用 / 别指望 / 再观察。reason 写清边界。"
        "judgment.one_line_judgment 必须说清现在能用什么、不能当什么。",
        "「{topic}」现在哪些能力能用，哪些别指望，哪些还要再观察。",
    ),
    "comprehensive": (
        "综合研判",
        "用户只给了主题。先判断这道题的收口语法：论证、决策、能力、购物、攻略或争议，再写 picks。"
        "禁止不管题目一律写成选/避/再看。先自定 tag，再填 verdict 上色。"
        "材料允许时正反都要有。不要只交一份叙事地图。",
        "关于「{topic}」，按这道题自己的语法收口，不要套购物清单。",
    ),
}

PICK_FRAMES = {
    "recommend": {
        "title": "现在更该选",
        "lead": "先看材料支持下手的对象。点一条打开原帖。",
        "foot": "只列被多条材料点过名、能下手的对象。单帖处境不进这张清单。",
        "go": "选",
        "skip": "不选",
        "watch": "再比",
    },
    "avoid": {
        "title": "现在更该避",
        "lead": "先看被反复点名撑不住的对象。点一条打开原帖。",
        "foot": "只列被反复点名该避的对象，不是把某条求助帖的个人清单写成黑名单。",
        "go": "可留",
        "skip": "避",
        "watch": "还说不准",
    },
    "argue": {
        "title": "论证收口",
        "lead": "撑得住是多家说法对得上；撑不住是这个能力没有，或这句话对不上；还说不准是不能当结论。",
        "foot": "只收能互相印证的判断。单帖观点写在深度阅读。",
        "go": "撑得住",
        "skip": "撑不住",
        "watch": "还说不准",
    },
    "decide": {
        "title": "决策收口",
        "lead": "先看现在能定的，和必须挂条件的。",
        "foot": "条件写在理由里。单人处境不进这张清单。",
        "go": "先做",
        "skip": "别做",
        "watch": "取决于",
    },
    "compare": {
        "title": "对比结论",
        "lead": "对照材料里点过名的对象。点一条看出处。",
        "foot": "对照停在这批材料能撑住的差异上，不是参数全集。",
        "go": "更强",
        "skip": "更弱",
        "watch": "打平",
    },
    "howto": {
        "title": "能落地的做法",
        "lead": "能落地的做法，点一条看依据。",
        "foot": "做法来自这批材料的收口，不是操作手册。",
        "go": "照做",
        "skip": "别照做",
        "watch": "缺条件",
    },
    "controversy": {
        "title": "争议收口",
        "lead": "先看材料够不够站队。点一条看原帖。",
        "foot": "只收被点过名的争议对象。",
        "go": "站这边",
        "skip": "站不住",
        "watch": "两边都弱",
    },
    "narrative": {
        "title": "人们怎么讲",
        "lead": "叙事里反复出现的对象，点一条看依据。",
        "foot": "只收反复出现的叙事对象。",
        "go": "主叙事",
        "skip": "杂音",
        "watch": "待核",
    },
    "assess": {
        "title": "能力判断",
        "lead": "先看现在能用的、别指望的、还要再观察的。",
        "foot": "能力判断不是购物清单。单帖体验写在深度阅读。",
        "go": "能用",
        "skip": "别指望",
        "watch": "再观察",
    },
    "comprehensive": {
        "title": "这次的结论",
        "lead": "撑得住是多家说法对得上；撑不住是这个能力没有，或这句话对不上；还说不准是不能当结论。",
        "foot": "结论只收能互相印证的对象。单帖处境写在深度阅读。",
        "go": "撑得住",
        "skip": "撑不住",
        "watch": "还说不准",
    },
}

_SHOP_MARK = re.compile(
    r"选购|购买|购入|入手|买什么|买哪款|怎么买|选购指南|购买指南|选哪个|买哪个|怎么选"
)
TAG_ALIGN = {
    "recommend": {
        "先做": "选",
        "照做": "选",
        "能用": "选",
        "成立": "选",
        "更强": "选",
        "站这边": "选",
        "主叙事": "选",
        "别做": "不选",
        "别照做": "不选",
        "别指望": "不选",
        "不成立": "不选",
        "更弱": "不选",
        "站不住": "不选",
        "杂音": "不选",
        "取决于": "再比",
        "缺条件": "再比",
        "再观察": "再比",
        "存疑": "再比",
        "还说不准": "再比",
        "撑得住": "选",
        "撑不住": "不选",
        "打平": "再比",
        "两边都弱": "再比",
        "待核": "再比",
    },
}
_MODE_PRIORITY = (
    "avoid",
    "argue",
    "decide",
    "compare",
    "recommend",
    "howto",
    "assess",
    "controversy",
    "narrative",
)
NARRATIVE_MODES = {"narrative", "comprehensive", "controversy"}
_TOPIC_TRIM = re.compile(r"^[的了吗呢啊呀吧\s]+|[的了吗呢啊呀吧\s]+$")


def _intent_keys() -> tuple[str, ...]:
    return tuple(key for _mode, keys in INTENT_RULES for key in keys)


def _strip_intent_words(query: str) -> str:
    topic = query
    for key in sorted(_intent_keys(), key=len, reverse=True):
        topic = re.sub(re.escape(key), " ", topic, flags=re.IGNORECASE)
    topic = re.sub(r"\s+", " ", topic).strip()
    return _TOPIC_TRIM.sub("", topic).strip()


_TAG_PLAIN = {
    "成立": "撑得住",
    "不成立": "撑不住",
    "存疑": "还说不准",
}
_OLD_PICK_LEADS = {
    "标签跟着这道题走，不是固定的选、避、再看。",
    "先看材料撑得住的判断，不是购物清单。",
    "成立与否只收能互相印证的命题。单帖观点写在深度阅读。",
    "撑得住与否只收能互相印证的命题。单帖观点写在深度阅读。",
}
_PLAIN_PICK_LEAD = "撑得住是多家说法对得上；撑不住是这个能力没有，或这句话对不上；还说不准是不能当结论。"


def _plain_copy(text: str) -> str:
    out = str(text or "")
    for old, new in (("不成立", "撑不住"), ("成立", "撑得住"), ("存疑", "还说不准")):
        out = out.replace(old, new)
    return out


def pick_frame(mode: str, custom: dict[str, Any] | None = None) -> dict[str, str]:
    frame = dict(PICK_FRAMES.get(mode) or PICK_FRAMES["comprehensive"])
    own = {frame["go"], frame["skip"], frame["watch"]}
    align = TAG_ALIGN.get(mode) or {}
    other_titles = {pack["title"] for key, pack in PICK_FRAMES.items() if key != mode}
    if isinstance(custom, dict):
        for key in ("go", "skip", "watch"):
            raw = str(custom.get(key) or "").strip()
            value = align.get(raw, raw)
            if value in own:
                frame[key] = value[:12]
        for key in ("title", "lead", "foot"):
            value = str(custom.get(key) or "").strip()
            if not value:
                continue
            if key == "title" and value in other_titles:
                continue
            frame[key] = value[:40]
    for key in ("title", "lead", "foot", "go", "skip", "watch"):
        frame[key] = _plain_copy(frame[key])
    if frame.get("lead") in _OLD_PICK_LEADS:
        frame["lead"] = (PICK_FRAMES.get(mode) or PICK_FRAMES["comprehensive"])["lead"]
    if frame.get("go") == "撑得住" and "对得上" not in frame.get("lead", ""):
        frame["lead"] = f"{frame['lead']} {_PLAIN_PICK_LEAD}".strip()
    return frame


def pick_tag(
    pick: dict[str, Any] | None,
    frame: dict[str, str] | None = None,
    mode: str = "",
) -> str:
    row = pick if isinstance(pick, dict) else {}
    tag = str(row.get("tag") or "").strip()
    aligned = (TAG_ALIGN.get(mode) or {}).get(tag)
    if aligned:
        return _TAG_PLAIN.get(aligned, aligned)
    if 1 <= len(tag) <= 8 and tag not in {"go", "skip", "watch"}:
        return _TAG_PLAIN.get(tag, tag)
    bucket = str(row.get("verdict") or "watch")
    pack = frame or PICK_FRAMES["comprehensive"]
    shown = pack.get(bucket) or pack.get("watch") or "还说不准"
    return _TAG_PLAIN.get(shown, shown)


def resolve_pick_mode(intent: dict[str, Any] | None, query: str = "") -> str:
    mode = str((intent or {}).get("mode") or "")
    if mode and mode != "comprehensive":
        return mode
    return detect_query_intent(query or str((intent or {}).get("query") or ""))["mode"]


def detect_query_intent(query: str) -> dict[str, str]:
    text = (query or "").strip()
    folded = text.lower()
    matched: dict[str, str] = {}
    for mode, keys in INTENT_RULES:
        for key in keys:
            if key and key.lower() in folded:
                matched[mode] = key
                break
    shop = _SHOP_MARK.search(text)
    if shop and not any(key in matched for key in ("avoid", "argue", "decide")):
        matched.pop("howto", None)
        matched.pop("assess", None)
        matched["recommend"] = shop.group(0)
    mode = "comprehensive"
    if len(matched) == 1:
        mode = next(iter(matched))
    elif matched:
        for candidate in _MODE_PRIORITY:
            if candidate in matched:
                mode = candidate
                break
    topic = _strip_intent_words(text) or text
    label, brief, job_tpl = INTENT_COPY[mode]
    frame = pick_frame(mode)
    return {
        "mode": mode,
        "label": label,
        "brief": brief,
        "job": job_tpl.format(topic=topic, query=text),
        "topic": topic,
        "query": text,
        "pick_title": frame["title"],
        "pick_lead": frame["lead"],
        "pick_foot": frame["foot"],
        "pick_go": frame["go"],
        "pick_skip": frame["skip"],
        "pick_watch": frame["watch"],
    }


def report_intent(query: str, extra_note: str = "") -> dict[str, str]:
    query = (query or "").strip()
    note = " ".join((extra_note or "").split())
    base = detect_query_intent(query)
    if not note:
        return base
    hinted = detect_query_intent(note)
    mixed = detect_query_intent(f"{query} {note}")
    mode = hinted["mode"] if hinted["mode"] != "comprehensive" else mixed["mode"]
    label, brief, job_tpl = INTENT_COPY[mode]
    topic = base["topic"] or query
    frame = pick_frame(mode)
    return {
        "mode": mode,
        "label": label,
        "brief": brief,
        "job": (
            job_tpl.format(topic=topic, query=query)
            + f" 使用者要求按新逻辑重写完整报告：{note}"
        ),
        "topic": topic,
        "query": query,
        "user_note": note,
        "pick_title": frame["title"],
        "pick_lead": frame["lead"],
        "pick_foot": frame["foot"],
        "pick_go": frame["go"],
        "pick_skip": frame["skip"],
        "pick_watch": frame["watch"],
    }
