# source_pack MCP 规格（V1 草稿）

## 1. 角色与作用

`source_pack` 的定位：把“URL 线索”变成可落盘、可复现、可引用的**证据包（source pack）**，用于后续的 digest/claim ledger/核验任务闭环。

它解决的问题不是“网页能不能打开”，而是：

- URL → **原文快照**（HTML/PDF/JSON） → **统一抽取正文**（`text.md`）→ **可引用线索**（`links.json`）→ **可审计元数据**（`manifest.json`）
- 抓取失败也要落盘：保留失败原因与最小证据（HTTP 状态/提示文本），并在上层转成 tasks（login/paywall/challenge 等）

> 该 MCP 只负责“抓取与证据包落盘”，不负责生成研究结论；结论/结构化产出建议交由 `glm_router_write_file` 或 `web-research`（ChatGPT Pro）完成。

## 2. 安全与隐私（强约束）

- 凭证只允许通过环境变量注入（`.env` 仅用于本机，必须在 `.gitignore`）。
- 禁止把孩子敏感内容（P3）或任何凭证（CRED）写入 source pack。
- 默认只在 `state/source_packs/` 写入证据包（`state/` 默认不入 git）。

## 3. 证据包目录结构（约定）

对每个 URL，生成一个目录：

```
state/source_packs/<topic_id?>/<pack_id>/
  manifest.json          # 元数据与抓取状态（含 attempts）
  raw.html               # 直连抓取到的 HTML（若为 HTML）
  rendered.html          # 预留（后续 browser/CDP 渲染抓取）
  download.pdf           # 若为 PDF（或附件）
  reader.json            # BigModel reader 原始返回（若使用）
  extract.json           # Tavily extract 原始返回（若使用）
  reader.txt             # Jina Reader 原始返回（若使用）
  links.json             # 从 HTML 抽取的 a[href]/canonical/og:url（若可用）
  text.md                # 统一正文抽取（供 digest/claim 使用）
```

### 3.1 pack_id 生成建议

默认建议由服务端生成：`YYYY-MM-DD_<slug>_<short_hash>`，并对外部输入做 `safe_slug` 清洗，防止路径穿越。

## 4. 环境变量

### 4.1 基础配置

- `SOURCE_PACK_REPO_ROOT`：仓库根目录（默认用进程 cwd；建议由启动脚本设置）
- `SOURCE_PACK_BASE_DIR`：输出 base dir（默认 `state/source_packs`）
- `SOURCE_PACK_ALLOW_OUTSIDE_STATE`：是否允许 `out_dir` 写到 `state/` 之外（默认 `false`，建议保持关闭）

### 4.2 可选抓取后端（额度/计费）

- Tavily Extract：`TAVILY_API_KEY` 或 `tavilyApiKey`
- BigModel reader：`BIGMODEL_API_KEY`

> 这些后端可能消耗额度/计费；默认 `allow_paid=false` 时不启用。

## 5. MCP 工具

### 5.1 工具：`source_pack_fetch`

用途：按“分层抓取 + 质量闸门”抓取一个 URL，落盘 source pack，并返回元信息（不回传长文）。

#### 请求参数（逻辑结构）

```json
{
  "url": "https://example.com/report.pdf",
  "topic_id": "datacenter_liquid_cooling",
  "pack_id": null,
  "allow_paid": false,
  "fetchers": ["local", "jina_reader", "tavily_extract", "bigmodel_reader"],
  "timeout_sec": 30,
  "min_chars": 2000
}
```

- `fetchers`：按顺序尝试的抓取器；建议默认 `local → jina_reader`，只有在高价值来源或召回不足时才追加 `tavily_extract/bigmodel_reader`
- `min_chars`：正文最小字符数质量闸门；若某层输出过短，会继续降级；最终仍会返回“最佳努力”的结果并标记 `status`

#### 返回值（逻辑结构）

```json
{
  "url": "...",
  "final_url": "...",
  "topic_id": "datacenter_liquid_cooling",
  "pack_id": "2025-12-16_ashrae_whitepaper_8a12c3d4",
  "status": "done",
  "fetcher_used": "jina_reader",
  "out_dir": "state/source_packs/datacenter_liquid_cooling/2025-12-16_ashrae_whitepaper_8a12c3d4",
  "manifest_path": ".../manifest.json",
  "text_path": ".../text.md",
  "raw_path": ".../download.pdf",
  "links_path": ".../links.json",
  "chars": 98214,
  "attempts": [
    { "fetcher": "local", "ok": true, "chars": 96620, "seconds": 3.69, "reason": "too_short" },
    { "fetcher": "jina_reader", "ok": true, "chars": 98214, "seconds": 2.78 }
  ],
  "needs_followup": false
}
```

`status` 建议枚举：

- `done`：已生成 `text.md` 且满足 `min_chars`
- `partial`：生成了 `text.md` 但不满足 `min_chars`（仍可用于初步阅读/后续再抓）
- `blocked`：检测到 challenge/login/paywall（仍会落 `manifest.json` 与最小证据）
- `failed`：所有层失败（仍会落 `manifest.json`）

## 6. 默认分层策略（建议固化到实现）

- `allow_paid=false`（默认）：`local → jina_reader`
- `allow_paid=true`：在 free 路径不足时追加 `tavily_extract → bigmodel_reader`

> browser/CDP 渲染抓取（Playwright/Chrome CDP）作为 V2：用于 JS-heavy、需要点击下载、以及更强留证（mhtml/screenshot）。

