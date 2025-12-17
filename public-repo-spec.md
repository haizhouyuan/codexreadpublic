# 公开代码审查仓库（`codexreadpublic`）导出规格（V1 草稿）

## 1. 目的与边界

### 1.1 目的

将本仓库（`codexread`）中**需要被外部代码审核的部分**导出为一个可公开发布的 GitHub 仓库（计划名：`codexreadpublic`），用于：

- 代码重构前后的公开 review / PR；
- 对 MCP servers / scripts / dashboard 的实现做第三方审计；
- 避免把个人研究资产、孩子相关数据、运行产物、凭证等带入公开仓库。

### 1.2 非目标

- 不在公开仓库中发布任何运行产物、档案内容（topics/investing）、对话历史、日志、缓存等。
- 不把 `codex/`（Codex CLI 源码/子仓）纳入公开仓库。
- 不试图在公开仓库中完整复刻“个人助理的全部工作资产”；公开仓库仅用于代码审查与可复现的最小运行示例。

## 2. 隐私与安全（强约束）

公开仓库属于**对外发布**，必须满足：

- 遵守 `privacy-retention-spec.md`：孩子相关输入、可识别信息、逐字原文默认不进入可提交文件。
- 不包含任何凭证与隐私配置（例如 `.env`、token、cookie、真实邮箱/手机号、含个人联系信息的 User-Agent 等）。
- 不包含运行目录与缓存：
  - `state/`、`imports/`、`exports/`、`logs/`、`.specstory/`、`notes/`、`archives/` 等目录不进入公开仓库。

> 备注：公开仓库允许出现“环境变量名/占位符”（如 `BIGMODEL_API_KEY`），但不允许出现真实 key 值。

## 3. 导出内容策略（Allowlist 优先）

### 3.1 默认包含（建议）

仅包含下列代码与必要的契约/模板（路径前缀）：

- `mcp-servers/`：本仓库实现的 MCP servers
- `scripts/`：工作流脚本与 smoke tests
- `apps/`：dashboard 等应用
- `templates/`：档案/摘要模板
- `skills-src/`：Skills（`SKILL.md`）
- `examples/`：不含凭证的配置示例
- 根目录必要契约/规格文件：
  - `README.md`（若包含与私有资产强绑定的信息，可在导出时替换为 public 版）
  - `spec.md`
  - `*-spec.md`（contracts）
  - `AGENTS.md`（可选；用于解释 agent 工作约束）

### 3.2 默认排除（必须）

公开仓库**不得包含**：

- 运行产物与本地状态：`state/`、`logs/`、`__pycache__/`、`*.pyc`、`.venv/`
- 原始输入：`imports/`
- 导出产物：`exports/`
- 长期档案与研究资产：`archives/`
- 临时笔记与会话历史：`notes/`、`.specstory/`、`codex-session-*.md`
- 外部项目或子仓：`codex/`
- 任何凭证/配置：
  - `.env`、`.env.*`
  - 任何包含真实 token/key 的文件

## 4. 导出工作流（推荐实现）

建议在本仓库提供“一键导出”脚本（实现细节见 `scripts/export_public_repo.py`）：

- 输入：仓库工作区（默认只导出 **git 已跟踪文件**，避免误拷贝本地敏感文件）
- 输出：一个干净的目录（建议放在 `state/` 下，避免被本仓库 git 追踪）
- 生成 public 专用 `.gitignore`（额外忽略 `archives/`、`exports/`、`imports/`、`state/` 等）
- 可选：在输出目录生成 public 版 `README.md`（声明“这是用于代码审查的子集”）

## 5. 发布前校验清单（必须执行）

在把导出目录推送到 GitHub 前，至少做：

1. **路径校验**：确认输出目录不含 `archives/`、`imports/`、`exports/`、`state/`、`logs/`、`codex/`、`.specstory/`。
2. **凭证扫描**（best-effort）：
   - 搜索常见 key 前缀与敏感字段：`api_key`、`token`、`Authorization:`、`BEGIN PRIVATE KEY` 等。
   - 确认仅出现“环境变量名/占位符”，不出现真实值。
3. **README/规格复核**：若出现个人信息/联系方式/敏感上下文，替换为 public 版或删除。
4. **最小可运行性**（可选）：`python -m py_compile` / 基本 smoke test（不要求联网与真实 key）。

## 6. 同步策略（建议）

- 私有仓库改动后：重新运行导出脚本覆盖输出目录；在公开仓库做一次清晰的 commit（按功能/重构分组）。
- 公共仓库不反向合并私有产物（避免混入档案/产物）。

