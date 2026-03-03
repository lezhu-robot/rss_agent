import json
import math
import os
from datetime import datetime
from itertools import combinations
from typing import Any, Dict, Iterable, List, Set, Tuple

from config import (
    DEDUP_EXPERIMENT_MODES,
    DEDUP_EXPERIMENT_THRESHOLDS,
    NEWS_DEDUP_EMBEDDING_MODEL,
    NEWS_DEDUP_THRESHOLD,
)
from news_dedup import dedupe_news_payload


RAW_PATH = "/root/rss_agent/experiments/dedup/data/raw/ai_2026-02-05_raw_payload.json"
LABEL_PATH = "/root/rss_agent/experiments/dedup/data/labels/ai_2026-02-05_event_labels_v1.json"
PRED_DIR = "/root/rss_agent/experiments/dedup/data/predictions"
OUT_DIR = "/root/rss_agent/experiments/dedup/results/local_eval"


def _pair(a: Any, b: Any) -> Tuple[Any, Any]:
    return (a, b) if a < b else (b, a)


def _pairs_from_groups(groups: Iterable[List[Any]]) -> Set[Tuple[Any, Any]]:
    s: Set[Tuple[Any, Any]] = set()
    for g in groups:
        if len(g) < 2:
            continue
        for a, b in combinations(sorted(g), 2):
            s.add(_pair(a, b))
    return s


def _true_groups_from_labels(label_obj: Dict[str, Any]) -> List[List[Any]]:
    labels = label_obj.get("labels", [])
    event_to_ids: Dict[str, List[Any]] = {}
    for row in labels:
        if row.get("uncertain"):
            continue
        eid = row.get("event_id")
        aid = row.get("id")
        if eid is None or aid is None:
            continue
        event_to_ids.setdefault(str(eid), []).append(aid)
    groups = []
    for ids in event_to_ids.values():
        groups.append(sorted(ids))
    groups.sort(key=lambda x: x[0])
    return groups


def _pred_groups_from_trace(trace: Dict[str, Any]) -> List[List[Any]]:
    groups = []
    for c in trace.get("clusters", []):
        ids = c.get("member_ids") or []
        if not isinstance(ids, list):
            continue
        ids2 = [i for i in ids if isinstance(i, int)]
        if ids2:
            groups.append(sorted(ids2))
    groups.sort(key=lambda x: x[0])
    return groups


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _calc_pairwise_metrics(true_pairs: Set[Tuple[Any, Any]], pred_pairs: Set[Tuple[Any, Any]]) -> Dict[str, Any]:
    tp = len(true_pairs & pred_pairs)
    fp = len(pred_pairs - true_pairs)
    fn = len(true_pairs - pred_pairs)

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run() -> None:
    os.makedirs(PRED_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    raw = json.load(open(RAW_PATH, "r", encoding="utf-8"))
    labels = json.load(open(LABEL_PATH, "r", encoding="utf-8"))

    title_by_id = {row.get("id"): row.get("title", "") for row in raw.get("data", [])}

    true_groups = _true_groups_from_labels(labels)
    true_pairs = _pairs_from_groups(true_groups)

    rows: List[Dict[str, Any]] = []

    for mode in DEDUP_EXPERIMENT_MODES:
        thresholds = DEDUP_EXPERIMENT_THRESHOLDS if mode == "semantic" else [NEWS_DEDUP_THRESHOLD]
        for threshold in thresholds:
            enabled = mode != "off"
            label = f"{mode}_t{threshold:.2f}" if mode == "semantic" else mode

            deduped, meta, trace = dedupe_news_payload(
                raw,
                enabled=enabled,
                mode=mode,
                threshold=threshold,
                debug=True,
                embedding_model=NEWS_DEDUP_EMBEDDING_MODEL,
            )

            pred_groups = _pred_groups_from_trace(trace)
            pred_pairs = _pairs_from_groups(pred_groups)
            pair_metrics = _calc_pairwise_metrics(true_pairs, pred_pairs)

            fp_pairs = sorted(list(pred_pairs - true_pairs))
            fn_pairs = sorted(list(true_pairs - pred_pairs))

            row = {
                "mode": mode,
                "threshold": threshold,
                "meta": meta,
                "pair_metrics": pair_metrics,
                "predicted_pair_count": len(pred_pairs),
                "true_pair_count": len(true_pairs),
                "sample_fp": [
                    {
                        "a": a,
                        "b": b,
                        "title_a": title_by_id.get(a, ""),
                        "title_b": title_by_id.get(b, ""),
                    }
                    for a, b in fp_pairs[:10]
                ],
                "sample_fn": [
                    {
                        "a": a,
                        "b": b,
                        "title_a": title_by_id.get(a, ""),
                        "title_b": title_by_id.get(b, ""),
                    }
                    for a, b in fn_pairs[:10]
                ],
            }

            rows.append(row)

            out_subdir = os.path.join(PRED_DIR, f"ai_2026-02-05_{label}")
            os.makedirs(out_subdir, exist_ok=True)
            _save_json(os.path.join(out_subdir, "deduped_payload.json"), deduped)
            _save_json(os.path.join(out_subdir, "meta.json"), meta)
            _save_json(os.path.join(out_subdir, "trace.json"), trace)

    out = {
        "generated_at": datetime.now().isoformat(),
        "dataset": os.path.basename(RAW_PATH),
        "label_file": os.path.basename(LABEL_PATH),
        "label_summary": labels.get("metadata", {}),
        "results": rows,
    }

    out_path = os.path.join(OUT_DIR, "ai_2026-02-05_metrics_v1.json")
    _save_json(out_path, out)

    print(f"✅ Local evaluation done: {out_path}")
    print("\n=== Pairwise Metrics ===")
    for r in rows:
        pm = r["pair_metrics"]
        m = r["meta"]
        print(
            f"{r['mode']:<10} t={r['threshold']:.2f} | "
            f"P={pm['precision']:<6} R={pm['recall']:<6} F1={pm['f1']:<6} | "
            f"in={m.get('input_count')} out={m.get('output_count')} drop={m.get('dropped_count')}"
        )


if __name__ == "__main__":
    run()
