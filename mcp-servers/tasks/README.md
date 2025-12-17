# tasks MCP server (Python)

Implements the `tasks` MCP defined in `tasks-mcp-spec.md`.

## Run

From repo root:

```bash
python3 mcp-servers/tasks/server.py
```

By default it stores tasks in `state/tasks.sqlite` (created automatically).

Optional flags:

```bash
python3 mcp-servers/tasks/server.py --db-path state/tasks.sqlite
```

## Codex config (example)

Add to your `~/.codex/config.toml`:

```toml
[mcp_servers.tasks]
command = "python3"
args = ["mcp-servers/tasks/server.py", "--db-path", "state/tasks.sqlite"]
cwd = "/path/to/your/repo"
startup_timeout_sec = 20
tool_timeout_sec = 30
enabled = true
```

## Smoke test (manual)

```bash
python3 mcp-servers/tasks/server.py --db-path state/tasks.test.sqlite <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"manual-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_task","arguments":{"title":"Test task","category":"personal"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_tasks","arguments":{"limit":5}}}
EOF
```

