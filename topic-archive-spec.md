# 主题研究档案（Topic Archive）规格（V1 草稿）

## 1. 目的

主题研究档案用于支持 M3（行业/主题研究管理），目标是让某个主题（如“航天”）形成可长期维护、可回溯的“研究工作台”：

- 框架明确：知道“该研究什么”与“下一步做什么”。
- 资料可追溯：每条结论能追溯到来源与摘要。
- 可演化：随着新资料不断更新，不会写散、写乱。

本规格只约束文件系统侧的档案结构；长期结论仍建议写入 mem0（`topic=<topic_id>`）。

## 2. 标识：`topic_id`

- `topic_id` 是稳定 slug（建议小写 + 下划线），例如：
  - `space_industry`
  - `semiconductor_foundry`
  - `ai_inference_stack`
- `topic_id` 用于贯穿：
  - 目录：`archives/topics/<topic_id>/`
  - 任务：`tasks.topic_id`
  - 记忆：`mem0.topic=<topic_id>`

## 3. 目录结构（推荐）

```
archives/
  topics/
    <topic_id>/
      overview.md
      framework.md
      timeline.md
      sources.md
      open_questions.md
      digests/
      notes/
```

说明：

- `overview.md`：主题定义、边界、研究目标、当前结论摘要。
- `framework.md`：研究框架与分析维度（可迭代）。
- `timeline.md`：关键事件时间线（用于追踪进展）。
- `sources.md`：已纳入的资料清单（指向 `digests/`）。
- `open_questions.md`：未解决问题与待验证假设（可链接到 `tasks`）。
- `digests/`：每条资料的结构化摘要（文章/视频/报告等）。
- `notes/`：临时笔记、杂项记录（后续可整理入 digests 或 overview）。

## 4. 文件模板（建议内容）

### 4.1 `overview.md`

建议包含：

- 主题一句话定义
- 研究边界（包含/不包含）
- 关键结论（3–10 条，随着研究更新）
- 核心假设（可验证）
- 风险与反例（需要关注的“打脸点”）
- 下一步行动（可生成 tasks）

### 4.2 `framework.md`

建议按维度组织，例如（可选）：

- 技术原理与关键瓶颈
- 历史与里程碑
- 产业链/生态位
- 竞争格局与关键玩家
- 政策/监管/地缘风险
- 商业模式与成本结构
- 投资视角：关键变量、估值框架、催化剂

### 4.3 `timeline.md`

建议每条事件包含：

- 日期（或时间范围）
- 事件描述（客观）
- 影响评估（可选）
- 资料引用（指向 digest 条目）

### 4.4 `sources.md`

建议维护一张“已收录资料清单”，每条包含：

- 标题
- 类型：`article|video|paper|report|transcript|official`
- 发布日期（如果有）
- 来源链接或文件路径
- 对应摘要文件路径（`digests/...`）
- 关联标签（可选）

### 4.5 `open_questions.md`

建议拆分：

- 未知（Unknowns）：缺资料/缺数据
- 争议（Disputes）：观点冲突，需要交叉验证
- 待验证（To verify）：可用明确实验/数据验证的假设

对需要推进的项，建议同步创建 `tasks` 并记录 `task_id`/链接。

## 5. Digest 条目规范（`digests/`）

### 5.1 文件命名

推荐：

- `YYYY-MM-DD_<source_slug>.md`

例如：

- `2025-12-01_spacex_starship_update.md`

### 5.2 Digest 文件格式

推荐在文件头使用 YAML frontmatter（便于机器处理）：

```markdown
---
title: "SpaceX Starship update"
source_type: "video"
source_url: "https://…"
source_path: null
published_at: "2025-12-01"
topic_id: "space_industry"
tags: ["space", "launch", "reusability"]
entities: ["SpaceX", "Starship"]
---
```

正文建议固定结构：

- 核心观点（3–7 条）
- 关键证据/数据点（带引用）
- 反驳点/局限性
- 对本主题框架的影响（更新了哪个维度）
- 建议写入 mem0 的“长期结论候选”（可选）

## 6. 与 mem0 / tasks 的联动约定

- 每次对主题做阶段性总结时：
  - 生成 `topic_insight` 或 `investing_thesis` 形式的记忆写入 mem0（`topic=<topic_id>`）。
- 每次在 `open_questions.md` 新增需要推进的事项时：
  - 创建 `tasks`（带 `topic_id=<topic_id>`），并在 `open_questions.md` 记录 `task_id`。

## 7. 模板

- 主题档案模板：`templates/topic/`
- Digest 模板：`templates/digest.md`
