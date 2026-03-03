from typing import TypedDict, List, Optional, Dict
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field
from typing import Literal

# --- 长度控制常量（视觉宽度，1中文字=2英文字母） ---
HEADLINE_LENGTH_MIN = 10  # 今日头条最短视觉宽度（中文字数）
HEADLINE_LENGTH_MAX = 19  # 今日头条最长视觉宽度（中文字数）
HEADLINE_LEN_MAX = 25     # 今日头条每条的字符数硬上限
SUMMARY_LENGTH_MIN = 45   # 深度专题摘要最短视觉宽度（中文字数）
SUMMARY_LENGTH_MAX = 60   # 深度专题摘要最长视觉宽度（中文字数）
SUMMARY_LEN_MAX = 65      # 深度专题摘要的字符数硬上限
HEADLINE_COUNT = 10    # 今日头条条数
CLUSTER_ITEM_COUNT = 5 # 每个专题板块的新闻条数

# --- 按赛道配置不同的深度专题板块 ---
CATEGORY_CLUSTERS = {
    "AI": [
        ("产品", "新产品发布、产品更新、功能迭代"),
        ("模型", "AI模型、算法、技术突破"),
        ("硬件与算力", "芯片、GPU、服务器、云计算、算力基建"),
        ("投融资与政策", "融资、收购、上市、政策法规、行业监管"),
    ],
    "GAMES": [
        ("产品", "新游发布、版本更新、DLC、评测"),
        ("生态", "电竞赛事、主播、玩家社区、游戏文化"),
        ("商业", "厂商财报、收购并购、裁员、政策监管"),
    ],
    "MUSIC": [
        ("产品", "新歌、新专辑、MV、榜单数据"),
        ("生态", "演唱会、音乐节、艺人动态、厂牌签约"),
        ("商业", "版权交易、流媒体平台、融资、行业政策"),
    ],
}

# --- Pydantic Data Models (用于 Writer 结构化输出) ---
class TopHeadline(BaseModel):
    title: str = Field(..., description=f"一句话热点总结, 视觉宽度控制在{HEADLINE_LENGTH_MIN}-{HEADLINE_LENGTH_MAX}个中文字之间")
    url: str = Field(..., description="对应新闻的原文链接")

class NewsItem(BaseModel):
    summary: str = Field(..., description=f"新闻摘要, 视觉宽度控制在{SUMMARY_LENGTH_MIN}-{SUMMARY_LENGTH_MAX}个中文字之间")
    url: str = Field(..., description="原文链接")

class NewsCluster(BaseModel):
    name: str = Field(..., description="板块名称, 根据赛道不同而不同")
    items: List[NewsItem] = Field(..., description=f"该板块下的新闻列表, 约{CLUSTER_ITEM_COUNT}条")

class NewsBriefing(BaseModel):
    headlines: List[TopHeadline] = Field(..., description=f"今日头条, 约{HEADLINE_COUNT}条最重要的热点新闻")
    clusters: List[NewsCluster] = Field(..., description="深度专题分类板块")

# --- Agent State ---
class AgentState(TypedDict):
    # 消息历史
    messages: List[BaseMessage]
    user_id: str
    message_id: Optional[str]
    user_preference: Optional[str]
    news_content: Optional[str] 
    
    # [新增] 结构化简报数据 (用于多轮回忆)
    briefing_data: Optional[Dict] # 实际存的是 NewsBriefing.model_dump()
    generated_at: Optional[str]
    
    # [新增] 当前选中的详情板块 (与 user_preference 长期偏好区分开)
    selected_cluster: Optional[str]
    selected_category: Optional[str]

    # 控制流标志
    intent: Optional[str] # write / read / chat
    force_refresh: Optional[bool] # [新增] 是否强制刷新


class RouterDecision(BaseModel):
    """Router 对用户意图的分析结果"""
    intent: Literal["write", "read", "chat"] = Field(
        ..., description="用户的核心意图"
    )
    category: Optional[str] = Field(
        None, description="提取出的具体领域关键词，如 'AI', '科技'"
    )

from tools import fetch_news
from news_dedup import dedupe_news_payload
from config import (
    NEWS_DEDUP_DEBUG,
    NEWS_DEDUP_EMBEDDING_MODEL,
    NEWS_DEDUP_ENABLED,
    NEWS_DEDUP_MODE,
    NEWS_DEDUP_THRESHOLD,
)
from simple_bot import llm_fast, llm_reasoning # Import capability-based LLMs
import json

