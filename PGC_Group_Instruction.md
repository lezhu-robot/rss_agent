# “PGC 行业趋势追踪” 频道使用说明

欢迎加入 **PGC 行业趋势追踪**（Chat ID: `oc_fd9668356d0271a07c8e19d25b82b679`）。本群组专注于自动化挖掘并推送全球关键竞品社交和视频平台的最新行业动态、产品迭代和战略资讯。

以下是关于该推流服务的详细配置与食用指南：

## 1. 新闻源范围 (News Sources)
本频道的资讯采集深度集成了底层的 `local-news-harvester` 系统，包含了精选的全球权威科技、商业与独立互联网分析媒体及官方团队博客。目前具体定向监控的优质新闻源列表如下：

**顶级科技与创投媒体**
- TechCrunch
- TLDR

**大厂及产品官方动态**
- Google 官方博客
- YouTube 官网新闻
- Meta Newsroom
- Facebook 开发者官网新闻
- Telegram 官网新闻动态
- Kwai 官网新闻
- Pinterest

**AI 与前沿科技**
- 量子位 (QbitAI)
- 机器之心
- 新智元 (Aiera)
- MIT News (AI)
- AI base
- AI hot
- TestingCatalog

**搜索与营销动态**
- Search Engine Roundtable
- Semrush

**社媒高权重 KOL 与线报账号**
- **Twitter (X)**: @testingcatalog 等核心抢先线报与发现类账号
- **Threads**: @mattnavarra, @yassermasood, @theahmedghanem, @oncescuradu, @btibor91, @lindseygamble_ 等行业专家、创作者生态与社交媒体观察者

**垂直产业与出海 (游戏/音乐/短剧等)**
- GameIndustry.biz
- Pocket Gamer
- Eurogamer
- Jayisgames
- Music Business World
- Music Ally
- 新腕儿、DataEye短剧观察、漫剧/短剧自习室

系统基于上述高质量大盘进行无死角检索，再通过关键词过滤筛选出群组匹配的内容。

## 2. 新闻内容监控目标 (Content Focus)
频道的监控逻辑经过了去燥修剪，目前**严格聚焦在海外四大关键社媒与视频竞品体系**，过滤掉了庞杂杂音。只有文章正文或标题提到了以下公司 / 产品的任意变体时，才会触发推送：

 - **Google / YouTube 体系**
   - 包含词汇：`YouTube`、`YTB`、`YT`、`油管`
 - **Meta (Facebook / Instagram) 体系**
   - 包含词汇：`Meta`、`Facebook`、`FB`、`脸书`、`Instagram`、`IG`、`Ins`
 - **X (原 Twitter) 体系**
   - 包含词汇：`Twitter`、`推特`、`X`

> **匹配模式 (Mode)**：基于单词边界（Word Boundaries）技术的 `OR` 模糊匹配。即任何一篇高质新闻中，只要精准提到了上述任一平台（即使是简称如 `X`、`YT` 也能准确匹配而不会引发单词内部嵌词误报），都能被第一时间捕捉到频道中。

## 3. 推送频率与策略 (Frequency & Delivery)
为了保障接收到的信息既具有时效性，又不至于造成频繁的信息轰炸，群组采用了以下推送策略：

- **巡检频率**：机器人每 **60 分钟**会对全网源进行一轮检索与合并洗稿。
- **触发机制 (Delivery_Mode: all)**：按批次进行智能聚合排版推送，如果在过去一小时内发生了多起相关新闻，会由机器人提炼并在一次推送中整理展示。
- **防止打扰**：如果过去一个小时内监控源没有任何关于这四大竞品的新闻，则保持静默，绝不发送无用消息。

## 4. 更多信息
- 当前运行状态：`Enabled` (已激活)
- 服务时区设定：`Asia/Shanghai` (北京时间)

如果您发现有需要增加的独特产品缩写，或期望进一步补充/裁剪竞品方向，可以随时对相关配置文件中的关键词列表进行扩展。
