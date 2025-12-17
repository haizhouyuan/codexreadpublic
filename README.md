# codexreadpublic

本仓库是 `codexread` 的公开子集，用于代码审查/重构 review。

包含：
- `mcp-servers/`、`scripts/`、`apps/`、`templates/`、`skills-src/`、`examples/`
- 根目录的核心规格/契约：`spec.md` 与 `*-spec.md`

不包含（避免隐私/产物误入公开仓库）：
- `archives/`（主题档案/投研资产）
- `imports/`（原始输入，含敏感数据）
- `exports/`（导出产物）
- `state/`、`logs/`、`.specstory/`、`notes/`、`codex/`、`.env*`

说明：
- 所有凭证仅允许通过环境变量注入；不要把 token/key 写入仓库文件。