from langchain_core.prompts import ChatPromptTemplate

def router_node(state: AgentState):
    """
    进阶版意图识别：使用 LLM 结构化输出 + 容错兜底
    
    新增：如果 state 中已有 user_preference（定时任务传入），直接返回 read 意图，跳过 LLM 解析
    """
    # --- 拦截器 0: 定时任务绕行通道 (scheduler 专用) ---
    if state.get("user_preference"):
        print(f"⚡ [Router] Scheduler mode detected, preference={state['user_preference']}, skipping LLM")
        return {"intent": "read"}  # 直接返回 read 意图，user_preference 保持不变
    
    last_message = state["messages"][-1].content
    print(f"🚦 Router handling message: {last_message}")
    
    # --- 拦截器 1: 详情展开指令 (来自卡片按钮) ---
    # 匹配 "展开：XXX" 或 "👉 XXX"
    if "展开：" in last_message or "👉" in last_message:
        # 简单粗暴提取：取冒号或符号后的内容，去除括号里的数字
        # e.g. "👉 硬件与算力 (8)" -> "硬件与算力"
        import re
        # 匹配 "展开：(.+)" 或 "👉 (.+)"
        match = re.search(r"(?:展开：|👉\s*)([^\(\)]+)", last_message)
        if match:
            category = match.group(1).strip()
            print(f"🚀 [Router] Intercepted Detail Request: {category}")
            return {
                "intent": "detail",
                "selected_cluster": category,
                "selected_category": state.get("selected_category"),
            }
    
    try:
        # 定义 System Prompt 强化指令 (适配 Reasoning 模型)
        system_prompt = """你是一个智能意图路由器。请分析用户的输入，提取核心意图和实体。
        
        规则：
        1. 如果用户想看新闻、日报、简报 -> intent: read
        2. 如果用户想订阅、关注、追踪某话题 -> intent: write, category: <话题>
        3. 其他情况（闲聊、问好、不想看了） -> intent: chat
        
        输出格式：必须是符合 RouterDecision 结构的 JSON。"""
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}"),
        ])
        
        # 绑定工具 (使用 Fast 模型 -> DeepSeek V3)
        print(f"🤖 User Input: {last_message}")
        structured_llm = llm_fast.with_structured_output(RouterDecision) 
        
        # 组合 chain
        # chain = prompt | structured_llm
        prompt_message = prompt.invoke({"input": last_message})
        decision = structured_llm.invoke(prompt_message)
        
        print(f"👉 LLM Decision: {decision.intent}, Category: {decision.category}")
        return {
            "intent": decision.intent, 
            "user_preference": decision.category
        }
    except Exception as e:
        print(f"⚠️ Router LLM Error: {e}")
        # 兜底策略：诚实报错，不进行猜测
        return {
            "intent": "error",
            "messages": [AIMessage(content=f"❌ 意图识别失败啦。\n错误详情: {str(e)}")]
        }


from database import upsert_preference, get_preference
from langchain_core.messages import AIMessage

def saver_node(state: AgentState):
    """保存用户偏好节点"""
    # 1. 优先使用 Router 提取的结构化数据
    extracted_category = state.get("user_preference")
    
    # 2. 如果 Router 没提出来，诚实地返回错误提示，而不是瞎猜
    if not extracted_category:
        print("⚠️ [Saver] Extraction failed")
        return {"messages": [AIMessage(content="🤔 我知道您想调整偏好，但我没能识别出具体的话题。\n\n请尝试更清晰的指令，例如：“订阅AI”、“关注游戏GAMES”、“关注音乐MUSIC”。")]}
    
    print(f"💾 [Saver] Saving preference: {extracted_category}")
    
    # 3. 存入数据库
    res = upsert_preference(state["user_id"], extracted_category)
    
    # 4. 返回动态消息
    return {"messages": [AIMessage(content=f"已关注：【{extracted_category}】板块，每日自动为您推送\n\n点击“当日{extracted_category}新闻”，即可获取今日动态。")]}



