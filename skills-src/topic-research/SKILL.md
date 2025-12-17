---
name: topic-research
description: 初始化或更新一个主题研究档案（文件系统）并联动 tasks/mem0，支持长期跟踪（M3）。
allowed-tools:
  - tasks
  - mem0-memory
  - glm_router
  - workspace
  - shell
---

# 触发条件

当用户说“建立一个行业/主题研究框架”“开始研究某主题”“把主题研究做成体系化档案”等场景使用本技能。

# 目标

- 初始化/维护主题档案目录：`archives/topics/<topic_id>/`（见 `topic-archive-spec.md`）。
- 将待验证项与下一步行动写入 `tasks`（带 `topic_id`）。
- 将阶段性结论写入 mem0（`user_id=U1_USER_ID`, `topic=<topic_id>`）。

# 操作步骤（SOP）

1. **确认 topic_id 与边界**
   - `topic_id`：稳定 slug（小写+下划线），例如 `space_industry`。
   - 边界：包含/不包含什么；研究目标是什么。

2. **初始化档案（若不存在）**
   - 创建 `archives/topics/<topic_id>/` 并写入模板：
     - `overview.md`、`framework.md`、`timeline.md`、`sources.md`、`open_questions.md`
   - 模板来源：`templates/topic/`。

3. **更新框架（可迭代）**
   - 将用户目标映射到 `framework.md` 的维度。
   - 生成一个“已知/未知/争议点”列表，写入 `overview.md` / `open_questions.md`。
   - 若已启用 `glm_router`：可用 `glm_router.glm_router_write_file` 写入 `overview/framework/open_questions` 的初稿（低成本、避免长文回流）；再由 Codex 统一校对与落盘。

4. **生成任务**
   - 对 `open_questions.md` 中需要推进的事项，调用 `tasks.create_task(topic_id=<topic_id>, source="topic_research")`。
   - 把创建的 `task_id` 记录回 `open_questions.md`（用括号标注即可）。

5. **写入 mem0（阶段性结论）**
   - 对于已经明确的结论/框架原则，写入 `mem0-memory.add_memory(user_id=U1_USER_ID, kind=topic_insight, topic=<topic_id>)`。

6. **对用户反馈**
   - 输出：
     - 档案路径
     - 新建/更新的任务列表
     - 写入 mem0 的结论摘要

# 输出格式

- `topic_path`: `archives/topics/<topic_id>/`
- `tasks_created`: 列表（`id`, `title`）
- `mem0_updates`: 列表（`kind`, `topic`, `summary`）
