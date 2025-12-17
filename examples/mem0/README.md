# mem0 配置示例（参考 `projects/homeagent`）

本仓库**不实现** `mem0-memory` MCP server；这里仅提供一个可参考的 mem0（SDK）配置示例，便于你在别处（例如 `homeagent` / `mem0-memory` MCP）复用同一套后端，从而让多个 agent 共享长期记忆。

## 你需要准备

- 一个向量库（示例使用 Qdrant：`localhost:6333`）
- 一个 OpenAI 兼容的 LLM/Embedding 服务（通过环境变量注入，不要写死 key）

## 文件

- `examples/mem0/mem0_config.yaml`：mem0 的 YAML 配置（可直接复制修改）
- `examples/mem0/mem0.env.example`：建议的环境变量（无任何密钥）

建议：

- 多项目共用同一套 mem0/Qdrant 时：
  - `MEM0_AGENT_ID` 用于命名空间隔离（例如 `codexread` vs `homeagent_brain`）。
  - `U1_USER_ID` / `CHILD_USER_ID` 建议用“人”的稳定 ID（而不是 device_id），并在多个 repo 里保持一致，这样才能真正共享同一条长期记忆（细节见 `mem0-memory-spec.md` 的跨项目协商章节）。