def fetcher_node(state: AgentState):
    """
    负责获取新闻数据：
    支持两种模式：
    1. 【定时任务模式】state 中已有 user_preference（直接从 config 传入）→ 使用该值
    2. 【用户交互模式】state 中无 user_preference → 从数据库查询用户订阅偏好
    
    然后检查缓存或抓取新闻：
    - 先检查数据库缓存 (除非 force_refresh=True)
    - 如果无缓存，调用 Tool 抓取 RSS
    """
    print("🕵️ [Fetcher] Node started")
    
    # 策略 1: 优先使用 State 中已存在的 user_preference（定时任务传入）
    pref = state.get("user_preference")
    
    # 策略 2: 如果 State 中没有，则从数据库查询（用户交互场景）
    if not pref:
        print("🔍 [Fetcher] No preference in state, querying database...")
        pref = get_preference(state["user_id"])
    else:
        print(f"✅ [Fetcher] Using preference from state: {pref}")
    
    # 策略 3: 如果两者都没有，返回提示
    if not pref:
        print("⚠️ [Fetcher] No preference found in state or database")
        return {
            "user_preference": None, 
            "messages": [AIMessage(content="您还没有订阅任何内容，请发送 '订阅 AI'，'订阅 MUSIC'，或者'订阅 GAMES'")]
        }
    
    # 1. 尝试从数据库读取今日已生成的缓存
    today = date.today().isoformat()
    # 注意：get_cached_news 返回 {"content": str, "briefing_data": str/json, "generated_at": str}
    
    # 策略：如果有缓存且非强制刷新，我们直接返回缓存
    if not state.get("force_refresh"):
        cached = get_cached_news(pref, today)
        if cached and cached.get("briefing_data"):
            print(f"✅ [Fetcher] Found cached data for {pref}. generated_at={cached.get('generated_at')}")
            try:
                briefing_json = json.loads(cached["briefing_data"])
                return {
                    "user_preference": pref, 
                    "news_content": None, 
                    "briefing_data": briefing_json,
                    "generated_at": cached.get("generated_at")
                }
            except Exception as e:
                print(f"⚠️ [Fetcher] Cache parse failed: {e}")
                pass
    else:
        print(f"🔄 [Fetcher] Force refresh enabled. Skipping cache check.")

    # 2. 无缓存或强制刷新，执行实时抓取
    # 2. 无缓存或强制刷新，执行实时抓取
    print(f"🌍 [Fetcher] Fetching news for: {pref}")
    
    news_data = fetch_news(pref)

    # 可插拔去重：默认由 config 开关控制，关闭时不影响原有流程
    if NEWS_DEDUP_ENABLED:
        news_data, dedup_meta, _ = dedupe_news_payload(
            news_data,
            enabled=NEWS_DEDUP_ENABLED,
            mode=NEWS_DEDUP_MODE,
            threshold=NEWS_DEDUP_THRESHOLD,
            debug=NEWS_DEDUP_DEBUG,
            embedding_model=NEWS_DEDUP_EMBEDDING_MODEL,
        )
        print(
            "🧹 [Fetcher] Dedup done: "
            f"in={dedup_meta.get('input_count')} "
            f"out={dedup_meta.get('output_count')} "
            f"rate={dedup_meta.get('dedup_rate')} "
            f"fail_open={dedup_meta.get('fail_open')}"
        )
    
    print(f"✅ [Fetcher] Got data (length: {len(str(news_data))})")
    # 关键：当需要重新抓取时，显式清空旧结构化结果，避免 writer 命中 checkpointer 残留 state
    return {
        "user_preference": pref,
        "news_content": json.dumps(news_data, ensure_ascii=False),
        "briefing_data": None,
        "generated_at": None,
        "selected_cluster": None,
    }

from messaging import reply_message

from lark_card_builder import build_cover_card

