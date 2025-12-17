# AGENTS.md（本项目：个人助理）

## 项目定位

- 本项目是 **U1 的个人助理**（不局限于投研；投研/行业/技术研究是重要模块之一）。
- 伴学机器人本体在另一个项目中开发；本项目只做：
  - 会话/内容的后处理与沉淀；
  - 为机器人生成“变更包（JSON）”供人工审核后应用；
  - 通过外部 `mem0-memory` MCP 读写长期记忆（本仓库不实现 mem0 MCP）。

## 规格优先（必遵守）

- 任何实现与新增能力必须以规格文件为准：
  - 总规格：`spec.md`
  - Contracts：`tasks-mcp-spec.md`、`video-pipeline-mcp-spec.md`、`glm-router-mcp-spec.md`、`mem0-memory-spec.md`、`robot-update-package-spec.md`、`topic-archive-spec.md`、`privacy-retention-spec.md`
- 如果发现规格缺失/歧义：先在相关 spec 中补齐，再写代码或改工作流。

## 数据与目录约定

- 原始输入：`imports/`（默认不入 git）
  - `imports/child/`：孩子相关输入（敏感）
  - `imports/content/`：文章/转录/报告原文（按需）
  - `imports/content/videos/`：本地视频文件（供 `video_pipeline` 处理，默认不入 git）
- 主题档案：`archives/topics/<topic_id>/`（可长期维护）
- 机器人变更包：`exports/robot-update-packages/`（默认不入 git；必要时只提交 `*.approved.json`）
- 本地状态：`state/`（默认不入 git）
  - `state/video-analyses/`：视频流水线产物（证据包/关键帧/OCR/转写等）

## 工具使用策略

### MCP

- `tasks`：本仓库实现（`mcp-servers/tasks/`），用于任务/行动项管理；SQLite 默认落 `state/tasks.sqlite`。
- `video_pipeline`：本仓库实现（`mcp-servers/video_pipeline/`），用于本地视频后处理（ASR/OCR/证据包）；默认输出到 `state/video-analyses/`。
- `glm_router`：本仓库实现（`mcp-servers/glm_router/`），用于把智谱 GLM 当作“低成本加工工具”（免费→付费回退），不替代 Codex 主模型。
  - 长文/模板化产出优先用 `glm_router_write_file`（写文件、只回传元信息与少量预览），避免长文回流占用主模型上下文。
  - 另见：GLM 作为 `codex exec` 低成本 worker 的用法与边界：`glm-codex-exec-worker.md`（适合一次性小活/并行；质量优先）。
- `websearch_router`：本仓库实现（`mcp-servers/websearch_router/`），用于成本/配额可控的结构化 SERP 搜索（free→quota→paid 回退 + 缓存）。
  - 用途：快速找一手来源（官方/标准/PDF/监管披露）；深度综合仍交给 `web-research`（ChatGPT Pro Deep Research）做。
- `source_pack`：本仓库实现（`mcp-servers/source_pack/`），用于把 URL 抓取并落盘为“证据包”（manifest/raw/text/links），避免“搜到 URL 但抓不到原文→深挖停摆”。
  - 默认只写入 `state/source_packs/`（不入 git）；只回传路径/元信息，不回传长文。
- `tmux_orchestrator`：本仓库实现（`mcp-servers/tmux_orchestrator/`），用于在 tmux 中以“作业方式”确定性调度 worker（创建/下发/查询/tail），避免往 TUI 注入键盘事件。
- `web-research`：外部项目提供（参考 `projects/chatgptMCP`），用于获取公开信息/事实核查/观点调研/Deep Research（见 `web-research-mcp-spec.md`）。
- `mem0-memory`：外部项目提供；本仓库仅对接。
  - `U1_USER_ID` / `CHILD_USER_ID` 原则上由配置注入；本仓库采用“用仓库名做命名空间”的默认约定（可按需覆盖）。
  - 备注：本仓库在 `mcp-servers/mem0_memory/` 提供了一个可选的参考实现（极薄 wrapper），可复用你现有的 mem0+Qdrant 配置。
  - 命名约定（按你确认，“用仓库名做命名空间”）：
    - `MEM0_AGENT_ID=codexread`
    - `U1_USER_ID=family_u1`
    - `CHILD_USER_ID=family_child`
  - 若要与 `projects/homeagent` 共享同一条长期记忆：建议把 `U1_USER_ID/CHILD_USER_ID` 改为“人”的稳定 ID，并在两个项目里保持一致；并明确 `MEM0_AGENT_ID` 的隔离/共享策略（见 `mem0-memory-spec.md` 的“跨项目协商与统一”）。

### 内置 Tools

- `workspace/shell/python`：用于读写档案、生成摘要、创建模板文件等小工具。
- `web_search`：只在需要一手信息时使用，并遵守 `spec.md` 的信息源优先级与引用规范。

## 安全与隐私（必遵守）

- 孩子数据与任何可识别信息按 `privacy-retention-spec.md` 执行：
  - 默认不记录逐字原文到可提交文件；
  - 输出以摘要/结构化结论为主；
  - 机器人侧更新必须走“变更包（JSON）+ 人工审核”，禁止自动应用。
- 凭证（API key/token/cookie）不得写入仓库；只允许环境变量注入。

## 输出规范（强约束）

- 对外部事实性信息：给出来源清单 + 不确定性提示。
- 主题研究：优先更新 `archives/topics/<topic_id>/`，并在必要时创建/更新 `tasks`。
- 投研收敛（投资）：在 topic 内产出 `investing.md`（细分赛道→公司池→KPI→催化剂→风险→下一步核验），并聚合到全局 `archives/investing/`：
  - `scripts/investability_gate_check.py`：Investability Gate（公司池≥10、至少 1 个 thesis_candidate、核验 tasks 达标）
  - `scripts/investing_build_universe.py`：生成 `archives/investing/universe.json` + `archives/investing/watchlist.md`
  - `decision-package-spec.md`：投资决策包规范（Evidence Map 引用 claim_id；通过 Decision Gate 后再进入 reviewed/active）
- 机器人更新：只生成 `exports/robot-update-packages/*.json`（`review.status="pending"`），不直接修改机器人项目。
- 视频处理：只在 `state/video-analyses/` 生成证据包与中间产物；结构约定见 `video-pipeline-mcp-spec.md`。
- 多 tmux worker 并行时：每个 worker 完工后建议落一份 `archives/topics/<topic_id>/notes/runs/...` 的 run 记录，并用 `scripts/tmux_notify_controller_done.sh` 通知主控 pane（主控 pane 先跑 `scripts/tmux_set_controller_pane.sh` 设置 `CODEX_CONTROLLER_PANE`）。
  - 备注：如果主控 pane 正在运行/忙于 Codex 任务，通知消息会在 Codex CLI 内排队，待当前运行结束后再处理，这是正常行为。
