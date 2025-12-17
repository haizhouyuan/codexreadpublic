# mem0-memory MCP（参考实现）

本目录提供一个**极薄**的 `mem0-memory` MCP server（stdio），用于让 Codex CLI/Skills 通过 MCP 统一读写 mem0/OpenMemory 的长期记忆。

设计目标：

- 与 `mem0-memory-spec.md` 对齐：提供 `add_memory` / `search_memory`
- 复用你在 `projects/homeagent` 里已经打通的 mem0 + Qdrant 配置（同一套后端即可多项目共用）
- 不在仓库中存放任何凭证（只通过环境变量注入）

## 依赖

- `mem0` Python 包（通常为 `mem0ai` 提供的 `from mem0 import Memory`）
- `qdrant-client`（当你的 mem0 配置使用 Qdrant）
- `PyYAML`（可选；用于在本进程内加载 YAML 并展开环境变量）

> 推荐直接用你 `homeagent` 的 venv 来运行本 MCP（避免重复装依赖）。

## 环境变量

- `MEM0_ENABLED`：`true/false`（默认 `false`）
- `MEM0_CONFIG_PATH`：mem0 配置 YAML 路径（推荐绝对路径）
- `MEM0_LLM_BASE_URL` / `MEM0_LLM_API_KEY`：供 mem0 配置里的 `${...}` 展开使用
- `MEM0_AGENT_ID`：默认写入/查询过滤用的 `agent_id`（用于多项目隔离）

## 手动启动（stdio）

```bash
MEM0_ENABLED=true \
MEM0_CONFIG_PATH=/abs/path/to/mem0_config.yaml \
MEM0_LLM_BASE_URL=http://127.0.0.1:8000/v1 \
MEM0_LLM_API_KEY=... \
MEM0_AGENT_ID=codexread \
python3 mcp-servers/mem0_memory/server.py
```

## Codex CLI 配置（示例）

见：`examples/codex/config.toml` 中 `[mcp_servers."mem0-memory"]` 片段。

