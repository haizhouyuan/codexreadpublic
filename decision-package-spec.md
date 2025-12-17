---
title: "投资决策包（Decision Package）规格（V1）"
created: 2025-12-16
status: draft
---

本规格定义本仓库在“投研收敛”阶段的终点产物：**投资决策包**。

目标：
- 把 topic 研究资产（sources/digests/claim ledger）强制收敛成 **可审计、可复盘、可证伪** 的“买/不买/怎么跟踪”文档；
- 允许多 topic 贡献同一标的的证据链；
- 不做自动下单；决策包是给 U1 做人工决策/复盘的“审计文件”。

---

## 1. 文件位置与命名

目录：
- `archives/investing/decisions/`

文件名（推荐）：
- `YYYY-MM-DD_<ticker>_decision.md`

例如：
- `2025-12-16_NVDA_decision.md`

---

## 2. Frontmatter（建议字段）

决策包文件头使用 YAML frontmatter（便于脚本校验与 dashboard 展示）：

```yaml
---
ticker: "NVDA"
name: "NVIDIA"
topic_ids: ["ai_compute", "optical_modules"]
decision_id: "2025-12-16_NVDA"
status: "draft"   # draft|reviewed|active|closed
created_at: "2025-12-16"
updated_at: "2025-12-16"
---
```

约束：
- `ticker` 必填（若无 ticker，可用稳定 slug，但建议尽快补齐）。
- `topic_ids` 至少 1 个。
- `status` 枚举固定：`draft|reviewed|active|closed`。

---

## 3. 正文结构（V1 必须包含）

V1 固定章节（必须保留标题，不要增删；可在章节内扩展小标题）：

1) `## Thesis`
- 一句话 thesis
- 3 条可证伪假设（pass/fail 条件 + 截止时间）

2) `## Evidence Map（强制引用）`
- 证据表必须引用到可回指的证据指针（见 §4）

3) `## Bull / Base / Bear`
- 关键变量与口径（避免混用）
- 主要分歧点（哪些变量驱动情景切换）

4) `## Trade Plan（规则化）`
- 触发条件（何时建仓/加仓/不建）
- 失效点（哪条假设被证伪就退出）
- 风险预算/仓位上限（允许用文字）

5) `## Monitoring Plan`
- KPI 列表 + 数据源 + 更新频率 + 告警阈值

6) `## Open Gaps & Tasks`
- 明确“缺口 → 影响 → 对应 tasks”

7) `## Decision Log`
- 每次复盘：结论变化、原因、下一步动作

---

## 4. Evidence Map 引用格式（强约束）

为避免引用漂移，Evidence Map 的每条证据必须使用稳定三元组引用：

- `topic=<topic_id>; digest=<digest_filename>; claim_id=<claim_id>`

示例：
- `topic=commercial_space; digest=2025-12-15_faa_compendium.md; claim_id=commercial_space_2025-12-15_faa_compendium_c03`

约束：
- `topic_id` 必须存在于 `archives/topics/<topic_id>/`。
- `digest` 必须存在于 `archives/topics/<topic_id>/digests/<digest>`。
- `claim_id` 必须在该 digest 的 Claim Ledger 中存在（空/缺失不允许通过决策闸门）。

补充允许的证据指针（可选）：
- `source_pack`：`state/source_packs/...` 的 `manifest.json` + 页码/章节（仅用于本地复盘；不对外暴露）。
- `video_pipeline`：`state/video-analyses/<analysis_id>/evidence.json` 的时间戳/帧索引（用于视频证据）。

---

## 5. 决策闸门（Decision Gate，V1）

任何会影响“仓位/交易动作”的结论，进入 `status=reviewed|active` 前必须通过闸门：

### 5.1 证据门槛

满足其一即可：
- 至少 1 条 **Level A** 证据；或
- 至少 2 条 **独立 Level B** 证据（不同来源/不同 digest，且不能来自同一份转述材料）。

证据等级标注要求：
- Evidence Map 表中必须为每条证据写 `Level A|Level B|Level C`。

### 5.2 引用有效性

- Evidence Map 中引用的 `(topic,digest,claim_id)` 必须真实存在。
- `claim_id` 不允许为空。

### 5.3 缺口任务化

- Evidence Map/Thesis 中标注的关键缺口必须对应 `tasks`（带 `topic_id`，并建议 tags 包含 `ticker`/`claim_id`）。

> 注：本仓库默认不自动下单；决策闸门只约束“文档是否可审计、是否满足证据门槛”。

---

## 6. 隐私与留存

- 决策包属于 U1 投研资产，原则上可提交到仓库（不含凭证/隐私数据）。
- 不得包含儿童敏感数据（P3）或任何凭证（CRED）。

---

## 7. 模板

- 决策包模板：`templates/decision_package.md`

