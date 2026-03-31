# RSS Agent 迁移报告

生成时间：2026-03-24

## 1. 迁移目标

将 RSS Agent 项目从原服务器迁移到新服务器，并在新服务器上完成服务启动与可用性验证。

## 2. 迁移信息

- 项目名称：`rss_agent`
- GitHub 仓库：`https://github.com/cantaible/rss_agent.git`
- 迁移分支：`main`
- 原项目目录：`/root/rss_agent`
- 新服务器地址：`43.165.175.44`
- 新服务器用户：`ubuntu`
- 新服务器项目目录：`/home/ubuntu/workspace/rss_agent`

## 3. 迁移前确认

迁移前已确认以下事实：

- 本地仓库远程地址为 `origin = https://github.com/cantaible/rss_agent.git`
- 当前分支为 `main`
- 本地代码与 GitHub `origin/main` 一致
- 本地存在运行时状态变更：`group_runtime.json`
- 新服务器已安装并可直接使用 Docker
- 新服务器 Docker 版本：`28.2.2`
- 新服务器 Docker Compose 版本：`2.37.1`

## 4. 本次迁移采用的方式

本次迁移采用“Git 拉取代码 + 手动同步运行时文件”的方式完成。

原因如下：

- 代码与静态配置适合通过 GitHub 仓库同步
- 环境变量文件 `.env` 不在 Git 管理中，需要单独迁移
- SQLite 数据库文件 `data/rss_agent.db` 不在 Git 管理中，需要单独迁移
- `group_runtime.json` 虽在 Git 中，但本地运行状态比远端仓库更新，因此也需要单独覆盖同步

## 5. 实际迁移内容

### 5.1 通过 Git 同步的内容

以下内容通过 GitHub 仓库迁移至新服务器：

- 项目代码
- `Dockerfile`
- `docker-compose.yml`
- `group_config.json`
- 其他已纳入 Git 管理的配置与脚本

### 5.2 手动同步的内容

以下内容由原服务器手动复制到新服务器：

- `.env`
- `group_runtime.json`
- `data/rss_agent.db`

## 6. 实际执行过程

本次迁移执行顺序如下：

1. 检查本地 Git 状态，确认代码已与 `origin/main` 同步
2. 确认新服务器 Docker / Docker Compose 可正常使用
3. 停止原服务器上的 `rss-agent` 容器，避免双机同时推送
4. 在新服务器 `43.165.175.44` 上克隆 GitHub 仓库到 `/home/ubuntu/workspace/rss_agent`
5. 在新服务器上创建 `data` 目录
6. 将 `.env`、`group_runtime.json`、`data/rss_agent.db` 同步到新服务器对应目录
7. 在新服务器执行 `docker compose up -d --build`
8. 通过容器状态、日志和 HTTP 健康检查验证服务可用

## 7. 启动与验证结果

迁移完成后，新服务器服务启动成功。

验证结果如下：

- 容器名称：`rss-agent`
- 容器状态：已启动
- 对外端口：`36000`
- 健康检查接口：`http://localhost:36000/`
- 返回结果：

```json
{"status":"ok","message":"Bot is running! (机器人正在运行)"}
```

应用日志中已确认：

- Fast LLM 初始化成功
- Reasoning LLM 初始化成功
- Uvicorn 已监听 `0.0.0.0:36000`
- 应用启动完成

## 8. 迁移后的当前状态

截至 2026-03-24，本次服务迁移已完成，应用已在新服务器正常启动。

当前状态说明：

- 原服务器容器已停止
- 新服务器容器已运行
- 项目代码、数据库、环境变量、群配置、运行时状态均已同步到新服务器
- 新服务器项目最终目录为 `/home/ubuntu/workspace/rss_agent`

## 9. 后续建议事项

虽然服务本体已迁移完成，但仍建议继续确认以下事项：

- 检查飞书事件订阅回调地址是否已切换到新服务器入口
- 检查域名、反向代理、隧道或公网入口是否仍指向旧服务器
- 观察一次实际消息收发和定时推送，确认外部访问链路完整
- 后续建议改用 SSH Key 登录，避免继续使用密码方式运维

## 10. 结论

本次 RSS Agent 项目已于 2026-03-24 成功迁移到新服务器 `43.165.175.44`，项目最终部署目录为 `/home/ubuntu/workspace/rss_agent`，应用已完成构建、启动和健康检查，具备继续运行条件。
