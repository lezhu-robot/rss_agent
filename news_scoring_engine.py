from __future__ import annotations

import json
import re
import time
import requests
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from langchain_core.prompts import ChatPromptTemplate

from news_scoring_spec_v2 import (
    BATCH_SIZE,
    MAX_WORKERS,
    ENTITY_ALIAS_MAP,
    MAJOR_ENTITY_INDEX,
    STEP_A_CLASSIFY_BATCH_PROMPT,
    STEP_A_CLASSIFY_BATCH_PROMPT_NON_AI,
    STEP_B_COMMON_SCORE_BATCH_PROMPT,
    STEP_B_COMMON_SCORE_DIRECT_BATCH_PROMPT,
    DEFAULT_WEIGHT_HINT,
    ClassificationOutput,
    BatchClassificationOutput,
    CommonScoreLLMOutput,
    BatchCommonScoreOutput,
    CommonScoreDirectLLMOutput,
    BatchCommonScoreDirectOutput,
    CommonScoreOutput,
    EntityTierValidatedItem,
    EventArticle,
    EventInput,
    EventScoringResult,
    PenaltyScoreOutput,
    build_placeholder_penalty,
    compute_heat_score,
    compute_prominence_score_from_validated_tiers,
    compute_source_volume_penalty,
    compute_source_quality_factor,
    get_category_cluster_labels,
    get_category_strategy,
    get_common_weight_key_by_category,
    get_category_theme,
)


def _safe_article_id(item: Dict[str, Any], fallback_idx: int) -> str:
    """从原始新闻项提取稳定 id；缺失时退化为基于下标的占位 id。"""
    value = item.get("id")
    if value is None:
        return f"idx_{fallback_idx}"
    return str(value)


def _extract_domain(url: Any) -> str:
    """从 URL 提取归一化域名（小写、去 www 前缀）；异常时返回空串。"""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        host = urlparse(text).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def build_events_from_dedup(
    deduped_payload: Any,
    dedup_trace: Optional[Dict[str, Any]] = None,
) -> List[EventInput]:
    """
    将 dedup 输出转换成 EventInput 列表。
    有 clusters 就按簇恢复 event_size；没有则每条视为一个事件。
    """
    if not isinstance(deduped_payload, dict):
        return []
    raw_data = deduped_payload.get("data")
    if not isinstance(raw_data, list):
        return []

    data: List[Dict[str, Any]] = [x for x in raw_data if isinstance(x, dict)]
    id_to_item = {_safe_article_id(item, i): item for i, item in enumerate(data)}
    events: List[EventInput] = []

    clusters = []
    if isinstance(dedup_trace, dict) and isinstance(dedup_trace.get("clusters"), list):
        clusters = dedup_trace["clusters"]

    if clusters:
        for i, cluster in enumerate(clusters):
            if not isinstance(cluster, dict):
                continue
            kept_id = str(cluster.get("kept_id") or "").strip()
            rep_item = id_to_item.get(kept_id)
            if rep_item is None and kept_id.startswith("idx_"):
                try:
                    idx = int(kept_id.split("_", 1)[1])
                    if 0 <= idx < len(data):
                        rep_item = data[idx]
                except Exception:
                    rep_item = None
            if rep_item is None:
                continue

            member_ids = cluster.get("member_ids")
            if not isinstance(member_ids, list) or not member_ids:
                member_ids = [kept_id or _safe_article_id(rep_item, i)]

            event_id = str(cluster.get("cluster_id") or kept_id or f"event_{i+1}")
            try:
                events.append(
                    EventInput(
                        event_id=event_id,
                        event_size=max(1, len(member_ids)),
                        articles=[EventArticle(**rep_item)],
                    )
                )
            except Exception:
                continue
    else:
        for i, item in enumerate(data):
            event_id = f"event_{_safe_article_id(item, i)}"
            try:
                events.append(
                    EventInput(
                        event_id=event_id,
                        event_size=1,
                        articles=[EventArticle(**item)],
                    )
                )
            except Exception:
                continue

    return events


def _chunk_list(items: List[Any], size: int) -> List[List[Any]]:
    """按固定批大小切分列表，供并发批处理调用。"""
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _event_to_llm_payload(event: EventInput) -> Dict[str, Any]:
    """将事件转换为传给 LLM 的最小输入结构。"""
    rep = event.articles[0]
    return {
        "event_id": event.event_id,
        "title": rep.title or "",
        # "summary": rep.summary or "",
        # "source_url": rep.sourceURL or "",
        "event_size": event.event_size,
    }


