# RSS Agent Configuration

# 每天需要生成日报的类别列表（供定时任务批量生成与分发）
DAILY_NEWS_CATEGORIES = ["AI", "GAMES", "MUSIC"]
# 飞书 Wiki 空间 token（用于归档日报到 Wiki）
WIKI_TOKEN = "S0Ckw3KCiiezJakYNj0crAvrnNR"

# --- News Dedup Config (参数控制，仅常量) ---
# 是否启用新闻去重（False 时完全跳过去重逻辑）
NEWS_DEDUP_ENABLED = True
# 去重模式：off=关闭，exact_only=仅规则去重，semantic=规则+语义去重
NEWS_DEDUP_MODE = "semantic"  # off | exact_only | semantic
# 语义去重阈值（相似度达到该值视为同事件）
NEWS_DEDUP_THRESHOLD = 0.70
# 是否输出去重调试信息（debug 统计）
NEWS_DEDUP_DEBUG = True
# 语义去重使用的 embedding 模型名
NEWS_DEDUP_EMBEDDING_MODEL = "openai/text-embedding-3-large"

# --- Dedup Experiment Config ---
# 实验目标日期（北京时间，格式 YYYY-MM-DD）
DEDUP_EXPERIMENT_DATE = "2026-02-05"  # 北京时间日期
# 实验要拉取并评估的新闻类别列表
DEDUP_EXPERIMENT_CATEGORIES = ["AI"]
# 实验要跑的去重模式集合
DEDUP_EXPERIMENT_MODES = ["off", "exact_only", "semantic"]
# semantic 模式下要扫描的阈值列表
DEDUP_EXPERIMENT_THRESHOLDS = [0.60]
