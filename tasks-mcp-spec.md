# tasks MCP 规格（V1 草稿）

## 1. 角色与作用

- `tasks` MCP 用于管理 U1 的任务与行动项（研究议题、跟进事项、待办）。
- 目标：
  - 将碎片化输入落为可追踪的任务；
  - 支撑研究/主题推进（可按 `topic_id` 聚合）；
  - 与 mem0 形成互补：`tasks` 管“要做什么”，mem0 管“长期结论与偏好”。

## 2. 数据模型：Task

Task（概念字段）：

- `id`：字符串，任务唯一标识（由服务端生成）。
- `title`：字符串（必填），一句话标题。
- `description`：字符串（可选），更完整的背景、假设、要回答的问题。
- `category`：字符串（可选，推荐枚举）：
  - `investing` / `tech` / `parenting` / `personal` / `other`
- `status`：字符串（必填，枚举）：
  - `pending`（待开始）
  - `in_progress`（进行中）
  - `done`（已完成）
  - `canceled`（已取消）
- `priority`：字符串或整数（可选；建议枚举）：
  - `low` / `medium` / `high`
- `tags`：字符串数组（可选），自由标签（如公司代码、主题名）。
- `topic_id`：字符串（可选），用于归档到某个行业/主题研究（稳定 slug，例如 `space_industry`）。
- `source`：字符串（可选），任务来源：
  - `manual_note` / `capture_idea` / `research_session` / `child_chat_summary` 等
- `created_at`：时间戳（服务端生成）。
- `updated_at`：时间戳（服务端更新）。

## 3. MCP 工具列表（V1）

V1 最小闭环工具：

1. `create_task`
2. `list_tasks`
3. `update_task_status`
4. （可选）`get_task`
5. （可选）`update_task`（更新标题/描述/标签等）

> 当前 spec 只要求实现 1-3；4-5 作为后续扩展。

## 4. 工具：`create_task`

**用途**：创建任务。  

### 4.1 请求参数（逻辑结构）

```json
{
  "title": "研究航天产业的成本结构",
  "description": "聚焦发射成本的主要驱动因素、可复用火箭的影响、主要玩家对比。",
  "category": "tech",
  "priority": "high",
  "tags": ["space", "launch_cost"],
  "topic_id": "space_industry",
  "source": "capture_idea"
}
```

约束：

- `title` 必须非空。
- `category/status/priority` 若缺省，服务端应提供合理默认值（例如 `status=pending`，`priority=medium`）。

### 4.2 返回值（逻辑结构）

```json
{
  "task": {
    "id": "task_123",
    "title": "研究航天产业的成本结构",
    "description": "…",
    "category": "tech",
    "status": "pending",
    "priority": "high",
    "tags": ["space", "launch_cost"],
    "topic_id": "space_industry",
    "source": "capture_idea",
    "created_at": "2025-12-13T00:00:00Z",
    "updated_at": "2025-12-13T00:00:00Z"
  }
}
```

## 5. 工具：`list_tasks`

**用途**：按条件查询任务列表。  

### 5.1 请求参数（逻辑结构）

```json
{
  "status": "pending",
  "category": "tech",
  "topic_id": "space_industry",
  "tags_any": ["space"],
  "order_by": "created_at_desc",
  "limit": 20
}
```

约束：

- 所有过滤字段均可选；不传则返回“最近任务”。
- `order_by` 建议支持：`created_at_desc` / `priority_desc` / `updated_at_desc`。

### 5.2 返回值（逻辑结构）

```json
{
  "tasks": [
    { "id": "task_123", "title": "…", "status": "pending", "priority": "high" }
  ]
}
```

## 6. 工具：`update_task_status`

**用途**：更新任务状态。  

### 6.1 请求参数（逻辑结构）

```json
{
  "id": "task_123",
  "status": "in_progress"
}
```

### 6.2 返回值（逻辑结构）

```json
{
  "task": { "id": "task_123", "status": "in_progress", "updated_at": "…" }
}
```

## 7. 行为约束与一致性要求

- 状态机：`pending -> in_progress -> done`；任意状态可转 `canceled`（由 U1 决定）。
- `topic_id` 应为稳定 slug，用于与“主题研究档案（文件系统）”关联。
- `tasks` 存储的是行动与研究议题，不应存放长篇内容；长篇内容应落在文件系统档案或 mem0。

