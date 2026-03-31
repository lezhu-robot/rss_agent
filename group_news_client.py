import json
import re
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Optional

from tools import format_news_api_datetime, post_news_search


class GroupNewsClientError(Exception):
    pass


def _log(log_type: str, **fields):
    payload = {
        "ts": datetime.now(dt_timezone.utc).isoformat(timespec="milliseconds"),
        "log_type": log_type,
    }
    payload.update(fields)
    print(f"[GroupNewsClient] {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}")


def _normalize_article(raw_article: Any, category: Optional[str]) -> Dict[str, Any]:
    if not isinstance(raw_article, dict):
        raise ValueError(f"article must be an object, got {type(raw_article).__name__}")

    import re
    title = str(raw_article.get("title") or "").strip()
    # Strip markdown links e.g. [text](url) -> text
    title = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', title)
    title = re.sub(r'\s+', ' ', title).strip()
    source_url = str(raw_article.get("sourceURL") or "").strip()
    published_at = str(raw_article.get("publishedAt") or "").strip()
    summary = str(raw_article.get("summary") or "").strip()
    source_name = str(raw_article.get("sourceName") or "").strip()
    article_category = str(raw_article.get("category") or category or "UNCATEGORIZED").strip() or "UNCATEGORIZED"

    if not title:
        raise ValueError("article title is empty")

    normalized = {
        "id": raw_article.get("id"),
        "title": title,
        "sourceURL": source_url,
        "sourceName": source_name,
        "publishedAt": published_at,
        "scrapedAt": str(raw_article.get("scrapedAt") or "").strip(),
        "summary": summary,
        "tags": raw_article.get("tags"),
        "thumbnailURL": str(
            raw_article.get("thumbnailURL")
            or raw_article.get("tumbnailURL")
            or ""
        ).strip(),
        "category": article_category,
        "rawContent": raw_article.get("rawContent"),
    }
    return normalized


def fetch_group_news(
    category: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    keyword_groups: Optional[List[List[str]]] = None,
    keyword_group_mode: str = "OR",
) -> List[Dict[str, Any]]:
    payload = {
        "startDateTime": format_news_api_datetime(start_dt),
        "endDateTime": format_news_api_datetime(end_dt),
        "sortOrder": "newest",
        "includeContent": False,
    }
    if category:
        payload["category"] = category
    if keyword_groups:
        payload["keywordGroups"] = keyword_groups
        payload["groupMode"] = keyword_group_mode

    _log(
        "request",
        category=category,
        keyword_groups=keyword_groups,
        startDateTime=payload["startDateTime"],
        endDateTime=payload["endDateTime"],
        sortOrder=payload["sortOrder"],
        includeContent=payload["includeContent"],
    )

    try:
        response = post_news_search(payload, timeout=15)
    except Exception as exc:
        raise GroupNewsClientError(f"request_failed: {exc}") from exc

    if response.status_code != 200:
        raise GroupNewsClientError(
            f"http_status={response.status_code}, body={response.text[:500]}"
        )

    try:
        response_payload = response.json()
    except Exception as exc:
        raise GroupNewsClientError(f"invalid_json: {exc}") from exc

    if not isinstance(response_payload, dict):
        raise GroupNewsClientError("response root must be an object")

    response_status = response_payload.get("status")
    if response_status not in (None, 200):
        raise GroupNewsClientError(
            f"unexpected_status={response_status}, message={response_payload.get('message')}"
        )

    raw_articles = response_payload.get("data")
    if not isinstance(raw_articles, list):
        raise GroupNewsClientError("response data must be a list")

    normalized_articles: List[Dict[str, Any]] = []
    skipped_count = 0
    for raw_article in raw_articles:
        try:
            normalized_articles.append(_normalize_article(raw_article, category))
        except Exception as exc:
            skipped_count += 1
            _log(
                "article_skipped",
                category=category,
                error=str(exc),
                raw_article=raw_article,
            )

    if keyword_groups and normalized_articles:
        filtered_articles = []
        for article in normalized_articles:
            text_to_search = (article["title"] + " " + article["summary"] + " " + article["sourceName"]).lower()
            group_matches = []
            for group in keyword_groups:
                match = False
                for kw in group:
                    # For pure alphabetic keywords, use word boundaries to avoid substring matches
                    # e.g. prevent "ig" from matching "significant"
                    if kw.replace(" ", "").isalpha():
                        pattern = r'\b' + re.escape(kw) + r'\b'
                        if re.search(pattern, text_to_search, flags=re.IGNORECASE):
                            match = True
                            break
                    else:
                        if kw.lower() in text_to_search:
                            match = True
                            break
                group_matches.append(match)
            
            if keyword_group_mode.upper() == "AND":
                if all(group_matches):
                    filtered_articles.append(article)
            else:
                if any(group_matches):
                    filtered_articles.append(article)
        normalized_articles = filtered_articles

    _log(
        "response",
        category=category,
        item_count=len(normalized_articles),
        skipped_count=skipped_count,
    )
    return normalized_articles
