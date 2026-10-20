# v0.4.0 测量证据目录

> 目的：**让 v0.4.0 的每一个数字都可复核。**
>
> 这条要求来自 v0.4.0 架构审查的 P0-4：两份 v0.4.0 实测报告里的全部关键数字
> （45% 稳定率、55% 翻转率、44/60 盲评命中、0/117 投票改判）所指向的脚本
> **全部不在仓库里**，原始数据又被 `.gitignore` 排除在版本控制外。
> ⇒ **没有人能复核任何一个数字。**
>
> 这也直接解释了「为什么三次尝试改判据都失败了」——不是判据难，是每一次尝试
> 的证据都没有被固定下来，于是下一轮无从对照、无法归因。

---

## 一、这里有什么、没有什么

| | 状态 | 说明 |
|---|---|---|
| **结论数字** | ✅ 在 | `conclusions/*.json`，来自两份 v0.4.0 报告，机器可读 |
| **复现脚本** | ✅ 在（两个是重建的） | 见下表 |
| **档位稳定性的原始数据** | ✅ **已重跑再生并归档** | `stability/`（含 sha256 + 与报告声称值的逐项对账） |
| **severe 盲评的原始数据与人工作答** | ❌ 仍缺失 | 需要真人评审，见 §五 |
| **原始 API 响应** | ❌ **丢了** | 随 F 盘被更换而消失，不可恢复 |
| **人工作答** | ❌ **丢了** | 且 `evaluation/answers.json` 当时还在 `.gitignore` 里 |

> ⚠️ **必须说清楚**：`conclusions/*.json` 里是**报告声称值**，不是原始数据。
> 它能证明「报告说了什么」，**不能**证明「模型当时真的返回了这些」。
> 只有重跑之后，两者对上，结论才重新站得住。

---

## 二、三次实验索引

| 实验 | 报告 | 脚本 | 脚本状态 | 原始数据 |
|---|---|---|---|---|
| 档位稳定性（180 次运行） | 效果验证报告 §三 | `evaluation/verify_stability.py` | **重建** | LOST |
| severe 澄清质量盲评（20 条 / 60 问） | 效果验证报告 §四 | `evaluation/v040_severe_blind.py` | **重建** | LOST |
| 档位投票（240 次调用） | 投票实测报告 §一 | `evaluation/verify_vote_stability.py` | 已恢复入库 | LOST |

机器可读索引见 `manifest.json`。

### 「重建」是什么意思

`verify_stability.py` 与 `v040_severe_blind.py` 的**原版随 F: 盘丢失**。
2026-10-20 按报告里给出的指标口径**重新实现**，并且：

1. 每条口径的判据都写进了代码的 docstring（不是散落在报告里）；
2. 新增 `evaluation/test_v040_measurement.py`（27 例，**0 次 API 调用**）钉住口径；
3. 其中一条用例 `test_reproduces_report_table_v040` 用报告 §3.2 的档位组合分布
   反推，验证重建脚本能复算出**报告原表的数字**（clear 20/11/55%、medium 18/6/33%）。

> 这不等于「原脚本被找回来了」。**脚本是新写的**，数字是**按新脚本的口径**
> 算的。它与原脚本逐字相同与否无法证明——原脚本已经不存在了。
> 能证明的只有一件事：**新脚本的口径与报告的表格自洽**。

---

## 三、怎么重跑（再生原始数据）

```powershell
# 前置：DEEPSEEK_API_KEY 环境变量 + 余额
cd D:\workspace\1-projects\ai-prompt-enhancer

# 1) 档位稳定性：60 条 × 3 次 = 180 次调用
py -3 evaluation\verify_stability.py --repeats 3
#    产物：evaluation\results\stability\<时间戳>__m-<模型>__t<温度>__r<次数>__<哈希>\
#    归档：py -3 tools\archive_v040_data.py --experiment stability

# 2) severe 盲评：先出一份含 questions 的原始结果，再导出盲评包
py -3 evaluation\verify_enhance.py                 # 产物在 evaluation\results\enhance\<身份目录>\
$raw = (Get-ChildItem evaluation\results\enhance -Recurse -Filter 'v040_enhance_raw*.json' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
py -3 evaluation\v040_severe_blind.py export --raw $raw --outdir evaluation\results\blind_v040
#    → 人工评审 blind_packet.md，填 blind_answers.json
py -3 evaluation\v040_severe_blind.py parse --answers <answers.json> --key <blind_key.json>
py -3 evaluation\v040_severe_blind.py aggregate --scored <scored.json>

# 3) 投票实测：240 次调用
py -3 evaluation\verify_vote_stability.py
```

一键脚本见 `regenerate.ps1`。

**成本**：三次实验合计约 **440 次 API 调用**。重跑前请确认余额与成本意愿。

### 产物去哪了

新脚本的产物**不会**再被静默覆盖：

