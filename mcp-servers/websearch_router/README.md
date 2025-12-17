# websearch_router MCP

将多个 Web Search 后端（Brave / Tavily / 夸克 IQS / GLM web_search / DashScope）统一封装成一个**成本/配额可控**的结构化 SERP 工具，并在服务端完成 free→quota→paid 回退与本地缓存。

规格：`websearch-router-mcp-spec.md`

## 启动（stdio）

推荐：把 key 放在本仓库根目录 `.env`（已在 `.gitignore`），然后用包装脚本启动：

```bash
scripts/run_websearch_router_mcp.sh
```

或直接启动（需要你在启动前导出环境变量）：

```bash
python3 mcp-servers/websearch_router/server.py
```

## Codex CLI 配置（示例）

见：`examples/codex/config.toml` 中 `[mcp_servers.websearch_router]` 片段。

