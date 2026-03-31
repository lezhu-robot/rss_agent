#!/usr/bin/env python3
"""
回填脚本：为缺失的日期生成日报并写入飞书文档。
用法：docker exec rss-agent python3 backfill_daily.py
"""
import json
import os
from datetime import datetime, timedelta, timezone as dt_timezone

from pytz import timezone

from tools import fetch_news
from news_dedup import dedupe_news_payload
from config import (
    DAILY_NEWS_CATEGORIES,
    WIKI_TOKEN,
    NEWS_DEDUP_ENABLED,
    NEWS_DEDUP_MODE,
    NEWS_DEDUP_THRESHOLD,
    NEWS_DEDUP_DEBUG,
    NEWS_DEDUP_EMBEDDING_MODEL,
    NEWS_SCORING_TOPK,
    NEWS_SCORING_DEBUG,
)
from news_scoring_spec_v2 import score_events
from simple_bot import llm_reasoning
from agent_graph import (
    NewsBriefing,
    CATEGORY_CLUSTERS,
    HEADLINE_COUNT,
    CLUSTER_ITEM_COUNT,
    RewrittenHeadlineBatch,
    RewrittenSummaryBatch,
    HEADLINE_LENGTH_MIN,
    HEADLINE_LENGTH_MAX,
    HEADLINE_LEN_MAX,
    SUMMARY_LENGTH_MIN,
    SUMMARY_LENGTH_MAX,
    SUMMARY_LEN_MAX,
)
from database import save_cached_news, get_cached_news, init_db
from doc_writer import FeishuDocWriter
from lark_card_builder import build_cover_card
from langchain_core.prompts import ChatPromptTemplate

beijing_tz = timezone("Asia/Shanghai")

# ========== 要回填的日期（按时间顺序，最终写入 wiki 时最新的在最上面） ==========
BACKFILL_DATES = ["2026-03-27", "2026-03-28", "2026-03-29", "2026-03-30"]


def fetch_news_for_date(category, target_date_str):
    """抓取指定日期（北京时间）的新闻。"""
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    start_beijing = beijing_tz.localize(target_date.replace(hour=0, minute=0, second=0))
    end_beijing = beijing_tz.localize(target_date.replace(hour=23, minute=59, second=59))
    start_utc = start_beijing.astimezone(dt_timezone.utc)
    end_utc = end_beijing.astimezone(dt_timezone.utc)
    return fetch_news(category, start_dt=start_utc, end_dt=end_utc)


