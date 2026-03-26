import json
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional, Tuple

from pytz import timezone

from group_config_loader import (
    ensure_group_storage_files,
    ensure_runtime_state,
    load_group_configs,
    load_group_runtime,
    parse_runtime_datetime,
    save_group_runtime,
    serialize_runtime_datetime,
)
from group_message_formatter import format_group_news_message
from group_news_client import GroupNewsClientError, fetch_group_news
from messaging import send_message


group_delivery_poll_lock = threading.Lock()


def _log(**fields):
    payload = {"ts": datetime.now(dt_timezone.utc).isoformat(timespec="milliseconds")}
    payload.update(fields)
    print(f"[GroupPush] {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}")


def _advance_next_run(due_at: datetime, interval_minutes: int, reference_time: datetime) -> datetime:
    next_run_at = due_at
    step = timedelta(minutes=interval_minutes)
    while next_run_at <= reference_time:
        next_run_at += step
    return next_run_at


def _is_within_group_delivery_window(group_config: dict, current_local_time: datetime) -> bool:
    start_hour = group_config.get("start_hour")
    end_hour = group_config.get("end_hour")
    current_hour = current_local_time.hour

    if start_hour is None and end_hour is None:
        return True
    if start_hour is None:
        return current_hour < end_hour
    if end_hour is None:
        return current_hour >= start_hour
    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    return current_hour >= start_hour or current_hour < end_hour


def _compute_window_start(
    runtime_state: dict,
    interval_minutes: int,
    overlap_minutes: int,
    now_utc: datetime,
) -> datetime:
    overlap_delta = timedelta(minutes=max(overlap_minutes, 0))
    last_window_end_at = parse_runtime_datetime(runtime_state.get("last_window_end_at"))
    if last_window_end_at:
        return min(last_window_end_at - overlap_delta, now_utc)

    last_success_at = parse_runtime_datetime(runtime_state.get("last_success_at"))
    if last_success_at:
        return min(last_success_at - overlap_delta, now_utc)

    return now_utc - timedelta(minutes=interval_minutes)


def _compute_expected_next_run(runtime_state: dict, interval_minutes: int):
    last_window_end_at = parse_runtime_datetime(runtime_state.get("last_window_end_at"))
    if last_window_end_at:
        return last_window_end_at + timedelta(minutes=interval_minutes)

    last_success_at = parse_runtime_datetime(runtime_state.get("last_success_at"))
    if last_success_at:
        return last_success_at + timedelta(minutes=interval_minutes)

    last_sent_at = parse_runtime_datetime(runtime_state.get("last_sent_at"))
    if last_sent_at:
        return last_sent_at + timedelta(minutes=interval_minutes)

    return None


def _has_delivery_window(group_config: dict) -> bool:
    return group_config.get("start_hour") is not None or group_config.get("end_hour") is not None


def _article_dedupe_key(article: dict) -> str:
    source_url = (article.get("sourceURL") or "").strip()
    if source_url:
        return f"url:{source_url}"

    article_id = article.get("id")
    if article_id is not None:
        return f"id:{article_id}"

    title = (article.get("title") or "").strip().lower()
    published_at = (article.get("publishedAt") or "").strip()
    return f"title:{title}|published:{published_at}"


