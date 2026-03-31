# RSS Agent

基于 LangGraph + 飞书的智能新闻订阅机器人，支持 **实时竞品追踪推送** 和 **AI 日报生成与归档**。

## ✨ 核心功能

### 1. 群组实时推送（Group Push）

按关键词或分类自动监控新闻源，定时将匹配的新闻推送到飞书群。

- **关键词过滤**：支持多关键词组 OR/AND 匹配（如追踪 YouTube、Instagram、Meta 等竞品动态）
- **分类过滤**：支持按 `AI`、`GAMES`、`MUSIC` 等分类推送
- **三层去重**：
  - URL/ID 精确去重（单次轮询内）
  - 跨轮询滚动去重（`sent_article_keys` 缓存，防止 overlap 导致重复）
  - **语义去重**（Embedding 相似度聚类，合并同事件不同来源报道）
- **Overlap 回溯**：可配置时间回溯窗口，弥补爬虫入库延迟

### 2. AI 日报生成（Daily Report）

基于 LangGraph 多节点 Agent 自动生成结构化新闻早报。

- **新闻评分引擎**：对抓取的新闻进行重要性评分，筛选 Top K 事件
- **语义去重**：规则去重 + Embedding 向量聚类，避免重复报道
- **多格式输出**：支持飞书卡片交互、详情展开
- **Wiki 归档**：每日定时将日报归档到飞书 Wiki 知识库

### 3. 飞书机器人交互

- 私聊对话：支持自然语言查询新闻
- 菜单操作：一键订阅/取消订阅类别
- 卡片交互：点击展开新闻详情
- 订阅管理：可视化多选订阅类别

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────┐
│                  RSS Agent                       │
│                                                  │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ LangGraph│  │ Group Push   │  │  Scheduler│  │
│  │  Agent   │  │  Service     │  │ (APSched) │  │
│  └────┬─────┘  └──────┬───────┘  └─────┬─────┘  │
│       │               │               │          │
│  ┌────┴─────┐  ┌──────┴───────┐  ┌────┴──────┐  │
│  │ News     │  │   News       │  │  Daily    │  │
│  │ Scoring  │  │   Dedup      │  │  Archive  │  │
│  │ Engine   │  │  (Semantic)  │  │  (Wiki)   │  │
│  └──────────┘  └──────────────┘  └───────────┘  │
│                       │                          │
│              ┌────────┴────────┐                 │
│              │  News API       │                 │
│              │ (Harvester)     │                 │
│              └─────────────────┘                 │
└─────────────────────────────────────────────────┘
         │                          │
    飞书群/私聊                 飞书 Wiki
```

## 📁 项目结构

```
rss_agent/
├── lark_service.py           # 主入口：FastAPI 服务 + 定时调度
├── group_push_service.py     # 群推送核心逻辑（轮询、去重、推送）
├── group_news_client.py      # 新闻 API 客户端（按分类/关键词查询）
├── group_message_formatter.py# 群推送消息格式化
├── group_config.json         # 群推送配置（群ID、关键词、间隔、overlap）
├── group_config_loader.py    # 配置加载与运行时状态管理
├── group_runtime.json        # 运行时状态（上次推送时间、去重缓存）
├── news_dedup.py             # 新闻去重模块（规则 + 语义 Embedding）
├── news_scoring_engine.py    # 新闻评分引擎（日报用）
├── agent_graph.py            # LangGraph Agent 定义（日报生成流程）
├── config.py                 # 全局配置（类别、去重参数、评分参数）
├── messaging.py              # 飞书消息发送封装
├── lark_card_builder.py      # 飞书卡片构建器
├── database.py               # SQLite 数据持久化
├── doc_writer.py             # 飞书 Wiki 文档写入
├── backfill_daily.py         # 日报补刷工具
├── docker-compose.yml        # Docker 部署配置
├── Dockerfile                # 容器构建定义
└── requirements.txt          # Python 依赖
```

## 🚀 部署指南

### 前置条件

- Docker & Docker Compose
- 飞书开放平台应用凭证（App ID / App Secret）
- LLM API Key（通过 OpenRouter 或兼容 OpenAI 的服务）
- 新闻爬虫服务（[local-news-harvester](../local-news-harvester)）已运行

### 1. 配置环境变量

在项目根目录创建 `.env` 文件：

```ini
# LLM 配置（通过 OpenRouter）
OPENAI_API_KEY=sk-or-v1-xxx
OPENAI_API_BASE=https://openrouter.ai/api/v1
LLM_FAST_MODEL=openai/gpt-4o-mini
LLM_REASONING_MODEL=openai/gpt-5.2-chat