def _normalize_subject(text: str) -> str:
    """对主体名做轻量归一化：小写、去噪、压缩空白，便于后续匹配。"""
    norm = " ".join((text or "").strip().lower().split())
    norm = re.sub(r"[^\w\u4e00-\u9fff\s\-\.]+", " ", norm)
    return " ".join(norm.split())


def _resolve_entity_tiers_by_rules(subjects: List[str]) -> List[EntityTierValidatedItem]:
    """
    规则映射主体 tier：别名归一化 -> exact 命中 -> 轻量包含匹配。
    """
    results: List[EntityTierValidatedItem] = []
    for raw in subjects:
        raw_subject = str(raw or "").strip()
        if not raw_subject:
            continue

        norm = _normalize_subject(raw_subject)
        canonical = ENTITY_ALIAS_MAP.get(norm, norm)
        tier = MAJOR_ENTITY_INDEX.get(canonical)

        if tier is None:
            for candidate, candidate_tier in MAJOR_ENTITY_INDEX.items():
                if not candidate:
                    continue
                if norm == candidate or norm.startswith(f"{candidate} ") or candidate in norm:
                    canonical = candidate
                    tier = candidate_tier
                    break

        if tier in {"tier1", "tier2"}:
            results.append(
                EntityTierValidatedItem(
                    raw_subject=raw_subject,
                    mapped_entity=canonical,
                    mapped_tier=tier,
                    status="accepted",
                )
            )
        else:
            results.append(
                EntityTierValidatedItem(
                    raw_subject=raw_subject,
                    mapped_entity=None,
                    mapped_tier=None,
                    status="rejected",
                )
            )
    return results


def _extract_token_usage(raw_msg: Any) -> Dict[str, int]:
    """从模型原始返回中提取 token 统计，兼容不同字段命名。"""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    response_meta = getattr(raw_msg, "response_metadata", None)
    usage = {}
    if isinstance(response_meta, dict):
        usage = response_meta.get("token_usage") or response_meta.get("usage") or {}

    if not usage:
        usage_meta = getattr(raw_msg, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            usage = usage_meta

    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _merge_token_usage(acc: Dict[str, int], delta: Dict[str, int]) -> Dict[str, int]:
    """将一次调用的 token 统计累加到聚合计数器。"""
    acc["prompt_tokens"] = int(acc.get("prompt_tokens", 0)) + int(delta.get("prompt_tokens", 0))
    acc["completion_tokens"] = int(acc.get("completion_tokens", 0)) + int(delta.get("completion_tokens", 0))
    acc["total_tokens"] = int(acc.get("total_tokens", 0)) + int(delta.get("total_tokens", 0))
    return acc


def _weighted_score(weights: Dict[str, float], values: Dict[str, float]) -> float:
    """按权重计算加权总分，并保留 4 位小数。"""
    score = 0.0
    for key, weight in weights.items():
        score += float(weight) * float(values.get(key, 0.0))
    return round(score, 4)


def _invoke_structured_step(
    llm: Any,
    system_prompt: str,
    schema: Any,
    payload: Dict[str, Any],
) -> Tuple[Any, Dict[str, int]]:
    """执行一次结构化 LLM 调用，并返回解析结果与 token 使用量。"""
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("human", "{payload_json}")]
    )
    structured_llm = llm.with_structured_output(
        schema,
        method="function_calling",
        include_raw=True,
    )
    chain = prompt | structured_llm
    resp = chain.invoke({"payload_json": json.dumps(payload, ensure_ascii=False)})

    parsed = resp
    raw = None
    if isinstance(resp, dict) and "parsed" in resp:
        parsed = resp.get("parsed")
        raw = resp.get("raw")

    return parsed, _extract_token_usage(raw)


def _choose_step_a_batch_prompt(category: str) -> str:
    """根据大类选择 Step A（分类/主体抽取）对应的系统提示词。"""
    theme = get_category_theme(category)
    if category == "AI":
        return STEP_A_CLASSIFY_BATCH_PROMPT.format(category_theme=theme)
    return STEP_A_CLASSIFY_BATCH_PROMPT_NON_AI.format(category_theme=theme)