def generate_briefing_from_scored_events(scored_events, category):
    """复用 writer_node 的 scored events 路径生成 NewsBriefing。"""
    cluster_config = CATEGORY_CLUSTERS.get(category, CATEGORY_CLUSTERS["AI"])
    cluster_names = [name for name, _ in cluster_config]

    sorted_events = sorted(
        scored_events, key=lambda x: float(x.get("final_score", 0)), reverse=True
    )
    top_events = sorted_events[:HEADLINE_COUNT]

    cluster_items = {name: [] for name in cluster_names}
    for ev in sorted_events:
        name = ev.get("cluster_label")
        if name in cluster_items and len(cluster_items[name]) < CLUSTER_ITEM_COUNT:
            cluster_items[name].append(
                {
                    "event_id": ev.get("event_id"),
                    "title": ev.get("source_title") or "",
                    "summary": ev.get("source_summary") or "",
                    "url": ev.get("selected_url") or "",
                    "score": ev.get("final_score", 0),
                }
            )

    # --- LLM 改写头条 ---
    headline_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"""你是资深行业情报编辑。用户订阅偏好：{category}。
你只负责改写标题，不负责排序、不负责选条、不负责URL。
请基于输入 events，逐条输出 event_id 和改写后的 title。
title的改写要用 **一句话总结**，按视觉宽度尽量控制长度：**1个中文字 = 2个英文字母/数字**，总视觉宽度必须在 **{HEADLINE_LENGTH_MIN}~{HEADLINE_LENGTH_MAX}个中文字** 之间，且总字符数（中英文加在一起）**不得超过{HEADLINE_LEN_MAX}个
文字要 **犀利、具体、直击要害**，必须提及具体公司名、产品名或关键数据
约束：
1. event_id 必须与输入完全一致，且数量一致
2. 不得新增/删除/合并事件
3. title 句末不要加句号
4. 不要输出任何解释文本
""",
            ),
            ("human", "{payload}"),
        ]
    )
    headline_chain = headline_prompt | llm_reasoning.with_structured_output(
        RewrittenHeadlineBatch
    )
    headline_payload = [
        {
            "event_id": ev.get("event_id"),
            "title": ev.get("source_title") or "",
            "summary": ev.get("source_summary") or "",
            "cluster_label": ev.get("cluster_label") or "",
            "score": ev.get("final_score", 0),
        }
        for ev in top_events
    ]
    rewritten_headlines = headline_chain.invoke(
        {"payload": json.dumps({"events": headline_payload}, ensure_ascii=False)}
    )

    # --- LLM 改写专题摘要 ---
    summary_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"""你是资深行业情报编辑。用户订阅偏好：{category}。
你只负责改写摘要，不负责排序、不负责分组、不负责URL。
请基于输入 events，逐条输出 event_id 和改写后的 summary。
每条改写后的 summary要 **有吸引力**，能让人一眼看出新闻的价值
       - 每条摘要仅可能尝试按照三小句的格式进行写作：发生了什么，细节补充描述，有什么影响
       - 每条摘要按视觉宽度尽量控制长度：**1个中文字 = 2个英文字母/数字**，总视觉宽度必须在 **{SUMMARY_LENGTH_MIN}~{SUMMARY_LENGTH_MAX}个中文字** 之间，且总字符数（中英文加在一起）**不得超过{SUMMARY_LEN_MAX}个**，信息密度高，直击核心
约束：
1. event_id 必须与输入完全一致，且数量一致
2. 不得新增/删除/合并事件
3. summary 句末不要加句号
4. 不要输出任何解释文本
""",
            ),
            ("human", "{payload}"),
        ]
    )
    summary_chain = summary_prompt | llm_reasoning.with_structured_output(
        RewrittenSummaryBatch
    )
    flat_cluster_items = []
    for cluster_name in cluster_names:
        for item in cluster_items[cluster_name]:
            flat_cluster_items.append(
                {
                    "event_id": item.get("event_id"),
                    "cluster_label": cluster_name,
                    "title": item.get("title") or "",
                    "summary": item.get("summary") or "",
                    "score": item.get("score", 0),
                }
            )

    rewritten_summaries = summary_chain.invoke(
        {"payload": json.dumps({"events": flat_cluster_items}, ensure_ascii=False)}
    )

    # --- 组装 ---
    headline_map = {str(it.event_id): it.title for it in rewritten_headlines.items}
    summary_map = {str(it.event_id): it.summary for it in rewritten_summaries.items}

    briefing = NewsBriefing(
        headlines=[
            {
                "title": headline_map.get(
                    str(ev.get("event_id")), ev.get("source_title", "")
                ),
                "url": ev.get("selected_url") or "",
            }
            for ev in top_events
        ],
        clusters=[
            {
                "name": cn,
                "items": [
                    {
                        "summary": summary_map.get(
                            str(it.get("event_id")), it.get("summary", "")
                        ),
                        "url": it.get("url") or "",
                    }
                    for it in cluster_items[cn]
                ],
            }
            for cn in cluster_names
        ],
    )
    return briefing


# ========== Wiki 写入（支持指定日期） ==========

def write_to_wiki_with_date(writer, wiki_token, all_categories_news, date_str):
    """将指定日期的日报写入 Wiki（复用 FeishuDocWriter 的 block 构建方法）。"""
    document_id = writer.get_document_id_from_wiki(wiki_token)
    if not document_id:
        return False

    blocks = []
    blocks.append(writer.create_divider_block())
    blocks.append(writer.create_heading_block(date_str, level=2))

    for category, briefing in all_categories_news.items():
        blocks.append(writer.create_heading_block(str(category), level=3))

        if not briefing or not isinstance(briefing, dict):
            blocks.append(writer.create_text_block("暂无数据"))
            continue

        headlines = briefing.get("headlines")
        blocks.append(writer.create_bold_text_block("── 🔥 今日头条 ──"))
        if isinstance(headlines, list) and headlines:
            for hl in headlines:
                if isinstance(hl, dict):
                    title = str(hl.get("title") or "无标题").strip()
                    url = writer.normalize_http_url(hl.get("url"))
                    blocks.append(writer.create_ordered_list_block(title, url))
        else:
            blocks.append(writer.create_text_block("暂无数据"))

        clusters = briefing.get("clusters")
        if not isinstance(clusters, list):
            clusters = []

        blocks.append(writer.create_bold_text_block("── 📂 深度专题 ──"))
        if not clusters:
            blocks.append(writer.create_text_block("暂无数据"))
            continue

        valid = 0
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            valid += 1
            blocks.append(
                writer.create_bold_text_block(
                    f"▸ {cluster.get('name') or '未命名专题'}"
                )
            )
            items = cluster.get("items")
            if not isinstance(items, list) or not items:
                blocks.append(writer.create_text_block("暂无条目"))
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "无摘要").strip()
                url = writer.normalize_http_url(item.get("url"))
                blocks.append(writer.create_ordered_list_block(summary, url))

        if valid == 0:
            blocks.append(writer.create_text_block("暂无数据"))

    insert_index = writer.find_first_callout_index(document_id)
    if insert_index == -1:
        print("  ⚠️ 未找到高亮块，追加到末尾")
    else:
        print(f"  📝 插入到索引 {insert_index}")

    return writer.append_blocks_in_batches(
        document_id=document_id,
        children=blocks,
        index=insert_index,
        batch_size=writer.MAX_CHILDREN_PER_REQUEST,
    )


