"""Turn a saved board into Markdown or a print-styled HTML page."""

from __future__ import annotations

import base64
import hashlib
import html
import re
from pathlib import Path
from typing import Any

from datetime import datetime, timezone

from freshness import dated_items, stamp_freshness
from intent_frame import pick_frame, pick_tag, resolve_pick_mode

VALUE = {1: "丢掉", 2: "知道即可", 3: "保持观察", 4: "认真看", 5: "立即处理"}
ROLE = {"support": "支撑", "counter": "反面", "context": "背景", "noise": "噪音"}
ACT = {"act": "动手", "read": "读完", "watch": "盯着", "know": "知道", "ignore": "放过"}
CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_APPENDIX = re.compile(r"互动最高|按点赞|传播排序|数据来源|原帖|标签和叙事|用户讨论最高")
_NUM = re.compile(
    r"(?<![A-Za-z\d%％])("
    r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(?:\s*[-—~～至到]\s*(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))?"
    r"(?:\s*(?:%|％|万|亿|倍|元|块|美元|美金|GB|TB|英寸|小时|分钟))?"
    r")"
)


def _judgment_view(
    board: dict[str, Any], report: dict[str, Any], query: str
) -> dict[str, Any]:
    judgment = board.get("judgment") if isinstance(board.get("judgment"), dict) else {}
    conclusion = report.get("conclusion") if isinstance(report.get("conclusion"), dict) else {}
    answer = str(conclusion.get("answer") or report.get("answer") or "").strip()
    one = str(judgment.get("one_line_judgment") or board.get("one_line_judgment") or "").strip()
    if len(one) < 12:
        one = (re.split(r"[。！？\n]", answer)[0].strip() if answer else "") or (
            f"这批材料能看「{query}」怎么被讨论，不能当成已核实结论。"
        )
        if one and one[-1] not in "。！？":
            one += "。"
    headline = str(judgment.get("headline_zh") or board.get("headline_zh") or "").strip()
    bare = re.sub(r"[「」《》]|检索报告", "", headline).strip()
    if not headline or bare == (query or "").strip():
        seed = one.split("：", 1)[-1] if "：" in one else one
        headline = seed.rstrip("。！？…")[:22] or "十一的判断"
    value = judgment.get("value") if isinstance(judgment.get("value"), dict) else {}
    caveats = [str(row) for row in (judgment.get("caveats") or board.get("caveats") or []) if row]
    if not any("叙事信号" in row or "已核实" in row for row in caveats):
        caveats.append("社交帖只当叙事信号，不是已核实新闻。")
    return {
        "headline_zh": headline,
        "one_line_judgment": one,
        "plain_language": str(judgment.get("plain_language") or board.get("plain_language") or "").strip()
        or str(report.get("overview") or "").strip()
        or answer
        or one,
        "worth_interrupting": bool(judgment.get("worth_interrupting") or board.get("worth_interrupting")),
        "value": value,
        "caveats": caveats,
        "next_watch": str(judgment.get("next_watch") or board.get("next_watch") or ""),
        "illustrations": judgment.get("illustrations") or [],
    }


def _pick_view(
    report: dict[str, Any], intent: dict[str, Any], query: str
) -> dict[str, str]:
    mode = resolve_pick_mode(intent, query)
    custom = report.get("pick_frame") if isinstance(report.get("pick_frame"), dict) else None
    return pick_frame(mode, custom)


def hoist_report(board: dict[str, Any]) -> dict[str, Any]:
    report = board.get("report") if isinstance(board.get("report"), dict) else {}
    report = dict(report)
    for key in (
        "sources",
        "sections",
        "facts",
        "map",
        "gallery",
        "timeline",
        "picks",
        "pick_frame",
        "platform_briefs",
        "answer",
        "conclusion",
    ):
        if not report.get(key) and board.get(key) is not None:
            report[key] = board[key]
    return report


def item_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _axis_when(stamp: datetime, with_time: bool = False, with_year: bool = False) -> str:
    local = stamp.astimezone()
    label = f"{local.year}年{local.month}月{local.day}日" if with_year else f"{local.month}月{local.day}日"
    if not with_time:
        return label
    return f"{label} {local.strftime('%H:%M')}"


