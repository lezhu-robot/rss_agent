import json
import re
from datetime import datetime
from typing import Dict, List

from pytz import timezone


def _format_window_label(window_start: datetime, window_end: datetime, timezone_name: str) -> str:
    tz = timezone(timezone_name)
    start_local = window_start.astimezone(tz)
    end_local = window_end.astimezone(tz)

    if start_local.date() == end_local.date():
        window_label = (
            f"{start_local.strftime('%m-%d %H:%M')} - {end_local.strftime('%H:%M')}"
        )
    else:
        window_label = (
            f"{start_local.strftime('%m-%d %H:%M')} - {end_local.strftime('%m-%d %H:%M')}"
        )

    return f"最新新闻（北京时间 {window_label}）"


def _escape_lark_md_text(text: str) -> str:
    escaped = str(text or "")
    escaped = escaped.replace("\\", "\\\\")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    escaped = escaped.replace("(", "\\(").replace(")", "\\)")
    return escaped


def _compact_summary(summary: str, title: str, max_length: int = 96) -> str:
    compact = " ".join(str(summary or "").split())
    compact = re.sub(r"^(?:据悉|报道称|消息称|据[^，。；:：]{1,24}(?:报道|消息|称))[：:，, ]*", "", compact)
    compact = re.sub(r"^[【\[][^】\]]+[】\]]\s*", "", compact)

    title_text = " ".join(str(title or "").split())
    if title_text and compact.startswith(title_text):
        compact = compact[len(title_text):].lstrip("：:，,;；。 ")

    compact = compact.strip(" \t\r\n-—|")
    language_probe = compact or title_text
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", language_probe))
    english_chars = len(re.findall(r"[A-Za-z]", language_probe))
    effective_max_length = max_length * 2 if english_chars > chinese_chars else max_length

    if len(compact) > effective_max_length:
        candidate = compact[:effective_max_length]
        cut_positions = [
            candidate.rfind("。"),
            candidate.rfind("；"),
            candidate.rfind("，"),
            candidate.rfind("、"),
            candidate.rfind(" "),
        ]
        natural_cut = max(cut_positions)
        if natural_cut >= effective_max_length // 2:
            compact = candidate[:natural_cut]
        else:
            compact = candidate[: effective_max_length - 3].rstrip("，,；;：:、 ")
        compact = compact.rstrip("，,；;：:、 ") + "..."
    return compact


def _parse_local_datetime(published_at: str, timezone_name: str):
    try:
        parsed = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        return parsed.astimezone(timezone(timezone_name))
    except Exception:
        return None


def _format_article_time_label(article: dict, window_end: datetime, timezone_name: str) -> str:
    published_at = str(article.get("publishedAt") or "").strip()
    local_dt = _parse_local_datetime(published_at, timezone_name)
    if not local_dt:
        return published_at

    end_local = window_end.astimezone(timezone(timezone_name))
    if local_dt.date() == end_local.date():
        return local_dt.strftime("%H:%M")
    return local_dt.strftime("%m-%d %H:%M")


def _article_sort_key(article: dict, timezone_name: str):
    local_dt = _parse_local_datetime(str(article.get("publishedAt") or "").strip(), timezone_name)
    if local_dt:
        return (1, local_dt.timestamp())
    return (0, 0)


def _format_article_markdown(article: dict, window_end: datetime, timezone_name: str) -> str:
    title = _escape_lark_md_text(article.get("title") or "")
    source_url = str(article.get("sourceURL") or "").strip()
    time_label = _escape_lark_md_text(_format_article_time_label(article, window_end, timezone_name))

    if source_url:
        title_line = f"{time_label} [{title}]({source_url})" if time_label else f"[{title}]({source_url})"
    else:
        title_line = f"{time_label} {title}".strip()

    lines = [title_line]

    summary = _compact_summary(article.get("summary") or "", article.get("title") or "")
    if summary:
        lines.append(f"摘要：{_escape_lark_md_text(summary)}")

    return "\n".join(lines)


def format_group_news_message(
    news_by_category: Dict[str, List[dict]],
    window_start: datetime,
    window_end: datetime,
    timezone_name: str,
) -> str:
    non_empty_categories = [
        category for category, articles in news_by_category.items() if articles
    ]
    card = {
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": _format_window_label(window_start, window_end, timezone_name),
            },
        },
        "elements": [],
    }

    if not non_empty_categories:
        card["elements"].append(
            {
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": "当前时段暂无相关新闻。",
                },
            }
        )
        return json.dumps(card, ensure_ascii=False)

    all_articles: List[dict] = []
    for category in non_empty_categories:
        all_articles.extend(news_by_category[category])

    article_blocks = [
        _format_article_markdown(article, window_end, timezone_name)
        for article in sorted(
            all_articles,
            key=lambda article: _article_sort_key(article, timezone_name),
            reverse=True,
        )
    ]
    card["elements"].append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n\n".join(article_blocks),
            },
        }
    )

    return json.dumps(card, ensure_ascii=False)
