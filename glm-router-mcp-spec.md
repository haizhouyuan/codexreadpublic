# glm_router MCP 规格（V2 草稿）

## 1. 角色与作用

- `glm_router` MCP 的定位：把智谱 GLM（BigModel OpenAI-compat 接口）作为**低成本“加工工具”**接入 Codex CLI。
- 目标：
  - Codex 继续用你已登录的官方模型做编排/决策；
  - 需要“模板化/结构化/填表/摘要压缩”等批量加工时，可调用 `glm_router`：
    - **默认先用免费模型**；
    - 免费失败（限流/5xx/输出不符合约束）时，在允许的情况下**自动回退到付费模型**；
    - 仍失败则返回 tool error，交回 Codex 用主模型完成（质量优先）。

## 2. 安全与隐私（强约束）

- 凭证只允许通过环境变量注入：`BIGMODEL_API_KEY`，禁止写入仓库/配置文件。
- 默认不落盘 prompt/answer（可选审计日志开关见 §5）。
- **孩子敏感内容（P3）默认不得发送到第三方模型**；如确需使用，必须先在本地完成脱敏与摘要化，并且通过人工审核流程控制（见 `privacy-retention-spec.md`）。
- **路径安全**：涉及文件读写的工具必须进行路径净化与目录白名单限制，默认仅允许在仓库内指定目录写入（见 §3 与 §4.2）。

## 3. 环境变量

- 必需：
  - `BIGMODEL_API_KEY`：智谱 BigModel API key
- 可选：
  - `BIGMODEL_API_BASE`：默认 `https://open.bigmodel.cn/api/paas/v4`
  - `GLM_ROUTER_ALLOW_PAID_DEFAULT`：默认 `false`（避免意外计费）
  - `GLM_ROUTER_CALL_LOG`：JSONL 审计日志路径（默认不写）
  - `GLM_ROUTER_CALL_LOG_INCLUDE_PROMPTS`：默认 `0`
  - `GLM_ROUTER_CALL_LOG_INCLUDE_ANSWERS`：默认 `0`
  - `GLM_ROUTER_REPO_ROOT`：仓库根目录（默认使用进程当前工作目录；用于相对路径解析与安全限制）
  - `GLM_ROUTER_WRITE_BASE_DIRS`：允许写入的目录白名单（逗号分隔，相对 `GLM_ROUTER_REPO_ROOT`；默认 `archives,exports,state`）
  - `GLM_ROUTER_ALLOW_OUTSIDE_REPO_READ`：默认 `0`（若为 `1`，允许读取仓库外文件；不推荐）
  - `GLM_ROUTER_ALLOW_OUTSIDE_REPO_WRITE`：默认 `0`（若为 `1`，允许写入仓库外路径；不推荐）

## 4. MCP 工具列表（V2）

V2 提供两个工具：

- `glm_router_chat`
- `glm_router_write_file`

### 4.1 工具：`glm_router_chat`

**用途**：向 GLM 发起一次 chat completion，并按策略进行“免费→付费”回退；可选对输出做 JSON 解析。

#### 请求参数（逻辑结构）

```json
{
  "expect": "text",
  "family": "auto",
  "system": "你是一个严谨的结构化写作助手。",
  "user": "把下面内容提炼成要点列表：......",
  "image_url": null,
  "allow_paid": false,
  "timeout_sec": 60,
  "meta": { "task": "digest_compact" }
}
```

字段说明：

- `expect`：输出类型
  - `text`：返回纯文本（Markdown 也属于 text）
  - `json`：要求“只输出 JSON”，服务端会做 JSON 提取与解析并返回 `structuredContent.json`
- `family`：模型族
  - `auto`（默认）：根据 `image_url` 或 `messages` 中是否含 image 判断 text/vision
  - `text`：只走文本模型路由
  - `vision`：只走视觉模型路由
- 输入二选一（推荐用简化字段；高级用 `messages`）：
  - 简化字段：`system`（可选）、`user`（必填）、`image_url`（可选）
  - 高级字段：`messages`（OpenAI chat 格式；用于多轮/复杂多模态）
- 回退控制：
  - `allow_paid`：是否允许付费回退（优先级高于 `GLM_ROUTER_ALLOW_PAID_DEFAULT`）
- `timeout_sec`：单次 HTTP 超时
- `meta`：透传元数据（服务端原样回传，便于上层关联任务）

#### 返回值（逻辑结构）

```json
{
  "text": "......",
  "json": null,
  "used_model": "glm-4.5-flash",
  "used_tier": "free",
  "attempts": [
    { "model": "glm-4.5-flash", "tier": "free", "http_status": 200, "ok": true }
  ],
  "meta": { "task": "digest_compact" }
}
```

返回字段说明：

- `text`：模型原始输出（用于上层直接引用/二次处理）
- `json`：当 `expect=json` 且解析成功时返回（对象或数组）
- `used_model` / `used_tier`：本次最终采用的模型与档位（`free|paid`）
- `attempts`：本次尝试链路（含失败原因，便于诊断/优化路由）
- `meta`：原样回传

