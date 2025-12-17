# video-pipeline MCP 规格（V1 草稿）

## 1. 角色与作用

`video-pipeline` MCP 是一个**本地视频后处理流水线**，目标是把“口播/路演/技术介绍/公司与行业分析视频”转成可追溯的证据包，供 Codex CLI 进一步生成：

- 可检索的资料沉淀（JSON/Markdown/CSV）
- 报告式总结（研报风格 digest / report）

本 MCP 只做**本地计算**（GPU/CPU），不负责：

- 下载视频（B 站/YouTube 等下载另做工具，或先下载到本地再处理）
- 调用云端大模型写摘要（由 Codex CLI 完成）
- 直接写入 mem0（由 review-first 工作流生成“候选记忆”后再写入）

> 备注：在 Codex 配置中，本 MCP server 的名称建议使用 `video_pipeline`（与本仓库示例配置一致）。

## 2. 设计目标（针对你的选择）

基于你确认的输入特点与偏好：

1. 视频以“观点/技术/公司/行业/投资标的分析”为主（口播 + 偶发图表/截图）
2. 既要可检索的结构化产物，也要报告式总结（由 Codex 基于证据包生成）
3. 画面侧只要求 **OCR 抽取数字/表格/关键文字**（不做曲线读数/图表点位拟合）

因此 V1 的重点是：

- ASR 转写（强优先级）
- 关键帧抽取（中等力度，避免爆量）
- OCR（只保留“数字命中/表格/关键文字”的证据）
- 统一产出 `evidence.json` + `evidence_compact.md`（给 Codex 输入的“压缩证据”）

## 3. 输出目录与文件（约定）

默认输出到：`state/video-analyses/<analysis_id>/`（重产物，不入 git）。

`<analysis_id>` 建议为：`YYYY-MM-DD_<video_slug>` 或 `YYYY-MM-DD_<video_slug>_<short_id>`。

产物建议（V1 最小闭环）：

- `manifest.json`：本次运行的参数、版本、时间、告警
- `audio.wav`：从视频抽出的 16k 单声道音频（可选）
- `transcript.json`：转写分段（带时间戳）
- `transcript.srt`：字幕文件（可选）
- `frames/`：抽取的关键帧图片
- `ocr.jsonl`：逐帧 OCR 结果（建议只保留“数字命中行”以控量）
- `key_metrics.csv`：从 OCR/转写中抽取的“疑似关键数字行”（弱结构化，供后续整理）
- `evidence.json`：统一证据包（路径 + 时间戳 + OCR/表格引用）
- `evidence.md`：人类可读证据摘要（可选）
- `evidence_compact.md`：给 Codex 使用的“压缩证据”（强烈推荐）

### 3.1 安全约束（强制）

为避免 agent/外部输入导致越权写文件，服务端必须执行以下约束：

- `analysis_id`：即使由调用方提供，也必须进行 slug 清洗（等价 `safe_slug`）；禁止包含 `../`、路径分隔符等。
- `out_dir`：默认只允许写入 `state/video-analyses/` 之下；若调用方传入 `out_dir` 且不在该目录下，必须拒绝（除非显式启用“允许越界输出”的开关）。

## 4. 证据包：`evidence.json`（V1）

建议结构（字段可扩展）：

```json
{
  "schema_version": "1.0",
  "generated_at": "2025-12-14T00:00:00Z",
  "video": {
    "path": "imports/content/videos/demo.mp4",
    "sha256": "...",
    "duration_sec": 1234.56
  },
  "transcript": [
    { "start": 12.34, "end": 18.90, "text": "..." }
  ],
  "frames": [
    {
      "frame_path": "frames/000123.jpg",
      "frame_index": 123,
      "approx_time_sec": 615.0,
      "ocr_numeric_lines": [
        { "text": "市场规模 2024E 123 亿", "score": 0.92 }
      ],
      "tables": []
    }
  ],
  "artifacts": {
    "manifest_json": "manifest.json",
    "transcript_json": "transcript.json",
    "frames_dir": "frames",
    "ocr_jsonl": "ocr.jsonl",
    "key_metrics_csv": "key_metrics.csv",
    "evidence_compact_md": "evidence_compact.md"
  }
}
```

约束：