def writer_node(state: AgentState):
    """
    核心写作节点：
    1. 接收 Fetcher 抓取到的原始新闻数据
    2. 调用 Reasoning LLM (DeepSeek R1) 进行深度分析
    3. 生成结构化简报 (Summary + Clusters)
    4. 将结果存入 State，并渲染飞书卡片
    """
    print("✍️ [Writer] Node started")
    
    if state.get("message_id"):
        reply_message(state["message_id"], "✍️ AI 正在深度分析新闻数据，生成交互式早报...")
        
    news_json = state.get("news_content")
    category = state.get("user_preference", "未知领域")
    
    # 策略 0: 仅在非强制刷新时允许复用 State 中的 briefing_data (来自 Cache)
    if (not state.get("force_refresh")) and state.get("briefing_data"):
        try:
            print(f"⏩ [Writer] Using cached briefing data for {category}")
            # Pydantic 还原
            briefing = NewsBriefing(**state["briefing_data"])
            
            # 构建卡片 (传入 generated_at 和 category)
            card_content = build_cover_card(briefing, generated_at=state.get("generated_at"), category=category)
            
            return {
                "briefing_data": state["briefing_data"], 
                "messages": [AIMessage(content=card_content)]
            }
        except Exception as e:
            print(f"⚠️ [Writer] Failed to reuse cache: {e}, falling back to generation")
            # 失败了则继续往下执行生成逻辑
    
    # 策略 1: 如果没有 News Content (这不应该发生，Fetcher 应该处理了)，报错
    if not news_json:
        return {"messages": [AIMessage(content="未能获取新闻数据")]}

    # 动态生成板块配置
    cluster_config = CATEGORY_CLUSTERS.get(category, CATEGORY_CLUSTERS["AI"])
    cluster_count = len(cluster_config)
    cluster_desc = "\n".join(f"         - **{name}**：{desc}" for name, desc in cluster_config)
        
    system_prompt = f"""你是一个资深的行业情报分析师。用户的订阅偏好是：{category}。
    请阅读输入的新闻 JSON 数据，运用你的专业洞察力，进行以下处理：

    1. **去重与清洗**：合并雷同新闻，剔除无关噪音。

    2. **今日头条 (headlines)**：
       - 从所有新闻中提炼出最重要的 **{HEADLINE_COUNT} 条** 热点
       - 每条热点用 **一句话总结**，按视觉宽度尽量控制长度：**1个中文字 = 2个英文字母/数字**，总视觉宽度必须在 **{HEADLINE_LENGTH_MIN}~{HEADLINE_LENGTH_MAX}个中文字** 之间，且总字符数（中英文加在一起）**不得超过{HEADLINE_LEN_MAX}个**
       - 文字要 **犀利、具体、直击要害**，必须提及具体公司名、产品名或关键数据
       - 标题要 **有吸引力**，能让人一眼看出新闻的价值
       - 每条必须附带对应新闻的原文 URL
       - **禁止**：套话、废话、笼统描述

    3. **深度专题 (clusters)**：
       - 将新闻 **固定** 归类到以下 {cluster_count} 个板块（即使某个板块暂无新闻，也保留空列表）：
{cluster_desc}
       - 每个板块约 **{CLUSTER_ITEM_COUNT} 条** 新闻摘要
       - 每条摘要要 **有吸引力**，能让人一眼看出新闻的价值
       - 每条摘要仅可能尝试按照三小句的格式进行写作：发生了什么，细节补充描述，有什么影响
       - 每条摘要按视觉宽度尽量控制长度：**1个中文字 = 2个英文字母/数字**，总视觉宽度必须在 **{SUMMARY_LENGTH_MIN}~{SUMMARY_LENGTH_MAX}个中文字** 之间，且总字符数（中英文加在一起）**不得超过{SUMMARY_LEN_MAX}个**，信息密度高，直击核心
       - 每条必须附带对应新闻的原文 URL
    
    请严格输出符合 NewsBriefing 结构的 JSON。
    **重要**：
    1. 直接输出 JSON 字符串，**不要**包含 ```json ... ``` 等 Markdown 格式。
    2. JSON 根对象直接包含 `headlines` 和 `clusters` 字段，**不要**包裹在 `NewsBriefing` 等根键下。
    3. 不要包含任何推理过程文本。
    4. 所有总结性文字（headlines 的 title 和 clusters items 的 summary）的句末 **不要加句号**（。），保持简洁干练。"""
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{news_data}"),
    ])
    
    print("🧠 [Writer] Invoking LLM for Structured Output...")
    # 切换到 llm_reasoning (Claude 3.5 Sonnet / DeepSeek R1) 以获得最佳写作质量
    structured_llm = llm_reasoning.with_structured_output(NewsBriefing) 
    chain = prompt | structured_llm
    
    try:
        briefing: NewsBriefing = chain.invoke({"news_data": news_json})
        print(f"✅ [Writer] Briefing Generated. Clusters: {[c.name for c in briefing.clusters]}")
        
        # 1. 构建飞书交互卡片
        card_content = build_cover_card(briefing, category=category)
        
        # 2. 返回结果
        # 注意：我们需要标记这是一张卡片，而不是普通文本
        # 下游发送端 (lark_service 或 messaging) 需要识别这个标记
        # 这里我们将 content 设为 card json，开头加一个特殊标记？
        # 或者使用 additional_kwargs
        
        return {
            "briefing_data": briefing.model_dump(),
            "messages": [AIMessage(content=card_content)] 
        }
    except Exception as e:
        print(f"❌ [Writer] Analysis Failed: {e}")
        return {"messages": [AIMessage(content=f"生成早报失败，请稍后重试。\nError: {str(e)}")]}