#### 回退规则（V1）

- 默认路由：
  - 文本：`glm-4.5-flash` →（允许付费时）`glm-4.6`
  - 视觉：`glm-4.6v-flash` →（允许付费时）`glm-4.6v`
- 判定“失败需要回退”的条件（任一满足）：
  - HTTP 非 200（含 429/5xx）
  - `expect=json` 且服务端无法从输出中提取/解析出合法 JSON
- 若免费与付费均失败：MCP tool 返回 `isError=true`，由 Codex 主模型接管。

## 5. 审计日志（可选）

- 若设置 `GLM_ROUTER_CALL_LOG=/path/to/file.jsonl`：
  - 每次 tool call 追加一行 JSON（时间戳、used_model、attempts、耗时等）。
- 默认不写入 prompts/answers；
  - 仅在显式设置 `GLM_ROUTER_CALL_LOG_INCLUDE_PROMPTS=1` / `GLM_ROUTER_CALL_LOG_INCLUDE_ANSWERS=1` 时才写入（慎用，涉及隐私与留存策略）。

---

### 4.2 工具：`glm_router_write_file`

**用途**：让 GLM 在服务端读取输入文件 → 生成长文本/JSON → **直接写入磁盘**，并仅回传 `output_path + 校验结果 + 少量 preview`，避免把长文作为 tool result 回到 Codex（从而节省主模型上下文）。

#### 请求参数（逻辑结构）

```json
{
  "expect": "text",
  "family": "text",
  "system": "你是一个严谨的结构化写作助手。",
  "instructions": "按 templates/digest.md 结构生成一份 digest。",
  "input_paths": ["archives/topics/xxx/digests/2025-12-15_xxx.md"],
  "template_path": "templates/digest.md",
  "output_path": "archives/topics/xxx/digests/2025-12-16_new.md",
  "overwrite": false,
  "validate": {
    "must_have_substrings": ["## 核心观点", "## Claim Ledger"],
    "max_chars": 20000
  },
  "preview_chars": 300,
  "max_input_bytes_per_file": 200000,
  "allow_paid": false,
  "timeout_sec": 120,
  "max_retries": 1,
  "meta": { "task": "digest_write" }
}
```

字段说明：

- `expect`：输出类型
  - `text`：写入文本/Markdown
  - `json`：要求“只输出合法 JSON”；服务端会解析并以规范化 JSON 写入文件
- `family`：默认建议 `text`（本工具一般用于文本生成；如需多模态可用 `glm_router_chat`）
- `system`：可选系统提示
- `instructions`：必填，写作指令（短文本）
- `input_paths`：可选，输入文件路径列表（由 MCP server 在本地读取；避免把原文塞回 Codex 上下文）
- `template_path`：可选，模板文件路径（如 `templates/digest.md`）
- `output_path`：必填，输出文件路径（服务端写入）
- `overwrite`：可选，是否覆盖已存在的 `output_path`（默认 `false`，避免误覆盖）
- `validate`：可选，本地校验规则
  - `must_have_substrings`：数组；每个字符串必须在输出中出现
  - `min_chars` / `max_chars`：长度约束（按字符数）
- `preview_chars`：返回的预览长度（默认 200；上限由实现约束）
- `max_input_bytes_per_file`：单个输入文件最大读取字节数（超出则报错；默认由实现给出，建议保持较小以避免把超大文本直接塞进模型）
- `allow_paid` / `timeout_sec`：同 `glm_router_chat`
- `max_retries`：当输出不满足 `expect/validate` 时的重试次数（默认 0 或 1；重试在 MCP 内部完成，不回传长文）
- `meta`：透传元数据

#### 返回值（逻辑结构）

```json
{
  "output_path": "archives/topics/xxx/digests/2025-12-16_new.md",
  "bytes": 12345,
  "sha256": "....",
  "chars": 9800,
  "used_model": "glm-4.5-flash",
  "used_tier": "free",
  "attempts": [
    { "model": "glm-4.5-flash", "tier": "free", "http_status": 200, "ok": true }
  ],
  "validation": { "ok": true },
  "preview": "前 300 字预览……",
  "meta": { "task": "digest_write" }
}
```

注意：

- 返回值默认**不包含全文**；如需查看内容，直接打开 `output_path` 文件。
- **路径安全约束（默认）**：
  - `input_paths` / `template_path` / `output_path` 默认必须位于 `GLM_ROUTER_REPO_ROOT` 下；
  - `output_path` 还必须位于 `GLM_ROUTER_WRITE_BASE_DIRS` 白名单目录之一；
  - 仅在显式开启 `GLM_ROUTER_ALLOW_OUTSIDE_REPO_READ=1` / `GLM_ROUTER_ALLOW_OUTSIDE_REPO_WRITE=1` 时才允许越界（不推荐）。
