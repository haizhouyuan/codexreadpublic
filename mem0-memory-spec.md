# mem0-memory MCP 规格（V1 草稿）

## 1. 角色与作用

- `mem0-memory` MCP 是本项目的统一长期记忆接口，用于读写 mem0 / OpenMemory。
- 在本项目中的主要用途：
  - 为 **U1 自身** 维护长期记忆（观念、原则、投研结论、主题研究进展、育儿反思等）。
  - 为 **孩子相关** 的观察与总结提供结构化记忆（供你和伴学机器人项目共用）。
- 本 MCP 不关心 mem0 的具体存储形式，仅通过 HTTP/API 或 SDK 与 mem0 通讯。

> 备注：本仓库在 `mcp-servers/mem0_memory/` 提供了一个**可选的参考实现**（极薄 wrapper，stdio transport），用于在 Codex CLI 中直接接入 mem0（复用 `projects/homeagent` 的 mem0+Qdrant 配置也可行）。

## 2. 记忆主体（user_id）约定

- `user_id` 必须显式区分不同主体，初期至少有两个：
  - `U1_USER_ID`（占位符；实际值从配置注入）  
    - 含义：关于你自己的长期记忆（价值观、偏好、投研框架、技术理解、人生决策反思等）。
  - `CHILD_USER_ID`（占位符；实际值从配置注入）  
    - 含义：关于孩子的长期记忆（兴趣、情绪模式、社交状态、有效/无效的互动方式等）。

- 本仓库默认命名约定（按“用仓库名做命名空间”）：
  - `U1_USER_ID=family_u1`
  - `CHILD_USER_ID=family_child`
  - `agent_id=codexread`（写入 metadata，用于多项目隔离；对应 `MEM0_AGENT_ID=codexread`）

- 后续如果有需要，可以扩展更多命名空间，例如：
  - `U1_investing`（只存投研相关记忆，作为 `U1_USER_ID` 的子空间）
  - `CHILD_USER_ID_school`（偏学业相关）

### 2.1 可选：用 `agent_id` 做多 Agent 隔离

当多个 agent/项目共用同一个 mem0 后端时，建议额外引入 `agent_id`（写入为 metadata，查询时作为过滤条件）：

- 好处：`user_id` 只表达“主体”（U1/孩子），`agent_id` 表达“来源/用途”（homeagent_brain、codexread 等）。
- 约定：不强制；若你的 mem0 MCP 实现支持，请在 `add_memory/search_memory` 参数中透传 `agent_id` 并写入 metadata。

### 2.2 跨项目统一建议：`user_id` 表达“人”，不是“设备”

在 `projects/homeagent` 的 brain_server 里，`user_id` 默认可能取 device_id（每台设备一条记忆线）；但如果你的目标是让 **codexread ↔ homeagent** 真正共享同一条长期记忆，建议把 `user_id` 统一为“主体 ID（人）”，并把 device_id 作为单独字段传递/落盘（短期会话/日志用途）。

推荐做法：

- U1：使用一个稳定且不含 PII 的 `U1_USER_ID`（例如 `u1` 或 `family_u1`）。
- 孩子：使用一个稳定且不含 PII 的 `CHILD_USER_ID`（例如 `child` 或 `family_child`）。
- homeagent 网关侧：尽量以 `X-User-Id`（或 OpenAI body 的 `user` 字段）传入上述稳定 ID，而不是 device_id。

## 3. 记忆条目统一结构

> 内部存储结构由 mem0 决定，这里定义的是本项目在使用时的逻辑字段，用于写入/读取时遵循。

记忆条目逻辑字段（概念模型）：

- `id`：字符串，mem0 生成的唯一 ID（读取时获得，写入时可为空）。
- `user_id`：字符串，见上。
- `kind`：字符串，记忆类型，初期建议枚举：
  - `investing_thesis`：某公司/赛道/主题的投资结论或重要假设；
  - `topic_insight`：某行业/技术/主题的关键洞见；
  - `personal_principle`：你的行为/决策原则；
  - `reflection`：自我反思或决策复盘；
  - `child_observation`：对孩子的具体观察（某一行为/事件的记录与理解）；
  - `parenting_guideline`：你对“如何与孩子相处/教育”的稳定原则；
  - 预留：`other_*`。
- `topic`：字符串（可选），简短主题名：
  - 例：`"NVDA_long_term"`, `"space_industry"`, `"child_emotions"`, `"math_learning"`。
