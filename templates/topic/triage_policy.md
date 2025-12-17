# <topic_id> — triage policy（任务化阈值与防爆炸）

> 目的：把 “open_questions / tasks / mem0” 的边界做成可执行规则，避免任务爆炸，同时保证关键结论可核验。

## 1) 字段枚举（建议固定口径）

- 影响范围：`high|medium|low`
- 置信度：`high|medium|low`
- 核验状态：`unverified|partially_verified|verified|falsified`
- 证据等级：`Level A|Level B|Level C`（写在 digest 的“来源/证据”中）

## 2) 什么时候必须建 task（Must）

满足任一条件就建 task（并尽量写入 `claim_id` 或 digest 路径）：

1) `overview.md` 里打算升级为“已验证口径”的结论，但关键证据尚缺 Level A 或多源 Level B。
2) 有明确的一手入口（标准/白皮书/监管披露/原始数据集）但尚未 digest：建 “阅读/提取/生成 digest” 任务。
3) 数值类关键结论（市场规模、成本、效率、阈值）已进入讨论，但口径/时间点/测点边界不明确：建 “补齐口径 + 原始来源 URL/页码” 任务。

## 3) 什么时候只记 open_questions，不建 task（Should not）

满足任一条件就不要建 task（先留在 `open_questions.md`）：

- 纯探索性问题，缺少可执行核验入口（没有 URL/机构/报告名/数据集线索）。
- 影响范围为 `low` 或当前阶段不影响决策路径（例如“未来可能的远期路线”）。
- 只是“更多阅读/更全面总结”的愿望，没有具体可交付验收条件。

## 4) 防爆炸阈值（Hard guard）

- 单个 topic：每周新增 “核验类 tasks” 建议不超过 `5` 条；超过时先做合并/去重/优先级重排。
- 每条 task 必须满足：可在 30–120 分钟内推进到“产出落盘”（digest/摘录/数据点），否则拆小或降级为 open_question。

## 5) 输出与验收（让质量可校验）

- tasks 的描述里尽量包含：
  - `digest` 路径（若任务是补齐某条 digest）
  - `claim_id`（若任务是核验某条 claim）
  - 明确的验收标准（“补齐完整 URL+页码+口径，并更新 Claim Ledger 核验状态”）

