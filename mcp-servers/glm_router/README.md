# glm_router MCP

将智谱 GLM（BigModel OpenAI-compat 接口）作为“低成本加工工具”接入 Codex CLI。

规格：`glm-router-mcp-spec.md`

## 工具列表

- `glm_router_chat`：一次性对话（可选 JSON 解析），适合短输出。
- `glm_router_write_file`：读本地文件 → 生成长文/JSON → 直接写入 `output_path`，只回传元信息与少量预览（用于减少 Codex 主模型上下文占用）。

## 启动（stdio）

必需环境变量：

- `BIGMODEL_API_KEY`：智谱 API key（只允许 env 注入；不要写入仓库）

可选环境变量：

- `BIGMODEL_API_BASE`：默认 `https://open.bigmodel.cn/api/paas/v4`
- `GLM_ROUTER_ALLOW_PAID_DEFAULT`：默认 `false`
- `GLM_ROUTER_CALL_LOG`：审计日志 JSONL（默认不落盘）

示例：

```bash
# 推荐：把 BIGMODEL_API_KEY 放到仓库根目录 `.env`（已在 .gitignore 中），然后：
./scripts/run_glm_router_mcp.sh

# 或者临时方式（不要把 key 写进任何文件）：
# export BIGMODEL_API_KEY='***'
# python3 mcp-servers/glm_router/server.py
```

## 冒烟测试

```bash
set -a; source .env; set +a
bash scripts/smoke_test_glm_router.sh
bash scripts/smoke_test_glm_router_write_file.sh
```

## Codex CLI 配置

参考：`examples/codex/config.toml`（推荐用 `scripts/run_glm_router_mcp.sh` 启动，以便仅对该 MCP 进程注入 `.env` 里的 key）。
