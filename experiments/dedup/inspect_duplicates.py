import argparse
import json
import os
from typing import Any, Dict, List


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_id(item: Any) -> Any:
    return item


def _build_id_to_article(raw_payload: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
    id_map: Dict[Any, Dict[str, Any]] = {}
    data = raw_payload.get("data", []) if isinstance(raw_payload, dict) else []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            continue
        aid = row.get("id")
        if aid is None:
            aid = f"idx_{idx}"
        id_map[_normalize_id(aid)] = row
    return id_map


def _build_duplicates_groups(trace: Dict[str, Any], id_to_article: Dict[Any, Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    clusters = trace.get("clusters", []) if isinstance(trace, dict) else []

    # 快速索引 similarity（dropped 明细里才有）
    sim_map: Dict[tuple, float] = {}
    for row in trace.get("dropped", []):
        if not isinstance(row, dict):
            continue
        did = row.get("id")
        kid = row.get("kept_id")
        if did is None or kid is None:
            continue
        sim = row.get("similarity")
        if isinstance(sim, (float, int)):
            sim_map[(did, kid)] = float(sim)

    for c in clusters:
        if not isinstance(c, dict):
            continue
        member_ids = c.get("member_ids") or []
        if not isinstance(member_ids, list) or len(member_ids) <= 1:
            continue

        kept_id = c.get("kept_id")
        if kept_id is None:
            continue

        kept_article = id_to_article.get(kept_id, {})
        dropped_items = []

        for mid in member_ids:
            if mid == kept_id:
                continue
            art = id_to_article.get(mid, {})
            dropped_items.append(
                {
                    "id": mid,
                    "title": art.get("title", ""),
                    "sourceURL": art.get("sourceURL", ""),
                    "summary": art.get("summary", ""),
                    "similarity": sim_map.get((mid, kept_id)),
                }
            )

        dropped_items.sort(key=lambda x: (x.get("similarity") is None, -(x.get("similarity") or -1)))

        sims = [x["similarity"] for x in dropped_items if isinstance(x.get("similarity"), (float, int))]
        groups.append(
            {
                "cluster_id": c.get("cluster_id"),
                "kept": {
                    "id": kept_id,
                    "title": kept_article.get("title", ""),
                    "sourceURL": kept_article.get("sourceURL", ""),
                    "summary": kept_article.get("summary", ""),
                },
                "duplicates": dropped_items,
                "group_size": len(member_ids),
                "duplicate_count": len(dropped_items),
                "similarity_min": min(sims) if sims else None,
                "similarity_max": max(sims) if sims else None,
                "similarity_avg": round(sum(sims) / len(sims), 4) if sims else None,
            }
        )

    groups.sort(key=lambda x: x["duplicate_count"], reverse=True)
    return groups


def _write_markdown(
    path: str,
    groups: List[Dict[str, Any]],
    mode_label: str,
    deduped_items: List[Dict[str, Any]],
) -> None:
    lines: List[str] = []
    lines.append(f"# Duplicates Grouped ({mode_label})")
    lines.append("")
    lines.append(f"- duplicate groups: **{len(groups)}**")
    lines.append(f"- deduped item count: **{len(deduped_items)}**")
    lines.append("")

    for idx, g in enumerate(groups, 1):
        kept = g["kept"]
        lines.append(f"## {idx}. Group {g.get('cluster_id')} (duplicates={g.get('duplicate_count')})")
        lines.append("")
        lines.append(f"- kept_id: `{kept.get('id')}`")
        lines.append(f"- kept_title: {kept.get('title', '')}")
        if kept.get("sourceURL"):
            lines.append(f"- kept_url: {kept.get('sourceURL')}")
        lines.append(
            f"- similarity(min/avg/max): {g.get('similarity_min')} / {g.get('similarity_avg')} / {g.get('similarity_max')}"
        )
        lines.append("")
        lines.append("| duplicate_id | similarity | title |")
        lines.append("|---:|---:|---|")
        for d in g["duplicates"]:
            sim = d.get("similarity")
            sim_text = "" if sim is None else f"{sim:.4f}"
            title = str(d.get("title", "")).replace("|", "\\|")
            lines.append(f"| `{d.get('id')}` | {sim_text} | {title} |")
        lines.append("")

    # 完整输出去重后的结果，便于人工直接审阅最终保留列表
    lines.append("---")
    lines.append("")
    lines.append("## Full Deduped Output")
    lines.append("")
    lines.append("| # | id | title | sourceURL |")
    lines.append("|---:|---:|---|---|")
    for idx, item in enumerate(deduped_items, 1):
        aid = item.get("id", "")
        title = str(item.get("title", "")).replace("|", "\\|")
        url = str(item.get("sourceURL", "")).replace("|", "\\|")
        lines.append(f"| {idx} | `{aid}` | {title} | {url} |")
    lines.append("")

    lines.append("### Full Deduped Items (With Summary)")
    lines.append("")
    for idx, item in enumerate(deduped_items, 1):
        aid = item.get("id", "")
        title = str(item.get("title", "")).strip()
        url = str(item.get("sourceURL", "")).strip()
        summary = str(item.get("summary", "") or "").strip()
        lines.append(f"{idx}. `{aid}` {title}")
        if url:
            lines.append(f"   - url: {url}")
        if summary:
            lines.append(f"   - summary: {summary}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _process_mode_dir(mode_dir: str, id_to_article: Dict[Any, Dict[str, Any]]) -> None:
    trace_path = os.path.join(mode_dir, "trace.json")
    deduped_path = os.path.join(mode_dir, "deduped_payload.json")
    if not os.path.exists(trace_path):
        return

    trace = _load_json(trace_path)
    groups = _build_duplicates_groups(trace, id_to_article)
    deduped_payload: Dict[str, Any] = {}
    deduped_items: List[Dict[str, Any]] = []
    if os.path.exists(deduped_path):
        deduped_payload = _load_json(deduped_path)
        if isinstance(deduped_payload, dict) and isinstance(deduped_payload.get("data"), list):
            deduped_items = [x for x in deduped_payload["data"] if isinstance(x, dict)]

    grouped_json = {
        "mode_dir": mode_dir,
        "group_count": len(groups),
        "groups": groups,
        "deduped_item_count": len(deduped_items),
    }

    with open(os.path.join(mode_dir, "duplicates_grouped.json"), "w", encoding="utf-8") as f:
        json.dump(grouped_json, f, ensure_ascii=False, indent=2)

    _write_markdown(
        os.path.join(mode_dir, "duplicates_grouped.md"),
        groups,
        mode_label=os.path.basename(mode_dir),
        deduped_items=deduped_items,
    )


def run(run_dir: str, category: str) -> None:
    category_dir = os.path.join(run_dir, category)
    raw_path = os.path.join(category_dir, "raw_payload.json")
    if not os.path.exists(raw_path):
        raise FileNotFoundError(f"raw_payload not found: {raw_path}")

    raw_payload = _load_json(raw_path)
    id_to_article = _build_id_to_article(raw_payload)

    mode_dirs = []
    for name in sorted(os.listdir(category_dir)):
        path = os.path.join(category_dir, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "trace.json")):
            mode_dirs.append(path)

    for d in mode_dirs:
        _process_mode_dir(d, id_to_article)
        print(f"✅ grouped duplicates generated: {d}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Group duplicate news by kept_id from trace.json")
    parser.add_argument("--run-dir", required=True, help="run directory path, e.g. experiments/dedup/results/run_xxx")
    parser.add_argument("--category", default="AI", help="category folder name, default AI")
    args = parser.parse_args()

    run(args.run_dir, args.category)
