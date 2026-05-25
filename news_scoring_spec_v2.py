"""
新闻评分规范层（可维护版本）
=========================
职责边界：
1) 固化策略/权重/实体白名单等配置；
2) 定义批量评分用的 Prompt；
3) 定义输入输出 Pydantic 契约；
4) 提供规则函数（prominence/heat/source penalty）；
5) 对外暴露统一入口 score_events（内部代理到执行引擎）。

固定总分公式：
FinalScore = CommonScore - PenaltyScore
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field


# ====================
# 0) 基础配置
# ====================

CATEGORY_CLUSTER_LABELS: Dict[str, List[str]] = {
    "AI": ["产品", "模型", "硬件与算力", "投融资与政策", "与主题无关"],
    "GAMES": ["产品", "生态", "商业", "与主题无关"],
    "MUSIC": ["产品", "生态", "商业", "与主题无关"],
    "PGC": ["产品", "生态", "商业", "与主题无关"],  # PGC 复用 GAMES/MUSIC 的分类
    "COMPETITORS": ["产品", "生态", "商业", "与主题无关"],  # 竞品监控
}

# 评分策略：AI=full，GAMES/MUSIC=simple
CATEGORY_SCORING_STRATEGY: Dict[str, Dict[str, object]] = {
    "AI": {
        "mode": "full",
        "use_entity_tier_mapping": True,
        "use_penalty": True,
        "common_weight_key": "common",
    },
}


def get_category_strategy(category: str) -> Dict[str, object]:
    """获取评分策略（未知类别默认 simple）。"""
    return CATEGORY_SCORING_STRATEGY.get(
        category,
        {
            "mode": "simple",
            "use_entity_tier_mapping": False,
            "use_penalty": False,
            "common_weight_key": "common_simple",
        },
    )


def get_category_cluster_labels(category: str) -> List[str]:
    """返回该类别允许的分类标签。"""
    return CATEGORY_CLUSTER_LABELS.get(category, CATEGORY_CLUSTER_LABELS["GAMES"])


def get_common_weight_key_by_category(category: str) -> str:
    """返回通用分权重键（common/common_simple）。"""
    return str(get_category_strategy(category)["common_weight_key"])


CATEGORY_THEMES: Dict[str, str] = {
    "AI": "人工智能（AI/LLM/AIGC/机器学习/芯片/算力/大模型/AI应用/智能体等）",
    "GAMES": "游戏（主机/手游/新游发布/电竞赛事/游戏厂商商业动态等）",
    "MUSIC": "音乐（新歌/唱片发行/歌手艺人动态/演唱会/流媒体平台等）",
    "PGC": "游戏与音乐（涵盖游戏发布/电竞赛事/歌手艺人/音乐新歌/行业商业动态等）",
    "COMPETITORS": "科技竞品公司动态（如 Google, Meta, ByteDance 等大厂的业务与产品更新）",
}


def get_category_theme(category: str) -> str:
    """获取类别的核心主题描述词，用于分类校验提示。"""
    return CATEGORY_THEMES.get(category, f"当前类别 {category}")


# ====================
# 1) 重点主体名单
# ====================

MAJOR_ENTITY_TIERS: Dict[str, Set[str]] = {
    "tier1": {
        "openai", "google", "deepmind", "anthropic", "meta", "microsoft", "nvidia",
        "amazon", "aws", "xai", "字节跳动", "豆包", "阿里", "阿里云", "腾讯", "百度", "华为",
    },
    "tier2": {
        "mistral", "cohere", "stability ai", "perplexity", "moonshot", "kimi",
        "智谱", "百川", "零一万物", "商汤", "科大讯飞", "快手", "美团",
    },
}


def _build_major_entity_index() -> Dict[str, str]:
    """展平为 canonical_name -> tier，供程序规则匹配。"""
    index: Dict[str, str] = {}
    for tier, names in MAJOR_ENTITY_TIERS.items():
        for name in names:
            index[" ".join(name.strip().lower().split())] = tier
    return index


MAJOR_ENTITY_INDEX: Dict[str, str] = _build_major_entity_index()

# 批量评分参数：可按压测结果微调
BATCH_SIZE = 20
MAX_WORKERS = 6

# 主体别名映射：仅用于规则匹配，不走 LLM
ENTITY_ALIAS_MAP: Dict[str, str] = {
    "谷歌": "google",
    "alphabet": "google",
    "google llc": "google",
    "open ai": "openai",
    "openai inc": "openai",
    "deep mind": "deepmind",
    "meta ai": "meta",
    "msft": "microsoft",
    "微软": "microsoft",
    "亚马逊": "amazon",
    "亚马逊云": "aws",
    "英伟达": "nvidia",
    "字节": "字节跳动",
}


# ====================
# 2) 输入数据契约
# ====================

class EventArticle(BaseModel):
    """事件中的代表稿（字段与 fetcher 输出保持一致）。"""

    id: str | int
    category: Optional[str] = None
    title: str
    summary: Optional[str] = ""
    sourceURL: str
    sourceName: Optional[str] = None
    publishedAt: Optional[str] = None
    rawContent: Optional[str] = None
    scrapedAt: Optional[str] = None
    tags: Optional[List[str]] = None
    tumbnailURL: Optional[str] = None


class EventInput(BaseModel):
    """dedup 后事件输入。event_size 是 heat 的唯一输入。"""

    event_id: str
    event_size: int = Field(..., ge=1)
    articles: List[EventArticle] = Field(..., min_length=1)


# ====================
# 3) 规则分函数
# ====================

def compute_prominence_score_from_validated_tiers(validated_tiers: List[Optional[str]]) -> float:
    """主体头部性规则分。"""
    compact = [t for t in validated_tiers if t]
    if not validated_tiers:
        return 1.5
    if "tier1" in compact:
        return 5.0
    if "tier2" in compact:
        return 4.0
    return 2.2


def compute_heat_score(event_size: int) -> float:
    """热度规则分：只看 event_size。"""
    if event_size <= 0:
        return 0.0
    score = 0.8 + 1.5 * math.log(event_size)
    return round(max(0.0, min(5.0, score)), 2)


def compute_source_volume_penalty(source_daily_count: int) -> float:
    """来源灌水惩罚：仅做轻量纠偏，避免惩罚项压过新闻价值主分。"""
    if source_daily_count <= 3:
        return 0.0
    raw = 0.8 * math.log(max(1, source_daily_count))
    return round(max(0.0, min(2.5, raw)), 2)


# --- 来源质量系数：优先原始/官方/海外源，降权中文二手聚合源 ---
# 域名关键词 → 系数（乘到 final_score 上）
# > 1.0 = 提权（官方/一手源），< 1.0 = 降权（聚合/二手源）
SOURCE_QUALITY_FACTOR: Dict[str, float] = {
    # 降权：中文二手聚合/转述站
    "36kr.com": 0.70,
    "36kr": 0.70,
    "量子位": 0.72,
    "qbitai": 0.72,
    "机器之心": 0.72,
    "jiqizhixin.com": 0.72,
    "新智元": 0.72,
    "aiera": 0.72,
    "aibase": 0.75,
    "aihot": 0.75,
    # 提权：官方博客/一手源
    "blog.google": 1.15,
    "openai.com": 1.15,
    "about.fb.com": 1.15,
    "ai.meta.com": 1.15,
    "newsroom.pinterest.com": 1.10,
    "techcrunch.com": 1.10,
    "theverge.com": 1.10,
    "gamesindustry.biz": 1.10,
    "pocketgamer.com": 1.10,
    "billboard.com": 1.10,
}


def compute_source_quality_factor(source_url: str, source_name: str = "") -> float:
    """
    根据来源域名/名称返回质量系数。
    匹配逻辑：域名包含匹配 > sourceName 包含匹配 > 默认 1.0
    """
    url_lower = (source_url or "").lower()
    name_lower = (source_name or "").lower()
    for key, factor in SOURCE_QUALITY_FACTOR.items():
        key_lower = key.lower()
        if key_lower in url_lower or key_lower in name_lower:
            return factor
    return 1.0


# ====================
# 4) 批量 Prompt（仅保留当前在用）
# ====================

STEP_A_CLASSIFY_BATCH_PROMPT = """
你是新闻事件分类与主体抽取器（AI 批量模式）。
对 events 列表逐条输出：event_id / cluster_label / event_subjects / primary_subject。
强约束：cluster_label 只能是 产品/模型/硬件与算力/投融资与政策/与主题无关 五选一。如果该事件与人工智能（AI/LLM/AIGC/机器学习/芯片/算力/大模型/AI应用/智能体等）没有直接关联，必须分类为“与主题无关”。
输出必须是 items 数组，且每个 item 的 event_id 必须来自输入。
严格按 BatchClassificationOutput JSON 返回，不要额外文本。
""".strip()

STEP_A_CLASSIFY_BATCH_PROMPT_NON_AI = """
你是新闻事件分类与主体抽取器（GAMES/MUSIC 批量模式）。
对 events 列表逐条输出：event_id / cluster_label / event_subjects / primary_subject。
强约束：cluster_label 只能从输入 allowed_labels 中选一个。如果该事件与当前类别的主题（如游戏/音乐/对应竞品等）没有任何直接关联，请将其分类为“与主题无关”。
输出必须是 items 数组，且每个 item 的 event_id 必须来自输入。
严格按 BatchClassificationOutput JSON 返回，不要额外文本。
""".strip()

STEP_B_COMMON_SCORE_BATCH_PROMPT = """
你是通用新闻价值评分器（AI 批量模式）。
对 events 列表逐条输出：event_id / impact(0~5) / controversy(0~5)。
prominence 和 heat 由程序规则计算，不由你输出。