def _axis_layout(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated = dated_items(items)
    if not dated:
        return None
    now = datetime.now(timezone.utc)
    oldest = dated[0][0]
    start = oldest
    if len(dated) >= 8:
        keep = dated[max(0, len(dated) * 6 // 100)][0]
        if (now - oldest).total_seconds() > 4 * max((now - keep).total_seconds(), 60):
            start = keep
    span = max((now - start).total_seconds(), 60.0)
    stale_cut = start.timestamp() - span
    stale_ms = 90 * 24 * 3600
    on_axis: list[tuple[datetime, dict[str, Any]]] = []
    stale: list[tuple[datetime, dict[str, Any]]] = []
    for stamp, item in dated:
        age = (now - stamp).total_seconds()
        far = stamp < start and (age >= stale_ms or stamp.timestamp() < stale_cut)
        if far:
            stale.append((stamp, item))
        else:
            on_axis.append((stamp, item))
    return {"now": now, "start": start, "span": span, "on_axis": on_axis, "stale": stale, "dated": dated}


def _axis_html(items: list[dict[str, Any]]) -> str:
    layout = _axis_layout(items)
    if not layout:
        return ""
    start = layout["start"]
    span = layout["span"]
    dots: list[str] = []
    for index, (stamp, _item) in enumerate(layout["on_axis"]):
        pct = min(98.5, max(1.2, (stamp - start).total_seconds() / span * 100))
        lift = (index % 5 - 2) * 3
        dots.append(f'<i style="left:{pct:.3f}%;margin-top:{lift}px"></i>')
    long = span >= 36 * 3600
    left = _axis_when(start, not long)
    stale_n = len(layout["stale"])
    stale_lead = (
        f'另有 {stale_n} 条过旧，没铺进这条轴，参考价值有限。'
        if stale_n
        else ""
    )
    return (
        '<section class="axis-wrap"><div class="kicker">时间分布</div><h2>时间线</h2>'
        f'<p class="lead">{len(layout["on_axis"])} / {len(items)} 条铺在轴上，从 {_esc(left)} 铺到现在。{_esc(stale_lead)}</p>'
        f'<div class="axis-rail">{"".join(dots)}<b></b></div>'
        f'<div class="axis-scale"><span>{_esc(left)}</span><span>现在</span></div></section>'
    )


def author_label(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    name = str(item.get("author") or "").strip()
    fans = str(item.get("fans") or "").strip()
    if name and fans:
        return f"{name}（{fans if '粉' in fans else fans + '粉'}）"
    return name


def is_appendix_section(section: dict[str, Any]) -> bool:
    return bool(_APPENDIX.search(str(section.get("heading") or "")))


def section_heading(section: dict[str, Any], index: int) -> str:
    raw = str(section.get("heading") or "论点")
    if re.match(r"^[一二三四五六七八九十]、", raw) or re.match(r"^论点[一二三四五六七八九十]", raw):
        return raw
    return f"{CN_NUM[index] if index < len(CN_NUM) else index + 1}、{raw}"


def safe_filename(title: str, suffix: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|]', "_", (title or "智搜报告").strip()) or "智搜报告"
    stem = re.sub(r"\s+", " ", stem)[:80]
    return f"{stem}{suffix}"


def md_cell(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "\\|").strip()


def md_paragraphs(text: Any) -> str:
    parts = [row.strip() for row in re.split(r"\n{2,}|\n", str(text or "")) if row.strip()]
    return "\n\n".join(parts)


def md_item_link(items: list[dict[str, Any]], item_id: str, fallback: str = "") -> str:
    item = item_by_id(items, item_id)
    title = fallback or (item or {}).get("title") or item_id
    url = (item or {}).get("url")
    return f"[{title}]({url})" if url else str(title)


def md_evidence(items: list[dict[str, Any]], rows: list[Any] | None) -> str:
    lines = []
    for row in rows or []:
        item_id = row.get("item_id") if isinstance(row, dict) else row
        item = item_by_id(items, str(item_id or ""))
        text = (row.get("text") if isinstance(row, dict) else None) or (item or {}).get("title") or item_id
        meta = " · ".join(part for part in [(item or {}).get("platform_label"), author_label(item)] if part)
        link = md_item_link(items, str(item_id or ""), str(text))
        lines.append(f"- {link}" + (f"（{meta}）" if meta else ""))
    return "\n".join(lines)


def md_table(table: dict[str, Any] | None) -> str:
    if not table or not (table.get("headers") or []):
        return ""
    headers = [md_cell(h) for h in table["headers"]]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in table.get("rows") or []:
        cells = row if isinstance(row, list) else (row.get("cells") if isinstance(row, dict) else [])
        lines.append("| " + " | ".join(md_cell(cell) for cell in (cells or [])) + " |")
    caption = f"*{table.get('caption')}*\n\n" if table.get("caption") else ""
    return caption + "\n".join(lines)


def md_chart(chart: dict[str, Any] | None) -> str:
    if not chart or not (chart.get("items") or []):
        return ""
    title = chart.get("title") or "数量对照"
    rows = "\n".join(f"- {row.get('label')}：{row.get('value')}" for row in chart["items"])
    return f"**{title}**\n\n{rows}"


def board_to_markdown(
    board: dict[str, Any],
    items: list[dict[str, Any]] | None = None,
    asks: list[dict[str, Any]] | None = None,
    query: str = "",
) -> str:
    items = items or []
    asks = asks or []
    report = hoist_report(board)
    intent = board.get("intent") if isinstance(board.get("intent"), dict) else {}
    sources = report.get("sources") if isinstance(report.get("sources"), dict) else {}
    judgment = board.get("judgment") if isinstance(board.get("judgment"), dict) else {}
    frame = _pick_view(report, intent, query)
    main = [s for s in (report.get("sections") or []) if isinstance(s, dict) and not is_appendix_section(s)]
    extra = [s for s in (report.get("sections") or []) if isinstance(s, dict) and is_appendix_section(s)]
    parts: list[str] = [f"# {report.get('title') or '智搜报告'}"]
    if intent.get("label") or query:
        parts.append("> " + " · ".join(part for part in [intent.get("label"), query] if part))
    if report.get("deck") or intent.get("job"):
        parts.append(str(report.get("deck") or intent.get("job")))
    if report.get("overview"):
        parts.append(md_paragraphs(report.get("overview")))
    brief = board.get("agent_search") if isinstance(board.get("agent_search"), dict) else {}
    conclusion = report.get("conclusion") if isinstance(report.get("conclusion"), dict) else {}
    answer = str(conclusion.get("answer") or report.get("answer") or "").strip()
    flow = [row for row in (conclusion.get("flow") or []) if isinstance(row, dict)]
    picks = report.get("picks") or []
    if answer or flow or picks:
        parts.append(f"## {frame['title']}")
        parts.append(frame["lead"])
        if answer:
            parts.append(md_paragraphs(answer))
        if flow:
            parts.append(
                "\n".join(
                    f"{index + 1}. **{row.get('name') or ''}**  \n   {row.get('note') or ''}"
                    for index, row in enumerate(flow)
                )
            )
        pick_lines = []
        mode = resolve_pick_mode(intent, query)
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            tag = pick_tag(pick, frame, mode)
            name = md_item_link(items, str(pick.get("item_id") or ""), str(pick.get("name") or "")) if pick.get("item_id") else pick.get("name") or ""
            pick_lines.append(f"- **{tag} · {name}**  \n  {pick.get('reason') or ''}")
        if pick_lines:
            parts.append("\n".join(pick_lines))
        parts.append(f"*{frame['foot']}*")
    mapping = report.get("map") or []
    if mapping:
        tree = [f"- **{report.get('title') or query or '这批材料'}**"]
        for node in mapping:
            if not isinstance(node, dict):
                continue
            tree.append(f"  - **{node.get('name')}**")
            tree.extend(f"    - {child}" for child in (node.get("children") or []))
        parts.append("## 导图")
        parts.append("\n".join(tree))
    briefs = [row for row in (report.get("platform_briefs") or []) if isinstance(row, dict)]
    if briefs:
        parts.append("## 各平台摘要")
        parts.append("先看这一站针对这次内容在说什么。限制只留一句。")
        for row in briefs:
            parts.append(f"### {row.get('name') or '这一站'}")
            if row.get("answer"):
                parts.append(str(row.get("answer")))
            if row.get("worth"):
                parts.append(f"限制：{row.get('worth')}")
    if brief.get("overview") or brief.get("points"):
        who = " · ".join(part for part in [brief.get("model"), brief.get("created_at")] if part)
        parts.append("## 模型参考资料")
        parts.append(f"> {who or '模型'}。这是模型知识，不是本机检索，只作辅助材料。")
        if brief.get("overview"):
            parts.append(md_paragraphs(brief.get("overview")))
    if main:
        parts.append("## 深度阅读")
        for index, section in enumerate(main):
            parts.append(f"### {section_heading(section, index)}")
            if section.get("thesis"):
                parts.append(f"**{section['thesis']}**")
            if section.get("body"):
                parts.append(md_paragraphs(section.get("body")))
            for pic in section.get("illustrations") or []:
                if not isinstance(pic, dict):
                    continue
                cap = pic.get("caption") or pic.get("query") or "配图"
                src = pic.get("source") or ""
                parts.append(f"![{cap}]({pic.get('url')})" if pic.get("url") else cap)
                if src:
                    parts.append(f"*{src}*")
            evidence = md_evidence(items, section.get("evidence"))
            if evidence:
                parts.append(f"**案例**\n\n{evidence}")
            table = md_table(section.get("table") if isinstance(section.get("table"), dict) else None)
            if table:
                parts.append(table)
            chart = md_chart(section.get("chart") if isinstance(section.get("chart"), dict) else None)
            if chart:
                parts.append(chart)
    eng = [row for row in (report.get("engagement") or []) if isinstance(row, dict)]
    if eng:
        parts.append("## 各站互动")
        parts.append("点赞只在同一站里比。平台展示数，不是官方统计。")
        for row in eng:
            parts.append(f"### {row.get('name') or '这一站'}")
            table = md_table(row.get("table") if isinstance(row.get("table"), dict) else None)
            if table:
                parts.append(table)
    parts.append("## 数据来源与覆盖")
    counts: dict[str, int] = {}
    for item in items:
        label = str(item.get("platform_label") or item.get("platform") or "其他")
        counts[label] = counts.get(label, 0) + 1
    parts.append(str(sources.get("summary") or f"共 {len(items)} 条检索。"))
    if sources.get("coverage"):
        parts.append(md_paragraphs(sources.get("coverage")))
    platforms = sources.get("platforms") if isinstance(sources.get("platforms"), list) else []
    if platforms:
        parts.append("\n".join(f"- {row.get('name')}：{row.get('count')}" for row in platforms if isinstance(row, dict)))
    elif counts:
        parts.append("\n".join(f"- {name}：{count}" for name, count in counts.items()))
    facts = [row for row in (report.get("facts") or []) if isinstance(row, dict)]
    if facts:
        parts.append("\n".join(f"- **{row.get('label')}**：{row.get('value')}" for row in facts))
    layout = _axis_layout(items)
    if layout:
        start = layout["start"].astimezone().strftime("%Y-%m-%d %H:%M")
        stale_n = len(layout["stale"])
        stale_note = f"另有 {stale_n} 条过旧，没铺进轴里，参考价值有限。" if stale_n else "网页沿轴悬停，点开这一刻的全部条目。"
        parts.append("## 时间线")
        parts.append(
            f"{len(layout['on_axis'])} / {len(items)} 条铺在轴上，从 {start} 铺到现在。{stale_note}"
        )
    for section in extra:
        if re.search(r"互动最高|按点赞|传播排序", str(section.get("heading") or "")):
            continue
        parts.append(f"## {section.get('heading') or '补充数据'}")
        if section.get("body"):
            parts.append(md_paragraphs(section.get("body")))
        evidence = md_evidence(items, section.get("evidence"))
        if evidence:
            parts.append(evidence)
        table = md_table(section.get("table") if isinstance(section.get("table"), dict) else None)
        if table:
            parts.append(table)
        chart = md_chart(section.get("chart") if isinstance(section.get("chart"), dict) else None)
        if chart:
            parts.append(chart)
    judgment = _judgment_view(board, report, query)
    value = judgment.get("value") if isinstance(judgment.get("value"), dict) else {}
    level = int(value.get("level") or 1)
    parts.append("## 十一的判断与限制")
    parts.append(f"### {judgment.get('headline_zh') or '判断'}")
    interrupt = "值得打断" if judgment.get("worth_interrupting") else "不必打断"
    parts.append(f"价值 {level}/5 · {value.get('label') or VALUE.get(level, '')} · {interrupt}")
    parts.append(str(judgment.get("one_line_judgment") or ""))
    if judgment.get("plain_language"):
        parts.append(str(judgment.get("plain_language")))
    if judgment.get("next_watch"):
        parts.append(f"还可以看：{judgment.get('next_watch')}")
    for caveat in judgment.get("caveats") or []:
        parts.append(f"- 限制：{caveat}")
    for pic in judgment.get("illustrations") or []:
        if not isinstance(pic, dict):
            continue
        cap = pic.get("caption") or pic.get("query") or "配图"
        if pic.get("url"):
            parts.append(f"![{cap}]({pic.get('url')})")
        if pic.get("source"):
            parts.append(f"*{pic.get('source')}*")
    if items:
        parts.append("## 原始检索结果")
        for item in items:
            author = author_label(item)
            stats = " · ".join(
                part
                for part in [
                    f"赞 {item.get('liked') or 0}",
                    f"藏 {item.get('collected') or 0}",
                    f"评 {item.get('comment_count') or 0}",
                    f"转 {item.get('share_count') or 0}",
                ]
                if part
            )
            title = item.get("title") or "无标题"
            link = f"[{title}]({item.get('url')})" if item.get("url") else title
            parts.append(f"- {item.get('platform_label') or ''} · {link}  \n  {author}{(' · ' + stats) if stats else ''}")
    if asks:
        parts.append("## 追问")
        for index, row in enumerate(asks, 1):
            parts.append(f"### {index}. {row.get('q') or '追问'}")
            if row.get("a"):
                parts.append(md_paragraphs(row.get("a")))
            for point in row.get("points") or []:
                parts.append(f"- {point}")
            for pic in row.get("illustrations") or []:
                if not isinstance(pic, dict) or not pic.get("url"):
                    continue
                cap = pic.get("caption") or pic.get("query") or "配图"
                parts.append(f"![{cap}]({pic.get('url')})")
                if pic.get("source"):
                    parts.append(f"*{pic.get('source')}*")
            for ref in row.get("web_refs") or []:
                if not isinstance(ref, dict):
                    continue
                parts.append(f"- [{ref.get('title') or ref.get('url')}]({ref.get('url')})" + (f" {ref.get('note')}" if ref.get("note") else ""))
    parts.append("---")
    parts.append(f"由一览导出。{f'材料 {len(items)} 条。' if items else ''}社交帖只当叙事信号，不是已核实新闻。")
    return "\n\n".join(part for part in parts if part) + "\n"


def _esc(text: Any) -> str:
    return html.escape(str(text or ""))


def _emphasize(text: Any) -> str:
    escaped = _esc(text)

    def repl(match: re.Match[str]) -> str:
        token = match.group(1)
        bare = re.sub(r"[,\s]", "", token)
        if re.fullmatch(r"20\d{2}", bare) or re.fullmatch(r"\d{1,2}", bare):
            return match.group(0)
        return f"<mark>{_esc(token)}</mark>"

    return _NUM.sub(repl, escaped)


def _fresh_html(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    label = str(item.get("fresh_label") or "").strip()
    if not label:
        return ""
    band = str(item.get("freshness") or "unknown")
    weight = str(item.get("fresh_weight") or "").strip()
    return (
        f'<span class="fresh {_esc(band)}">{_esc(label)}'
        + (f"<i>{_esc(weight)}</i>" if weight else "")
        + "</span>"
    )


def _illus_html(rows: Any) -> str:
    if not isinstance(rows, list):
        return ""
    cards = []
    for pic in rows:
        if not isinstance(pic, dict) or not pic.get("url"):
            continue
        cap = pic.get("caption") or pic.get("query") or "配图"
        src = pic.get("source") or ""
        cards.append(
            f'<figure class="illus-card"><img src="{_esc(pic.get("url"))}" alt="{_esc(cap)}" />'
            f"<figcaption>{_esc(cap)}{(' · ' + _esc(src)) if src else ''}</figcaption></figure>"
        )
    return f'<div class="illus">{"".join(cards)}</div>' if cards else ""


def _paras(text: Any, cls: str = "") -> str:
    rows = [row.strip() for row in re.split(r"\n{2,}|\n", str(text or "")) if row.strip()]
    if not rows:
        return ""
    klass = f' class="{cls}"' if cls else ""
    return "".join(f"<p{klass}>{_emphasize(row)}</p>" for row in rows)


def _file_data_uri(path: Path) -> str | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > 1_200_000:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def local_image_uri(job_dir: Path | None, item: dict[str, Any] | None, index: int = 0) -> str:
    if not job_dir or not item:
        return ""
    images = item.get("images") or []
    if index >= len(images):
        return ""
    url = str(images[index])
    if url.startswith("frame:"):
        path = Path(url[6:])
        try:
            resolved = path.resolve()
            resolved.relative_to(job_dir.resolve())
        except (OSError, ValueError):
            return ""
        return _file_data_uri(resolved) or ""
    digest = hashlib.sha1(f"{item.get('id')}:{index}:{url}".encode("utf-8")).hexdigest()[:20]
    return _file_data_uri(job_dir / "media" / f"{digest}.jpg") or ""


def _table_html(table: dict[str, Any] | None) -> str:
    if not table or not (table.get("headers") or []):
        return ""
    head = "".join(f"<th>{_esc(h)}</th>" for h in table["headers"])
    body = []
    for row in table.get("rows") or []:
        cells = row if isinstance(row, list) else (row.get("cells") if isinstance(row, dict) else [])
        body.append("<tr>" + "".join(f"<td>{_emphasize(cell)}</td>" for cell in (cells or [])) + "</tr>")
    caption = f"<caption>{_esc(table.get('caption'))}</caption>" if table.get("caption") else ""
    return f"<table>{caption}<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _chart_html(chart: dict[str, Any] | None) -> str:
    items = (chart or {}).get("items") or []
    if not items:
        return ""
    max_value = max((float(row.get("value") or 0) for row in items), default=1) or 1
    rows = []
    for row in items:
        value = float(row.get("value") or 0)
        width = max(2, min(100, round(value / max_value * 100)))
        rows.append(
            f'<div class="bar"><span>{_esc(row.get("label"))}</span>'
            f'<i style="width:{width}%"></i><b>{_esc(row.get("value"))}</b></div>'
        )
    return f'<div class="bars"><div class="kicker">{_esc(chart.get("title") or "数量对照")}</div>{"".join(rows)}</div>'


def _evidence_chips(items: list[dict[str, Any]], rows: list[Any] | None) -> str:
    chips = []
    for row in rows or []:
        item_id = str((row.get("item_id") if isinstance(row, dict) else row) or "")
        item = item_by_id(items, item_id)
        text = (row.get("text") if isinstance(row, dict) else None) or (item or {}).get("title") or item_id
        who = str((item or {}).get("author") or "").strip()
        href = (item or {}).get("url") or ""
        inner = (
            f"<strong>{_esc(text)}</strong>{_fresh_html(item)}"
            + (f'<span class="who">{_esc(who)}</span>' if who else "")
        )
        chips.append(
            f'<a class="fact" href="{_esc(href)}">{inner}</a>' if href else f'<div class="fact">{inner}</div>'
        )
    return "".join(chips)


def _related_shots(
    items: list[dict[str, Any]],
    rows: list[Any] | None,
    job_dir: Path | None,
    image_budget: list[int],
    cap: int = 6,
) -> str:
    pics = []
    seen: set[str] = set()
    for row in rows or []:
        if len(pics) >= cap or image_budget[0] <= 0:
            break
        item_id = str((row.get("item_id") if isinstance(row, dict) else row) or "")
        if not item_id or item_id in seen:
            continue
        item = item_by_id(items, item_id)
        uri = local_image_uri(job_dir, item, 0)
        if not uri:
            continue
        seen.add(item_id)
        text = (item or {}).get("title") or item_id
        pics.append(
            f'<figure class="shot"><img src="{uri}" alt="" /><figcaption>{_esc(text)}</figcaption></figure>'
        )
        image_budget[0] -= 1
    return f'<div class="shots">{"".join(pics)}</div>' if pics else ""


def _pane_block(label: str, inner: str, hint: str = "") -> str:
    if not inner:
        return ""
    extra = f"<span>{_esc(hint)}</span>" if hint else ""
    return f'<div class="pane-block"><div class="pane-label">{_esc(label)}{extra}</div>{inner}</div>'


def _section_pane(
    items: list[dict[str, Any]],
    section: dict[str, Any],
    job_dir: Path | None,
    image_budget: list[int],
) -> str:
    chips = _evidence_chips(items, section.get("evidence"))
    shots = _related_shots(items, section.get("evidence"), job_dir, image_budget)
    table = section.get("table") if isinstance(section.get("table"), dict) else None
    chart = section.get("chart") if isinstance(section.get("chart"), dict) else None
    table_html = _table_html(table)
    chart_html = _chart_html(chart)
    parts = [
        _pane_block("案例详情", f'<div class="evidence">{chips}</div>' if chips else "", "点标题打开原帖"),
        _pane_block("相关配图", shots),
        _pane_block("数据对照", table_html, str((table or {}).get("caption") or "")),
        _pane_block(str((chart or {}).get("title") or "数量对照"), chart_html),
    ]
    body = "".join(part for part in parts if part)
    if not body:
        return '<p class="deep-empty">这一节没有单独抽出案例或图表，先看左边结论。</p>'
    return body


def board_to_html(
    board: dict[str, Any],
    items: list[dict[str, Any]] | None = None,
    asks: list[dict[str, Any]] | None = None,
    query: str = "",
    job_dir: Path | None = None,
) -> str:
    items = items or []
    asks = asks or []
    for item in items:
        if isinstance(item, dict) and not item.get("fresh_label"):
            stamp_freshness(item, query)
    report = hoist_report(board)
    intent = board.get("intent") if isinstance(board.get("intent"), dict) else {}
    sources = report.get("sources") if isinstance(report.get("sources"), dict) else {}
    judgment = board.get("judgment") if isinstance(board.get("judgment"), dict) else {}
    frame = _pick_view(report, intent, query)
    main = [s for s in (report.get("sections") or []) if isinstance(s, dict) and not is_appendix_section(s)]
    extra = [s for s in (report.get("sections") or []) if isinstance(s, dict) and is_appendix_section(s)]
    image_budget = [24]
    chunks: list[str] = [
        '<div class="brandline"><b>一览</b><span>公开检索报告</span></div>',
        f'<header class="report-hero"><div class="kicker">{_esc(intent.get("label") or "调研报告")}</div>'
        f'<h1>{_esc(report.get("title") or "智搜报告")}</h1>'
        f'<p class="deck">{_emphasize(report.get("deck") or intent.get("job") or query)}</p></header>',
    ]
    if report.get("overview"):
        chunks.append(f'<section class="overview">{_paras(report.get("overview"), "overview")}</section>')
    conclusion = report.get("conclusion") if isinstance(report.get("conclusion"), dict) else {}
    answer = str(conclusion.get("answer") or report.get("answer") or "").strip()
    flow = [row for row in (conclusion.get("flow") or []) if isinstance(row, dict) and (row.get("name") or row.get("note"))]
    picks = [row for row in (report.get("picks") or []) if isinstance(row, dict)]
    concl_table = conclusion.get("table") if isinstance(conclusion.get("table"), dict) else None
    concl_chart = conclusion.get("chart") if isinstance(conclusion.get("chart"), dict) else None
    if answer or flow or picks or (concl_table or {}).get("rows") or (concl_chart or {}).get("items"):
        mode = resolve_pick_mode(intent, query)
        cards = []
        if answer:
            cards.append(f'<div class="concl-answer">{_paras(answer, "concl-prose")}</div>')
        if flow:
            cards.append(
                '<ol class="concl-flow">'
                + "".join(
                    f"<li><strong>{_esc(row.get('name') or '')}</strong><span>{_emphasize(row.get('note') or '')}</span></li>"
                    for row in flow
                )
                + "</ol>"
            )
        data = ""
        if (concl_table or {}).get("rows") or (concl_chart or {}).get("items"):
            data = (
                f'<div class="concl-data">{_table_html(concl_table)}{_chart_html(concl_chart)}</div>'
            )
        pick_cards = []
        for pick in picks:
            verdict = pick.get("verdict") or "watch"
            name = pick.get("name") or ""
            item = item_by_id(items, str(pick.get("item_id") or ""))
            href = (item or {}).get("url") or ""
            title = f'<a href="{_esc(href)}">{_esc(name)}</a>' if href else _esc(name)
            pick_cards.append(
                f'<div class="pick {_esc(verdict)}"><span class="pick-tag">{_esc(pick_tag(pick, frame, mode))}</span>'
                f"<div><strong>{title}</strong><span class=\"hint\">{_emphasize(pick.get('reason'))}</span></div></div>"
            )
        chunks.append(
            '<section class="picks-wrap">'
            '<div class="picks-head"><div class="kicker">先看结论</div>'
            f'<h2>{_esc(frame["title"])}</h2>'
            f'<p>{_esc(frame["lead"])}</p></div>'
            f'{"".join(cards)}{data}'
            + (f'<div class="picks">{"".join(pick_cards)}</div>' if pick_cards else "")
            + f'<div class="picks-foot">{_esc(frame["foot"])}</div></section>'
        )
    mapping = [row for row in (report.get("map") or []) if isinstance(row, dict)]
    if mapping:
        arms = []
        for node in mapping:
            kids = "".join(f"<li>{_emphasize(child)}</li>" for child in (node.get("children") or []))
            arms.append(f'<div class="mind-arm"><div class="mind-hub">{_esc(node.get("name"))}</div><ul>{kids}</ul></div>')
        chunks.append(
            f'<section><h2>导图</h2><div class="mindmap">'
            f'<div class="mind-root">{_esc(report.get("title") or query or "这批材料")}</div>'
            f'<div class="mind-arms">{"".join(arms)}</div></div></section>'
        )
    briefs = [row for row in (report.get("platform_briefs") or []) if isinstance(row, dict)]
    if briefs:
        cards = []
        for row in briefs:
            cards.append(
                f'<article class="brief"><strong>{_esc(row.get("name") or "这一站")}</strong>'
                f'<p class="brief-body">{_emphasize(row.get("answer"))}</p>'
                + (f'<span class="hint">限制：{_esc(row.get("worth"))}</span>' if row.get("worth") else "")
                + "</article>"
            )
        chunks.append(
            '<section class="briefs-wrap"><div class="kicker">中档材料</div>'
            "<h2>各平台摘要</h2><p>先看这一站针对这次内容在说什么。限制只留一句。</p>"
            f'<div class="briefs">{"".join(cards)}</div></section>'
        )
    brief = board.get("agent_search") if isinstance(board.get("agent_search"), dict) else {}
    if brief.get("overview") or brief.get("points"):
        who = " · ".join(part for part in [brief.get("model"), brief.get("created_at")] if part)
        chunks.append(
            '<section class="agent-ref-wrap"><div class="kicker">模型参考 · 不进时间线</div>'
            "<h2>关于这个话题的 AI 参考资料</h2>"
            f'<p class="hint">{_esc(who or "模型")}。模型知识，只作辅助材料。</p>'
            f"<p>{_emphasize(brief.get('overview'))}</p></section>"
        )
    if main:
        pills = []
        for index, section in enumerate(main):
            heading = re.sub(r"^[一二三四五六七八九十]、", "", section_heading(section, index))
            pills.append(
                f'<span class="deep-card{" on" if index == 0 else ""}"><span class="deep-no">'
                f"{CN_NUM[index] if index < len(CN_NUM) else index + 1}</span>"
                f"<strong>{_esc(heading)}</strong></span>"
            )
        chunks.append(
            '<section class="deep-read"><div class="deep-head"><div class="kicker">对照着读</div>'
            "<h2>深度阅读</h2><p>围着上面的结论读。左边是论点，右边是这一节的论据、案例和数据。</p></div>"
            f'<div class="deep-cards">{"".join(pills)}</div>'
        )
        for index, section in enumerate(main):
            heading = section_heading(section, index)
            chunks.append(
                f'<article class="deep-spread">'
                f'<div class="deep-grid">'
                f'<div class="deep-copy"><h3>{_esc(heading)}</h3>'
                + (f'<p class="thesis">{_emphasize(section.get("thesis"))}</p>' if section.get("thesis") else "")
                + _paras(section.get("body"))
                + _illus_html(section.get("illustrations"))
                + "</div>"
                f'<aside class="deep-right"><div class="kicker">这一节的材料</div>'
                f"{_section_pane(items, section, job_dir, image_budget)}</aside>"
                "</div></article>"
            )
        chunks.append("</section>")
    eng = [row for row in (report.get("engagement") or []) if isinstance(row, dict)]
    if eng:
        blocks = [
            '<section class="pack open"><div class="kicker">分站对照</div><h2>各站互动</h2>'
            '<p class="lead">点赞只在同一站里比。平台展示数，不是官方统计。抖音和贴吧量级差太多，不能放一张表。</p>'
        ]
        for row in eng:
            blocks.append(f'<h3>{_esc(row.get("name") or "这一站")} · {_esc(row.get("count") or "")}</h3>')
            blocks.append(_table_html(row.get("table") if isinstance(row.get("table"), dict) else None))
        blocks.append("</section>")
        chunks.append("".join(blocks))
    plat_rows = sources.get("platforms") if isinstance(sources.get("platforms"), list) else []
    counts: dict[str, float] = {}
    if plat_rows:
        for row in plat_rows:
            if isinstance(row, dict):
                counts[str(row.get("name") or "其他")] = float(row.get("count") or 0)
    else:
        for item in items:
            label = str(item.get("platform_label") or item.get("platform") or "其他")
            counts[label] = counts.get(label, 0) + 1
    facts = [row for row in (report.get("facts") or []) if isinstance(row, dict)]
    fact_html = "".join(
        f'<div class="fact"><strong>{_esc(row.get("label"))}</strong>{_emphasize(row.get("value"))}'
        f"{_fresh_html(item_by_id(items, str(row.get('item_id') or '')))}</div>"
        for row in facts
    )
    chunks.append(
        f'<section class="pack open"><h2>数据来源与覆盖</h2>'
        f'<p class="lead">{_esc(sources.get("summary") or f"共 {len(items)} 条检索。")}</p>'
        + _paras(sources.get("coverage"))
        + _chart_html({"title": "平台条数", "items": [{"label": n, "value": c} for n, c in counts.items()]})
        + (f'<div class="facts">{fact_html}</div>' if fact_html else "")
        + "</section>"
    )
    axis = _axis_html(items)
    if axis:
        chunks.append(axis)
    for section in extra:
        heading = str(section.get("heading") or "")
        if re.search(r"互动最高|按点赞|传播排序", heading):
            continue
        chunks.append(
            f'<section class="pack open"><h2>{_esc(heading or "补充数据")}</h2>'
            + _paras(section.get("body"))
            + _section_pane(items, section, job_dir, image_budget)
            + "</section>"
        )
    judgment = _judgment_view(board, report, query)
    value = judgment.get("value") if isinstance(judgment.get("value"), dict) else {}
    level = int(value.get("level") or 1)
    interrupt = "值得打断" if judgment.get("worth_interrupting") else "不必打断"
    caveats = "".join(
        f'<p class="hint">限制：{_emphasize(c)}</p>'
        for c in (judgment.get("caveats") or [])
    )
    watch = judgment.get("next_watch") or ""
    chunks.append(
        f'<section class="judge"><div class="kicker">十一的判断与限制</div>'
        f'<h2>{_esc(judgment.get("headline_zh") or "判断")}</h2>'
        f'<p class="score">价值 {level}/5 · {_esc(value.get("label") or VALUE.get(level, ""))} · {_esc(interrupt)}</p>'
        f'<p>{_emphasize(judgment.get("one_line_judgment"))}</p>'
        + (f'<p>{_emphasize(judgment.get("plain_language"))}</p>' if judgment.get("plain_language") else "")
        + (f'<p class="hint">还可以看：{_emphasize(watch)}</p>' if watch else "")
        + caveats
        + _illus_html(judgment.get("illustrations"))
        + "</section>"
    )
    if items:
        lines = []
        for item in items:
            title = item.get("title") or "无标题"
            href = item.get("url") or ""
            name = f'<a href="{_esc(href)}">{_esc(title)}</a>' if href else _esc(title)
            author = author_label(item)
            stats = " · ".join(
                part
                for part in [
                    f"赞 {item.get('liked') or 0}",
                    f"藏 {item.get('collected') or 0}",
                    f"评 {item.get('comment_count') or 0}",
                    f"转 {item.get('share_count') or 0}",
                ]
            )
            lines.append(
                f'<article class="ref"><div class="ref-top"><span>{_esc(item.get("platform_label") or "")}</span>'
                f'{_fresh_html(item)}<em>{_esc(author)}</em></div>'
                f"<strong>{name}</strong>"
                + (f'<small>{_esc(stats)}</small>' if stats else "")
                + "</article>"
            )
        chunks.append(f'<section class="pack open"><h2>原始检索结果</h2><div class="refs">{"".join(lines)}</div></section>')
    if asks:
        ask_blocks = []
        for index, row in enumerate(asks, 1):
            ask_blocks.append(
                f'<article class="ask"><h3>{index}. {_esc(row.get("q") or "追问")}</h3>{_paras(row.get("a"))}{_illus_html(row.get("illustrations"))}</article>'
            )
        chunks.append(f'<section class="pack open"><h2>追问</h2>{"".join(ask_blocks)}</section>')
    chunks.append(
        f'<footer>由一览导出。{f"材料 {len(items)} 条。" if items else ""}社交帖只当叙事信号，不是已核实新闻。</footer>'
    )
    body = "\n".join(chunks)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{_esc(report.get("title") or "智搜报告")}</title>
<style>
  @page {{
    size: A4;
    margin: 12mm 11mm 16mm;
    @bottom-center {{
      content: "一览  ·  " counter(page);
      font: 500 9px/1 "PingFang SC", "Hiragino Sans GB", sans-serif;
      color: #5B6E7A;
    }}
  }}
  :root {{
    --c1: #1A8CFF;
    --c2: #12E0A5;
    --c3: #5B6E7A;
    --paper: #F2FAFF;
    --ink: #0F3A4A;
    --mute: #5B6E7A;
    --line: rgba(15, 58, 74, 0.12);
    --ok: #12E0A5;
    --bad: #C45B72;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; color: var(--ink); }}
  html {{
    background: linear-gradient(180deg, #d7efff 0%, #F2FAFF 46%, #d8fff2 100%);
  }}
  body {{
    font: 500 13.5px/1.65 "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Heiti SC", sans-serif;
    background: transparent;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  h1, h2, h3 {{ font-family: "Songti SC", "STSong", "Source Han Serif SC", serif; font-weight: 600; }}
  h1 {{ font-size: 30px; line-height: 1.25; margin: 6px 0 8px; }}
  h2 {{ font-size: 22px; margin: 26px 0 10px; padding-top: 6px; border-top: 1px solid var(--line); }}
  h3 {{ font-size: 18px; margin: 0 0 8px; }}
  p {{ margin: 0 0 10px; }}
  a {{ color: inherit; text-decoration: none; }}
  .brandline {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin: 0 0 10px;
    color: var(--mute);
    font-size: 11px;
    letter-spacing: .12em;
  }}
  .brandline b {{ color: var(--ink); letter-spacing: .18em; font-size: 12px; }}
  .kicker {{ color: var(--c1); font-size: 11px; letter-spacing: .16em; font-weight: 650; }}
  .report-hero {{
    padding: 18px 20px 16px;
    border-radius: 20px;
    background: linear-gradient(135deg, #e3f3ff, #e8fff6);
    border: 1px solid rgba(26,140,255,.12);
    margin-bottom: 18px;
  }}
  .report-hero h1 {{ margin: 4px 0 8px; border: 0; padding: 0; }}
  .deck, .lead, .hint, small {{ color: var(--mute); }}
  .deck {{ font-size: 15px; margin: 0; }}
  .overview p {{ font-size: 15px; line-height: 1.75; }}
  mark {{
    background: linear-gradient(180deg, transparent 62%, rgba(26,140,255,.22) 0);
    color: inherit;
    padding: 0 .04em;
    font-weight: 650;
    text-decoration: underline;
    text-decoration-color: rgba(26,140,255,.45);
    text-underline-offset: 2px;
  }}
  .picks-wrap {{
    border: 1px solid rgba(26,140,255,.16);
    border-radius: 22px;
    overflow: hidden;
    background: linear-gradient(180deg, #f3f9ff, #fff 42%);
    margin: 8px 0 22px;
  }}
  .picks-head, .picks-foot {{ padding: 16px 18px 12px; }}
  .picks-head h2 {{ border: 0; margin: 4px 0 6px; font-size: 26px; }}
  .picks-head p {{ margin: 0; color: var(--mute); font-size: 13px; }}
  .concl-answer {{ padding: 0 18px 12px; }}
  .concl-prose p {{ font-size: 15px; line-height: 1.7; }}
  .concl-flow {{ list-style: none; margin: 0; padding: 2px 18px 14px; }}
  .concl-flow li {{ position: relative; padding: 0 0 14px 22px; }}
  .concl-flow li::before {{
    content: "";
    position: absolute;
    left: 5px; top: 7px;
    width: 9px; height: 9px;
    border-radius: 50%;
    background: #6db6ff;
  }}
  .concl-flow li::after {{
    content: "";
    position: absolute;
    left: 8px; top: 18px; bottom: 0;
    width: 2px;
    background: rgba(26,140,255,.18);
  }}
  .concl-flow li:last-child {{ padding-bottom: 0; }}
  .concl-flow li:last-child::after {{ display: none; }}
  .concl-flow strong {{ display: block; }}
  .concl-flow span {{ display: block; margin-top: 3px; color: var(--mute); font-size: 12.5px; }}
  .concl-data {{ padding: 0 18px 12px; }}
  .picks {{ display: grid; gap: 8px; padding: 0 14px 8px; }}
  .pick {{
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 10px 12px;
    background: #fff;
    break-inside: avoid;
  }}
  .pick-tag {{
    display: inline-grid;
    place-items: center;
    min-width: 40px;
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    font-size: 11px;
    background: #eef3f6;
  }}
  .pick.go .pick-tag {{ background: #d8fff2; }}
  .pick.skip .pick-tag {{ background: #ffdce4; }}
  .pick .hint {{ display: block; margin-top: 4px; font-size: 12px; }}
  .picks-foot {{
    border-top: 1px dashed rgba(26,140,255,.22);
    background: #f3fffb;
    color: var(--mute);
    font-size: 12.5px;
  }}
  .briefs-wrap {{ margin: 8px 0 22px; }}
  .briefs-wrap h2 {{ border: 0; margin: 4px 0 6px; }}
  .briefs-wrap > p {{ margin: 0 0 12px; color: var(--mute); font-size: 13px; }}
  .briefs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .brief {{
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 12px 14px;
    background: #f6f8fa;
    break-inside: avoid;
  }}
  .brief strong {{ display: block; margin-bottom: 6px; }}
  .brief-body {{ margin: 0; font-size: 13px; line-height: 1.65; }}
  .brief .hint {{ display: block; margin-top: 8px; }}
  .mindmap {{
    padding: 16px;
    border-radius: 18px;
    background: linear-gradient(180deg, #eef7ff, #f3fffb);
    border: 1px solid rgba(26,140,255,.14);
  }}
  .mind-root {{
    display: inline-block;
    background: linear-gradient(90deg, var(--c1), #12c8b0);
    color: #fff;
    border-radius: 999px;
    padding: 6px 14px;
    margin-bottom: 12px;
  }}
  .mind-arms {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
  .mind-arm {{ padding-left: 12px; border-left: 2px solid rgba(26,140,255,.35); }}
  .mind-hub {{ font-weight: 650; margin-bottom: 4px; }}
  .mind-arm ul {{ margin: 0; padding: 0; list-style: none; color: var(--mute); font-size: 12.5px; }}
  .mind-arm li {{ padding: 3px 0 3px 12px; position: relative; }}
  .mind-arm li::before {{
    content: "";
    position: absolute;
    left: 0; top: 11px;
    width: 8px; height: 0;
    border-top: 2px solid rgba(18,224,165,.55);
  }}
  .deep-read {{ margin: 22px 0 8px; }}
  .deep-head {{ margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }}
  .deep-head h2 {{ border: 0; margin: 4px 0 6px; }}
  .deep-head p {{ margin: 0; color: var(--mute); }}
  .deep-cards {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; }}
  .deep-card {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px solid var(--line);
    background: #fff;
    border-radius: 999px;
    padding: 4px 10px 4px 4px;
    font-size: 12px;
  }}
  .deep-card.on {{ border-color: rgba(26,140,255,.45); background: #eef7ff; }}
  .deep-no {{
    width: 22px; height: 22px;
    border-radius: 999px;
    display: grid; place-items: center;
    background: #d8fff2;
    font-family: "Songti SC", "STSong", serif;
    font-size: 12px;
  }}
  .deep-card.on .deep-no {{ background: var(--c1); color: #fff; }}
  .deep-spread {{ margin: 0 0 16px; }}
  .deep-grid {{ display: grid; grid-template-columns: 1.08fr .92fr; gap: 14px; align-items: start; }}
  .deep-copy {{
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 16px 18px 10px;
    background: #fff;
    font-family: "Songti SC", "STSong", "Source Han Serif SC", Georgia, serif;
    font-size: 15.5px;
    line-height: 1.88;
    letter-spacing: .02em;
  }}
  .deep-copy h3 {{ font-size: 20px; letter-spacing: .04em; }}
  .deep-copy .thesis {{
    font-family: "PingFang SC", "Hiragino Sans GB", sans-serif;
    font-size: 13.5px;
    line-height: 1.65;
    letter-spacing: 0;
    padding: 0 0 0 10px;
    border-left: 3px solid #6db6ff;
    color: #0F3A4A;
  }}
  .deep-copy p {{ margin: 0 0 12px; }}
  .deep-right {{
    border: 1px solid rgba(18,224,165,.22);
    border-radius: 18px;
    padding: 12px;
    background: linear-gradient(180deg, #e8fff6, #fff 38%);
    font-size: 12.5px;
    line-height: 1.5;
  }}
  .pane-block {{ margin: 0 0 12px; }}
  .pane-label {{
    font-size: 11px;
    font-weight: 650;
    letter-spacing: .12em;
    color: var(--c1);
    margin-bottom: 6px;
  }}
  .pane-label span {{ margin-left: 8px; letter-spacing: 0; color: var(--mute); font-weight: 500; }}
  .evidence, .facts {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .fact {{
    display: block;
    border: 1px solid var(--line);
    background: #fff;
    border-radius: 12px;
    padding: 7px 9px;
    font-size: 12px;
    text-decoration: none;
    max-width: 100%;
  }}
  .fact strong {{ display: block; }}
  .who {{ display: block; margin-top: 3px; color: #8A5310; font-size: 11px; font-weight: 650; }}
  .deep-empty {{ color: var(--mute); font-size: 13px; padding: 8px 2px; }}
  .illus {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 10px 0 4px; }}
  .illus-card, .shot {{ margin: 0; width: auto; }}
  .illus-card img {{ width: 100%; height: 118px; object-fit: cover; border-radius: 10px; display: block; }}
  .shots {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .shot img {{ width: 100%; height: 88px; object-fit: cover; border-radius: 10px; display: block; background: #eef3f6; }}
  figcaption {{ font-size: 10px; color: var(--mute); margin-top: 4px; }}
  .fresh {{
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    padding: 1px 7px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 650;
    margin-left: 6px;
  }}
  .fresh i {{ font-style: normal; font-weight: 500; opacity: .78; }}
  .fresh.week {{ background: #d8fff2; color: #0A6B52; }}
  .fresh.month {{ background: #e8fff6; color: #0A6B52; }}
  .fresh.quarter {{ background: #e7f4ff; color: #1A6DB8; }}
  .fresh.half {{ background: #fff3df; color: #8A5310; }}
  .fresh.year, .fresh.old {{ background: #ffe8ee; color: #C45B72; }}
  .fresh.unknown {{ background: #eef3f6; color: var(--mute); }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }}
  th, td {{ border: 1px solid var(--line); padding: 6px 8px; text-align: left; }}
  th {{ background: #eef7ff; }}
  caption {{ text-align: left; color: var(--mute); padding-bottom: 4px; }}
  .bars {{ margin: 10px 0; }}
  .bar {{ display: grid; grid-template-columns: 88px 1fr 42px; gap: 8px; align-items: center; margin: 5px 0; font-size: 12px; }}
  .bar i {{ display: block; height: 8px; border-radius: 999px; background: linear-gradient(90deg, var(--c1), var(--c2)); }}
  .axis-wrap {{ margin: 18px 0; }}
  .axis-wrap h2 {{ border: 0; margin: 4px 0 8px; font-size: 20px; }}
  .axis-rail {{
    position: relative;
    height: 48px;
    margin: 16px 4px 6px;
    border-radius: 999px;
    background: linear-gradient(90deg, #eef7ff, #e8fff6);
    box-shadow: inset 0 0 0 1px var(--line);
  }}
  .axis-rail i {{
    position: absolute;
    top: 50%;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #1A8CFF;
    transform: translate(-50%, -50%);
  }}
  .axis-rail b {{
    position: absolute;
    right: 7px;
    top: 50%;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #1A8CFF;
    transform: translateY(-50%);
  }}
  .axis-scale {{ display: flex; justify-content: space-between; color: var(--mute); font-size: 11px; padding: 0 6px; }}
  .pack {{
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 10px 14px 12px;
    margin: 14px 0;
    background: #fff;
  }}
  .pack h2 {{ border: 0; margin: 4px 0 8px; font-size: 20px; }}
  .judge {{
    padding: 16px 18px;
    border-radius: 16px;
    background: linear-gradient(180deg, #e8fff6, #fff 46%);
    border: 1px solid rgba(18,224,165,.2);
  }}
  .judge h2 {{ border: 0; margin-top: 4px; }}
  .score {{ color: var(--mute); font-size: 12px; }}
  .refs {{ display: grid; gap: 8px; }}
  .ref {{
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 9px 11px;
    background: #fff;
    break-inside: avoid;
  }}
  .ref-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    color: var(--mute);
    font-size: 11px;
    margin-bottom: 4px;
  }}
  .ref-top em {{ font-style: normal; color: #8A5310; font-weight: 650; }}
  .ref strong {{ display: block; }}
  .ask {{ margin: 0 0 12px; }}
  footer {{ margin-top: 18px; color: var(--mute); font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