# ========== 主流程 ==========

def backfill_one_date(target_date_str):
    """为单个日期生成并归档日报。"""
    print(f"\n{'='*60}")
    print(f"📅 正在回填 {target_date_str}")
    print(f"{'='*60}")

    all_news_data = {}

    for category in DAILY_NEWS_CATEGORIES:
        print(f"\n  --- {category} ---")

        # 检查已有缓存
        cached = get_cached_news(category, target_date_str)
        if cached and cached.get("briefing_data"):
            try:
                briefing_dict = json.loads(cached["briefing_data"])
                if isinstance(briefing_dict, dict) and briefing_dict.get("headlines"):
                    print(f"  ✅ 已有缓存，跳过生成")
                    all_news_data[category] = briefing_dict
                    continue
            except Exception:
                pass

        # 1. 抓取
        print(f"  🌍 抓取 {category} 在 {target_date_str} 的新闻...")
        news_data = fetch_news_for_date(category, target_date_str)

        if not isinstance(news_data, dict) or not news_data.get("data"):
            print(f"  ⚠️ 无新闻数据")
            all_news_data[category] = None
            continue

        # 2. 去重
        dedup_trace = None
        if NEWS_DEDUP_ENABLED:
            news_data, dedup_meta, dedup_trace = dedupe_news_payload(
                news_data,
                enabled=True,
                mode=NEWS_DEDUP_MODE,
                threshold=NEWS_DEDUP_THRESHOLD,
                debug=NEWS_DEDUP_DEBUG,
                embedding_model=NEWS_DEDUP_EMBEDDING_MODEL,
            )
            print(
                f"  🧹 去重: {dedup_meta.get('input_count')} → {dedup_meta.get('output_count')}"
            )

        # 3. 评分
        try:
            scored_events, _ = score_events(
                category=category,
                deduped_payload=news_data,
                dedup_trace=dedup_trace,
                llm=llm_reasoning,
                topk=NEWS_SCORING_TOPK,
                debug=NEWS_SCORING_DEBUG,
            )
            print(f"  🧮 评分完成: {len(scored_events)} 事件")
        except Exception as e:
            print(f"  ❌ 评分失败: {e}")
            all_news_data[category] = None
            continue

        if not scored_events:
            print(f"  ⚠️ 无可用事件")
            all_news_data[category] = None
            continue

        # 4. LLM 生成日报
        try:
            briefing = generate_briefing_from_scored_events(scored_events, category)
            briefing_dict = briefing.model_dump()
            card_content = build_cover_card(briefing, category=category)
            save_cached_news(
                category,
                card_content,
                target_date_str,
                json.dumps(briefing_dict, ensure_ascii=False),
            )
            print(f"  💾 已缓存 {category}/{target_date_str}")
            all_news_data[category] = briefing_dict
        except Exception as e:
            print(f"  ❌ 生成失败: {e}")
            all_news_data[category] = None

    # 5. 写入飞书文档
    print(f"\n  📝 写入 {target_date_str} 到飞书文档...")
    try:
        app_id = os.getenv("LARK_APP_ID")
        app_secret = os.getenv("LARK_APP_SECRET")
        writer = FeishuDocWriter(app_id, app_secret)
        ok = write_to_wiki_with_date(writer, WIKI_TOKEN, all_news_data, target_date_str)
        print(f"  {'✅ 归档成功!' if ok else '❌ 归档失败'}")
    except Exception as e:
        print(f"  ❌ 归档异常: {e}")


def main():
    init_db()
    for date_str in BACKFILL_DATES:
        backfill_one_date(date_str)
    print(f"\n🎉 全部回填完成！共 {len(BACKFILL_DATES)} 天")


if __name__ == "__main__":
    main()