- `content`：字符串，记忆正文（已经是压缩过的“结论/原则/洞见”，不是满篇原文）。
- `source`：字符串，用于标记来源：
  - 例：`"manual_note"`, `"article_digest"`, `"research_session"`, `"child_chat_summary"`。
- `related_entities`：字符串数组（可选），与公司/技术/主题相关：
  - 例：`["NVDA", "HBM", "TSMC"]`、`["spaceX", "reusable_rockets"]`。
- `tags`：字符串数组（可选），自由标签（领域/情绪等）。
- `created_at` / `updated_at`：时间戳（由 mem0 或本项目统一格式处理）。

## 4. MCP 工具列表

在 `mem0-memory` MCP server 中暴露的工具（初版）：

1. `add_memory`
2. `search_memory`
3. （可选）`list_memories`（分页浏览某主题/主体）
4. （可选）`update_memory`（后续版本再考虑）

当前本项目**必需**的是：`add_memory` 和 `search_memory`。

## 5. 工具：`add_memory`

**用途**：向 mem0 写入一条新的长期记忆（或让 mem0 自行合并到已有记忆）。  
**调用场景**：

- M1 碎片捕获后，确定某条内容值得长期保留；
- M2 内容消化后，抽取出的“精华结论/原则”；
- M3 行业/主题研究阶段性总结；
- M4 孩子成长观察与育儿指南的升级。

### 5.1 请求参数（逻辑结构）

```json
{
  "user_id": "U1_USER_ID",
  "kind": "investing_thesis",
  "topic": "space_industry",
  "content": "关于航天发射成本，当前我的核心判断是：……",
  "source": "research_session",
  "agent_id": "codexread",
  "related_entities": ["spaceX", "ULA"],
  "tags": ["space", "cost_structure", "long_term"]
}
```

- 所有字段均为高层语义要求，具体字段如何映射到 mem0 的 API，可在实现时适配。
- `agent_id`：可选；用于多 agent 共享后端时的命名空间隔离（建议写入 metadata）。

### 5.2 返回值（逻辑结构）

```json
{
  "id": "mem_123456",
  "status": "ok"
}
```

或在 mem0 不返回 ID 时，`id` 可为 `null` 或省略。

### 5.3 行为约束

- 调用前，应尽量将原始内容压缩为清晰的记忆单元，而不是原文照搬。
- 对同一 `topic` 下频繁写入时，应考虑“新增一条”还是“更新已有记忆”（后续可引入 `update_memory` 规范）。

## 6. 工具：`search_memory`

**用途**：在 mem0 中检索与某个查询相关的记忆条目，用于回答问题、对比新旧观点、生成建议。  
**调用场景**：

- 在回答投研/主题研究问题前，先查看 U1 过去的相关观点；
- 在内容消化时，对比新内容与既有记忆是否一致；
- 在给育儿建议时，先查看孩子相关的历史记忆和过去有效/无效的做法。

### 6.1 请求参数（逻辑结构）

```json
{
  "user_id": "U1_USER_ID",
  "query": "我关于航天行业的长期判断和关键假设",
  "agent_id": "codexread",
  "topic": "space_industry",
  "k": 10
}
```

- `user_id`：必须；
- `query`：自然语言查询；
- `agent_id`：可选；用于过滤出某个 agent 写入的记忆；
- `topic`：可选，用于缩小范围；
- `k`：返回条数上限，默认可为 5～10。

### 6.2 返回值（逻辑结构）

```json
{
  "memories": [
    {
      "id": "mem_123456",
      "user_id": "U1_USER_ID",
      "kind": "topic_insight",
      "topic": "space_industry",
      "content": "……",
      "source": "article_digest",
      "related_entities": ["spaceX"],
      "tags": ["space", "launch_cost"],
      "created_at": "2025-01-01T12:00:00Z"
    }
  ]
}
```

### 6.3 行为约束

- 用 `search_memory` 结果回答问题时，应：
  - 明确区分“来自记忆的内容”和“本次新推理的结论”；
  - 在结论中指出：当前回答基于哪些历史记忆，是否有冲突。
- 若查询结果为空，可提示是否要将当前对话内容写入为新的记忆。

## 7. 本项目中的使用约定

- 模块 M1（碎片捕获）：
  - 当识别为长期有价值的反思/原则/决策时，调用 `add_memory(user_id="U1_USER_ID", kind="reflection"` 或 `personal_principle`)。