请按下面口径打分：
1) impact（影响力）
- 定义：事件对产品能力、行业竞争格局、商业化进程或监管环境的实质影响。
- 高分（4~5）：会改变行业判断或用户行为的事件。
  例子：头部公司发布关键模型并显著提升能力；大规模融资/并购改变赛道竞争；重大监管政策落地。
- 中分（2~3）：有进展，但影响范围较局部或短期。
  例子：某产品上线新功能、区域性合作、单一业务线升级。
- 低分（0~1）：信息增量小、重复报道、难以形成实际影响。
  例子：仅观点复述、无关键事实的“行业观察”。

2) controversy（争议性）
- 定义：事件在法律、伦理、安全、舆论层面的冲突与分歧强度。
- 高分（4~5）：存在明显对立立场或高风险争议。
  例子：版权诉讼、隐私合规争议、安全事故、强监管冲突。
- 中分（2~3）：有讨论度，但争议边界有限。
  例子：产品策略争论、商业模式分歧。
- 低分（0~1）：基本无争议，属于常规进展。
  例子：常规发布、常规融资公告。

打分要求：
- 只基于输入事件内容，不要脑补未给出的事实。
- 保持同一批次内尺度一致。
- 分数允许小数（如 3.5）。

严格按 BatchCommonScoreOutput JSON 返回，不要额外文本。
""".strip()

STEP_B_COMMON_SCORE_DIRECT_BATCH_PROMPT = """
你是通用新闻价值评分器（GAMES/MUSIC 批量模式）。
对 events 列表逐条输出：event_id / impact / controversy / prominence / heat（均 0~5）。

