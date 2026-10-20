# 人工盲评答案归档目录

**这里放的是脱敏后的人工盲评答案归档副本。**

## 为什么要有这个目录

架构审查 D8 判定：旧版 `.gitignore` 把 `evaluation/answers.json` 整条忽略，于是

1. **人工校准环天然不可复现** —— 报告里写「48 个盲评单元由 6 名评审评完」，
   但答案文件不在仓库里，任何人都无法核对；
2. **发布门槛可被一个数字击穿** —— `report.py` 的门槛拿
   `meta["human_review_count"]` 比 20%，而那个值来自 CLI 自报的
   `--human-review-count`。`--human-review-count 999` 就能在**零人工评审**的
   情况下让门槛通过。

第 2 点已在 v0.4.0 修掉：`human_review_count` 现在**由答案文件的条目数派生**
（`evaluation/run_eval.py` 的 `_count_manual_answers()`），CLI 自报不再被采信。

第 1 点由本目录解决：**脱敏后的答案必须归档入库**。

## 归档规则

| 项 | 规则 |
|---|---|
| 文件名 | `answers_<用途>_<日期>.json`，例如 `answers_severe_blind_2026-10-20.json` |
| 内容 | 与 `evaluation/answers.json` 同结构（`{"answers": {sample_id: {target_original, target_enhanced, ...}}}`） |
| **必须脱敏** | 不得含 API Key、凭据、Cookie、邮箱、真实姓名、内部 ID 等隐私或凭据信息 |
| 登记 | 归档后必须在本目录的 `INDEX.md` 里登记一行：文件名、用途、评审人数、单元数、来源脚本 |
| 校验 | `INDEX.md` 里记 sha256，便于核对未被改写 |

## 现状

**本目录目前是空的。** v0.4.0 的三轮实测的原始 API 响应与人工作答都随 F 盘更换
一并丢失（见 `../README.md` 的「数据丢失」一节），无法补归档。

⇒ **重跑并重新评审之后**，把脱敏答案按上面的规则放进本目录，并在 `INDEX.md`
登记。这是 v0.4.0 验收「线一」里 D8 的闭环前提。
