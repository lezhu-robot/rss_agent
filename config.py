# RSS Agent Configuration

# 每天需要生成日报的类别列表（供定时任务批量生成与分发）
DAILY_NEWS_CATEGORIES = ["AI", "PGC"]

# --- 每日日报展示分类（数据采集仍按 DAILY_NEWS_CATEGORIES，展示层合并） ---
# AI 日报 = AI 类新闻；PGC 日报 = 游戏 + 音乐 + 竞品公司动态
DAILY_DISPLAY_CATEGORIES = {
    "AI": {"sources": ["AI"], "topk": 10},
    "PGC": {
        "sources": ["GAMES", "MUSIC", "COMPETITORS"],
        "topk": 15,
        # 补充：从 AI 分类中按关键词过滤竞品动态
        # （因为 YouTube Blog、Meta Newsroom 等 Feed 归在 AI 类下）
        "competitor_keywords_from": ["AI"],
        "competitor_keywords": [
            "YouTube", "YTB", "YT", "油管",
            "Instagram", "IG", "Ins",
            "Facebook", "FB", "脸书",
            "Meta",
            "Twitter", "推特",
        ],
    },
}

# 每日日报推送时间（北京时间 HH:MM）
# 两次推送：中国早晨 09:00 + 美西早晨（BJT 00:00）
DAILY_PUSH_TIMES = ["09:00", "00:00"]

# 每次日报拉取的新闻时间窗口（小时），略大于 12h 确保不漏
DAILY_FETCH_WINDOW_HOURS = 14
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

# --- News Scoring Config (默认关闭，确保主链路不受影响) ---
# 是否启用评分模块（False 时 fetcher 直接进入 writer，完全保持旧行为）
NEWS_SCORING_ENABLED = True
# 评分失败是否自动降级为旧流程（建议保持 True，避免影响日报稳定性）
NEWS_SCORING_FAIL_OPEN = True
# 是否输出评分调试日志
NEWS_SCORING_DEBUG = True
# 今日头条默认取前 K 条高分事件
NEWS_SCORING_TOPK = 10
# 评分规范模块名（便于后续平滑切换不同实现）
NEWS_SCORING_SPEC_MODULE = "news_scoring_spec_v2"