请按下面口径打分：
1) impact（影响力）
- 定义：事件对内容供给、平台生态、商业收入或行业结构的实质影响。
- 高分（4~5）：影响范围广、持续时间长、可能改变竞争格局。
  例子：头部平台重大战略调整、核心商业模式变化、关键政策落地。
- 低分（0~1）：仅局部动态，外溢影响很弱。

2) controversy（争议性）
- 定义：事件在舆论、伦理、合规、权益冲突上的争议强度。
- 高分（4~5）：明显两极分化或涉及高风险议题。
  例子：版权纠纷、未成年人保护争议、平台治理冲突。
- 低分（0~1）：常规信息更新、基本无冲突。

3) prominence（头部性）
- 定义：事件主体（公司/艺人/IP/平台/厂商）在行业中的头部程度与影响半径。
- 高分（4~5）：全球或全国头部主体。
  例子：顶级游戏厂商、头部流媒体平台、一线艺人/顶级IP。
- 低分（0~1）：长尾主体，行业影响半径较小。

4) heat（热度）
- 定义：事件当日传播强度与关注度（跨媒体可见度、讨论密度）。
- 高分（4~5）：多源同时报道、明显热点、持续讨论。
- 低分（0~1）：单源提及、传播范围有限。

打分要求：
- 只基于输入事件内容，不要脑补未给出的事实。
- 保持同一批次内尺度一致。
- 分数允许小数（如 2.5）。