- 所有“数字/数据结论”都必须能回指到：
  - `transcript[].start/end`（口述来源）或
  - `frames[].frame_path` + OCR 行（画面来源）
- `evidence_compact.md` 必须可在不读取原视频的情况下支撑写报告。

## 5. MCP 工具列表（V1）

V1 最小闭环只需要 1 个工具：

1. `analyze_video`

（可选扩展：`list_runs` / `get_run` / `read_evidence`，后续再加）

## 6. 工具：`analyze_video`

### 6.1 用途

对本地视频执行后处理，生成 `state/video-analyses/<analysis_id>/` 下的证据包与中间产物。

### 6.2 请求参数（逻辑结构）

```json
{
  "video_path": "imports/content/videos/demo.mp4",
  "analysis_id": "2025-12-14_demo",
  "out_dir": "state/video-analyses/2025-12-14_demo",
  "lang": "zh",
  "asr_model": "large-v3",
  "asr_device": "auto",
  "asr_compute_type": "auto",
  "asr_vad_filter": true,
  "frame_every_sec": 5.0,
  "max_height": 1080,
  "enable_asr": true,
  "enable_frames": true,
  "enable_ocr": true,
  "ocr_mode": "numeric_only",
  "dry_run": false,
  "overwrite": false
}
```

说明：

- `video_path`：必填，本地文件路径。
- `analysis_id/out_dir`：二选一可填；都不填则由服务端生成默认值。
- `asr_device/asr_compute_type`：推荐默认 `auto`；`auto` 会在有 GPU 时用 `cuda+float16`，否则回落到 `cpu+int8`。
- `asr_vad_filter`：默认 `true`；当发现转写长度明显短于视频（尤其尾段较安静）时，可设为 `false` 尝试补齐。
- `frame_every_sec`：口播视频建议 3～8 秒；越小越准但越慢。
- `ocr_mode`：V1 推荐 `numeric_only` 控量；后续可扩展 `full_text`。
- `dry_run`：只校验输入与计算输出路径，不做实际处理（用于连通性测试）。

### 6.3 返回值（逻辑结构）

```json
{
  "analysis_id": "2025-12-14_demo",
  "out_dir": "state/video-analyses/2025-12-14_demo",
  "artifacts": {
    "evidence_json": "state/video-analyses/2025-12-14_demo/evidence.json",
    "evidence_compact_md": "state/video-analyses/2025-12-14_demo/evidence_compact.md",
    "transcript_json": "state/video-analyses/2025-12-14_demo/transcript.json",
    "frames_dir": "state/video-analyses/2025-12-14_demo/frames",
    "ocr_jsonl": "state/video-analyses/2025-12-14_demo/ocr.jsonl",
    "key_metrics_csv": "state/video-analyses/2025-12-14_demo/key_metrics.csv"
  },
  "stats": { "frames": 123, "transcript_segments": 456, "ocr_frames": 80 },
  "warnings": ["paddleocr not installed; skipped ocr"]
}
```

## 7. 与本仓库工作流的集成（建议）

### 7.1 生成 digest（可检索沉淀）

1. 运行 `video-pipeline.analyze_video` 生成 `evidence_compact.md`
2. 生成 digest（两种常用方式二选一）：
   - 批处理/粗筛：用 `digest-content`（或启发式脚本）读取 `evidence_compact.md`，生成 digest；
   - handoff/高质量：用 `chatgptMCP`（ChatGPT Pro + Gemini Web 登录态）生成研报式 digest（见 `scripts/generate_video_digest_via_web_research.py`）。
   - `archives/topics/<topic_id>/digests/YYYY-MM-DD_<source_slug>.md`

### 7.2 生成报告（报告式总结）

1. 运行 `video-pipeline.analyze_video`
2. 让 Codex 读取 `evidence_compact.md` + `key_metrics.csv` 输出：
   - `exports/digests/<analysis_id>_report.md` 或写入 topic 档案 `notes/`

### 7.3 写入 mem0（可选）

从 digest/report 中抽取“长期价值结论候选”，走 review-first：

- 先生成候选清单（或机器人变更包/记忆候选包）
- 再人工审核
- 最后调用 `mem0-memory.add_memory(user_id=U1_USER_ID, kind=topic_insight|investing_thesis, topic=<topic_id>)`