# --- 详情展示节点 ---

from database import get_cached_news # Import at top or inside if circular
from datetime import date

# --- 详情展示节点 ---
def detail_node(state: AgentState):
    """
    接收用户选择的板块名 -> 从 State 缓存或数据库中查找新闻 -> 渲染详情
    """
    print("🔍 [Detail] Node started")
    target_cluster = state.get("selected_cluster")
    selected_category = state.get("selected_category")
    print(
        f"🔎 [Detail] target_cluster={target_cluster}, "
        f"selected_category={selected_category}, resolved_category=None"
    )

    if not target_cluster:
        return {"messages": [AIMessage(content="⚠️ 未指定要展开的专题，请重新点击卡片按钮")]}

    if not selected_category:
        return {
            "messages": [
                AIMessage(
                    content="当前卡片版本较旧，缺少类别信息。请先重新生成日报卡片后再展开专题。"
                )
            ]
        }

    today = date.today().isoformat()
    cached = get_cached_news(selected_category, today)
    if not cached or not cached.get("briefing_data"):
        return {
            "messages": [
                AIMessage(
                    content=f"⚠️ 未找到 {selected_category} 今日缓存。\n\n请先重新生成该类别日报后再展开专题。"
                )
            ]
        }

    try:
        briefing_dump = json.loads(cached["briefing_data"])
        briefing = NewsBriefing(**briefing_dump)
    except Exception as e:
        print(f"⚠️ [Detail] Parse cache failed for category={selected_category}: {e}")
        return {"messages": [AIMessage(content="⚠️ 数据解析错误")]}

    # 仅做精确匹配，避免同名专题串到其他类别
    found_cluster = None
    for cluster in briefing.clusters:
        if cluster.name == target_cluster:
            found_cluster = cluster
            break

    if not found_cluster:
        return {
            "messages": [
                AIMessage(content=f"⚠️ 在 {selected_category} 类别下未找到专题：{target_cluster}")
            ]
        }

    print(
        f"✅ [Detail] target_cluster={target_cluster}, "
        f"selected_category={selected_category}, resolved_category={selected_category}"
    )
        
    # 渲染详情：每条新闻的摘要本身就是超链接
    msg = f"## 📂 专题详情：{found_cluster.name}\n\n"
    for i, item in enumerate(found_cluster.items, 1):
        msg += f"{i}. [{item.summary}]({item.url})\n"
    
    return {"messages": [AIMessage(content=msg)]}



# --- 组装图谱 (The Map) ---
from langgraph.graph import StateGraph, END

# 1. 拿出一张空白地图
workflow = StateGraph(AgentState)

# Chat Node: 使用 LLM 进行自然对话
def chat_node(state):
    """聊天模式节点 - 调用 LLM 进行多轮对话"""
    # state["messages"] 已包含历史上下文（由 run_agent 的滑动窗口提供）
    response = llm_fast.invoke(state["messages"])
    return {"messages": [response]}

# 2. 在地图上画站点 (Nodes)
workflow.add_node("router", router_node)
workflow.add_node("saver", saver_node)
workflow.add_node("fetcher", fetcher_node)
workflow.add_node("writer", writer_node)
workflow.add_node("detail", detail_node) # 新增 Detail 节点
workflow.add_node("chat", chat_node)

# 3. 设置起点
workflow.set_entry_point("router")

# 4. 设置分岔路口
workflow.add_conditional_edges(
    "router",
    lambda x: x["intent"],
    {
        "write": "saver",
        "read": "fetcher",
        "detail": "detail", 
        "chat": "chat",
        "error": END
    }
)

# 5. 设置终点
workflow.add_edge("saver", END)
workflow.add_edge("chat", END)
workflow.add_edge("fetcher", "writer")
workflow.add_edge("writer", END)
workflow.add_edge("detail", END) # Detail -> END

# 6. 编译（启用 Checkpointer 以持久化 State）
from langgraph.checkpoint.memory import MemorySaver
memory = MemorySaver()
graph = workflow.compile(checkpointer=memory)
