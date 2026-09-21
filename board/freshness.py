"""Check crawled posts against the search need, then keep extras to refill."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

QUERY_STOP = frozenset(
    {
        "推荐",
        "攻略",
        "怎么",
        "如何",
        "哪些",
        "什么",
        "一个",
        "一下",
        "可以",
        "适合",
        "相关",
        "内容",
        "文章",
        "视频",
        "分享",
        "最新",
        "希望",
        "需要",
        "还有",
        "或者",
        "以及",
        "这个",
        "那个",
        "我们",
        "建议",
        "问题",
        "看看",
        "求问",
        "谢谢",
        "就是",
        "还是",
        "不是",
    }
)
PLACE_FALLBACK = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "成都",
    "重庆",
    "杭州",
    "南京",
    "武汉",
    "西安",
    "苏州",
    "天津",
    "长沙",
    "郑州",
    "青岛",
    "厦门",
    "昆明",
    "大连",
    "哈尔滨",
    "沈阳",
    "济南",
    "福州",
    "合肥",
    "南昌",
    "南宁",
    "海口",
    "贵阳",
    "太原",
    "石家庄",
    "兰州",
    "乌鲁木齐",
    "拉萨",
    "银川",
    "西宁",
    "呼和浩特",
    "香港",
    "澳门",
    "台北",
    "四川",
    "云南",
    "西藏",
    "新疆",
    "海南",
    "广西",
    "贵州",
    "日本",
    "泰国",
    "韩国",
    "新加坡",
    "马来西亚",
    "越南",
    "柬埔寨",
    "加拿大",
    "美国",
    "澳洲",
    "澳大利亚",
    "英国",
    "法国",
    "意大利",
    "德国",
    "西班牙",
    "瑞士",
    "新西兰",
    "东京",
    "大阪",
    "京都",
    "首尔",
    "曼谷",
    "清迈",
    "大理",
    "丽江",
    "桂林",
    "三亚",
    "扬州",
    "嘉兴",
    "绍兴",
    "珠海",
    "佛山",
    "东莞",
    "无锡",
    "宁波",
    "洛阳",
)
_PLACE_FALSE = frozenset(
    {
        "入门",
        "上门",
        "门口",
        "门店",
        "热门",
        "专门",
        "部门",
        "门户",
        "门路",
        "门面",
        "开门",
        "关门",
        "网关",
    }
)
_GEO_SUFFIX = ("市", "县", "区", "镇", "乡", "州", "盟", "旗", "省", "国", "村")
_TOKEN_ALIASES = {
    "入门": ("beginner", "introductory", "introduction", "from scratch", "getting started"),
    "学习": ("learn", "learning", "study"),
    "规划": ("roadmap", "plan", "curriculum"),
    "教程": ("tutorial", "course", "how to"),
    "路线": ("roadmap", "path"),
    "推荐": ("recommend", "best", "review"),
    "选购": ("buy", "buying", "purchase"),
}
HOLIDAY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("national", ("国庆", "十一黄金周", "十一长假", "十一假期", "national day")),
    ("mayday", ("五一", "劳动节", "五一黄金周", "五一长假")),
    ("spring", ("春节", "过年", "除夕", "大年初", "春运")),
    ("midautumn", ("中秋", "八月十五")),
    ("dragon", ("端午", "端午节", "龙舟")),
    ("newyear", ("元旦",)),
    ("qingming", ("清明",)),
    ("xmas", ("圣诞", "圣诞节", "christmas")),
)
_TIME_QUERY = re.compile(
    r"国庆|五一|春节|过年|中秋|端午|元旦|清明|圣诞|黄金周|假期|出行|旅游|攻略|门票|今年|明年|活动|游园|报名|市集|展览"
)
_YEAR = re.compile(r"(?:19|20)\d{2}")
_DATE = re.compile(
    r"(?P<y>(?:19|20)\d{2})[-/.年](?P<m>\d{1,2})(?:[-/.月](?P<d>\d{1,2}))?"
)
_FULL_DATE = re.compile(
    r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"
)
_EVENT_MARKS = (
    "游园会",
    "游园",
    "市集",
    "展览",
    "展会",
    "报名",
    "场次",
    "门票",
    "举办",
    "开放时间",
    "公益活动",
    "活动时间",
    "无需报名",
    "时间：",
    "时间:",
)
_BLOB_KEYS = (
    "title",
    "text",
    "desc",
    "content",
    "author",
    "snippet",
    "text_zh",
    "video_text",
)
SENSITIVE_DAYS = 800
HARD_STALE_DAYS = 365 * 8


def item_blob(item: dict[str, Any]) -> str:
    parts = [str(item.get(key) or "") for key in _BLOB_KEYS]
    tags = item.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    elif tags:
        parts.append(str(tags))
    return " ".join(parts)


def holidays_in(text: str) -> set[str]:
    blob = text or ""
    low = blob.lower()
    found: set[str] = set()
    for key, marks in HOLIDAY_GROUPS:
        if any((mark.lower() in low) if mark.isascii() else (mark in blob) for mark in marks):
            found.add(key)
    return found


def _is_place_token(token: str) -> bool:
    text = (token or "").strip()
    if len(text) < 2 or text in _PLACE_FALSE:
        return False
    if text in PLACE_FALLBACK:
        return True
    if any(text.endswith(suffix) for suffix in _GEO_SUFFIX):
        return True
    return len(text) >= 3


def query_places(query: str) -> list[str]:
    text = query or ""
    found: list[str] = []
    try:
        import jieba.posseg as pseg

        for word, flag in pseg.cut(text):
            token = str(word).strip()
            if flag == "ns" and _is_place_token(token) and token not in found:
                found.append(token)
    except Exception:
        pass
    for place in PLACE_FALLBACK:
        if place in text and place not in found:
            found.append(place)
    return found


def token_in_blob(token: str, blob: str) -> bool:
    text = blob or ""
    low = text.lower()
    needle = (token or "").strip()
    if not needle:
        return False
    if needle.lower() in low:
        return True
    return any(alias in low for alias in _TOKEN_ALIASES.get(needle, ()))


def query_tokens(query: str) -> list[str]:
    text = " ".join((query or "").split())
    if not text:
        return []
    tokens: list[str] = []
    try:
        import jieba

        tokens = [
            token.strip()
            for token in jieba.cut(text)
            if len(token.strip()) >= 2 and token.strip() not in QUERY_STOP
        ]
    except Exception:
        tokens = [
            token
            for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,}", text)
            if token not in QUERY_STOP
        ]
    ordered: list[str] = []
    for token in [*query_places(text), *tokens]:
        if token and token not in ordered:
            ordered.append(token)
    return ordered


def is_time_sensitive(query: str) -> bool:
    return bool(_TIME_QUERY.search(query or ""))


def is_event_like(text: str) -> bool:
    return any(mark in (text or "") for mark in _EVENT_MARKS)


def event_dates(text: str) -> list[datetime]:
    found: list[datetime] = []
    for year, month, day in _FULL_DATE.findall(text or ""):
        try:
            found.append(datetime(int(year), int(month), int(day), tzinfo=timezone.utc))
        except ValueError:
            continue
    return found


def max_age_days(query: str) -> int:
    return SENSITIVE_DAYS if is_time_sensitive(query) else HARD_STALE_DAYS


def parse_item_date(item: dict[str, Any]) -> datetime | None:
    raw = str(
        item.get("published")
        or item.get("time")
        or item.get("create_time")
        or item.get("publish_time")
        or item.get("createTime")
        or item.get("created_time")
        or item.get("published_at")
        or ""
    ).strip()
    if not raw:
        return None
    try:
        number = int(float(raw))
        if number > 10_000_000_000:
            number //= 1000
        if number >= 1_000_000_000:
            return datetime.fromtimestamp(number, tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    for size, fmt in (
        (19, "%Y-%m-%d %H:%M:%S"),
        (16, "%Y-%m-%d %H:%M"),
        (10, "%Y-%m-%d"),
        (10, "%Y/%m/%d"),
    ):
        try:
            return datetime.strptime(raw[:size], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    match = _DATE.search(raw)
    if not match:
        return None
    year = int(match.group("y"))
    month = int(match.group("m"))
    day = int(match.group("d") or 1)
    try:
        return datetime(year, month, min(day, 28), tzinfo=timezone.utc)
    except ValueError:
        return None


def dated_items(
    items: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[tuple[datetime, dict[str, Any]]]:
    now = now or datetime.now(timezone.utc)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for item in items:
        stamp = parse_item_date(item)
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp > now:
            stamp = now
        dated.append((stamp, item))
    dated.sort(key=lambda row: row[0])
    return dated


def reject_reason(
    query: str,
    item: dict[str, Any],
    now: datetime | None = None,
) -> str | None:
    q = (query or "").strip()
    if not q:
        return None
    now = now or datetime.now(timezone.utc)
    blob = item_blob(item)
    if not blob.strip():
        return "空内容"
    q_places = [place for place in query_places(q) if place != "加拿大" or "加拿大" in q]
    if q_places and not all(place in blob for place in q_places):
        return "地点对不上"
    if "国庆" in q and "加拿大" not in q:
        if "加拿大国庆" in blob or "canada day" in blob.lower():
            return "跑题"
        if "加拿大" in blob and "国庆" in blob and "中国" not in blob:
            if not any(city in blob for city in ("成都", "北京", "上海", "广州", "深圳", "杭州")):
                return "跑题"
    q_holidays = holidays_in(q)
    item_holidays = holidays_in(blob)
    if q_holidays and item_holidays and not (q_holidays & item_holidays):
        return "节假日对不上"
    tokens = query_tokens(q)
    if tokens and not any(token_in_blob(token, blob) for token in tokens):
        return "对不上检索词"
    dates = event_dates(blob)
    if dates:
        latest = max(dates)
        if latest.date() < now.date() and (is_event_like(blob) or bool(q_holidays)):
            return f"活动已过：{latest.date().isoformat()}"
    dated = parse_item_date(item)
    title = str(item.get("title") or "")
    years = [int(year) for year in _YEAR.findall(title + " " + blob[:180])]
    if q_holidays and is_event_like(blob):
        past_years = [year for year in years if 2015 <= year < now.year]
        future_or_now = [year for year in years if year >= now.year]
        if past_years and not future_or_now:
            return f"去年的活动：{max(past_years)}"
        if dated and dated.year < now.year and not future_or_now:
            return f"去年的活动：{dated.year}"
    if dated:
        age = (now - dated).days
        if age > max_age_days(q):
            return f"太旧：{dated.date().isoformat()}"
    if is_time_sensitive(q) and not dated:
        old = [year for year in years if 1990 <= year <= now.year - 3]
        recent = [year for year in years if year >= now.year - 1]
        if old and not recent:
            return f"太旧：{min(old)}年"
    return None


def select_items(
    query: str,
    items: list[dict[str, Any]],
    wanted: int | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[tuple[dict[str, Any], str]] = []
    for item in items:
        reason = reject_reason(query, item, now=now)
        if reason:
            dropped.append((item, reason))
            continue
        kept.append(item)
        if wanted and len(kept) >= wanted:
            break
    return kept, dropped


_TITLE_STRIP = re.compile(r"[#＃@]|的视频|的微博|的抖音|的小红书|的快手")
_TITLE_PUNCT = re.compile(r"[\s\-_|｜·•【】\[\]（）()「」\"\"''、,，.。!！?？:：;；~～]+")
_AUTHOR_STRIP = re.compile(r"[\s_\-·]+")


def title_fingerprint(text: str) -> str:
    raw = _TITLE_STRIP.sub("", str(text or ""))
    raw = re.sub(r"https?://\S+", "", raw)
    return _TITLE_PUNCT.sub("", raw).lower()[:40]


def author_fingerprint(name: str) -> str:
    return _AUTHOR_STRIP.sub("", str(name or "")).lower()[:20]


def syndicate_key(item: dict[str, Any]) -> str:
    title = title_fingerprint(item.get("title") or item.get("text") or "")
    author = author_fingerprint(item.get("author") or "")
    if len(title) >= 16 and author:
        return f"{author}|{title[:18]}"
    if len(title) >= 12:
        return title
    return ""


def cluster_cross_platform(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = syndicate_key(item)
        if not key:
            continue
        groups.setdefault(key, []).append(item)
    return {
        key: rows
        for key, rows in groups.items()
        if len({str(row.get("platform") or "") for row in rows}) >= 2
    }


def pick_unique_items(
    query: str,
    items: list[dict[str, Any]],
    wanted: int | None = None,
    taken_keys: set[str] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]], int]:
    taken = taken_keys if taken_keys is not None else set()
    unique: list[dict[str, Any]] = []
    dups: list[dict[str, Any]] = []
    dropped: list[tuple[dict[str, Any], str]] = []
    for item in items:
        reason = reject_reason(query, item, now=now)
        if reason:
            dropped.append((item, reason))
            continue
        key = syndicate_key(item)
        if key and key in taken:
            dups.append(item)
        else:
            unique.append(item)
    out = list(unique)
    if wanted:
        out = unique[:wanted]
        if len(out) < wanted:
            out.extend(dups[: wanted - len(out)])
    for item in out:
        key = syndicate_key(item)
        if key:
            taken.add(key)
        stamp_freshness(item, query)
    return out, dropped, len(unique)


def fetch_budget(wanted: int, cap: int = 200) -> int:
    need = max(1, int(wanted))
    extra = min(8, max(3, need // 5))
    return min(cap, need + extra)


def item_freshness(
    item: dict[str, Any],
    now: datetime | None = None,
    query: str = "",
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    dated = parse_item_date(item)
    if dated is None:
        return {
            "age_days": None,
            "band": "unknown",
            "label": "时间不明",
            "weight": "时间不明",
            "hint": "没有发布时间，不能当近况用。",
        }
    if dated.tzinfo is None:
        dated = dated.replace(tzinfo=timezone.utc)
    age = max(0, (now - dated).days)
    tight = is_time_sensitive(query)
    if age <= 7:
        band, label, weight, hint = "week", "近7天", "近况", "最近发出，结论可优先采信。"
    elif age <= 30:
        band, label, weight, hint = "month", "近一个月", "近况", "仍属近期，可作当前判断。"
    elif age <= 90:
        band, label, weight, hint = "quarter", "近三月", "可参考", "还能对照，但不是最新近况。"
        if tight:
            weight, hint = "仅背景", "这道题时效紧，三个月以上只作背景。"
    elif age <= 180:
        band, label, weight, hint = "half", "近半年", "仅背景", "只能当背景，不能单独支撑现在怎么选。"
        if tight:
            weight, hint = "慎用", "这道题时效紧，偏旧的材料要降权。"
    elif age <= 365:
        band, label, weight, hint = "year", "半年前", "仅背景", "已过半年，只适合看当时怎么说。"
        if tight:
            weight, hint = "慎用", "过旧，不能当这次活动或当下选择的证据。"
    else:
        years = max(1, age // 365)
        band, label, weight, hint = "old", f"{years}年前", "慎用", "过旧，只能说明曾经有过这种说法。"
    return {
        "age_days": age,
        "band": band,
        "label": label,
        "weight": weight,
        "hint": hint,
    }


def stamp_freshness(
    item: dict[str, Any],
    query: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    mark = item_freshness(item, now=now, query=query)
    item["freshness"] = mark["band"]
    item["fresh_label"] = mark["label"]
    item["fresh_weight"] = mark["weight"]
    item["fresh_hint"] = mark["hint"]
    item["age_days"] = mark["age_days"]
    return item


def freshness_summary(
    items: list[dict[str, Any]],
    query: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    weights: dict[str, int] = {}
    for item in items:
        mark = item_freshness(item, now=now, query=query)
        counts[mark["label"]] = counts.get(mark["label"], 0) + 1
        weights[mark["weight"]] = weights.get(mark["weight"], 0) + 1
    recent = weights.get("近况", 0)
    background = weights.get("仅背景", 0) + weights.get("慎用", 0)
    unknown = weights.get("时间不明", 0)
    if recent and background:
        note = f"近况 {recent} 条，偏旧 {background} 条。结论先看近况，旧帖只作对照。"
    elif recent:
        note = f"近况 {recent} 条，这批材料的时间还站得住。"
    elif background:
        note = f"偏旧 {background} 条，先别把当时的说法写成现在的结论。"
    else:
        note = f"时间不明 {unknown} 条，判断时把时效留白。"
    return {
        "counts": counts,
        "weights": weights,
        "note": note,
    }