- 模块 M2（内容消化）：
  - 对文章/视频/报告抽出的“长期可复用洞见”，以 `topic_insight` 或 `investing_thesis` 形式写入 `U1_USER_ID`；
  - 需要对比已有观点时，先 `search_memory` 再生成回答与更新建议。
- 模块 M3（行业/主题研究）：
  - 主题级结论与阶段性总结写入 `topic_insight`；
  - 重大假设/Thesis 写入 `investing_thesis`。
- 模块 M4（孩子成长观察与育儿建议）：
  - 对孩子的具体事件/表现写入 `child_observation(user_id="CHILD_USER_ID")`；
  - 对育儿策略/原则写入 `parenting_guideline(user_id="U1_USER_ID")`；
  - 在给建议前可 `search_memory` 两边（孩子主体 + `U1_USER_ID` 的育儿原则）。

## 8. 跨项目协商与统一（homeagent ↔ codexread）

> 目标：两边对 mem0 的“命名、隔离、读写策略、敏感数据边界”达成一致，避免：查不到记忆/串库/Embedding 维度冲突/把 P3 原文写进长期记忆等问题。

### 8.1 需要对齐的 4 个“硬变量”

1) **Qdrant 与 collection**
- 两边必须指向同一套 Qdrant（URL/端口）与一致的 collection 配置策略。
- 若多个工程要写入同一 collection：必须确保使用**同一 embedder 模型与向量维度**（否则会写入失败或造成不可用）。

2) **`agent_id`（命名空间）**
- mem0 的 `agent_id` 往往会参与隔离（可能是 metadata filter，也可能是更强的 namespace/collection 级隔离，取决于 mem0 版本与配置）。
- 如果希望两边“默认共享同一批记忆”，两边应使用同一个 `MEM0_AGENT_ID`。
- 如果希望“隔离为主、选择性共享”，则两边使用不同的 `MEM0_AGENT_ID`，并在需要共享时显式指定目标 `agent_id`（见 8.2）。

3) **`user_id`（主体）**
- 建议统一为“人”的稳定 ID：`U1_USER_ID` 与 `CHILD_USER_ID`（见 2.2）。

4) **写入策略（尤其是孩子 P3）**
- 强烈建议：孩子侧不要把逐字对话当作长期记忆写入 mem0；只写“提炼后的观察/原则/偏好/有效互动方式”等高密度条目，并尽量走 review-first。

### 8.2 推荐的“隔离为主，选择性共享”方案（更安全）

约定两条 mem0 命名空间（`agent_id`）：

- `homeagent_brain`：伴学机器人运行时使用的记忆空间（孩子对话、机器人可用的长期偏好/原则/观察）
- `codexread`：个人研究工作台使用的记忆空间（topic_insight / investing_thesis / research framework 等）

共享规则（建议）：

- codexread → homeagent：只把**已审阅/已确认**的“孩子相关长期结论”写入 `agent_id=homeagent_brain`（通过 `mem0-memory.add_memory(agent_id="homeagent_brain", user_id=CHILD_USER_ID, kind=child_observation|parenting_guideline, ...)`）。
- homeagent → codexread：默认不需要；若要把“孩子侧稳定偏好/变化”同步给 codexread，可由 codexread 读取 `agent_id=homeagent_brain` 做后处理，再写入 `agent_id=codexread` 的“U1 育儿原则/反思”。

### 8.3 元数据字段统一（建议最小集合）

为了跨项目可追溯与避免“记忆从哪来”混乱，建议两边写入时都携带：

- `agent_id`：命名空间（同时可写入 metadata）
- `source`：`manual_note|article_digest|research_session|child_chat_summary|robot_update_approved|...`
- `kind` / `topic` / `related_entities` / `tags`
- `origin_project`：`homeagent|codexread`（或更细：`homeagent_brain|codexread`）
- （可选）`evidence_ref`：引用指针（例如某个 digest 路径 `archives/topics/<topic_id>/digests/...`，或 `path+hash`）

### 8.4 两边的最小自检清单（建议固化为 SOP）

- homeagent：
  - 起 Qdrant → 配 `.env` → 跑 `mem0_selftest.py`（写入→检索能搜回）
  - 用固定的 `X-User-Id` 验证“同一人跨设备共享”是否生效
- codexread：
  - 跑 `scripts/smoke_test_mem0_memory.sh`（需要 `MEM0_ENABLED=true` 与 mem0 依赖）
  - 用 `mem0-memory.search_memory(agent_id="homeagent_brain", user_id=CHILD_USER_ID, ...)` 验证能否读到机器人侧记忆（若采用 8.2 方案）
