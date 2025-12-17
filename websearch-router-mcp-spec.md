# websearch_router MCP 规格（V1 草稿）

## 1. 角色与作用

`websearch_router` 的定位：为 `codexread` 提供一个**结构化 SERP 搜索工具**，把多个搜索后端（免费/配额/付费）统一封装成 MCP，并在服务端完成：

- **成本/配额优先**的路由与回退（free → quota → paid）
- 结果规范化（title/url/snippet/published_at/score）
- 本地缓存与（可选）审计日志（落 `state/`，默认不入 git）

它不替代 `web-research`（ChatGPT Web Deep Research）。`websearch_router` 负责“找线索/列来源队列”；`web-research` 负责“需要强推理/强综合的深研”。

## 2. 安全与隐私（强约束）

- 凭证只允许通过环境变量注入（可用仓库根目录 `.env` + 启动脚本注入，`.env` 必须在 `.gitignore`）。
- **禁止**把孩子敏感内容（P3）作为搜索 query 发送到第三方服务；search 仅用于公开信息检索。
- 默认仅在 `state/websearch_router/` 写缓存/计数/日志（都不入 git）。

## 3. 支持的后端（Providers）

按成本层级分为三档：

- `free`：
  - `brave`（Brave Search API）
  - `tavily`（Tavily Search API）
- `quota`（有限次数/试用额度）：
  - `tongxiao_iqs`（阿里 IQS/夸克）
- `paid`（按调用/按 token 计费）：
  - `bigmodel_web_search`（智谱 BigModel web_search：`search_std|search_pro|search_pro_sogou|search_pro_quark`）
  - `dashscope_web`（阿里百炼 Qwen enable_search / web-search）

## 4. 环境变量

### 4.1 Provider keys

- Brave：`BRAVE_API_KEY` 或 `braveapikey`
- Tavily：`TAVILY_API_KEY` 或 `tavilyApiKey`
- IQS（夸克）：`TONGXIAO_API_KEY`
- BigModel：`BIGMODEL_API_KEY`
- DashScope：`DASHSCOPE_API_KEY` 或 `WEBSEARCH_API_KEY`

### 4.2 Router 配置

- `WEBSEARCH_ROUTER_REPO_ROOT`：仓库根目录（默认用进程 cwd；建议由启动脚本设置）
- `WEBSEARCH_ROUTER_CACHE_TTL_SECONDS`：缓存 TTL（默认 `86400`，1 天）
- `WEBSEARCH_ROUTER_ALLOW_PAID_DEFAULT`：默认是否允许 paid 回退（默认 `false`）
- 配额/频控（默认值可按实际订阅调整）：
  - `WEBSEARCH_ROUTER_LIMIT_TONGXIAO_TOTAL`（默认 `1000`）
  - `WEBSEARCH_ROUTER_LIMIT_BRAVE_PER_DAY`（默认 `500`）
  - `WEBSEARCH_ROUTER_LIMIT_TAVILY_PER_DAY`（默认 `200`）
  - `WEBSEARCH_ROUTER_LIMIT_BIGMODEL_PER_DAY`（默认 `50`）
  - `WEBSEARCH_ROUTER_LIMIT_DASHSCOPE_PER_DAY`（默认 `50`）
- 质量/成本调参（可选）：
  - `WEBSEARCH_ROUTER_TAVILY_DEPTH`：`basic|advanced`（默认 `basic`）
  - `WEBSEARCH_ROUTER_BIGMODEL_CONTENT_SIZE`：`medium|high`（默认 `medium`）
  - `WEBSEARCH_ROUTER_DASHSCOPE_MODEL`：默认 `qwen-turbo`
- （可选）审计日志：
  - `WEBSEARCH_ROUTER_CALL_LOG`：JSONL 路径（默认不写）
  - `WEBSEARCH_ROUTER_CALL_LOG_INCLUDE_QUERY`：默认 `0`（仅记录 query_hash；设为 `1` 才记录 query 原文，慎用）

## 5. MCP 工具

### 5.1 工具：`websearch_router_search`

用途：对一个 query 返回结构化 SERP，并按策略进行缓存/回退。

#### 请求参数（逻辑结构）

```json
{
  "query": "ASHRAE TC 9.9 water-cooled servers whitepaper pdf",
  "max_results": 5,
  "min_results": 5,
  "language": "auto",
  "allow_paid": false,
  "recency": "noLimit",
  "domain_filter": null,
  "timeout_sec": 30
}
```

- `language`：`auto|en|zh-hans|zh-hant`（auto 会基于 query 简单判别）
- `allow_paid`：若为 `false`，最多回退到 `quota`（IQS）；若为 `true`，允许进一步回退到 `paid`
- `recency`：`noLimit|oneDay|oneWeek|oneMonth|oneYear`（不同 provider 可能部分支持；不支持时会在服务端忽略）
- `domain_filter`：可选；对支持的 provider 会下推（不支持则服务端过滤）

#### 返回值（逻辑结构）

```json
{
  "query": "...",
  "language": "en",
  "provider_used": "brave",
  "cache_hit": false,
  "attempts": [
    { "provider": "brave", "tier": "free", "ok": true, "cache_hit": false, "result_count": 5 }
  ],
  "results": [
    { "title": "...", "url": "https://...", "snippet": "...", "published_at": null, "score": null, "source": "brave" }
  ],
  "raw_path": "state/websearch_router/cache/brave/<hash>.json",
  "usage": { "brave": { "total": 12, "today": 3 } }
}
```

### 5.2 工具：`websearch_router_get_usage`

用途：查看本地 usage 计数（用于监控 IQS/paid 的消耗）。

## 6. 默认路由策略（可在实现中固化）

- 先 `free`：`brave` → `tavily`
- 若 `language` 为中文且结果不足：再 `quota`：`tongxiao_iqs`
- 若仍不足且 `allow_paid=true`：`bigmodel_web_search`（按语言选引擎）→ `dashscope_web`

> 若所有允许的 provider 都失败/结果不足，返回当前最佳结果，并在 `attempts` 中标记失败原因，交由上层选择 `web-research` 深研或人工补充。
