# Research Dashboard（Web）规格（V1 草稿）

## 1. 目的

提供一个**只读** Web 界面，用于把本仓库的研究资产可视化，方便 U1：

- 浏览主题档案（Topic Archive）的框架、结论、资料清单与时间线；
- 浏览 digests（含 claim ledger）与来源；
- 浏览与该主题相关的任务（tasks）进度，形成“研究过程 → 结论 → 下一步行动”的闭环视图。

运行目标端口：`8787`（可配置）。

## 2. 安全与隐私（强制）

1) **默认只读**
- 不提供写入能力（不修改文件、不写入 mem0、不创建 tasks）。
- 所有可视化仅从本地文件系统与本地 SQLite 读取。

2) **访问控制**
- 若服务监听在非回环地址（例如 `0.0.0.0`）或对外网开放：必须启用认证（Basic 或 Bearer token）。
- 建议同时在反向代理层（Caddy/Nginx/Traefik）启用 TLS 与额外认证/限流。

3) **目录白名单**
- 只允许读取：
  - `archives/topics/**`（主题档案与 digests）
  - `archives/investing/**`（全局 watchlist/universe/decisions）
  - （可选）`exports/digests/**`
  - （可选）`state/tasks.sqlite`（任务进度）
  - （可选）`state/topics/**`（每个 topic 的 workflow 状态与 run manifest；仅用于监控视图）
- **禁止**提供对 `imports/**`、`logs/**`、以及 `state/**` 的通用文件浏览/下载能力（除上面列出的特定白名单路径）。

4) **内容最小化**
- Dashboard 默认不展示 P3（儿童敏感数据）与任何凭证（CRED）。
- 如果未来引入“机器人变更包”浏览：必须明确标注敏感级别，并默认隐藏/需要额外确认。

## 3. 信息架构（V1）

### 3.1 全局

- `/`：Topic 列表
- `/workflow`：工作流监控（读取 `state/topics/*/status.json`）
- `/topics/<topic_id>`：Topic 总览（overview）
- `/investing/watchlist`：全局 Watchlist（从 `archives/investing/watchlist.md` 读取）
- `/investing/decisions`：决策包列表（读取 `archives/investing/decisions/*.md`）

### 3.2 单个 Topic（推荐导航项）

- Overview：`archives/topics/<topic_id>/overview.md`
- Framework：`archives/topics/<topic_id>/framework.md`
- Investing：`archives/topics/<topic_id>/investing.md`（投研收敛页；可选但推荐）
- Sources：`archives/topics/<topic_id>/sources.md`
- Timeline：`archives/topics/<topic_id>/timeline.md`
- Open questions：`archives/topics/<topic_id>/open_questions.md`
- Digests：`archives/topics/<topic_id>/digests/` 列表与详情页
- Tasks（可选）：从 `state/tasks.sqlite` 读取并按 `topic_id` 过滤
- Runs：`archives/topics/<topic_id>/notes/runs/` 列表与详情页（用于回放/审计每次 worker run）

## 4. 配置约定（V1）

通过环境变量控制（建议）：

- `CODEXREAD_DASH_HOST`：默认 `127.0.0.1`
- `CODEXREAD_DASH_PORT`：默认 `8787`
- `CODEXREAD_DASH_TOPICS_ROOT`：默认 `archives/topics`
- `CODEXREAD_DASH_INVESTING_ROOT`：默认 `archives/investing`
- `CODEXREAD_DASH_TASKS_DB`：默认 `state/tasks.sqlite`
- `CODEXREAD_DASH_STATE_ROOT`：默认 `state`（用于 workflow 监控读取 `state/topics/*/status.json`）

认证（满足其一即可启用）：

- Basic auth：
  - `CODEXREAD_DASH_BASIC_USER`
  - `CODEXREAD_DASH_BASIC_PASS`
- Bearer token：
  - `CODEXREAD_DASH_TOKEN`

安全策略（建议默认）：

- 若 `CODEXREAD_DASH_HOST != 127.0.0.1` 且未设置任何认证：启动时报错并拒绝启动。

## 5. 非目标（V1 不做）

- 知识库问答（RAG）
- 发送问题自动开始 task / 自动调用 MCP
- 用户体系、多人协作、权限细分
- 任意目录浏览器（file explorer）

## 6. 后续扩展（V2+ 方向）

- “研究行动”面板：从页面触发 `tasks.create_task`、`web-research`、`topic-ingest` 等工作流（仍需 review-first 与权限控制）
- Topic 内搜索与 tags/实体聚类视图（基于 digest frontmatter）
- 对“投资决策包”的结构化呈现（thesis / risks / catalysts / KPIs / monitoring plan）
