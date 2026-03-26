import json
import os
import tempfile
import errno
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Tuple

from pytz import UnknownTimeZoneError, timezone

from config import DAILY_NEWS_CATEGORIES


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GROUP_CONFIG_PATH = os.path.join(BASE_DIR, "group_config.json")
GROUP_RUNTIME_PATH = os.path.join(BASE_DIR, "group_runtime.json")
ALLOWED_DELIVERY_MODES = {"all", "round_robin"}


def _log(log_type: str, **fields):
    payload = {
        "ts": datetime.now(dt_timezone.utc).isoformat(timespec="milliseconds"),
        "log_type": log_type,
    }
    payload.update(fields)
    print(f"[GroupConfig] {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}")


def _atomic_write_json(path: str, payload: Any):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as tmp_file:
        json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
        tmp_path = tmp_file.name

    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        if exc.errno not in {errno.EBUSY, errno.EXDEV}:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Docker bind mount 到单文件时，replace 到挂载点可能触发 EBUSY。
        # 这里回退为直接覆盖写入，保证宿主机与容器文件可同步。
        with open(path, "w", encoding="utf-8") as target_file:
            json.dump(payload, target_file, ensure_ascii=False, indent=2)
        os.unlink(tmp_path)


def ensure_group_storage_files():
    if not os.path.exists(GROUP_CONFIG_PATH):
        _atomic_write_json(GROUP_CONFIG_PATH, [])
        _log("file_initialized", path=GROUP_CONFIG_PATH, default_type="list")

    if not os.path.exists(GROUP_RUNTIME_PATH):
        _atomic_write_json(GROUP_RUNTIME_PATH, {})
        _log("file_initialized", path=GROUP_RUNTIME_PATH, default_type="dict")


def _read_json_file(path: str, default_value: Any):
    ensure_group_storage_files()
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        _atomic_write_json(path, default_value)
        return default_value
    except json.JSONDecodeError as exc:
        _log("json_decode_error", path=path, error=str(exc))
        return default_value
    except Exception as exc:
        _log("json_read_error", path=path, error=str(exc))
        return default_value


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).isoformat()


def _parse_datetime(value: Any):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed.astimezone(dt_timezone.utc)
    except Exception:
        return None


def serialize_runtime_datetime(value: datetime) -> str:
    return _serialize_datetime(value)


def parse_runtime_datetime(value: Any):
    return _parse_datetime(value)


def build_default_runtime_state(now: datetime = None) -> Dict[str, Any]:
    current_time = now or datetime.now(dt_timezone.utc)
    return {
        "last_sent_at": None,
        "next_run_at": _serialize_datetime(current_time),
        "round_robin_index": 0,
        "last_success_at": None,
        "last_window_end_at": None,
        "last_error": None,
    }


def _normalize_preferences(raw_preferences: Any) -> Tuple[List[str], List[str]]:
    if raw_preferences is None:
        return [], []
    if not isinstance(raw_preferences, list):
        return [], ["preferences must be an array"]

    normalized: List[str] = []
    seen = set()
    errors = []

    for item in raw_preferences:
        if not isinstance(item, str):
            errors.append(f"invalid preference type: {item!r}")
            continue

        category = item.strip()
        if category not in DAILY_NEWS_CATEGORIES:
            errors.append(f"invalid preference category: {category}")
            continue

        if category not in seen:
            seen.add(category)
            normalized.append(category)

    return normalized, errors


def _normalize_keyword_groups(raw_keyword_groups: Any) -> Tuple[List[List[str]], List[str]]:
    if raw_keyword_groups is None:
        return [], []
    if not isinstance(raw_keyword_groups, list):
        return [], ["keyword_groups must be an array"]
    
    normalized: List[List[str]] = []
    errors = []
    
    for group in raw_keyword_groups:
        if not isinstance(group, list):
            errors.append("each item in keyword_groups must be an array of strings")
            continue
        
        valid_group = [str(k).strip() for k in group if str(k).strip()]
        if valid_group:
            normalized.append(valid_group)
            
    return normalized, errors


def _validate_optional_hour(raw_value: Any, field_name: str, errors: List[str]):
    if raw_value is None:
        return None

    if not isinstance(raw_value, int):
        errors.append(f"{field_name} must be an integer between 0 and 23")
        return None

    if raw_value < 0 or raw_value > 23:
        errors.append(f"{field_name} must be between 0 and 23")
        return None

    return raw_value


def _validate_non_negative_int(raw_value: Any, field_name: str, errors: List[str], default_value: int = 0):
    if raw_value is None:
        return default_value

    if not isinstance(raw_value, int):
        errors.append(f"{field_name} must be a non-negative integer")
        return default_value

    if raw_value < 0:
        errors.append(f"{field_name} must be a non-negative integer")
        return default_value

    return raw_value


