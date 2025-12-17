# tmux_orchestrator MCP 规格（V1 草稿）

## 1. 角色与作用

`tmux_orchestrator` MCP 的定位：把 tmux 的“多 worker 并行调度”封装成**确定性工具**，让主控 Codex 通过 MCP 工具完成：

- 创建/确保 worker（tmux session）
- 下发作业（以脚本/非交互命令方式运行，避免往 TUI 注入键盘事件）
- 查询状态、抓取 tail 输出

目标：

- 主控 Codex 专注于：拆解目标、分派、验收（只看元信息/摘要/校验结果，不通读长文）。
- worker 专注于：执行作业脚本（可调用 `glm_router_write_file` 生成长文并写盘）。
- 通过 `glm_router_write_file` 达成“省流”：**长文不回流到主控上下文**，只回传 `path + 校验结果 + 少量 preview`。

## 2. 安全边界（强约束）

- **不允许任意命令执行**：
  - `dispatch_*` 只能执行仓库内白名单脚本（默认仅 `scripts/worker_topic_init_glm.sh`）。
- **路径安全**：
  - 任何输入/输出路径默认必须在仓库根目录下（由 `TMUX_ORCH_REPO_ROOT` 或 cwd 决定）。
  - 输出仅允许写入白名单目录（默认 `archives,state,exports`）。
- **隔离**：
  - 每个 worker 为独立 tmux session（`<prefix>-<worker_id>`），避免互相污染。
- **不依赖 send-keys 驱动 TUI**：
  - `dispatch` 通过 `tmux respawn-pane` 以命令方式启动作业（可复现、可重试）。

## 3. 环境变量

- `TMUX_ORCH_REPO_ROOT`：仓库根目录（默认使用进程 cwd）。
- `TMUX_ORCH_SESSION_PREFIX`：worker session 名前缀（默认 `codexw`）。
- `TMUX_ORCH_WRITE_BASE_DIRS`：允许写入目录白名单（逗号分隔，相对 repo_root；默认 `archives,state,exports`）。

> 说明：worker 作业脚本内部可能会调用 `glm_router`，其凭证只允许通过 `.env`/环境变量注入（见 `glm-router-mcp-spec.md`）。

## 4. MCP 工具列表（V1）

### 4.1 工具：`ensure_workers`

**用途**：确保存在 `n` 个 worker（tmux sessions），返回可调度的 worker 列表。

#### 请求参数（逻辑结构）

```json
{
  "n": 2,
  "session_prefix": "codexw"
}
```

- `n`：worker 数量（建议 1–8）
- `session_prefix`：可选（默认 `TMUX_ORCH_SESSION_PREFIX`）

#### 返回值（逻辑结构）

```json
{
  "workers": [
    {"worker_id": 0, "session": "codexw-0", "pane_target": "codexw-0:0.0", "pane_id": "%12"},
    {"worker_id": 1, "session": "codexw-1", "pane_target": "codexw-1:0.0", "pane_id": "%13"}
  ]
}
```

### 4.2 工具：`dispatch_script`（通用派单）

**用途**：在指定 worker 上运行“白名单脚本”作业，并支持 **busy 保护**（默认不允许误杀正在运行的作业）。

#### 请求参数（逻辑结构）

```json
{
  "worker_id": 0,
  "script": "scripts/worker_topic_init_glm.sh",
  "env": {
    "ORCH_TOPIC_ID": "datacenter_liquid_cooling",
    "ORCH_TOPIC_TITLE": "数据中心的液冷",
    "ORCH_SCOPE_HINT": "聚焦 direct-to-chip cold plate、immersion、rear-door HX 等；不展开普通机房空调科普。"
  },
  "record_path": "archives/topics/datacenter_liquid_cooling/notes/runs/2025-12-15_2359_init.md",
  "require_idle": true,
  "force_kill": false,
  "session_prefix": "codexw"
}
```

- `worker_id`：目标 worker
- `script`：仓库内脚本路径（相对 repo_root），必须位于 `scripts/` 且在允许范围内（默认允许 `scripts/worker_*.sh`）。
- `env`：传入 worker 的环境变量（键需为 `A-Z0-9_`，值会转成字符串）；`ORCH_WORKER_ID` 会由 orchestrator 强制注入/覆盖。
- `record_path`：可选；若提供会被校验为安全路径，并作为 `ORCH_RECORD_PATH` 注入（除非 env 已显式提供）。注意：若 env 中提供了 `ORCH_RECORD_PATH`，也会被同样校验并规范化为安全绝对路径，否则会被拒绝。
- `require_idle`：可选；默认 `true`。若 worker 状态为 running，则拒绝派单（避免误杀）。
- `force_kill`：可选；默认 `false`。若为 `true`，允许在 busy 时强制派单（会 kill 当前进程）。
- `session_prefix`：可选（默认 `TMUX_ORCH_SESSION_PREFIX`）

#### 返回值（逻辑结构）

```json
{
  "ok": true,
  "worker_id": 0,
  "session": "codexw-0",
  "pane_target": "codexw-0:0.0",
  "pane_id": "%12",
  "ts": "2025-12-15T15:59:00Z"
}
```

### 4.3 工具：`dispatch_topic_init_glm`

**用途**：在指定 worker 上运行“topic 初始化（GLM 写文件）”作业。

#### 请求参数（逻辑结构）

```json
{
  "worker_id": 0,
  "topic_id": "datacenter_liquid_cooling",
  "topic_title": "数据中心的液冷",
  "scope_hint": "聚焦 direct-to-chip cold plate、immersion、rear-door HX 等；不展开普通机房空调科普。",
  "tag": "init",
  "allow_paid": false,
  "record_path": "archives/topics/datacenter_liquid_cooling/notes/runs/2025-12-15_2359_init.md"
}
```

- `worker_id`：目标 worker
- `topic_id`：topic slug
- `topic_title`：人类可读标题（用于生成内容）
- `scope_hint`：可选；研究边界/范围提示（用于避免生成过于泛化的概览）
- `tag`：可选（默认 `init`）
- `allow_paid`：是否允许 GLM 付费回退（默认 `false`）
- `record_path`：可选；不提供则由作业脚本自动生成

#### 返回值（逻辑结构）

```json
{
  "ok": true,
  "worker_id": 0,
  "session": "codexw-0",
  "pane_target": "codexw-0:0.0",
  "record_path": "archives/topics/datacenter_liquid_cooling/notes/runs/2025-12-15_2359_init.md"
}
```

### 4.4 工具：`tail_worker`

**用途**：抓取 worker pane 最后 N 行输出（便于主控低频监控）。

#### 请求参数

```json
{"worker_id": 0, "lines": 80}
```

#### 返回值

```json
{"text": "... last lines ..."}
```

### 4.5 工具：`get_worker_status`

**用途**：读取 worker 的状态文件（由作业脚本写入），返回 idle/running/done/failed 以及最近一次 record_path。

#### 请求参数

```json
{"worker_id": 0}
```

#### 返回值

```json
{
  "worker_id": 0,
  "status": "done",
  "topic_id": "datacenter_liquid_cooling",
  "record_path": "archives/topics/datacenter_liquid_cooling/notes/runs/2025-12-15_2359_init.md",
  "ts": "2025-12-15T15:59:00Z"
}
```
