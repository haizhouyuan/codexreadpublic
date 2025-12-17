# AGENTS.md（topic：<topic_id>）

本文件对 `archives/topics/<topic_id>/` 目录树生效，用于把 Codex 的行为收敛到“主题研究”的稳定工作流上。

## 输出与归档（强约束）

- 所有产出优先落到本目录：
  - `overview.md`：主题定义、边界、关键结论、假设、风险、下一步行动
  - `framework.md`：研究框架（持续迭代）
  - `investing.md`：投研收敛页（细分赛道→公司池→KPI→催化剂→风险→下一步核验；见 `templates/topic/investing.md`）
  - `open_questions.md`：未知/争议/待验证（必要时关联 tasks）
  - `sources.md`：资料清单（必须可追溯到 `digests/`）
  - `timeline.md`：关键进展时间线（引用 `digests/`）
  - `digests/`：每条资料一条 digest（含 Claim Ledger）
  - `notes/`：SOP、模板、临时笔记（可后续整理回档案正文）；建议包含 `notes/triage_policy.md`（任务化阈值与防爆炸策略）

## Investability Gate（强约束）

- 新建/恢复一个可投资 topic 后，优先产出 `investing.md`（用 `templates/topic/investing.md`）：
  - 公司池（候选池）≥ 10；
  - 至少 1 个 `status=thesis_candidate`；
  - 关键缺口必须任务化（`tasks`，建议 tags 含 `ticker`/`claim_id`）。
- 若 `investing.md` 为空/不达标：暂停扩 digests，先把“可投资暴露”补齐再继续深挖，避免 topic 变成资料坑。

## 事实与来源（强约束）

- 外部事实必须附来源链接/出处；无法核验的 claim 必须标记为 `unverified`；是否创建核验 tasks 由 `notes/triage_policy.md` 约束。
- 不要把“模型推断”伪装成事实；写清不确定性与假设。
- 建议在 digest 的 Claim Ledger 中标注证据等级 `[Level A/B/C]`，并为关键 claim 引入稳定 `claim_id`，避免引用漂移。

## 完工通知（tmux，可选但推荐）

多 tmux worker 并行时，为便于主控会话低频监控：

1) 在本 topic 下落一份 run 记录（便于回放）：

- `archives/topics/<topic_id>/notes/runs/<YYYY-MM-DD_HHMM>_<tag>.md`

2) 然后通知主控 pane（主控 pane 先跑 `bash scripts/tmux_set_controller_pane.sh`）：

```bash
bash scripts/tmux_notify_controller_done.sh \
  --topic <topic_id> \
  --record archives/topics/<topic_id>/notes/runs/<YYYY-MM-DD_HHMM>_<tag>.md \
  --status done
```