严格按 BatchCommonScoreDirectOutput JSON 返回，不要额外文本。
""".strip()


# ====================
# 5) 输出数据契约
# ====================

# 单个事件的分类结果：给事件打上细分类标签，并提取主体信息（用于后续头部性计算）
class ClassificationOutput(BaseModel):
    event_id: str
    cluster_label: str
    event_subjects: List[str] = Field(default_factory=list)
    primary_subject: Optional[str] = None


# 主体映射校验结果：记录原始主体是否命中大公司名单，以及命中的层级
class EntityTierValidatedItem(BaseModel):
    raw_subject: str
    mapped_entity: Optional[str] = None
    mapped_tier: Optional[Literal["tier1", "tier2"]] = None
    status: Literal["accepted", "rejected"]


# 通用分（AI全量模式）LLM输出：仅由模型评 impact / controversy 两个维度
class CommonScoreLLMOutput(BaseModel):
    event_id: str
    impact: float = Field(..., ge=0, le=5)
    controversy: float = Field(..., ge=0, le=5)


# 通用分（非AI简化模式）LLM直出：一次返回四个通用维度分数
class CommonScoreDirectLLMOutput(BaseModel):
    event_id: str
    impact: float = Field(..., ge=0, le=5)
    controversy: float = Field(..., ge=0, le=5)
    prominence: float = Field(..., ge=0, le=5)
    heat: float = Field(..., ge=0, le=5)


# 分类阶段批量输出：一批事件对应的一组 ClassificationOutput
class BatchClassificationOutput(BaseModel):
    items: List[ClassificationOutput] = Field(default_factory=list)


# 通用分阶段批量输出（AI全量模式）
class BatchCommonScoreOutput(BaseModel):
    items: List[CommonScoreLLMOutput] = Field(default_factory=list)


# 通用分阶段批量输出（非AI简化模式）
class BatchCommonScoreDirectOutput(BaseModel):
    items: List[CommonScoreDirectLLMOutput] = Field(default_factory=list)


# 通用分聚合结果：统一保存四个通用维度和加权后的 common_score
class CommonScoreOutput(BaseModel):
    event_id: str
    impact: float = Field(..., ge=0, le=5)
    controversy: float = Field(..., ge=0, le=5)
    prominence: float = Field(..., ge=0, le=5)
    heat: float = Field(..., ge=0, le=5)
    common_score: float = Field(..., ge=0)


# 惩罚分结果：规则计算来源灌水惩罚，并汇总 penalty_score
class PenaltyScoreOutput(BaseModel):
    event_id: str
    source_volume_penalty: float = Field(..., ge=0)
    penalty_score: float = Field(..., ge=0)


# 事件最终打分结果：writer 侧可直接消费的标准结构（分类、分项分、总分、展示信息）
class EventScoringResult(BaseModel):
    event_id: str
    cluster_label: str
    event_subjects: List[str] = Field(default_factory=list)
    resolved_entity_tiers: List[EntityTierValidatedItem] = Field(default_factory=list)
    event_size: int = Field(..., ge=1)
    common: CommonScoreOutput
    penalty: PenaltyScoreOutput
    final_score: float
    source_title: Optional[str] = None
    source_summary: Optional[str] = None
    selected_url: Optional[str] = None
    entity_hits: List[str] = Field(default_factory=list)
    score_breakdown: Dict[str, float] = Field(default_factory=dict)


# 简化模式占位惩罚：保证输出 schema 一致

def build_placeholder_penalty(event_id: str) -> PenaltyScoreOutput:
    return PenaltyScoreOutput(
        event_id=event_id,
        source_volume_penalty=0.0,
        penalty_score=0.0,
    )


DEFAULT_WEIGHT_HINT = {
    "common": {
        "impact": 0.65,
        "prominence": 0.5,
        "heat": 0.2,
        "controversy": 0.1,
    },
    "common_simple": {
        "impact": 0.35,
        "prominence": 0.25,
        "heat": 0.25,
        "controversy": 0.15,
    },
    "penalty": {
        "penalty_score": 0.75,
    },
}


# ====================
# 6) 对外入口（代理）
# ====================

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
    对外统一入口：代理到执行引擎。
    这样外部 import 路径保持不变，便于低风险重构。
    """
    from news_scoring_engine import score_events as _engine_score_events

    return _engine_score_events(
        category=category,
        deduped_payload=deduped_payload,
        dedup_trace=dedup_trace,
        llm=llm,
        topk=topk,
        debug=debug,
    )
