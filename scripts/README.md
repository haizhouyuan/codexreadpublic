# scripts

一些辅助脚本（可选），用于把“规格”变成可用的日常工作流：

- `install_skills.sh`：把 `skills-src/` 软链到 `~/.codex/skills/`
- `export_public_repo.py`：导出用于公开代码审查的子集（默认输出到 `state/public-repos/codexreadpublic/`）
- `new_topic.py`：按模板创建 `archives/topics/<topic_id>/`
- `new_robot_update_package.py`：从模板生成一个待审核的机器人变更包 JSON
- `apply_robot_update_package.py`：对已审核变更包导出“可应用清单/提示词补丁”（可选写入 mem0）
- `smoke_test_tasks.sh`：stdio 冒烟测试 `tasks` MCP
- `smoke_test_video_pipeline.sh`：stdio 冒烟测试 `video_pipeline` MCP（dry-run）
- `smoke_test_mem0_memory.sh`：stdio 冒烟测试 `mem0-memory` MCP（需要已配置 mem0 + Qdrant；未启用则自动跳过）
- `smoke_test_glm_models.py`：GLM-4.5-Flash / GLM-4.6V-Flash API 冒烟测试（JSON 输出 + tool call + 4.6V 图片理解；API key 仅允许 env 注入）
- `smoke_test_glm_router.sh`：stdio 冒烟测试 `glm_router` MCP（需要 `BIGMODEL_API_KEY`）
- `smoke_test_glm_router_write_file.sh`：stdio 冒烟测试 `glm_router_write_file`（写文件；仅回传元信息；需要 `BIGMODEL_API_KEY`）
- `run_websearch_router_mcp.sh`：启动 `websearch_router` MCP（会自动加载仓库根目录 `.env`）
- `smoke_test_websearch_router.sh`：stdio 冒烟测试 `websearch_router` MCP（会跑少量真实查询；需要至少一个 provider key）
- `run_source_pack_mcp.sh`：启动 `source_pack` MCP（URL → 证据包落盘；会自动加载仓库根目录 `.env`）
- `smoke_test_source_pack.sh`：stdio 冒烟测试 `source_pack` MCP（默认只跑免费路径；若配置了 Tavily/BigModel key 会额外跑一条 JS-heavy 示例）
- `smoke_test_tmux_orchestrator.sh`：stdio 冒烟测试 `tmux_orchestrator` MCP（ensure/tail/status；需要 `tmux`）
- `websearch_benchmark.py`：对比测试 DashScope(enable_search)/Tavily/Brave 等 Web 搜索后端（落盘 JSON+Markdown 到 `state/tmp/`；用于验证“真实项目问题”的检索质量）
- `fetch_benchmark.py`：对比测试 URL 抓取/抽取效果（local / Jina Reader；可选 Tavily Extract / BigModel reader，可能消耗额度）（落盘到 `state/tmp/`）
- `sogou_weixin_latest.py`：通过搜狗微信搜索抓取某公众号的“最新 N 篇”文章列表（best-effort；纯 HTTP 常会被 `antispider` 拦截，需配合浏览器/CDP 手动验证）
- `sogou_weixin_fetch_latest.py`：用搜狗微信搜索抓“最新 N 篇”并解析 `/link` 的 JS 跳转拿到 `mp.weixin` 真实链接，再用 BigModel `reader` 抓取正文落盘到 `imports/content/wechat/<account>/`
- `run_glm_router_mcp.sh`：启动 `glm_router` MCP（会自动加载仓库根目录 `.env`）
- `glm_write_file.py`：调用 `glm_router_write_file`（stdio）并输出结构化 JSON 结果（适合 worker 脚本集成）
- `websearch_client.py`：调用 `websearch_router_search`（stdio）并输出结构化 JSON 结果（适合 worker 脚本集成）
- `source_pack_client.py`：调用 `source_pack_fetch`（stdio）并输出结构化 JSON 结果（适合 worker 脚本集成）
- `fetch_url_text.py`：抓取 URL 并抽取为纯文本（落盘到 `state/tmp/...`；方便喂给 `glm_router_write_file` 做 digest）
- `topic_run_state.py`：写入 topic/run 级状态与 manifest（`state/topics/<topic_id>/...`；供并行调度与 dashboard 读取）
- `topic_investing_parse.py`：解析 `archives/topics/<topic_id>/investing.md` 的“公司池表格”并输出结构化 JSON（供 worker 批量生成公司卡/任务化缺口）
- `topic_investing_create_tasks.py`：从 `investing.md` 的“关键缺口”列自动创建 investing 类任务（写入 `state/tasks.sqlite`，按 title 去重）
- `bilibili_up_batch.py`：批量下载 B 站 UP 最新 N 条视频（依赖 `yt-dlp`），并用本地 `video_pipeline` 生成证据包（`state/video-analyses/`）
- `video_pipeline_run.py`：本地 `video_pipeline` runner（用于 batch 脚本在 `.venv` 下稳定运行 ASR/OCR）
- `generate_video_digests_from_run.py`：把 `state/runs/bilibili_up_batch/*.json` 转成一组 digest，并可写入 topic 的 `sources.md`
- `mcp_streamable_http_client.py`：最小实现的 MCP streamable-http 客户端（stdlib），供“脚本直接调用 HTTP MCP（如 chatgptMCP）”使用
- `chatgpt_mcp_ask.py`：调用 chatgptMCP（HTTP）里的任意 ask/wait 工具并把答复落盘为 Markdown（供 worker 做质量审计/深研）
- `generate_video_digest_via_web_research.py`：视频证据包（ASR/OCR）→ 研报式 digest（ChatGPT Pro + Gemini Web，经 `chatgpt_web` HTTP MCP）；落盘到 topic 或 `exports/digests/`
- `topic_video_handoff_rerun.py`：按 topic 的 `sources.md` 批量重跑视频 digests（handoff v2：ChatGPT Pro + Gemini 审计），默认覆盖原 digest 文件并写一份汇总 run 记录（供批处理/重跑 30 条视频）
- `rebuild_topic_sources_from_digests.py`：从 topic 的 `digests/` frontmatter 重建 `sources.md`（去重/修复索引）
- `claim_ledger_to_tasks.py`：扫描 digest 的 Claim Ledger，把“未核验 + 数字高影响”的条目转成 `tasks`（写入 `state/tasks.sqlite`）
- `worker_topic_init_glm.sh`：topic 初始化作业（GLM 写入 overview/framework/open_questions；同时写 `state/topics/<topic_id>/...` 运行状态；供 tmux_orchestrator 调度；可选 `ORCH_SCOPE_HINT` 限定研究边界）
- `worker_topic_investing_glm.sh`：投研收敛（investing.md）作业：GLM 生成/更新 `investing.md` → 从缺口列创建 tasks → 走 chatgptMCP 做审计 → 写 run 记录并通知主控
- `topic_generate_company_cards.py`：从 investing.md 公司池中挑前 N 家，批量生成 CFA Company Cards（GLM 写盘；只回传元信息）
- `worker_topic_company_cards_glm.sh`：公司卡作业：调用 `topic_generate_company_cards.py` 批量写公司卡，并用 chatgptMCP 抽样审计
- `worker_topic_decision_package_glm.sh`：决策包作业：WebSearch 找 SEC/官方披露 → source_pack 落证据包 → GLM 写 digest（含 claim_id）→ 生成决策包正文 → decision_gate_check → chatgptMCP 审计 → 写 run 记录并通知主控
- `worker_topic_video_handoff_web.sh`：视频 digests 重跑作业：按 topic 的 `sources.md` 逐条调用 `generate_video_digest_via_web_research.py`（ChatGPT Pro + Gemini），并落盘 run 记录/通知主控
- `tmux_set_controller_pane.sh`：在“主控 pane”里运行，设置 `CODEX_CONTROLLER_PANE`（供 worker 完工通知使用）
- `tmux_notify_controller_done.sh`：worker 完工后通知主控 pane（若主控在跑 Codex CLI 则发送提示消息；Codex 忙时消息会排队；否则注入 `echo ...`）
- `topic_ingest_digest.py`：将某条 digest 挂到 topic（更新 `sources.md`，可选追加 `timeline.md`）
- `run_dashboard.sh`：启动 Research Dashboard（Web，可视化，只读；默认端口 `8787`）

