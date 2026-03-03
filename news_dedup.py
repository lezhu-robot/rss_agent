import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from config import (
    NEWS_DEDUP_DEBUG,
    NEWS_DEDUP_EMBEDDING_MODEL,
    NEWS_DEDUP_ENABLED,
    NEWS_DEDUP_MODE,
    NEWS_DEDUP_THRESHOLD,
)

# ============================================================================
# 新闻去重模块（可插拔）
# ----------------------------------------------------------------------------
# 设计目标：
# 1) 输入/输出保持与业务 payload 兼容（status/message/data）
# 2) 支持三种模式：off / exact_only / semantic
# 3) 失败降级（fail-open）：语义阶段异常时不阻塞主流程，原样返回输入
# 4) 输出可解释信息：meta（统计）+ trace（去重映射）
# ============================================================================


def dedupe_news_payload(
    payload: Any,
    *,
    enabled: bool = NEWS_DEDUP_ENABLED,
    mode: str = NEWS_DEDUP_MODE,
    threshold: float = NEWS_DEDUP_THRESHOLD,
    debug: bool = NEWS_DEDUP_DEBUG,
    embedding_model: str = NEWS_DEDUP_EMBEDDING_MODEL,
) -> Tuple[Any, Dict[str, Any], Dict[str, Any]]:
    """
    新闻去重统一入口。

    输入: 固定格式 payload: {"status": 200, "message": "ok", "data": [...]}
    输出: (去重后 payload, meta, trace)

    说明：
    - payload 结构校验失败时直接 fail-open。
    - exact_only 只做规则去重（URL/标题）。
    - semantic 在 exact 基础上再做向量相似度聚类（complete linkage）。
    """
    t0 = time.time()
    # meta: 面向实验与观测的统计信息
    meta: Dict[str, Any] = {
        "mode": mode,
        "threshold": threshold,
        "input_count": 0,
        "after_exact_count": 0,
        "output_count": 0,
        "dropped_count": 0,
        "dedup_rate": 0.0,
        "duration_ms": 0,
        "warnings": [],
        "fail_open": False,
    }
    # trace: 面向可解释性的明细（保留ID、删除映射、簇信息）
    trace: Dict[str, Any] = {
        "kept_ids": [],
        "dropped": [],
        "clusters": [],
    }

    # 模式关闭：直接透传输入
    if not enabled or mode == "off":
        input_count = _safe_count(payload)
        meta.update(
            {
                "input_count": input_count,
                "after_exact_count": input_count,
                "output_count": input_count,
                "dropped_count": 0,
                "dedup_rate": 0.0,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        )
        return payload, meta, trace

    # 输入不是 dict，直接降级
    if not isinstance(payload, dict):
        meta["warnings"].append("payload_not_dict")
        meta["fail_open"] = True
        meta["duration_ms"] = int((time.time() - t0) * 1000)
        return payload, meta, trace

    # 顶层结构不符合约定：status!=200 或 data 非数组
    if payload.get("status") != 200 or not isinstance(payload.get("data"), list):
        meta["warnings"].append("invalid_payload_shape")
        meta["fail_open"] = True
        meta["duration_ms"] = int((time.time() - t0) * 1000)
        return payload, meta, trace

    records: List[Dict[str, Any]] = payload["data"]
    meta["input_count"] = len(records)

    # 0/1 条无需去重，直接返回
    if len(records) <= 1:
        meta.update(
            {
                "after_exact_count": len(records),
                "output_count": len(records),
                "dropped_count": 0,
                "dedup_rate": 0.0,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        )
        trace["kept_ids"] = [_article_id(item, i) for i, item in enumerate(records)]
        return payload, meta, trace

    # Step 1: 规则去重（URL / 标题）
    exact_keep, exact_dropped = _exact_dedup(records)
    meta["after_exact_count"] = len(exact_keep)
    trace["dropped"].extend(exact_dropped)

    # exact_only 模式，或规则去重后只剩 0/1 条，直接返回
    if mode == "exact_only" or len(exact_keep) <= 1:
        deduped_payload = dict(payload)
        deduped_payload["data"] = exact_keep
        _finalize_meta(meta, len(exact_keep), t0)
        trace["kept_ids"] = [_article_id(item, i) for i, item in enumerate(exact_keep)]
        trace["clusters"] = [
            {
                "cluster_id": f"c{i+1:03d}",
                "kept_id": _article_id(item, i),
                "member_ids": [_article_id(item, i)],
            }
            for i, item in enumerate(exact_keep)
        ]
        return deduped_payload, meta, trace

    # Step 2: 语义去重
    semantic_items: List[Dict[str, Any]] = []
    semantic_texts: List[str] = []
    # 记录“语义样本索引 -> exact_keep 索引”的映射
    semantic_idx_to_exact_idx: List[int] = []

    # 仅对有语义文本的条目进行 embedding
    for exact_idx, item in enumerate(exact_keep):
        text = _semantic_text(item)
        if text:
            semantic_items.append(item)
            semantic_texts.append(text)
            semantic_idx_to_exact_idx.append(exact_idx)

    # 没有足够语义样本可比，直接返回规则去重结果
    if len(semantic_texts) <= 1:
        deduped_payload = dict(payload)
        deduped_payload["data"] = exact_keep
        _finalize_meta(meta, len(exact_keep), t0)
        trace["kept_ids"] = [_article_id(item, i) for i, item in enumerate(exact_keep)]
        trace["clusters"] = [
            {
                "cluster_id": f"c{i+1:03d}",
                "kept_id": _article_id(item, i),
                "member_ids": [_article_id(item, i)],
            }
            for i, item in enumerate(exact_keep)
        ]
        return deduped_payload, meta, trace

    try:
        # 2.1 批量向量化
        embeddings = _get_embeddings(semantic_texts, embedding_model)
        # 2.2 精确余弦相似度矩阵
        sim_matrix = _pairwise_cosine_similarity(embeddings)
        # 2.3 完全链接聚类（簇内最小相似度约束）
        semantic_clusters = _complete_linkage_clusters(sim_matrix, threshold)
    except Exception as e:
        # 语义阶段失败 -> fail-open（返回原始输入）
        meta["warnings"].append(f"semantic_stage_failed:{str(e)}")
        meta["fail_open"] = True
        meta["duration_ms"] = int((time.time() - t0) * 1000)
        return payload, meta, trace

    # 将语义簇映射回 exact_keep 索引空间
    exact_clusters: List[List[int]] = []
    for cluster in semantic_clusters:
        exact_cluster = [semantic_idx_to_exact_idx[i] for i in cluster]
        exact_cluster.sort()
        exact_clusters.append(exact_cluster)

    # 对无语义文本的条目，补单元素簇，保证条目不丢失
    semantic_covered = {idx for cluster in exact_clusters for idx in cluster}
    for exact_idx in range(len(exact_keep)):
        if exact_idx not in semantic_covered:
            exact_clusters.append([exact_idx])

    # 固定簇顺序，保证输出稳定性
    exact_clusters.sort(key=lambda c: c[0])

    # 每簇代表稿选择策略（当前实现：保留最早索引，保证结果稳定）
    final_keep_indices: List[int] = []
    # 反向映射：exact 索引 -> semantic 索引
    semantic_local_by_exact = {
        exact_idx: sem_idx for sem_idx, exact_idx in enumerate(semantic_idx_to_exact_idx)
    }

    for cluster_id, cluster in enumerate(exact_clusters, 1):
        rep_exact_idx = min(cluster)
        final_keep_indices.append(rep_exact_idx)
        rep_item = exact_keep[rep_exact_idx]
        rep_id = _article_id(rep_item, rep_exact_idx)
        member_ids = [_article_id(exact_keep[i], i) for i in cluster]

        trace["clusters"].append(
            {
                "cluster_id": f"c{cluster_id:03d}",
                "kept_id": rep_id,
                "member_ids": member_ids,
            }
        )

        # 为被删条目写 trace，便于后续人工复核
        rep_sem_idx = semantic_local_by_exact.get(rep_exact_idx)
        for exact_idx in cluster:
            if exact_idx == rep_exact_idx:
                continue
            item = exact_keep[exact_idx]
            item_id = _article_id(item, exact_idx)
            drop_entry: Dict[str, Any] = {
                "id": item_id,
                "kept_id": rep_id,
                "reason": "semantic_complete_linkage",
            }

            member_sem_idx = semantic_local_by_exact.get(exact_idx)
            if rep_sem_idx is not None and member_sem_idx is not None:
                drop_entry["similarity"] = round(
                    sim_matrix[rep_sem_idx][member_sem_idx], 4
                )

            trace["dropped"].append(drop_entry)

    final_keep_indices.sort()
    deduped_data = [exact_keep[i] for i in final_keep_indices]

    deduped_payload = dict(payload)
    deduped_payload["data"] = deduped_data

    trace["kept_ids"] = [_article_id(item, i) for i, item in enumerate(deduped_data)]
    _finalize_meta(meta, len(deduped_data), t0)

    if debug:
        meta["debug"] = {
            "clusters_count": len(exact_clusters),
            "semantic_items": len(semantic_texts),
        }

    return deduped_payload, meta, trace


def _safe_count(payload: Any) -> int:
    """安全读取 payload 中 data 列表长度。"""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return len(payload["data"])
    return 0


def _article_id(item: Dict[str, Any], fallback_idx: int) -> Any:
    """获取文章唯一标识；若缺失 id，则回退为 idx_xxx。"""
    value = item.get("id")
    return value if value is not None else f"idx_{fallback_idx}"


def _normalize_title(text: Any) -> str:
    """标题标准化：去首尾空白、转小写、合并多空格。"""
    if text is None:
        return ""
    s = str(text).strip().lower()
    return " ".join(s.split())


def _normalize_url(text: Any) -> str:
    """URL 标准化：当前仅做字符串化+strip（最小改动策略）。"""
    if text is None:
        return ""
    return str(text).strip()


def _semantic_text(item: Dict[str, Any]) -> str:
    """
    构造语义文本：
    - 优先 title + summary
    - 缺一个就用另一个
    - 两者都空则返回空字符串
    """
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if title and summary:
        return f"{title}\n{summary}"
    return title or summary


def _exact_dedup(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    规则去重（轻量）：
    1) sourceURL 完全一致 -> 重复
    2) 标题标准化后一致 -> 重复
    返回：
    - keep: 保留条目
    - dropped: 被删除映射（含原因）
    """
    keep: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    url_to_kept_id: Dict[str, Any] = {}
    title_to_kept_id: Dict[str, Any] = {}

    for i, item in enumerate(records):
        article_id = _article_id(item, i)
        url_key = _normalize_url(item.get("sourceURL"))
        title_key = _normalize_title(item.get("title"))

        if url_key and url_key in url_to_kept_id:
            dropped.append(
                {
                    "id": article_id,
                    "kept_id": url_to_kept_id[url_key],
                    "reason": "exact_url",
                }
            )
            continue

        if title_key and title_key in title_to_kept_id:
            dropped.append(
                {
                    "id": article_id,
                    "kept_id": title_to_kept_id[title_key],
                    "reason": "exact_title",
                }
            )
            continue

        keep.append(item)
        kept_id = article_id
        if url_key:
            url_to_kept_id[url_key] = kept_id
        if title_key:
            title_to_kept_id[title_key] = kept_id

    return keep, dropped


def _get_embeddings(texts: List[str], model: str) -> List[List[float]]:
    """
    批量调用 OpenRouter Embedding API 并按 index 排序返回向量列表。
    调用方式与 OpenAI SDK 兼容：
    - client = OpenAI(base_url=\"https://openrouter.ai/api/v1\", api_key=...)
    - client.embeddings.create(model=..., input=[...], encoding_format=\"float\")
    """
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(f"openai_sdk_missing:{str(e)}")

    # 优先读取 OPENROUTER_API_KEY；兼容已有 OPENAI_API_KEY 配置
    api_key = (
        os.getenv("OPENROUTER_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    api_base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY/OPENAI_API_KEY is missing")

    client = OpenAI(base_url=api_base, api_key=api_key)

    extra_headers: Dict[str, str] = {}
    site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
    site_name = os.getenv("OPENROUTER_SITE_NAME", "").strip()
    if site_url:
        extra_headers["HTTP-Referer"] = site_url
    if site_name:
        extra_headers["X-OpenRouter-Title"] = site_name

    req_kwargs: Dict[str, Any] = {
        "model": model,
        "input": texts,
        "encoding_format": "float",
    }
    if extra_headers:
        req_kwargs["extra_headers"] = extra_headers

    resp = client.embeddings.create(**req_kwargs)
    data = getattr(resp, "data", None)
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("invalid_embedding_response")

    def _idx(row: Any) -> int:
        if hasattr(row, "index"):
            return int(getattr(row, "index"))
        if isinstance(row, dict):
            return int(row.get("index", 0))
        return 0

    data_sorted = sorted(data, key=_idx)
    vectors: List[List[float]] = []
    for row in data_sorted:
        emb = getattr(row, "embedding", None)
        if emb is None and isinstance(row, dict):
            emb = row.get("embedding")
        if not isinstance(emb, list):
            raise RuntimeError("embedding_not_list")
        vectors.append(emb)
    return vectors


def _pairwise_cosine_similarity(vectors: List[List[float]]) -> List[List[float]]:
    """
    计算精确余弦相似度矩阵：
    - 先做 L2 归一化
    - 再做点积得到 cosine
    """
    normed: List[List[float]] = []
    for vec in vectors:
        norm = math.sqrt(sum(float(x) * float(x) for x in vec))
        if norm == 0:
            normed.append([0.0 for _ in vec])
        else:
            normed.append([float(x) / norm for x in vec])

    n = len(normed)
    sim = [[1.0 for _ in range(n)] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dot = 0.0
            vi = normed[i]
            vj = normed[j]
            for k in range(len(vi)):
                dot += vi[k] * vj[k]
            sim[i][j] = dot
            sim[j][i] = dot
    return sim


def _complete_linkage_clusters(
    sim_matrix: List[List[float]], threshold: float
) -> List[List[int]]:
    """
    完全链接层次聚类（相似度版本）：
    - 每个样本初始为单簇
    - 每轮选择簇间相似度最高的一对尝试合并
    - 簇间相似度定义为“跨簇最小相似度”
    - 仅当 best_sim >= threshold 时才合并

    作用：抑制链式误合并（A~B, B~C, 但 A!~C）。
    """
    n = len(sim_matrix)
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    members: Dict[int, List[int]] = {i: [i] for i in range(n)}
    active = set(range(n))
    pair_sim: Dict[Tuple[int, int], float] = {}

    for i in range(n):
        for j in range(i + 1, n):
            pair_sim[(i, j)] = sim_matrix[i][j]

    next_cluster_id = n

    while True:
        best_pair: Optional[Tuple[int, int]] = None
        best_sim = -1.0

        for (a, b), score in pair_sim.items():
            if a in active and b in active and score > best_sim:
                best_sim = score
                best_pair = (a, b)

        if best_pair is None or best_sim < threshold:
            break

        a, b = best_pair
        new_id = next_cluster_id
        next_cluster_id += 1

        members[new_id] = members[a] + members[b]

        active.remove(a)
        active.remove(b)
        active.add(new_id)

        others = [c for c in active if c != new_id]
        for c in others:
            sim_ac = _get_pair_score(pair_sim, a, c)
            sim_bc = _get_pair_score(pair_sim, b, c)
            pair_sim[_pair_key(new_id, c)] = min(sim_ac, sim_bc)

        to_delete = [k for k in pair_sim if a in k or b in k]
        for k in to_delete:
            pair_sim.pop(k, None)

    clusters = [sorted(members[cid]) for cid in active]
    clusters.sort(key=lambda x: x[0])
    return clusters


def _pair_key(a: int, b: int) -> Tuple[int, int]:
    """无序 pair 的规范键，保证 (a,b) 与 (b,a) 统一。"""
    return (a, b) if a < b else (b, a)


def _get_pair_score(pair_sim: Dict[Tuple[int, int], float], a: int, b: int) -> float:
    """安全读取 pair 相似度；同一索引返回 1.0。"""
    if a == b:
        return 1.0
    return pair_sim[_pair_key(a, b)]


def _finalize_meta(meta: Dict[str, Any], output_count: int, t0: float) -> None:
    """统一收敛 meta 中的输出统计字段。"""
    input_count = int(meta.get("input_count", 0))
    dropped_count = max(input_count - output_count, 0)
    dedup_rate = (dropped_count / input_count) if input_count > 0 else 0.0

    meta["output_count"] = output_count
    meta["dropped_count"] = dropped_count
    meta["dedup_rate"] = round(dedup_rate, 4)
    meta["duration_ms"] = int((time.time() - t0) * 1000)