def _deduplicate_articles(news_by_category: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    seen = set()
    deduplicated: Dict[str, List[dict]] = {}

    for category, articles in news_by_category.items():
        deduplicated[category] = []
        for article in articles:
            dedupe_key = _article_dedupe_key(article)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduplicated[category].append(article)

    return deduplicated


def _collect_group_news(
    selected_categories: List[str],
    keyword_groups: List[List[str]],
    keyword_group_mode: str,
    window_start: datetime,
    window_end: datetime,
) -> Tuple[Dict[str, List[dict]], List[str]]:
    news_by_category: Dict[str, List[dict]] = {}
    errors: List[str] = []

    if not selected_categories and keyword_groups:
        try:
            news_by_category["ALL"] = fetch_group_news(
                category=None,
                start_dt=window_start,
                end_dt=window_end,
                keyword_groups=keyword_groups,
                keyword_group_mode=keyword_group_mode,
            )
        except GroupNewsClientError as exc:
            errors.append(f"ALL: {exc}")
        except Exception as exc:
            errors.append(f"ALL: unexpected_error={exc}")
        return news_by_category, errors

    for category in selected_categories:
        try:
            news_by_category[category] = fetch_group_news(
                category=category,
                start_dt=window_start,
                end_dt=window_end,
                keyword_groups=keyword_groups,
                keyword_group_mode=keyword_group_mode,
            )
        except GroupNewsClientError as exc:
            errors.append(f"{category}: {exc}")
        except Exception as exc:
            errors.append(f"{category}: unexpected_error={exc}")

    return news_by_category, errors


def _run_group_delivery_once(
    force_run: bool = False,
    target_chat_ids: Optional[List[str]] = None,
):
    runtime_dirty = False
    now_utc = datetime.now(dt_timezone.utc)
    runtime_data = {}
    target_chat_id_set = set(target_chat_ids or [])

    try:
        ensure_group_storage_files()
        group_configs = load_group_configs()
        runtime_data = load_group_runtime()

        for group_config in group_configs:
            chat_id = group_config["chat_id"]
            if target_chat_id_set and chat_id not in target_chat_id_set:
                continue

            group_name = group_config["name"]
            runtime_state, state_changed = ensure_runtime_state(runtime_data, chat_id, now_utc)
            runtime_dirty = runtime_dirty or state_changed

            next_run_at = parse_runtime_datetime(runtime_state.get("next_run_at")) or now_utc
            expected_next_run_at = _compute_expected_next_run(
                runtime_state=runtime_state,
                interval_minutes=group_config["interval_minutes"],
            )
            if (
                expected_next_run_at
                and not _has_delivery_window(group_config)
                and next_run_at > expected_next_run_at
            ):
                next_run_at = expected_next_run_at
                runtime_state["next_run_at"] = serialize_runtime_datetime(next_run_at)
                runtime_dirty = True
                _log(
                    send_result="next_run_realigned",
                    chat_id=chat_id,
                    group_name=group_name,
                    interval_minutes=group_config["interval_minutes"],
                    expected_next_run_at=serialize_runtime_datetime(expected_next_run_at),
                    error=None,
                )

            serialized_next_run_at = serialize_runtime_datetime(next_run_at)
            if runtime_state.get("next_run_at") != serialized_next_run_at:
                runtime_state["next_run_at"] = serialized_next_run_at
                runtime_dirty = True

            selected_categories = list(group_config["preferences"])
            keyword_groups = group_config.get("keyword_groups", [])
            keyword_group_mode = group_config.get("keyword_group_mode", "OR")
            window_start = _compute_window_start(
                runtime_state=runtime_state,
                interval_minutes=group_config["interval_minutes"],
                overlap_minutes=group_config["overlap_minutes"],
                now_utc=now_utc,
            )
            window_end = now_utc
            base_log_fields = {
                "chat_id": chat_id,
                "group_name": group_name,
                "preferences": group_config["preferences"],
                "keyword_groups": keyword_groups,
                "keyword_group_mode": keyword_group_mode,
                "delivery_mode": group_config.get("delivery_mode"),
                "delivery_mode_effective": "merged_all_preferences",
                "interval_minutes": group_config["interval_minutes"],
                "overlap_minutes": group_config["overlap_minutes"],
                "selected_categories": selected_categories,
                "window_start": serialize_runtime_datetime(window_start),
                "window_end": serialize_runtime_datetime(window_end),
                "force_run": force_run,
            }

            if not group_config["enabled"]:
                _log(
                    send_result="skipped_disabled",
                    next_run_at=runtime_state.get("next_run_at"),
                    error=None,
                    **base_log_fields,
                )
                continue

            if not force_run and next_run_at > now_utc:
                continue

            current_local_time = now_utc.astimezone(timezone(group_config["timezone"]))
            if not force_run and not _is_within_group_delivery_window(group_config, current_local_time):
                next_due = _advance_next_run(next_run_at, group_config["interval_minutes"], now_utc)
                runtime_state["next_run_at"] = serialize_runtime_datetime(next_due)
                runtime_state["last_error"] = None
                runtime_dirty = True
                _log(
                    send_result="skipped_outside_window",
                    next_run_at=runtime_state["next_run_at"],
                    error=None,
                    **base_log_fields,
                )
                continue

            news_by_category, category_errors = _collect_group_news(
                selected_categories=selected_categories,
                keyword_groups=keyword_groups,
                keyword_group_mode=keyword_group_mode,
                window_start=window_start,
                window_end=window_end,
            )
            next_due = window_end + timedelta(minutes=group_config["interval_minutes"])
            runtime_state["next_run_at"] = serialize_runtime_datetime(next_due)
            runtime_dirty = True

            if category_errors:
                runtime_state["last_error"] = " | ".join(category_errors)
                _log(
                    send_result="fetch_failed",
                    next_run_at=runtime_state["next_run_at"],
                    error=runtime_state["last_error"],
                    **base_log_fields,
                )
                continue

            deduplicated_news = _deduplicate_articles(news_by_category)
            has_news = any(articles for articles in deduplicated_news.values())
            if not has_news:
                runtime_state["last_window_end_at"] = serialize_runtime_datetime(window_end)
                runtime_state["last_error"] = None
                runtime_dirty = True
                _log(
                    send_result="skipped_no_news",
                    content_type="no_news",
                    next_run_at=runtime_state["next_run_at"],
                    error=None,
                    **base_log_fields,
                )
                continue

            message_text = format_group_news_message(
                news_by_category=deduplicated_news,
                window_start=window_start,
                window_end=window_end,
                timezone_name=group_config["timezone"],
            )

            sent = send_message(chat_id, message_text, receive_id_type="chat_id")
            if not sent:
                runtime_state["last_error"] = "send_message returned False"
                _log(
                    send_result="send_failed",
                    content_type="news" if has_news else "no_news",
                    next_run_at=runtime_state["next_run_at"],
                    error=runtime_state["last_error"],
                    **base_log_fields,
                )
                continue

            runtime_state["last_sent_at"] = serialize_runtime_datetime(now_utc)
            runtime_state["last_success_at"] = serialize_runtime_datetime(window_end)
            runtime_state["last_window_end_at"] = serialize_runtime_datetime(window_end)
            runtime_state["last_error"] = None
            runtime_dirty = True
            _log(
                send_result="success",
                content_type="news",
                next_run_at=runtime_state["next_run_at"],
                error=None,
                **base_log_fields,
            )
    except Exception as exc:
        _log(send_result="poll_failed", error=str(exc))
    finally:
        if runtime_dirty:
            try:
                save_group_runtime(runtime_data)
            except Exception as exc:
                _log(send_result="runtime_save_failed", error=str(exc))


def poll_group_delivery_task():
    if not group_delivery_poll_lock.acquire(blocking=False):
        _log(send_result="poll_skipped_locked", error="poll already running")
        return

    try:
        _run_group_delivery_once(force_run=False, target_chat_ids=None)
    finally:
        group_delivery_poll_lock.release()


def force_push_groups_once(target_chat_ids: Optional[List[str]] = None):
    if not group_delivery_poll_lock.acquire(blocking=False):
        raise RuntimeError("group delivery is already running")

    try:
        _run_group_delivery_once(force_run=True, target_chat_ids=target_chat_ids)
    finally:
        group_delivery_poll_lock.release()