def _choose_step_b_batch_prompt(mode: str) -> str:
    """根据策略模式选择 Step B（通用打分）对应的系统提示词。"""
    if mode == "full":
        return STEP_B_COMMON_SCORE_BATCH_PROMPT
    return STEP_B_COMMON_SCORE_DIRECT_BATCH_PROMPT


def _is_url_working(url: str) -> bool:
    """用轻量 HEAD/GET 请求校验 URL 是否有效（不是 404 或连接失败）"""
    if not url:
        return False
    if not url.startswith("http"):
        return False
    try:
        # 使用 HEAD 请求，设置 2.5 秒超时，跟随重定向，伪装 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.head(url, headers=headers, timeout=2.5, allow_redirects=True)
        if resp.status_code == 404:
            return False
        # 如果返回不支持 HEAD (如 405/403/400)，用 GET 验证
        if resp.status_code in (405, 403, 400):
            resp = requests.get(url, headers=headers, timeout=2.5, stream=True)
            return resp.status_code != 404
        return True
    except Exception:
        # 异常（如超时）均判定为失效，触发自愈或递补
        return False


def heal_event_urls(
    results: List[EventScoringResult],
    deduped_payload: Any,
    dedup_trace: Optional[Dict[str, Any]],
) -> List[EventScoringResult]:
    """
    自愈事件 URL：
    1. 并发检测排名前列的事件 URL。
    2. 如果代表链接失效 (404/无法访问)，在事件簇（cluster）中寻找其他备用稿的链接。
    3. 如果簇内所有链接都失效，则放弃该事件，由后续高分事件递补。
    """
    if not results:
        return []

    # 1. 建立文章 ID 到原始文章对象的映射
    id_to_article = {}
    if isinstance(deduped_payload, dict) and isinstance(deduped_payload.get("data"), list):
        for art in deduped_payload["data"]:
            art_id = str(art.get("id") or "")
            if art_id:
                id_to_article[art_id] = art

    # 2. 建立 event_id 到其成员文章 ID 列表的映射
    event_to_members = {}
    if isinstance(dedup_trace, dict) and isinstance(dedup_trace.get("clusters"), list):
        for c in dedup_trace["clusters"]:
            kept_id = str(c.get("kept_id") or "")
            member_ids = c.get("member_ids") or []
            if kept_id:
                event_to_members[kept_id] = [str(mid) for mid in member_ids]
                event_to_members[f"event_{kept_id}"] = [str(mid) for mid in member_ids]

    # 我们并发检测前 30 个候选事件，防止过滤后不够 topk 条
    candidates = results[:30]
    remaining = results[30:]

    def check_and_heal(res: EventScoringResult) -> Optional[EventScoringResult]:
        url = res.selected_url or ""
        if _is_url_working(url):
            return res

        print(f"⚠️ [Self-Healing] Default URL is broken (404/timeout) for event {res.event_id}: {url}")
        
        # 尝试从事件簇中寻找备用链接
        member_ids = event_to_members.get(res.event_id) or []
        for mid in member_ids:
            alt_art = id_to_article.get(mid)
            if alt_art:
                alt_url = alt_art.get("sourceURL") or ""
                if alt_url and alt_url != url:
                    print(f"🔄 [Self-Healing] Testing backup URL from cluster: {alt_url}")
                    if _is_url_working(alt_url):
                        print(f"✅ [Self-Healing] Successfully healed event {res.event_id} with backup URL: {alt_url}")
                        res.selected_url = alt_url
                        return res
                        
        # 实在没有可用链接，放弃该事件，交由递补机制
        print(f"❌ [Self-Healing] Abandoning event {res.event_id} due to no working URL.")
        return None

    healed_results = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        future_to_res = {executor.submit(check_and_heal, r): r for r in candidates}
        for future in as_completed(future_to_res):
            res_val = future.result()
            if res_val:
                healed_results.append(res_val)

    # 保持原来的排序
    healed_results.sort(key=lambda x: x.final_score, reverse=True)
    return healed_results + remaining


