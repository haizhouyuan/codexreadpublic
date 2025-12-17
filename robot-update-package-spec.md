# 伴学机器人变更包（JSON）规格（V1 草稿）

## 1. 目的与原则

本规格定义本项目输出给“伴学机器人项目”的**变更包**格式，用于：

- 将孩子对话/事件的后处理结果，结构化为：
  - 可写入 mem0 的“建议记忆变更”（孩子侧、U1 育儿侧）；
  - 可用于更新机器人系统提示词的“建议补丁”；
  - 给 U1 的建议与风险提示。

原则：

- **人工审核优先**：变更包默认只落盘，不自动应用到机器人项目，也不自动写入孩子侧 mem0。
- **可回放/可追溯**：记录输入来源、时间范围、摘要与理由，便于复盘。
- **隐私最小化**：默认不在变更包中包含完整原始对话，仅包含必要的摘要与结构化结论（原文可通过引用/哈希定位）。

## 2. 文件存放建议

建议输出到仓库目录：

- `exports/robot-update-packages/`

文件名建议：

- `YYYY-MM-DD_<CHILD_USER_ID>_<short_id>.json`

> 实际路径可通过配置覆盖；本规格只给推荐默认值。

## 2.1 模板

- 变更包模板：`templates/robot-update-package.json`

## 2.2 审核与应用（review-first）

- 默认只生成 `review.status="pending"` 的变更包；必须人工审核后再应用。
- 本仓库提供一个“应用器”脚本用于导出可执行清单（默认不写入 mem0）：
  - `python3 scripts/apply_robot_update_package.py exports/robot-update-packages/<file>.json`
- 该脚本会在 `state/robot-update-applies/`（默认）输出：
  - `<file>.prompt_patches.md`：可复制的系统提示词补丁片段
  - `<file>.mem0_calls.json`：待调用 `mem0-memory.add_memory` 的参数清单
  - `<file>.receipt.json`：本次导出/应用的回执（用于追溯）
- 如需自动写入 mem0（已审核前提下），可在你提供 `mem0-memory` MCP 启动命令后使用：
  - `--apply-mem0 --mem0-mcp-command "..." --mem0-scope u1|child|both`

## 3. 顶层结构（建议字段）

```json
{
  "schema_version": "1.0",
  "generated_at": "2025-12-13T00:00:00Z",
  "review": {
    "required": true,
    "status": "pending",
    "reviewed_by": null,
    "reviewed_at": null,
    "notes": ""
  },
  "target": {
    "system": "child-bot",
    "child_user_id": "CHILD_USER_ID",
    "u1_user_id": "U1_USER_ID"
  },
  "inputs": [],
  "u1_advice": [],
  "proposed_actions": [],
  "risks": [],
  "open_questions": []
}
```

说明：

- `schema_version`：变更包 schema 版本号。
- `review.status`：`pending` / `approved` / `rejected`。
- `target.*_user_id`：占位符，实际值由配置提供。

## 4. 输入数据：Child Chat Bundle（暂定）

上游 App 尚未稳定，本节给一个“暂定 schema”，用于指导后续冻结接口。实际以你 app 落地为准。

### 4.1 `inputs[]` 项（建议字段）

```json
{
  "type": "child_chat_bundle",
  "source": "child-app",
  "time_range": { "from": "2025-12-12T00:00:00Z", "to": "2025-12-12T23:59:59Z" },
  "path": "imports/child/2025-12-12.json",
  "sha256": "..."
}
```

### 4.2 `child_chat_bundle` 文件内容（暂定）

```json
{
  "schema_version": "0.1",
  "child_user_id": "CHILD_USER_ID",
  "session_id": "sess_abc",
  "messages": [
    { "ts": "2025-12-12T10:00:00Z", "role": "child", "text": "…" },
    { "ts": "2025-12-12T10:01:00Z", "role": "bot", "text": "…" }
  ]
}
```

约束建议：

- `messages[].role` 限定为 `child|bot|parent|system`（可扩展）。
- 默认不要求携带音频/图片原始内容；如需要可通过 `attachments[]` 引用路径与哈希。

## 5. 给 U1 的建议：`u1_advice[]`

用于输出你可直接执行的建议（关注情绪价值与陪伴质量，不偏向学业规划）。

```json
{
  "title": "本周情绪波动的应对建议",
  "summary": "…",
  "rationale": ["来自对话摘要 X", "历史记忆 Y"],
  "confidence": 0.7,
  "follow_ups": ["建议你问孩子一个开放式问题：…"]
}
```

## 6. 建议动作清单：`proposed_actions[]`

变更包的核心是“建议动作”，每个动作必须包含可执行参数 + 理由。

### 6.1 动作类型：写入 mem0（建议）

```json
{
  "type": "mem0.add_memory",
  "user_id": "CHILD_USER_ID",
  "memory": {
    "kind": "child_observation",
    "topic": "child_emotions",
    "content": "最近孩子在…场景更容易…（摘要化表述）",
    "source": "child_chat_summary",
    "related_entities": [],
    "tags": ["emotion", "sleep"]
  },
  "rationale": "该模式在过去两周多次出现，值得写入长期画像。",
  "privacy": "high",
  "confidence": 0.6
}
```

说明：

- `memory` 字段遵循 `mem0-memory-spec.md` 的概念模型（本项目可按需映射到实际 mem0 API）。
- `privacy` 用于提醒审核者敏感程度：`low|medium|high`。

### 6.2 动作类型：系统提示词建议（机器人侧）

```json
{
  "type": "child_bot.prompt_patch",
  "mode": "append",
  "title": "当孩子表达沮丧时的回应方式",
  "content_md": "## 情绪回应\n- 先共情…\n- 再提问…\n",
  "placement_hint": "Append to system prompt: 'Interaction Style' section",
  "rationale": "孩子在对话中多次提到…，当前策略…效果更好。",
  "confidence": 0.7
}
```

约束：

- 本项目只输出建议，不直接修改机器人项目文件。
- `content_md` 必须是可直接复制粘贴进系统提示词的片段（尽量短、可读）。

### 6.3 动作类型：U1 侧记忆更新（育儿原则）

```json
{
  "type": "mem0.add_memory",
  "user_id": "U1_USER_ID",
  "memory": {
    "kind": "parenting_guideline",
    "topic": "parenting",
    "content": "当孩子…时，我优先…而不是…",
    "source": "parenting_review",
    "related_entities": [],
    "tags": ["parenting", "boundaries"]
  },
  "rationale": "本次互动验证了该做法更有效。",
  "privacy": "medium",
  "confidence": 0.7
}
```

## 7. 风险与未决问题

### 7.1 `risks[]`

```json
{
  "title": "样本量不足",
  "detail": "只观察到一次…，不建议写入长期画像，建议先观察一周。",
  "severity": "low"
}
```

### 7.2 `open_questions[]`

```json
{
  "question": "孩子最近睡眠是否有变化？",
  "why": "对情绪波动可能是关键变量。",
  "suggested_question_to_ask_child": "你最近晚上睡得怎么样？"
}
```