## Examples

### Web search benchmark（液冷相关真实问题）

运行后会在 `state/tmp/websearch_benchmark/` 落盘 `benchmark_*.json` 与 `benchmark_*.md`（`state/` 默认不入 git）。

```bash
python3 scripts/websearch_benchmark.py
```

需要在环境变量或仓库根目录 `.env` 中提供对应 key（不要提交 `.env`）：

- DashScope（enable_search）：`DASHSCOPE_API_KEY` 或 `WEBSEARCH_API_KEY`
- BigModel web_search：`BIGMODEL_API_KEY`
- Tavily：`TAVILY_API_KEY` 或 `tavilyApiKey`
- Brave：`BRAVE_API_KEY` 或 `braveapikey`
- Tongxiao IQS（夸克）：`TONGXIAO_API_KEY`

### URL 抓取/抽取 benchmark（抓取链路验证）

默认只跑免费/本地路径（`local + jina_reader`），输出落盘到 `state/tmp/fetch_benchmark/`：

```bash
./.venv/bin/python scripts/fetch_benchmark.py
```

如需同时对比 Tavily Extract / BigModel reader（会消耗额度），显式指定 fetchers：

```bash
./.venv/bin/python scripts/fetch_benchmark.py --fetchers local,jina_reader,tavily_extract,bigmodel_reader
```

