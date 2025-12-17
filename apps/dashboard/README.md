# Research Dashboard（Web，可视化）

只读 Web 界面：用于浏览 `archives/topics/` 下的主题档案与 digests，并（可选）展示 `state/tasks.sqlite` 的任务进度；同时提供全局投研收敛页：

- `/investing/watchlist`：读取 `archives/investing/watchlist.md`
- `/investing/decisions`：读取 `archives/investing/decisions/*.md`
- `/workflow`：读取 `state/topics/*/status.json`（工作流监控）

默认端口：`8787`。

## 安全建议（强烈建议）

- 对外网开放前务必启用认证（Basic 或 Bearer token）。
- 建议同时在反向代理层启用 TLS 与额外认证/限流。

## 运行（推荐）

1) 安装依赖（使用本仓库根目录的 venv）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r apps/dashboard/requirements.txt
```

2) 启动（本机浏览）：

```bash
.venv/bin/python apps/dashboard/run.py
```

3) 对外网开放（示例：Basic auth + 0.0.0.0）：

```bash
export CODEXREAD_DASH_HOST=0.0.0.0
export CODEXREAD_DASH_BASIC_USER=admin
export CODEXREAD_DASH_BASIC_PASS='change-me'
.venv/bin/python apps/dashboard/run.py
```

## 配置

见 `dashboard-web-spec.md` 的 “配置约定（V1）”。
