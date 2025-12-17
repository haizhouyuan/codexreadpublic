# <topic_id> — 研究框架

> 注：本页描述“要研究什么、用什么口径、怎么把证据变成可回放结论”。关键结论必须落在 `digests/` 并在 `overview.md` 中引用后才视为已验证。

## 研究方法论与迭代流程（必遵守）

### 总体流程（sources → digests → overview/timeline/open_questions → tasks → mem0）

- Sources：在 `sources.md` 维护资料清单，保证每条记录能追溯到原文链接或本地路径；新增资料时只记元信息与简单用途，不抄大段原文。
- Digests：每条资料对应 `digests/` 下的一份 digest，尽量一源一文。Digest 使用统一模板（frontmatter + 正文），正文至少包含：核心观点、关键证据、Claim Ledger、对本 topic 框架/假设/风险的影响，以及建议的核验动作（如有）。
- 概览与时间线：从 digests 抽取由证据支撑的结论同步到 `overview.md` / `timeline.md`，并在段落中引用对应 digest 文件名；关键结论建议进一步引用到 Claim Ledger 的 `claim_id`（见下）。
- 未解问题与任务：将仍不确定的假设/争议写入 `open_questions.md`；是否创建 tasks 由 `notes/triage_policy.md` 约束（防止任务爆炸）。
- mem0：仅将“稳定结论/框架/决策原则”写入 mem0，不写长文原文或临时猜测。

### 证据等级（Evidence Levels）

- Level A（强）：审计/监管/官方统计、正式监管申报、明确合同文件等一手披露。
- Level B（中）：公司投关材料、行业研究报告等需要与 Level A 交叉验证的资料。
- Level C（弱）：新闻报道、二手评论、未署名材料，仅作线索。

### Claim Ledger 规范（在每个 digest 内）

每个 digest 建议包含一个 `Claim Ledger` 表，用于记录“重要论断及其核验状态”。推荐沿用 `templates/digest.md` 的列，并遵守：

- 引入稳定 `claim_id`（新增/重排时不变），避免 `Claim #` 漂移导致引用失效。
- `影响范围/置信度/核验状态` 建议用枚举（例如 `high|medium|low`、`unverified|partially_verified|verified|falsified`），便于脚本统计与 triage。
- `来源/证据`：尽量给到可回溯指针（URL + 页码/章节；视频则时间戳/关键帧），并标注证据等级 `[Level A/B/C]`。

### 质量闸门（最小可校验）

建议用 `scripts/topic_validate.py` 做静态检查，至少保证：

- `sources.md` 表中每条 `Digest` 路径存在；
- 每个 digest frontmatter 的 `topic_id` 与目录一致；
- Claim Ledger 存在且含必要列（并尽量含 `claim_id`）；
- `timeline.md` 与 `sources.md` 采用“按日期排序”，不是“写入顺序”。

### 更新节奏（Iteration Rhythm）

- 日常：有新资料时先补 `sources.md` 与对应 digest；如时间有限，可先建占位 digest，后续补全 Claim Ledger。
- 每周：整理新增 digest，将影响较大的 claim 同步到 `overview.md` 的“关键结论/假设/风险”；检查 `open_questions.md` 是否需要更新条目与 tasks。
- 阶段性：复盘框架（补指标口径、更新路线矩阵、清理已证伪假设），并将相对稳定的研究原则写入 mem0。

## 维度 1：技术原理与关键瓶颈

## 维度 2：历史与里程碑

## 维度 3：产业链/生态位

## 维度 4：竞争格局与关键玩家

## 维度 5：政策/监管/地缘风险

## 维度 6：商业模式与成本结构

## 维度 7：投资视角（关键变量/估值框架/催化剂）