- 目录名带 UTC 微秒时间戳 + 身份标签 ⇒ 每次运行一个独立目录；
- 文件名编码「模型 / 温度 / 提示词版本 / 样本集哈希 / 重复次数 / 短哈希」；
- `--resume` 前做身份一致性闸，身份不同**直接拒绝续跑**（而不是把新旧混进一份产物）。

机制见 `evaluation/run_identity.py`。

**但工作区里的产物仍不入库**（`evaluation/results/*` 被 `.gitignore` 排除）——
参与结论的那一份要**显式归档**：

```powershell
py -3 tools\archive_v040_data.py --experiment stability
```

它会：复制产物到 `docs/evaluation-data-v040/<实验>/`、记下 sha256、
把**产物里的数**与**报告里的数**逐项对账并把差异写进 `manifest.json`。
对账不是形式——差异本身就是证据（两次独立运行之间的波动，正是 41 点噪声地板的体现）。

---

## 四、门禁

| 门禁 | 脚本 | 作用 |
|---|---|---|
| 失败伪装清单 | `scripts/check-fail-open.py` | **41 条**「把失败显示成成功」同族项的机器复核（2026-10-20 起全部闭环，退出码 0） |
| 测量口径单测 | `evaluation/test_v040_measurement.py` | 运行身份、稳定性指标口径、盲评流程（0 次 API） |
| 跨层等价单测 | `evaluation/test_enhancer.py` | 读 `prompts.rs` / `provider.rs` / `models.rs` 原文做断言 |

`check-fail-open.py` 的纪律（与它自己审查出的结论一致）：

- **探测不到 ≠ 通过**：`probe.kind=manual` 的条目一律报 `UNVERIFIED`，绝不报 `PASS`；
- 还有 P0 未闭环、或有 P0/P1 处于 `UNVERIFIED` ⇒ **退出码非 0**；
- 结论必须来自**当前工作区实际文件内容**，不能来自清单里写的行号；
- **探针只看代码，不看散文**（2026-10-20 加的 `_strip_comments`）——
  否则「把旧缺陷写进注释」会被误判成缺陷仍在；
- **反向探针** `required_regex` 用于「压根没做某个检查」这类缺陷
  （没有坏字符串可找，只能要求某段逻辑必须存在）。

> 它的探测方式多为「某个字符串是否还在」。这能抓住**原地不改**的缺陷，
> 抓不住「换了个写法、bug 还在」的缺陷。**所以每条修复还必须配一条
> 行为级回归测试**——清单里每条都有 `acceptance` 字段，就是给这个用的。

---

## 五、接下来该做什么

1. ~~**重跑再生原始数据**（需成本意愿确认）~~ —— **档位稳定性已重跑（180 次）并归档**；
2. ~~把重跑产物**提交进版本控制**~~ —— **已归档**（`stability/`，含 sha256）；
3. ~~重跑结果与 `conclusions/*.json` **逐项对账**~~ —— **已对账**，结果记在
   `manifest.json` 的 `reproduction_check`；
4. **人工作答脱敏后入库**（`answers/`）—— **未做**：需要真人评审 severe 档的 20 条。
   ⇒ v0.4.0 的「线二 · 澄清质量」因此**仍未达成**；
5. **重跑投票实测（240 次）** —— 未做。该结论是**数学恒等式**
   （`majority_vote([A, B]) ≡ A`），不是经验观察，重跑的边际价值有限。

---

## 六、文件清单

```
docs/evaluation-data-v040/
├── README.md                    本文件
├── manifest.json                三次实验的机器可读索引
├── regenerate.ps1               一键重跑
├── conclusions/
│   ├── stability_v040.json      档位稳定性的报告声称值
│   ├── severe_blind_v040.json   severe 盲评的报告声称值
│   └── vote_v040.json           投票实测的报告声称值
└── fixtures/
    ├── summary_pass.json                    一切齐备 ⇒ 门禁 PASS
    ├── summary_missing_cost.json           无比值样本 ⇒ 膨胀比门槛 FAIL
    ├── summary_denominator_lost.json       15/60 裁判失败 ⇒ 分母门槛 FAIL
    ├── summary_single_run.json              runs=1 ⇒ 轮次门槛 FAIL
    └── summary_v040_gates_missing.json      效果门槛无数据 ⇒ 全部 FAIL
```

> `fixtures/` 里的 5 个 summary **全部由 `evaluation/report.py` 的 `_aggregate()` 真实聚合产出**
> （生成脚本 `evaluation/_make_v040_fixtures.py`，0 次 API 调用），**不是手搓的假数据**。
>
> 它们是门禁的行为级回归夹具：
> `python evaluation/check_gate_behavior.py` 逐个跑一遍，判定结果必须与期望一致，
> 退出码非 0 = 门禁行为已漂移。
>
> 门禁规范见 `docs/v040-验收门禁规范.md`；判定入口
> `python evaluation/release_gate.py --summary <summary.json>`（退出码 0=PASS，1=FAIL）。
