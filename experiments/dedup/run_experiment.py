import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from config import (
    DEDUP_EXPERIMENT_CATEGORIES,
    DEDUP_EXPERIMENT_DATE,
    DEDUP_EXPERIMENT_MODES,
    DEDUP_EXPERIMENT_THRESHOLDS,
    NEWS_DEDUP_EMBEDDING_MODEL,
    NEWS_DEDUP_THRESHOLD,
)
from news_dedup import dedupe_news_payload
from tools import fetch_news


def _beijing_window_utc(date_str: str) -> Tuple[datetime, datetime]:
    # date_str example: 2026-02-05
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    beijing_tz = timezone(timedelta(hours=8))

    start_bj = dt.replace(tzinfo=beijing_tz)
    end_bj = start_bj + timedelta(days=1) - timedelta(seconds=1)

    return start_bj.astimezone(timezone.utc), end_bj.astimezone(timezone.utc)


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run() -> None:
    start_utc, end_utc = _beijing_window_utc(DEDUP_EXPERIMENT_DATE)

    now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    run_dir = os.path.join(base_dir, "results", f"run_{now_tag}")
    os.makedirs(run_dir, exist_ok=True)

    summary: List[Dict[str, Any]] = []

    print(
        f"🧪 Dedup experiment start. date={DEDUP_EXPERIMENT_DATE}, "
        f"window_utc={start_utc.isoformat()}~{end_utc.isoformat()}"
    )

    for category in DEDUP_EXPERIMENT_CATEGORIES:
        print(f"\n📥 Fetching category={category} ...")
        raw_payload = fetch_news(category, start_dt=start_utc, end_dt=end_utc)

        category_dir = os.path.join(run_dir, category)
        os.makedirs(category_dir, exist_ok=True)
        _save_json(os.path.join(category_dir, "raw_payload.json"), raw_payload)

        for mode in DEDUP_EXPERIMENT_MODES:
            thresholds = (
                DEDUP_EXPERIMENT_THRESHOLDS if mode == "semantic" else [NEWS_DEDUP_THRESHOLD]
            )

            for threshold in thresholds:
                enabled = mode != "off"
                label = f"{mode}_t{threshold:.2f}" if mode == "semantic" else mode
                print(f"  ▶ Running mode={mode}, threshold={threshold:.2f}")

                deduped, meta, trace = dedupe_news_payload(
                    raw_payload,
                    enabled=enabled,
                    mode=mode,
                    threshold=threshold,
                    debug=True,
                    embedding_model=NEWS_DEDUP_EMBEDDING_MODEL,
                )

                out_dir = os.path.join(category_dir, label)
                os.makedirs(out_dir, exist_ok=True)
                _save_json(os.path.join(out_dir, "deduped_payload.json"), deduped)
                _save_json(os.path.join(out_dir, "meta.json"), meta)
                _save_json(os.path.join(out_dir, "trace.json"), trace)

                summary.append(
                    {
                        "category": category,
                        "mode": mode,
                        "threshold": threshold,
                        "input_count": meta.get("input_count", 0),
                        "output_count": meta.get("output_count", 0),
                        "dropped_count": meta.get("dropped_count", 0),
                        "dedup_rate": meta.get("dedup_rate", 0),
                        "duration_ms": meta.get("duration_ms", 0),
                        "fail_open": meta.get("fail_open", False),
                        "warnings": meta.get("warnings", []),
                    }
                )

    _save_json(os.path.join(run_dir, "summary.json"), summary)

    print(f"\n✅ Done. Results saved to: {run_dir}")
    print("\n=== Summary ===")
    for row in summary:
        print(
            f"{row['category']:>6} | {row['mode']:<10} | t={row['threshold']:.2f} | "
            f"in={row['input_count']:<4} out={row['output_count']:<4} "
            f"drop={row['dropped_count']:<4} rate={row['dedup_rate']:<6} "
            f"ms={row['duration_ms']:<5} fail_open={row['fail_open']}"
        )


if __name__ == "__main__":
    run()