需要在环境变量或 `.env` 中提供 key（不要提交 `.env`）：

- Tavily：`TAVILY_API_KEY` 或 `tavilyApiKey`
- BigModel reader：`BIGMODEL_API_KEY`

### WeChat 公众号抓取（搜狗发现 + BigModel reader）

抓取结果落盘到 `imports/content/wechat/<account>/`（`imports/` 默认不入 git）：

```bash
python3 scripts/sogou_weixin_fetch_latest.py --account capitalwatch --top 3 --pages 4
```

说明：

- 搜狗 `weixin.sogou.com` 可能触发 `antispider`（验证码）；脚本会 best-effort，若被拦截需要改用浏览器/CDP 或降低频率。
- 公众号正文抓取使用 BigModel `reader`，需要 `BIGMODEL_API_KEY`。

### 视频 digest（handoff：ChatGPT Pro + Gemini Web）

前提：你已经在 tmux 启动了单例 HTTP chatgptMCP（见 `web-research-mcp-spec.md` 与 `projects/chatgptMCP/README.md`），并且 `~/.codex/config.toml` 中配置了：

- `mcp_servers.chatgpt_web.url = "http://127.0.0.1:18701/mcp"`

生成 digest（默认写到 `exports/digests/`；指定 `--topic` 则写到 `archives/topics/<topic_id>/digests/` 并记录 run note）：

```bash
python3 scripts/generate_video_digest_via_web_research.py --analysis-id BV1iCmmBLE5k --topic commercial_space
```

提示：
- 默认每次 Web ask 的 `timeout_seconds=1200`（可调大）；ChatGPT 侧发送新 prompt 会在 server 端按 `CHATGPT_MIN_PROMPT_INTERVAL_SECONDS`（推荐 20s）节流，避免触发风控。
- 如只想先验收 ChatGPT 产出，可加 `--no-gemini` 跳过 Gemini 审计步骤。

### 批量重跑一个 topic 的视频 digests（handoff v2）

示例（按 `archives/topics/<topic_id>/sources.md` 的 video 行逐条重跑，并覆盖原 digest 文件；会额外写一份汇总 run 记录）：

```bash
python3 scripts/topic_video_handoff_rerun.py \
  --topic-id bili_up_414609825_touyanxianji \
  --record-path archives/topics/bili_up_414609825_touyanxianji/notes/runs/$(date +%Y-%m-%d_%H%M)_handoff_v2.md
```

### 投研收敛（Investing：公司池/Watchlist/决策包）

1) topic 内投研收敛页（`investing.md`）闸门检查（公司池≥10、至少 1 个 thesis_candidate、investing tasks 达标）：

```bash
python3 scripts/investability_gate_check.py <topic_id>
```

2) 聚合所有 topic 的公司池，生成全局候选池与 watchlist：

```bash
python3 scripts/investing_build_universe.py
```

输出：
- `archives/investing/universe.json`
- `archives/investing/watchlist.md`

3) 新建一个标的的“投资决策包”（骨架）：

```bash
python3 scripts/new_decision_package.py --ticker NVDA --name NVIDIA --topic-id ai_compute --topic-id optical_modules
```

4) 决策闸门检查（Evidence Map 引用 claim_id；证据门槛 Level A/B）：

```bash
python3 scripts/decision_gate_check.py archives/investing/decisions/<file>.md
```
