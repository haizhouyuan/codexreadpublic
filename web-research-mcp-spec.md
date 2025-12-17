# web-research MCP 规格（V1 草稿）

## 1. 角色与作用

`web-research` MCP 用于帮助本项目在需要时获取/核验**公开信息**，并把“观点调研 / 事实核查 / 主题研究”从一次性问答升级为可追溯、可复用的研究资产（tasks + topic archive + mem0）。

本仓库不实现该 MCP，参考实现位于你的另一个仓库（`projects/chatgptMCP`）：

- 通过 Playwright 连接到已登录的 Chrome（CDP）自动化 **ChatGPT Web**（`chatgpt.com`）
- 自动化 **Gemini Web**
- 直接调用 **Gemini API**（无需网页，稳定性更高）

> 本规格重点定义“工具接口 + 使用约束 + 研究工作流要求”，以便后续可替换实现（例如换成其他浏览器自动化或其他信息源）。

## 2. 安全与隐私（强制）

### 2.1 不绕过风控/验证码

- 不要尝试绕过/规避 Cloudflare、Google 验证码或任何风控。
- 若遇到验证/登录问题：提示你在 noVNC/真实浏览器里手动处理后再继续。

### 2.2 速率与稳定性

- 禁止高频点击/连续重试；一次工具调用结束后再发下一次。
- 为降低风控概率：两次“发送新 prompt”（例如 `chatgpt_web_ask*` / `gemini_web_ask*`）之间应留出**明确时间间隔**（建议 12–20 秒 + 随机抖动；多 agent 建议默认 20 秒）；尽量用 `*_wait` 拉取结果而不是重发 prompt。参考实现可用 `CHATGPT_MIN_PROMPT_INTERVAL_SECONDS` 在 server 侧做最小间隔保护。
- 如果在同一工作流里交替使用 ChatGPT Web 与 Gemini（Web/API）：仍应避免“来回快速发问”，建议至少间隔约 `10s` 再发送下一条新 prompt（更保守可沿用 12–20 秒）。
- Deep research 可能需要长等待：应优先增加 `timeout_seconds`，并在同一会话上用 `wait` 拉取最终报告。

### 2.2.1 多 agent 并发的“统一节流”（强推荐）

- 推荐方式：运行**单例 HTTP MCP server**（`streamable-http`），所有 Codex/agent 连接同一个 `url`，由 server 侧统一：
  - 串行化对 ChatGPT Web 的交互；
  - 强制最小 prompt 间隔（如 `CHATGPT_MIN_PROMPT_INTERVAL_SECONDS=20`）。
- 不推荐但可兜底：如果确实需要“每个 agent 启一个 stdio MCP server”，至少启用跨进程的全局互斥/节流（参考实现可用 `CHATGPT_GLOBAL_RATE_LIMIT_FILE` / `CHATGPT_GLOBAL_LOCK_FILE`）。

### 2.3 数据最小化（尤其是儿童）

- **禁止**向 web-research 发送：
  - P3：孩子逐字对话、可识别信息、画像原文等
  - CRED：任何 token/cookie/key/登录态信息
- P2（投研敏感细节、账户/资金/交易等）默认也不发送；如确有需要，必须先脱敏并明确告知风险。
- web-research 的输入应尽量是“研究问题/关键词/公开信息范围”，不要包含与你/孩子强绑定的私密上下文。

### 2.4 产物与落盘

- 浏览器自动化产生的调试产物（截图/HTML dump/日志）默认视为敏感（P2+），必须落在 `state/` 或外部目录，并确保不入 Git（见 `privacy-retention-spec.md`）。
- 研究结论进入本仓库时，必须按 `spec.md` 的信息源优先级与引用规范输出“来源清单 + 不确定性提示”。

### 2.5 调用审计日志（可选，但建议开启）

- 建议在 web-research MCP server 侧开启 JSONL 审计日志，便于长期维护与复盘：
  - `MCP_CALL_LOG=.../mcp_calls.jsonl`
- 默认**不落** prompt/answer（避免敏感信息落盘）；需要时再开启：
  - `MCP_CALL_LOG_INCLUDE_PROMPTS=1`
  - `MCP_CALL_LOG_INCLUDE_ANSWERS=1`
- 审计日志与调试产物同样按敏感数据处理：必须落在 `state/` 或外部目录，默认不入 git（见 `privacy-retention-spec.md`）。

## 3. 工具分组（参考实现：chatgptMCP）

> 以下工具名为参考实现暴露的 tool name；在 Codex CLI 中实际函数名通常形如 `mcp__<server>__<tool>`。

### 3.1 ChatGPT Web（`chatgpt_web_*`）

#### 3.1.1 `chatgpt_web_ask`

