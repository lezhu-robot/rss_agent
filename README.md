# RSS Agent

基于 LangGraph 和飞书的新闻订阅机器人，用于按订阅分类生成和推送日报。

## 项目说明

- 支持按分类订阅新闻内容，例如 `AI`、`GAMES`、`MUSIC`
- 通过飞书机器人接收消息、管理订阅和获取日报
- 服务以 Docker 容器方式运行，数据库数据持久化到本地目录

## 运行前准备

部署前请确认环境具备以下条件：

- 已安装 Docker
- 已安装 Docker Compose
- 已准备好飞书应用凭证
- 已准备好可用的 LLM API Key 和模型配置

## 环境变量

在项目根目录创建 `.env` 文件，并填写以下配置：

```ini
OPENAI_API_KEY=your_api_key
OPENAI_API_BASE=https://your-api-base
LLM_FAST_MODEL=your_fast_model
LLM_REASONING_MODEL=your_reasoning_model
LARK_APP_ID=your_lark_app_id
LARK_APP_SECRET=your_lark_app_secret
```

## Docker 部署

### 1. 启动服务

```bash
docker compose down
docker compose up -d --build
docker compose exec rss-agent python manual_trigger.py
```

服务启动后，容器会监听 `36000` 端口。

### 2. 查看运行状态

```bash
docker compose ps
```

## 验证服务

可以通过以下命令验证服务是否正常启动：

```bash
curl http://localhost:36000/
```

正常情况下会返回服务状态信息。

如果需要查看日志：

```bash
docker logs rss-agent
```

## 飞书事件订阅配置

飞书事件回调路径为：

```text
/api/lark/event
```

在飞书开放平台中，将事件订阅地址配置为你的实际可访问服务地址，例如：

```text
https://your-domain/api/lark/event
```

说明：

- 生产环境请使用你自己的域名、反向代理或云端入口暴露该服务

## 数据持久化

项目通过 `docker-compose.yml` 挂载本地目录保存数据：

- `./data:/app/data`
- `./group_config.json:/app/group_config.json`
- `./group_runtime.json:/app/group_runtime.json`

SQLite 数据库文件会保存在 `data` 目录下，容器重建后仍会保留。
群推送配置和运行时进度也会与宿主机本地文件自动同步，便于直接在仓库内查看和编辑。

## 常用命令

启动服务：

```bash
docker compose up -d
```

停止服务：

```bash
docker compose down
```

重新构建并启动：

```bash
docker compose up -d --build
```

查看容器日志：

```bash
docker logs -f rss-agent
```