# 飞书应用
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx

# 新闻 API 地址（爬虫服务）
NEWS_API_URL=http://your-harvester-ip:9090/api/newsarticles/search

# 服务端口
PORT=36000
```

### 2. 配置群推送

编辑 `group_config.json`：

```json
[
  {
    "chat_id": "oc_xxx",
    "name": "竞品追踪群",
    "enabled": true,
    "keyword_groups": [["YouTube", "Instagram", "Meta", "Twitter"]],
    "keyword_group_mode": "OR",
    "interval_minutes": 60,
    "overlap_minutes": 120,
    "delivery_mode": "all",
    "timezone": "Asia/Shanghai"
  },
  {
    "chat_id": "oc_yyy",
    "name": "AI News",
    "enabled": true,
    "preferences": ["AI"],
    "interval_minutes": 120,
    "overlap_minutes": 30,
    "delivery_mode": "all",
    "timezone": "Asia/Shanghai"
  }
]
```

**配置说明：**

| 字段 | 说明 |
|---|---|
| `chat_id` | 飞书群 ID |
| `keyword_groups` | 关键词过滤组（匹配文章标题/摘要） |
| `preferences` | 按分类过滤（如 AI、GAMES、MUSIC） |
| `interval_minutes` | 轮询间隔（分钟） |
| `overlap_minutes` | 回溯窗口（分钟），覆盖爬虫入库延迟 |
| `delivery_mode` | 推送模式：`all`（全部推送） |

### 3. 启动服务

```bash
docker compose up -d --build
```

服务启动后监听 `36000` 端口。

### 4. 配置飞书事件订阅

在飞书开放平台中，将事件回调地址配置为：

```
https://your-domain/api/lark/event
```

### 5. 验证服务

```bash
# 健康检查
curl http://localhost:36000/

# 手动触发群推送测试
docker compose exec rss-agent python manual_group_push.py

# 测试特定群
docker compose exec rss-agent python manual_group_push.py --chat-id oc_xxx
```

## ⏰ 定时任务

| 任务 | 触发时间 | 说明 |
|---|---|---|
| 新闻生成 | 每天 08:00（北京） | 生成 AI/GAMES/MUSIC 三个类别的日报 |
| 归档 + 推送 | 每天 09:10（北京） | 将日报归档至 Wiki 并推送给订阅用户 |
| 群推送轮询 | 每分钟 | 检查各群是否到达推送时间，触发推送 |

## 🧠 语义去重原理

使用 `text-embedding-3-large` 模型进行 Embedding 向量化，通过余弦相似度 + 完全链接层次聚类合并同事件报道：

```
输入: 10 篇文章 (来自 TechCrunch, Engadget, The Verge...)
  → Step 1: URL/标题精确去重 (免费)
  → Step 2: Embedding 向量化 → 余弦相似度矩阵 → 聚类 (阈值 0.70)
  → Step 3: 每簇保留一篇代表稿
输出: 7 篇去重后文章

成本: ~$0.28/月 (Embedding tokens)
安全: 失败自动降级 (fail-open)，不影响推送
```

## 📊 数据持久化

| 数据 | 存储位置 | 说明 |
|---|---|---|
| 用户订阅 | `data/news.db` (SQLite) | 用户订阅类别、缓存新闻 |
| 群推送配置 | `group_config.json` | 群ID、关键词、间隔、overlap |
| 运行时状态 | `group_runtime.json` | 最后推送时间、去重缓存 |

以上文件通过 Docker volumes 挂载到宿主机，容器重建后数据不丢失。

## 🛠️ 常用运维命令

```bash
# 启动
docker compose up -d

# 停止
docker compose down

# 重建并启动
docker compose up -d --build

# 查看日志
docker logs -f rss-agent

# 手动触发日报生成
docker compose exec rss-agent python manual_trigger.py

# 手动触发群推送
docker compose exec rss-agent python manual_group_push.py

# 补刷历史日报
docker compose exec rss-agent python backfill_daily.py
```

## 📡 关联服务

| 服务 | 地址 | 说明 |
|---|---|---|
| RSS Agent (本项目) | `:36000` | 飞书机器人 + 群推送 + 日报 |
| [local-news-harvester](../local-news-harvester) | `:9090` | 新闻爬虫 + API |
| [RSSHub](../rsshub) | `:1200` | RSS 代理（自建实例） |

## License

MIT
