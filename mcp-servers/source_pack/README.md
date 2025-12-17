# source_pack MCP

将一个 URL 抓取并落盘为“证据包（source pack）”，供后续 digest/claim ledger/核验任务使用。**工具只返回路径与元信息，不回传长文**。

## 工具

- `source_pack_fetch(url, topic_id?, pack_id?, allow_paid?, fetchers?, timeout_sec?, min_chars?)`

默认 fetchers：`local → jina_reader → tavily_extract → bigmodel_reader`，其中 `tavily_extract/bigmodel_reader` 需要 `allow_paid=true` 才会启用。

## 输出

默认写入：`state/source_packs/<topic_id?>/<pack_id>/`

- `manifest.json`：状态与 attempts（可审计）
- `text.md`：最终选定的正文抽取（供后续处理）
- `raw.html` / `download.pdf` / `reader.json` / `extract.json` 等：按抓取器落盘

## 环境变量

- `SOURCE_PACK_REPO_ROOT`：仓库根目录（建议由启动脚本设置）
- `SOURCE_PACK_BASE_DIR`：输出 base dir（默认 `state/source_packs`）
- `SOURCE_PACK_ALLOW_OUTSIDE_STATE`：允许写出 `state/`（默认 `false`，不建议开）
- `SOURCE_PACK_USER_AGENT`：自定义抓取 UA（建议含联系信息；抓 `sec.gov` 这类站点时几乎是必需）

可选（可能消耗额度/计费）：

- Tavily Extract：`TAVILY_API_KEY` 或 `tavilyApiKey`
- BigModel reader：`BIGMODEL_API_KEY`

## 运行（stdio）

```bash
SOURCE_PACK_REPO_ROOT=/vol1/1000/projects/codexread \
  /vol1/1000/projects/codexread/.venv/bin/python mcp-servers/source_pack/server.py
```

推荐用 wrapper（自动加载仓库根目录 `.env`）：

```bash
bash scripts/run_source_pack_mcp.sh
```