def score_events(
    category: str,
    deduped_payload: Any,
    dedup_trace: Optional[Dict[str, Any]] = None,
    *,
    llm: Any = None,
    topk: int = 10,
    debug: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    批量评分主入口（核心链路）：
    1) 将 dedup 结果转事件列表
    2) Step A 批量分类与主体抽取
    3) Step A2 主体 tier 规则映射（按策略启用）
    4) Step B 批量通用维度打分
    5) Step D 规则惩罚计算
    6) 合成 final_score 并按分数排序
    """
    from simple_bot import llm_fast, llm_reasoning

    working_llm = llm or llm_reasoning or llm_fast 
    strategy = get_category_strategy(category)
    mode = str(strategy.get("mode", "simple"))
    t0 = time.perf_counter()

    meta: Dict[str, Any] = {
        "category": category,
        "mode": mode,
        "topk": topk,
        "fail_open": False,
        "warnings": [],
        "timing_ms": {
            "build_events_ms": 0,
            "step_a_ms": 0,
            "step_a2_ms": 0,
            "step_b_ms": 0,
            "step_d_ms": 0,
            "total_ms": 0,
        },
        "token_usage": {
            "overall": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "by_step": {
                "step_a": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "step_a2": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "step_b": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "step_d": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        },
    }

    build_t0 = time.perf_counter()
    events = build_events_from_dedup(deduped_payload, dedup_trace)
    meta["timing_ms"]["build_events_ms"] = int((time.perf_counter() - build_t0) * 1000)
    meta["input_event_count"] = len(events)
    if not events:
        meta["warnings"].append("no_events_after_build")
        meta["timing_ms"]["total_ms"] = int((time.perf_counter() - t0) * 1000)
        meta["total_ms"] = meta["timing_ms"]["total_ms"]
        return [], meta

    allowed_labels = get_category_cluster_labels(category)
    # 按 category 选择通用维度权重（严格按配置键读取，不做降级兜底）
    common_weights = DEFAULT_WEIGHT_HINT[get_common_weight_key_by_category(category)]
    penalty_weight = float(
        DEFAULT_WEIGHT_HINT.get("penalty", {}).get("penalty_score", 1.0)
    )
    event_by_id = {e.event_id: e for e in events}
    source_counter = Counter(_extract_domain(e.articles[0].sourceURL) for e in events)

    # Step A：批量分类 + 主体抽取（LLM）
    step_a_t0 = time.perf_counter()
    cls_map: Dict[str, ClassificationOutput] = {}
    batches = _chunk_list(events, BATCH_SIZE)

    def run_step_a_batch(batch: List[EventInput]) -> Tuple[Dict[str, ClassificationOutput], Dict[str, int], List[str]]:
        """执行单个 Step A 批次：输入事件列表，输出分类结果映射。"""
        # Step A 输入是事件最小表示，输出必须覆盖该 batch 的全部 event_id
        ids = [e.event_id for e in batch]
        payload = {
            "category": category,
            "allowed_labels": allowed_labels,
            "events": [_event_to_llm_payload(e) for e in batch],
        }
        parsed, usage = _invoke_structured_step(
            working_llm,
            _choose_step_a_batch_prompt(category),
            BatchClassificationOutput,
            payload,
        )
        out = parsed if isinstance(parsed, BatchClassificationOutput) else BatchClassificationOutput.model_validate(parsed)
        result = {x.event_id: x for x in out.items if x.event_id in event_by_id}
        missing_ids = [event_id for event_id in ids if event_id not in result]
        if missing_ids:
            raise ValueError(f"step_a_missing_event:{','.join(missing_ids)}")
        return result, usage, []

    workers = max(1, min(MAX_WORKERS, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_step_a_batch, batch) for batch in batches]
        for future in as_completed(futures):
            result, usage, warnings = future.result()
            cls_map.update(result)
            _merge_token_usage(meta["token_usage"]["overall"], usage)
            _merge_token_usage(meta["token_usage"]["by_step"]["step_a"], usage)
            meta["warnings"].extend(warnings)
    meta["timing_ms"]["step_a_ms"] = int((time.perf_counter() - step_a_t0) * 1000)

    # Step A2：主体 tier 规则映射（仅策略开启时启用）
    step_a2_t0 = time.perf_counter()
    tiers_map: Dict[str, List[EntityTierValidatedItem]] = {}
    for event in events:
        # Step A2 只消费 Step A 的确定输出，不再回退默认分类
        c = cls_map[event.event_id]
        subjects = c.event_subjects or ([] if not c.primary_subject else [c.primary_subject])
        tiers_map[event.event_id] = _resolve_entity_tiers_by_rules(subjects) if bool(strategy.get("use_entity_tier_mapping")) else []
    meta["timing_ms"]["step_a2_ms"] = int((time.perf_counter() - step_a2_t0) * 1000)

    # Step B：批量通用维度评分（LLM）
    step_b_t0 = time.perf_counter()
    full_map: Dict[str, CommonScoreLLMOutput] = {}
    simple_map: Dict[str, CommonScoreDirectLLMOutput] = {}

    def run_step_b_batch(batch: List[EventInput]) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
        """执行单个 Step B 批次：输入分类增强事件，输出通用分映射。"""
        # Step B 输入带上 Step A 的分类与主体信息；输出同样必须覆盖整个 batch
        ids = [e.event_id for e in batch]
        payload_items = []
        for event in batch:
            c = cls_map[event.event_id]
            cluster_label = c.cluster_label
            if cluster_label not in allowed_labels:
                raise ValueError(f"step_b_invalid_cluster_label:{event.event_id}:{cluster_label}")
            payload_items.append(
                {
                    **_event_to_llm_payload(event),
                    "cluster_label": cluster_label,
                    "event_subjects": c.event_subjects or ([] if not c.primary_subject else [c.primary_subject]),
                }
            )
        payload = {"category": category, "events": payload_items}
        if mode == "full":
            parsed, usage = _invoke_structured_step(
                working_llm,
                _choose_step_b_batch_prompt(mode),
                BatchCommonScoreOutput,
                payload,
            )
            out = parsed if isinstance(parsed, BatchCommonScoreOutput) else BatchCommonScoreOutput.model_validate(parsed)
            result = {x.event_id: x for x in out.items if x.event_id in event_by_id}
            missing_ids = [event_id for event_id in ids if event_id not in result]
            if missing_ids:
                raise ValueError(f"step_b_missing_event:{','.join(missing_ids)}")
            return result, usage, []

        parsed, usage = _invoke_structured_step(
            working_llm,
            _choose_step_b_batch_prompt(mode),
            BatchCommonScoreDirectOutput,
            payload,
        )
        out = parsed if isinstance(parsed, BatchCommonScoreDirectOutput) else BatchCommonScoreDirectOutput.model_validate(parsed)
        result = {x.event_id: x for x in out.items if x.event_id in event_by_id}
        missing_ids = [event_id for event_id in ids if event_id not in result]
        if missing_ids:
            raise ValueError(f"step_b_direct_missing_event:{','.join(missing_ids)}")
        return result, usage, []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_step_b_batch, batch) for batch in batches]
        for future in as_completed(futures):
            result, usage, warnings = future.result()
            if mode == "full":
                full_map.update(result)
            else:
                simple_map.update(result)
            _merge_token_usage(meta["token_usage"]["overall"], usage)
            _merge_token_usage(meta["token_usage"]["by_step"]["step_b"], usage)
            meta["warnings"].extend(warnings)
    meta["timing_ms"]["step_b_ms"] = int((time.perf_counter() - step_b_t0) * 1000)

    # Step D：规则惩罚分计算（不走 LLM）
    step_d_t0 = time.perf_counter()
    penalty_map: Dict[str, PenaltyScoreOutput] = {}
    for event in events:
        # Step D 为规则分，不走 LLM；是否启用惩罚由策略控制
        rep = event.articles[0]
        event_id = event.event_id
        if bool(strategy.get("use_penalty")):
            domain = _extract_domain(rep.sourceURL)
            source_count = int(source_counter.get(domain, 0))
            source_penalty = compute_source_volume_penalty(source_count)
            penalty_map[event_id] = PenaltyScoreOutput(
                event_id=event_id,
                source_volume_penalty=source_penalty,
                penalty_score=round(source_penalty, 4),
            )
        else:
            penalty_map[event_id] = build_placeholder_penalty(event_id)
    meta["timing_ms"]["step_d_ms"] = int((time.perf_counter() - step_d_t0) * 1000)

    # 汇总所有分项并计算最终分，输出统一的 EventScoringResult
    results: List[EventScoringResult] = []
    for event in events:
        event_id = event.event_id
        rep = event.articles[0]
        c = cls_map[event_id]
        cluster_label = c.cluster_label
        if cluster_label not in allowed_labels:
            raise ValueError(f"invalid_cluster_label:{event_id}:{cluster_label}")
        if cluster_label == "与主题无关":
            print(f"🗑️ [Scorer] Filtering out unrelated event {event_id}: {rep.title}")
            continue
        subjects = c.event_subjects or ([] if not c.primary_subject else [c.primary_subject])
        tiers = tiers_map[event_id]
        penalty = penalty_map[event_id]

        if mode == "full":
            llm_score = full_map[event_id]
            accepted_tiers = [x.mapped_tier for x in tiers if x.status == "accepted"]
            # 头部性对总分做封顶约束：避免“主体很大但事件影响力一般”时被过度抬分
            prom_raw = float(compute_prominence_score_from_validated_tiers(accepted_tiers))
            impact_value = float(llm_score.impact)
            prom_effective = min(prom_raw, impact_value + 1.0)
            common_values = {
                "impact": impact_value,
                "prominence": prom_effective,
                "heat": float(compute_heat_score(event.event_size)),
                "controversy": float(llm_score.controversy),
            }
            common = CommonScoreOutput(
                event_id=event_id,
                impact=common_values["impact"],
                controversy=common_values["controversy"],
                prominence=common_values["prominence"],
                heat=common_values["heat"],
                common_score=_weighted_score(common_weights, common_values),
            )
        else:
            llm_score = simple_map[event_id]
            common_values = {
                "impact": float(llm_score.impact),
                "prominence": float(llm_score.prominence),
                "heat": float(llm_score.heat),
                "controversy": float(llm_score.controversy),
            }
            common = CommonScoreOutput(
                event_id=event_id,
                impact=common_values["impact"],
                controversy=common_values["controversy"],
                prominence=common_values["prominence"],
                heat=common_values["heat"],
                common_score=_weighted_score(common_weights, common_values),
            )

        # 来源质量系数：降权二手聚合源，提权官方/一手源
        source_quality = compute_source_quality_factor(
            rep.sourceURL or "", rep.sourceName or ""
        )
        final_score = round(
            (float(common.common_score) - penalty_weight * float(penalty.penalty_score))
            * source_quality,
            4,
        )
        hits = [x.mapped_entity for x in tiers if x.status == "accepted" and x.mapped_entity]
        results.append(
            EventScoringResult(
                event_id=event_id,
                cluster_label=cluster_label,  # type: ignore[arg-type]
                event_subjects=subjects,
                resolved_entity_tiers=tiers,
                event_size=event.event_size,
                common=common,
                penalty=penalty,
                final_score=final_score,
                source_title=rep.title,
                source_summary=rep.summary,
                selected_url=rep.sourceURL,
                entity_hits=[x for x in hits if x],
                score_breakdown={
                    "common_score": float(common.common_score),
                    "penalty_score": float(penalty.penalty_score),
                    "impact": float(common.impact),
                    "prominence": float(common.prominence),
                    "heat": float(common.heat),
                    "controversy": float(common.controversy),
                },
            )
        )

    results.sort(key=lambda x: x.final_score, reverse=True)
    
    # URL 连通性自愈与校验
    try:
        heal_t0 = time.perf_counter()
        results = heal_event_urls(results, deduped_payload, dedup_trace)
        meta["timing_ms"]["url_healing_ms"] = int((time.perf_counter() - heal_t0) * 1000)
    except Exception as e:
        print(f"⚠️ [Self-Healing] Failed to run URL self-healing: {e}")
        meta["warnings"].append(f"url_healing_failed:{str(e)}")

    scored_events = [x.model_dump() for x in results]
    if topk > 0:
        meta["topk_preview"] = [x.event_id for x in results[:topk]]
    meta["output_event_count"] = len(scored_events)
    meta["timing_ms"]["total_ms"] = int((time.perf_counter() - t0) * 1000)
    meta["total_ms"] = meta["timing_ms"]["total_ms"]

    if debug:
        print(
            f"📊 [Scoring] category={category} mode={mode} "
            f"in={meta['input_event_count']} out={meta['output_event_count']} "
            f"ms={meta['total_ms']} tokens={meta['token_usage']['overall']['total_tokens']}"
        )

    return scored_events, meta