def _validate_group_config_item(item: Any, index: int, seen_chat_ids: set):
    if not isinstance(item, dict):
        return None, [f"config item at index {index} must be an object"]

    errors: List[str] = []
    chat_id = item.get("chat_id")
    name = item.get("name")
    enabled = item.get("enabled")
    raw_preferences = item.get("preferences")
    raw_keyword_groups = item.get("keyword_groups")
    keyword_group_mode = str(item.get("keyword_group_mode", "OR")).strip().upper()
    interval_minutes = item.get("interval_minutes")
    delivery_mode = item.get("delivery_mode")
    timezone_name = item.get("timezone")
    overlap_minutes = _validate_non_negative_int(item.get("overlap_minutes"), "overlap_minutes", errors)

    if not isinstance(chat_id, str) or not chat_id.strip():
        errors.append("chat_id is required and must be a non-empty string")
    else:
        chat_id = chat_id.strip()
        if chat_id in seen_chat_ids:
            errors.append(f"duplicate chat_id: {chat_id}")

    if not isinstance(name, str) or not name.strip():
        errors.append("name is required and must be a non-empty string")
    else:
        name = name.strip()

    if not isinstance(enabled, bool):
        errors.append("enabled is required and must be a boolean")

    preferences, preference_errors = _normalize_preferences(raw_preferences)
    errors.extend(preference_errors)

    keyword_groups, keyword_errors = _normalize_keyword_groups(raw_keyword_groups)
    errors.extend(keyword_errors)

    if not preferences and not keyword_groups:
        errors.append("at least one of preferences or keyword_groups must be provided and non-empty")

    if keyword_group_mode not in {"AND", "OR"}:
        errors.append("keyword_group_mode must be 'AND' or 'OR'")

    if not isinstance(interval_minutes, int) or interval_minutes <= 0:
        errors.append("interval_minutes must be an integer greater than 0")

    if delivery_mode not in ALLOWED_DELIVERY_MODES:
        errors.append("delivery_mode must be one of: all, round_robin")

    if not isinstance(timezone_name, str) or not timezone_name.strip():
        errors.append("timezone is required and must be a valid timezone string")
    else:
        timezone_name = timezone_name.strip()
        try:
            timezone(timezone_name)
        except UnknownTimeZoneError:
            errors.append(f"invalid timezone: {timezone_name}")

    start_hour = _validate_optional_hour(item.get("start_hour"), "start_hour", errors)
    end_hour = _validate_optional_hour(item.get("end_hour"), "end_hour", errors)

    if start_hour is not None and end_hour is not None and start_hour == end_hour:
        errors.append("start_hour and end_hour cannot be the same")

    if errors:
        return None, errors

    return {
        "chat_id": chat_id,
        "name": name,
        "enabled": enabled,
        "preferences": preferences,
        "keyword_groups": keyword_groups,
        "keyword_group_mode": keyword_group_mode,
        "interval_minutes": interval_minutes,
        "delivery_mode": delivery_mode,
        "timezone": timezone_name,
        "overlap_minutes": overlap_minutes,
        "start_hour": start_hour,
        "end_hour": end_hour,
    }, []


def load_group_configs() -> List[Dict[str, Any]]:
    raw_configs = _read_json_file(GROUP_CONFIG_PATH, [])
    if not isinstance(raw_configs, list):
        _log("config_invalid_root", path=GROUP_CONFIG_PATH, error="top-level JSON must be an array")
        return []

    valid_configs = []
    seen_chat_ids = set()

    for index, raw_item in enumerate(raw_configs):
        normalized, errors = _validate_group_config_item(raw_item, index, seen_chat_ids)
        if errors:
            _log(
                "config_invalid_item",
                path=GROUP_CONFIG_PATH,
                index=index,
                raw_item=raw_item,
                errors=errors,
            )
            continue

        seen_chat_ids.add(normalized["chat_id"])
        valid_configs.append(normalized)

    return valid_configs


def load_group_runtime() -> Dict[str, Dict[str, Any]]:
    raw_runtime = _read_json_file(GROUP_RUNTIME_PATH, {})
    if not isinstance(raw_runtime, dict):
        _log("runtime_invalid_root", path=GROUP_RUNTIME_PATH, error="top-level JSON must be an object")
        return {}
    return raw_runtime


def ensure_runtime_state(
    runtime_data: Dict[str, Dict[str, Any]],
    chat_id: str,
    now: datetime = None,
):
    current_time = now or datetime.now(dt_timezone.utc)
    default_state = build_default_runtime_state(current_time)
    raw_state = runtime_data.get(chat_id)
    changed = False

    if not isinstance(raw_state, dict):
        raw_state = {}
        changed = True

    last_sent_at = _parse_datetime(raw_state.get("last_sent_at"))
    next_run_at = _parse_datetime(raw_state.get("next_run_at"))
    last_success_at = _parse_datetime(raw_state.get("last_success_at"))
    last_window_end_at = _parse_datetime(raw_state.get("last_window_end_at"))
    round_robin_index = raw_state.get("round_robin_index")
    last_error = raw_state.get("last_error")

    if round_robin_index is None:
        round_robin_index = 0
        changed = True
    elif not isinstance(round_robin_index, int) or round_robin_index < 0:
        round_robin_index = 0
        changed = True

    if raw_state.get("last_sent_at") is not None and last_sent_at is None:
        changed = True
    if raw_state.get("next_run_at") is not None and next_run_at is None:
        changed = True
    if raw_state.get("last_success_at") is not None and last_success_at is None:
        changed = True
    if raw_state.get("last_window_end_at") is not None and last_window_end_at is None:
        changed = True

    if last_error is not None and not isinstance(last_error, str):
        last_error = str(last_error)
        changed = True

    normalized_state = {
        "last_sent_at": _serialize_datetime(last_sent_at) if last_sent_at else default_state["last_sent_at"],
        "next_run_at": _serialize_datetime(next_run_at) if next_run_at else default_state["next_run_at"],
        "round_robin_index": round_robin_index,
        "last_success_at": _serialize_datetime(last_success_at) if last_success_at else default_state["last_success_at"],
        "last_window_end_at": (
            _serialize_datetime(last_window_end_at)
            if last_window_end_at
            else default_state["last_window_end_at"]
        ),
        "last_error": last_error if last_error else default_state["last_error"],
    }

    previous_state = runtime_data.get(chat_id)
    runtime_data[chat_id] = normalized_state
    if previous_state != normalized_state:
        changed = True

    return runtime_data[chat_id], changed


def save_group_runtime(runtime_data: Dict[str, Dict[str, Any]]):
    _atomic_write_json(GROUP_RUNTIME_PATH, runtime_data)
