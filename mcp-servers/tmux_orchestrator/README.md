# tmux_orchestrator MCP

将 tmux 的 worker 并行调度封装成 MCP 工具，供主控 Codex 以“确定性工具调用”的方式管理 worker（避免往 TUI 注入键盘事件）。

规格：`tmux-orchestrator-mcp-spec.md`

## 启动（stdio）

```bash
python3 mcp-servers/tmux_orchestrator/server.py
```

## 重要参数（dispatch_topic_init_glm）

- `scope_hint`：可选，用于限定研究边界（避免概览过于泛化）；最终会作为 `ORCH_SCOPE_HINT` 传入 worker 脚本。

## 通用派单（dispatch_script）

- 默认启用 busy 保护：当 `state/tmux_orch/workers/<worker_id>/status.json` 显示 `status=running` 时会拒绝派单，避免误杀。
- 如需强制抢占：用 `dispatch_script(force_kill=true)`（会 kill 当前 pane 内进程并启动新作业）。
- 脚本必须在允许范围内（默认允许 `scripts/worker_*.sh`；也可通过 `TMUX_ORCH_ALLOWED_SCRIPTS` 显式收紧/放开）。

## 状态文件

worker 作业脚本会写入：

- `state/tmux_orch/workers/<worker_id>/status.json`

主控可通过 `get_worker_status` 工具读取。