用途：用 ChatGPT Web 进行问答；可选切模型、开启 Deep research、启用 GitHub connector。

请求参数（逻辑结构）：

```json
{
  "question": "用公开信息总结 SpaceX 星舰 2024-2025 的关键技术里程碑与风险，并给出处链接。",
  "conversation_url": null,
  "timeout_seconds": 600,
  "model": "5.2 pro",
  "thinking_time": "extended",
  "deep_research": true,
  "web_search": false,
  "github_repo": null
}
```

返回值（逻辑结构）：

```json
{
  "answer": "…",
  "status": "completed",
  "conversation_url": "https://chatgpt.com/c/...",
  "elapsed_seconds": 123.45
}
```

`status`：
- `completed`：已得到可用最终回答
- `needs_followup`：Deep research 先追问（需继续在同一 `conversation_url` 回答追问）
- `in_progress`：Deep research 已开始但报告未完成（需用 `chatgpt_web_wait` 等待）

#### 3.1.2 `chatgpt_web_wait`

用途：不发送新问题，仅等待某个 `conversation_url` 的最新输出稳定（适合 Deep research 长报告）。

```json
{
  "conversation_url": "https://chatgpt.com/c/...",
  "timeout_seconds": 900,
  "min_chars": 800
}
```

返回结构同 `chatgpt_web_ask`。

#### 3.1.3 快捷工具（推荐）

- `chatgpt_web_ask_pro_extended`
- `chatgpt_web_ask_deep_research`
- `chatgpt_web_ask_web_search`
- `chatgpt_web_ask_thinking_heavy_github`

### 3.2 Gemini Web（`gemini_web_*`）

用于：需要 Gemini Web UI 的特定能力（例如网页 Deep Research 或网页生图）。可能遇到 Google 验证；不要绕过。

工具：

- `gemini_web_ask`
- `gemini_web_ask_pro_thinking`
- `gemini_web_deep_research`
- `gemini_web_wait`
- `gemini_web_generate_image`（可选）

### 3.3 Gemini API（`gemini_*`）

优点：不依赖网页 UI，不易被验证码/改版影响；适合稳定的“研究/总结/深度检索”能力。

工具：

- `gemini_ask_pro_thinking`
- `gemini_deep_research`
- `gemini_generate_image`（可选）

## 4. Deep Research 标准流程（强推荐）

以 ChatGPT Deep research 为例（Gemini Web 类似）：

1. 发起研究：调用 `chatgpt_web_ask_deep_research(question=...)`，读取 `status` 与 `conversation_url`。
2. 若 `status=needs_followup`：
   - 把追问当成“用户问题”来回答；
   - 在同一 `conversation_url` 上调用 `chatgpt_web_ask(question=..., conversation_url=...)`。
3. 若 `status=in_progress`：
   - 调用 `chatgpt_web_wait(conversation_url=..., timeout_seconds=..., min_chars=...)`，直到 `status=completed` 或超时。
4. 对最终报告执行本项目的“二次加工”（见第 5 节）。

## 5. 本项目的“二次加工”要求（面向投研/主题研究）

web-research 的输出必须经过本项目二次加工后才能进入长期资产：

1. **Claim Ledger（断言清单）**
   - 把重要事实性陈述拆成条目：`claim / 影响范围 / 置信度 / 需要核验的点 / 来源链接`。
2. **来源清单与优先级**
   - 对关键 claim，优先补上：官方/监管披露、公司公告、一手材料等来源。
   - 备注（ChatGPT Deep research 的引用显示）：部分 UI 会把引用以“域名 pill（如 `nature.com`）”呈现，正文里可能看不到完整 URL。此时应：
     - 优先在同一 `conversation_url` 里追问“只输出 Sources（完整 URL）”；或
     - 从 MCP 的调试产物（`CHATGPT_DEBUG_DIR`/`MCP_DEBUG_DIR` 的 HTML dump）中提取 `a[href]`；或
     - 将该条 claim 标注为 `unverified` 并创建“补齐一手 URL”任务。
3. **生成可执行 tasks**
   - 对“高影响且未核验”的 claim，创建 `tasks`（source=web_research），用于后续核验与阅读原文。
4. **归档到 topic（可选但推荐）**
   - 将来源链接与关键结论更新到 `archives/topics/<topic_id>/`（`sources.md`/`timeline.md`/`open_questions.md`）。
5. **写入 mem0（谨慎）**
   - 只写“长期稳定的框架/原则/结论”，不要写整段网页回答，更不要写 P2/P3 敏感细节。

## 6. Codex 配置约定（本仓库建议）

- 推荐 MCP server 名称：`chatgpt_web`（与参考实现的工具前缀一致）。
- 参考配置见：`examples/codex/config.toml`（本仓库侧仅提供接入示例；实际路径按你机器调整）。
