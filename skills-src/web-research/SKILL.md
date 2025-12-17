---
name: web-research
description: 基于公开信息做事实核查/观点调研/Deep Research，并产出可追溯的“claim 清单 + 来源 + tasks +（可选）topic 更新”（M2/M3 支撑能力）。
allowed-tools:
  - chatgpt_web
  - websearch_router
  - web_search
  - tasks
  - mem0-memory
  - glm_router
  - workspace
  - shell
---

# 触发条件

当用户表达以下意图时使用本技能：

- “帮我查一下/核实一下某个事实/数据”
- “帮我调查各方观点/争议点”
- “做一个主题的快速研究/深度研究（Deep Research）”
- “给我一份带来源的研究笔记，并生成后续核验任务”

# 目标

1. 输出一份可归档的研究记录（Markdown）：
   - 若提供 `topic_id`：`archives/topics/<topic_id>/digests/`
   - 否则：`exports/digests/`
2. 生成 Claim Ledger（断言清单）与来源清单（URL/出处），并对高影响且未核验的点创建 `tasks`。
3. （可选）把阶段性、长期稳定的结论写入 mem0（`user_id=U1_USER_ID`）。

# 硬约束（安全与隐私）

- 禁止把孩子逐字对话、可识别信息（P3）或任何凭证（CRED）发到 web-research（包括 ChatGPT/Gemini Web/API）。
- 避免触发风控：若需要连续多次调用 `chatgpt_web_ask*` / `gemini_web_ask*`，两次“发送新 prompt”之间**必须留出时间间隔**（建议默认 20 秒；如需更高吞吐可在 12–20 秒范围内调小并加随机抖动），尽量合并问题、减少重试；长任务优先用 `chatgpt_web_wait` 等待。若你可控 MCP server env，建议设置 `CHATGPT_MIN_PROMPT_INTERVAL_SECONDS` 做硬保护。
- 若在同一流程里交替使用 ChatGPT Web 与 Gemini（Web/API），仍按“单线程 + 间隔”原则执行（建议至少 ~10s 再发下一条 prompt，或沿用 12–20 秒的保守值）。
- 研究输入要“最小化”：只给公开问题/关键词/范围，不要塞个人隐私上下文。
- 外部事实必须给出来源；无法给出处时要明确标注“未核验”，并创建核验任务。

# 操作步骤（SOP）

1. **澄清研究问题（最多 3 个问题）**
   - 要解决的核心问题是什么？（事实核查 / 观点调研 / 主题综述）
   - 需要的深度：快速（5–10 分钟）还是 Deep Research（长报告）？
   - 是否关联某个 `topic_id`（例如 `space_industry`）？

2. **双轨信息获取（默认策略：ChatGPT Pro + WebSearch 并行）**
   - 把 ChatGPT Pro 当作“顾问/老师/审计员”，把 `websearch_router` 当作“快速找一手来源的工具”：
     - **行动前（顾问）**：先发起 ChatGPT Pro（或 Deep Research）让它给出研究框架/关键问题/必须拿到的一手来源清单。
     - **行动中（老师）**：在等待 ChatGPT 输出期间，用 `websearch_router_search` 把“官方/标准/白皮书/监管披露”链接快速拉出来；必要时把候选来源交给 ChatGPT 追问补齐口径。
     - **行动后（审计员）**：当你已写出结论/Claim Ledger 后，再让 ChatGPT Pro 审查：找缺失证据、矛盾点、需要核验的任务清单。
   - 成本/配额约束（由 `websearch_router` 服务端执行）：
     - 默认 `allow_paid=false`（只用 free→quota），只有当“问题高价值且 free/quota 召回不足”才允许 `allow_paid=true`。

3. **行动前：启动 ChatGPT Pro（顾问）**
   - 快速检索（需要“联网搜索”且要快）：优先 `chatgpt_web_ask_web_search(question=...)`，让它输出：
     - 3–7 条关键结论候选（标注不确定性）
     - Sources（完整 URL，优先 Level A）
     - 下一步核验动作（可任务化）
   - 系统深研（长报告）：`chatgpt_web_ask_deep_research(question=...)`，读取 `status`/`conversation_url`：
     - `status=needs_followup`：在同一 `conversation_url` 上继续回答追问（`chatgpt_web_ask`）
     - `status=in_progress`：用 `chatgpt_web_wait(conversation_url, min_chars>=800)` 等到长报告
   - 注意：为避免风控，连续多次“发送新 prompt”必须留出间隔；长任务优先用 `chatgpt_web_wait` 拉结果。

4. **行动中：快速找一手来源（WebSearch）**
   - 用 `websearch_router_search(query=..., allow_paid=false)` 快速找：
     - 官方/监管披露（SEC/FAA/NASA/DoD/行业标准组织）
     - 标准/白皮书 PDF（ASHRAE/OCP/IEEE/IEC 等）
     - 公司公告/技术文档（厂商官网、PDF、investor relations）
   - 若 `websearch_router` MCP 不可用：退化为内置 `web_search` 做少量 SERP（只用于兜底找链接，不直接当事实来源）。
   - 产出要求：
     - 把“候选来源 URL 清单”写进研究记录的 Sources 区；
     - 对关键数字/口径，优先拉到 Level A（否则标 `unverified` + 建 tasks）。

5. **二次加工（关键）：形成可归档产物**
   - 用 `templates/research.md` 的结构组织输出，至少包含：
     - TL;DR（结论与不确定性）
     - Claim Ledger（断言清单：每条 claim 的来源链接、置信度、是否需要核验）
     - Sources（来源清单，优先官方/一手）
     - Next actions（下一步核验/阅读/补数据）
   - 若需要“写较长正文”且已启用 `glm_router`：优先用 `glm_router_write_file` 写文件（只回传路径/校验结果），避免把长文回流占用主模型上下文。
   - 若 ChatGPT Deep Research 的引用只显示域名 pill：优先在同一 `conversation_url` 追问“只输出 Sources（完整 URL）”；或从调试 HTML dump 提取 `a[href]`；无法补齐则把该 claim 标记为 `unverified` 并创建核验任务。

6. **创建 tasks（高影响未核验）**
   - 对每条高影响 claim（尤其是数字/政策/财报/口径），创建 `tasks.create_task`：
     - `category=investing|tech`
     - `topic_id=<topic_id>`（如有）
     - `source=web_research`
     - title 以“核验/阅读原文/找官方口径”为主

7. **行动后：让 ChatGPT Pro 做审计（强推荐）**
   - 在你写完 TL;DR + Claim Ledger 后，再用 `chatgpt_web_ask_pro_extended` 追问一次：
     - “找出结论中证据不足/自相矛盾/需要补齐的一手来源”
     - “给出核验优先级（Top 5）与建议任务标题”
   - 把它的审计意见写回 `Next actions` 与 `open_questions`（如有 topic）。

8. **落盘**
   - 文件名建议：`YYYY-MM-DD_web_research_<slug>.md`
   - 目录：topic 优先，其次 `exports/digests/`。

9. **写入 mem0（可选，谨慎）**
   - 只写长期稳定的框架/原则/洞见（不要写长报告全文）。
   - `mem0-memory.add_memory(user_id=U1_USER_ID, kind=topic_insight|investing_thesis, topic=<topic_id>)`

# 输出格式

- `research_path`: `<path>`
- `tasks_created`: 列表（`id`, `title`）
- `mem0_updates`: 列表（`kind`, `topic`, `summary`）
