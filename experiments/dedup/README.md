# 新闻去重实验复盘（合并版）

## 1. 背景与目标
新闻聚合场景中，同一事件常被多源改写重复报道。目标是提升“语义去重”能力，减少重复内容，同时保持可控接入与可解释结果。

## 2. 技术路线选择
| 路线 | 核心思路 | 优点 | 缺点 | 本项目结论 |
|---|---|---|---|---|
| 开源实现（参考 [semhash](https://github.com/MinishLab/semhash)） | 复用现成去重框架与向量检索后端 | 工程成熟、扩展方便 | 引入成本和耦合较高 | 先借鉴思路，不整包引入 |
| 大模型 Embedding（参考 [CSDN案例](https://blog.csdn.net/weixin_35732273/article/details/158371636)） | 文本向量化 + 相似度阈值 + 聚类 | 对语义改写识别能力强 | 成本高于纯规则，需阈值调优 | 作为主路线 |
| 传统方法（关键词/规则） | URL/标题归一化、词面匹配 | 简单、低成本、稳定 | 语义召回弱 | 保留为第一层过滤 |

选择大模型 Embedding 的原因：本次数据中 `exact_only` 去重率为 0，主要重复来自语义改写，必须靠语义向量识别。

## 3. 去重算法简介
当前算法为两段式：
1. `exact` 去重：`sourceURL` 或标准化标题重复则直接去重。  
2. `semantic` 去重：`title + summary` 生成 embedding，按余弦相似度做 `complete linkage` 层次聚类，每簇保留 1 条代表。  

`complete linkage` 的作用是防止链式误并：簇间相似度取跨簇最小值，只有该最小值不低于阈值才允许合并。

## 4. 实验设置
- 类别：`AI`
- 数据窗口（UTC）：`2026-02-05T04:24:24.264Z ~ 2026-02-06T09:24:24.264Z`。选择这个时间是因为当天gpt-5.3-codex和claude 4.6同时发布，还有一些关于春节红包大战的新闻，有大量媒体推送了重复的新闻，因此有大量重复新闻，很适合作为例子。
- 样本量：`146`
- Embedding 模型：`openai/text-embedding-3-large`
- 主实验目录：`/root/rss_agent/experiments/dedup/results/run_custom_or_20260303_165649`
- 补充实验目录（threshold=0.60）：`/root/rss_agent/experiments/dedup/results/run_custom_or_t060_20260303_173531`

## 5. 结果汇总（主实验+补充实验合并）
| 实验组 | 模式 | 阈值 | 输入 | 输出 | 去重数 | 去重率 |
|---|---|---:|---:|---:|---:|---:|
| 主实验 | off | 0.82 | 146 | 146 | 0 | 0.00% |
| 主实验 | exact_only | 0.82 | 146 | 146 | 0 | 0.00% |
| 主实验 | semantic | 0.78 | 146 | 123 | 23 | 15.75% |
| 主实验 | semantic | 0.80 | 146 | 128 | 18 | 12.33% |
| 主实验 | semantic | 0.82 | 146 | 128 | 18 | 12.33% |
| 主实验 | semantic | 0.84 | 146 | 131 | 15 | 10.27% |
| 主实验 | semantic | 0.86 | 146 | 135 | 11 | 7.53% |
| 补充实验 | semantic | 0.60 | 146 | 91 | 55 | 37.67% |

关键结论：
1. 规则去重（exact_only）在本批数据几乎无效。  
2. 阈值越低，去重强度越高（0.60 去重最强）。  
3. 若以“去重优先”为目标，0.60 效果最佳；若要更保守，可选择 0.70 附近上线并持续抽样复核。  


## 【附录】 一些合并结果（threshold=0.6）
完整结果见[这里](./results/run_custom_or_t060_20260303_173531/AI/semantic_t0.60/duplicates_grouped.md)

### 1. Group c005 (duplicates=5)

- kept_id: `1501`
- kept_title: GPT-5.3上线Codex，OpenAI回应Claude新模型只用了15分钟 奥特曼还给Anthropic一拳
- kept_url: https://36kr.com/p/3671505759298178
- similarity(min/avg/max): 0.7341 / 0.8091 / 0.9164

| duplicate_id | similarity | title |
|---:|---:|---|
| `1438` | 0.9164 | GPT-5.3上线Codex！OpenAI回应Claude新模型只用了15分钟 |
| `1504` | 0.8351 | 硅谷一夜两弹，GPT-5.3-Codex狙击Claude 4.6, 奥特曼真急了 ChatGPT造出了自己 |
| `1436` | 0.7989 | Claude Opus 4.6 和GPT-5.3 Codex接管软件世界 Agent 正在腐蚀旧世界 |
| `1435` | 0.7611 | 最强牛马狙击编程之王，OpenAI和Anthropic深夜同发大招 Claude Opus 4.6vsGPT-5.3 Codex，IPO倒计时 |
| `1406` | 0.7341 | 刚刚，ChatGPT 和 Claude 同时大更新，不会给 AI 当老板的打工人要被淘汰 火星撞地球。 |

### 2. Group c011 (duplicates=5)

- kept_id: `1487`
- kept_title: OpenAI debuts Frontier to deploy AI agents for enterprise users
- kept_url: https://www.testingcatalog.com/openai-debuts-frontier-to-deploy-ai-agents-for-enterprise-users/
- similarity(min/avg/max): 0.6756 / 0.7868 / 0.8512

| duplicate_id | similarity | title |
|---:|---:|---|
| `1471` | 0.8512 | ​OpenAI 发布 Frontier 平台：打造“AI 同事”生态，加速企业级智能体落地 |
| `1432` | 0.8204 | OpenAI发布Frontier平台:打造“AI同事”，重塑企业协作新范式 |
| `1462` | 0.8175 | OpenAI放大招：企业级AI平台Frontier上线，专治各种智能体 OpenAI推出企业AI平台Frontier，统一管理AI智能体。 |
| `1427` | 0.7691 | 拒绝做“复读机”！OpenAI 祭出 Frontier 平台：打造你的专属“AI 同事”，软件巨头们坐不住了？ |
| `1390` | 0.6756 | OpenAI推出供企业构建和管理AI智能体的新方式 丽贝卡·斯库塔克 |

### 3. Group c013 (duplicates=4)

- kept_id: `1494`
- kept_title: AI日报：Anthropic发布Claude Opus 4.6；千问“春节大免单”首日火爆；腾讯推出“火龙漫剧”
- kept_url: https://www.aibase.com/zh/news/25352
- similarity(min/avg/max): 0.6249 / 0.6438 / 0.6557

| duplicate_id | similarity | title |
|---:|---:|---|
| `1470` | 0.6557 | 千问“春节大免单”首日火爆:3小时下单百万单，服务器一度告急 |
| `1441` | 0.6484 | 千问30亿免单引爆春节AI大战，奶茶免单开启AI购物时代 |
| `1440` | 0.6462 | 春节AI大战杀疯了！千问APP发起奶茶攻势，每人可领525元免单卡 |
| `1434` | 0.6249 | 体验AI一句话下单，千问APP发放千万张免单券助力“奶茶自由” |
