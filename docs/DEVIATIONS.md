# DEVIATIONS —— 实施中偏离/补充规格之处

> 规则：**不改写规格原文**。发现缺口时按行业惯例取默认值实现，
> 把「缺口 + 采用值 + 理由」登记在这里，并让缺口出现在机器可读的产物里。
> 只有影响项目方向或会引发大量返工的**产品级**决策才回头问人。

本文件记录 v0.4.1（v0.4.0 前置）实施过程中的偏离与补充。

---

## D-001 门禁门槛的 `n` 单位未在任何地方写明

**缺口**：`docs/PromptCraft-v0.4.0-iteration-spec.md` §7 给出门槛 4/5/6 的
基线与目标，但**没有写 n 是什么的单位**。而从
`docs/PromptCraft-v0.4.0-效果验证报告.md` §4.2/§4.3 反推可知三者各不相同：

| 门槛 | 基线 | n 的实际单位 |
|---|---|---|
| G4 翻译场景人工盲评命中率 | 1/9 = 11% | **问题条数**（翻译 3 条 × 每条 3 个问题） |
| G5 severe 整体人工盲评命中率 | 44/60 = 73.3% | **问题条数**（severe 20 条 × 每条 3 个问题） |
| G6 严重档「问已知信息」条数 | 2/20 条 | **样本条数**（标注过的样本） |
| G7 交付状态漂移 | 360 次运行零漂移 | **运行次数** |

装配 `v040_gates` 时若把「样本数」和「问题数」搞混，门禁就是在拿错误分母判对错。

**采用值**：`evaluation/gate_spec_v040.json` 每条 active 门槛新增 `n_unit`
字段（`questions` / `samples` / `runs`），并在 `notes` 里写明
`count_unit` 只是「计数型门槛」这一分支的标记、其字面值不表示 n 的单位。

**理由**：把口径钉进机器可读文件，而不是留在人脑或某份报告里。
`evaluation/test_gate_decidability.py` 用 5 条断言把它钉住
（3 条关于 `n_unit`：单位存在且取值合法 / 逐条等于预期值 /
计数型门槛的 n 单位不可能是 questions；2 条关于 `known_gap`：
非空 / 与推导出的 required_n 一致）。

**为什么不用 `count_unit` 表达**：`count_unit` 的字面值是 `"samples"`，
但 G7 的 n 是 360 次**运行**，不是样本。沿用它会把 G7 的口径说错。

---

## D-002 G5 / G6 在现有样本集下结构性不可达

**缺口**：门槛的认证要求超过了它所测的总体。

- G5 需 n=68 个**问题**（`required_n_two_proportions(0.733, 0.85)` 现场推导），
  而 severe 档只有 20 条样本 × 每条 3 个问题 = **60 个问题**。
- G6 需 n=61 个**标注样本**（`certify_rate_max=0.05` ⇒ 0 事件的 95% 率上界
  约 3/n ≤ 5%），而 severe 样本总体只有 **20 条**。
  即便 20 条全部标注，n=20 < 61，门禁永远红。

**采用值**：**只登记，不改阈值**。给 G5 / G6 各加一个 `known_gap` 字段，
写明缺口的量级；`evaluation/gate_spec_report.py` 在推导报告里直接打印它。

**理由**：
1. 「门槛要求的样本量超过被测总体」不是「严」，是**不可达**——
   与 v0.4.0 把 G1/G2/G3 作废是同一类判断。
2. 改 `required_n` 会让门禁变绿，但**证明的仍然是别的东西**，
   属于「为了让门禁变绿而改判据」——本项目的纪律明令禁止。
3. 改口径的决定属于 v0.4.2 的范围（需先做可达性核算，
   核算结果决定「扩样本」还是「降口径」），**不在前置版里偷做**。

**后续**：v0.4.2 开工前必须先跑一次可达性核算（`docs/PromptCraft-v0.4.0-iteration-spec.md`
§3.1 的口径），产出缺口表，再决定扩样本还是改口径。
核算未完成前，v0.4.2 不许开跑。

---

## D-003 `must_not_question` 的标注范围

**缺口**：SPEC §4.3 要求 20 条 severe 样本**全部**带该字段，
但只强制填写翻译 3 条，其余 17 条写「能确定就填，不确定就留空」，
并附「**宁可漏填，不要凑数**」。

**采用值**：8 条有标注、12 条留空。
其中 3 条是 SPEC 表格强制（翻译场景，唯一被实测证实的失败案例），
另 5 条由 §4.2 的「典型反例」清单推导
（`v30_code_refactor_goal` / `v30_code_contradictory` / `v30_office_report_summary` /
`v30_qa_compare_plans` / `v30_data_analyze_excel`，
它们的用户原话命中了反例里的「这段代码 / 这份文档 / 这两个方案 / 这份数据」）。

**理由**：
- 那 5 条是**规则推导**而非实测证实，标注内容一律取反例里的**字面问法**
  （如「代码在哪里」「请粘贴代码」），子串匹配因此几乎不会假阳。
- 标注写成空列表而不是省略字段，两者的判定口径不同
  （`[]` = 有标注但本样本无需禁止的问法 ⇒ 走「无标注-不判定」）。
- 没有为了凑数把 12 条也填上——那会引入假阳性，而假阳性比没有门禁更坏。

**已知代价**：n=8 时，0 命中的 95% 率上界约 37.5%。
⇒ 「0 命中」只是**下限证据**，不能写成「不再问已知信息」。

---

## D-004 `gate_spec_report.py` 对 GBK 控制台做编码兜底

**缺口**：本机 PowerShell 控制台默认 GBK，而规范文本里出现了
`⇒`（U+21D2）等 GBK 编不了的符号，`print()` 会抛 `UnicodeEncodeError`
**把整份报告打断**。

**采用值**：脚本启动时 `sys.stdout.reconfigure(errors="replace")`，
编不了的字符退化成 `?`，其余照常输出，报告不中断。

**理由**：报告工具被自己的数据搞崩，比显示一个 `?` 严重得多。
这是实现细节，不改变任何判定逻辑。

---

## D-005 发布工作流按版本号取发布说明

**缺口**：`.github/workflows/release.yml` 把 `body_path` 和发布标题写死在
v0.4.0 上，v0.4.1 不改这个文件就发不出去（`fail_on_unmatched_files: true`
会让它直接失败）。

**采用值**：`body_path` 改为 `release-assets/RELEASE_NOTES-${{ steps.target.outputs.version }}.md`；
发布标题去掉 v0.4.0 专属文案；`workflow_dispatch` 的 tag 输入去掉写死的
`default: 'v0.4.0'`（留空即走 `github.ref_name`）。

**理由**：新版本不再需要改工作流文件。
「发布说明文件不存在 ⇒ 必须失败」这条保留 —— 静默发一个空说明比不发更坏。

---

## D-006 数据集真值必须排在模型输出之后

**缺口**：`verify_enhance.py` 的 `enhance_one()` 原来把模型输出 `**result`
展开在**最后**，于是模型输出里的同名字段会覆盖样本真值。
而 `parse_result` 只做 JSON 解析、**不过滤未知字段**，
`normalize_result` 也只补缺失的数组字段 —— 模型有可能吐出一个
`must_not_question` / `must_preserve` / `original`。

**采用值**：抽出 `build_record(sample, result, error)`，
把 `**result` 放在**最前**，数据集真值（`id` / `original` /
`must_preserve` / `must_not_add` / `must_not_question` /
`expected_behavior` / `error`）一律排在**后面**。

**理由**：否则判定会变成「拿模型自己声称的判据去判模型自己」——
判定照样出数，只是那个数是模型写的。这属于「把失败显示成成功」同族，
且比缺字段更难发现：数字看着完全正常。

**回归测试**：`evaluation/test_verify_enhance.py`
的 `test_model_output_cannot_override_dataset_ground_truth`
喂一条**敌意**模型输出（含清空后的 `must_not_question`、
改写过的 `original`、编造的 `id`），断言真值全部存活、
且第六维仍判「严重问题」。

---

## D-007 补装 `@types/node`（devDependency）

**缺口**：跨源断言需要在前端测试里读 `src-tauri/src/prompts.rs`，
于是 `src/promptVersion.test.ts` 用了 `node:fs` / `node:path` / `node:url`。
但项目**从未安装 `@types/node`** —— 此前没有任何 TS 文件碰过 Node API。
结果：`tsc` 报 `TS2307: Cannot find module 'node:fs'`，
`npm run build` 失败 ⇒ **CI 与发布工作流双双红**（本地 `npm test` 全绿，
因为 vitest 不做类型检查 —— 又一个「本机绿、CI 红」）。

**采用值**：`@types/node@22` 进 devDependencies（CI 用 Node 22）。

**理由**：这是任何要在 TS 里用 Node API 的项目的标准依赖；
不装它就写不了这条断言，删掉断言又会让前端版本号再次裸奔。
另可考虑改用 Vite 的 `?raw` 导入绕开 Node API，但那样还要额外引入
`vite/client` 类型声明，绕的弯更长。

**教训**：**本机 `npm test` 绿 ≠ `npm run build` 绿。**
vitest 不做类型检查，`tsc` 才做。发布前必须单独跑一次 `npm run build`。

---

## D-009 发布说明路径必须用 tag 而不是 version

**缺口**：`release.yml` 的 `body_path` 参数化时用了
`steps.target.outputs.version`（= `0.4.1`，**去掉了前缀 v**），
而发布说明的文件名约定是 `RELEASE_NOTES-<tag>.md`
（= `RELEASE_NOTES-v0.4.1.md`，**带 v**）⇒ 路径对不上。

**后果**：发布工作流会在**构建完十几分钟之后**、进入发布步时才失败。
本地任何测试都发现不了——这条路径只在 GitHub runner 上解析。

**采用值**：`body_path` 改用 `steps.target.outputs.tag`；
同时新增 `evaluation/test_release_workflow.py`，把这条路径变成**本机可判定**：

- 解析出 `body_path` 表达式 → 按当前版本算出实际路径 → 断言该文件真的存在；
- 断言表达式用的是 `tag` 而不是 `version`；
- 反向对照：断言 `RELEASE_NOTES-v<version>.md` 与 `RELEASE_NOTES-<version>.md`
  解析到**不同**的文件（否则测试失去区分力）；
- 断言 `files:` 里的产物名与 `build-portable.ps1` 实际生成的 zip 名一致；
- 断言发布说明里不含效果宣称。

**已验证的反向对照**：把 `body_path` 改回 `version` 口径后，
上述测试有 2 条立刻转红。

**理由**：**发布管线本身也是代码，也要有门禁。**
一个只会在 CI 上、且在流水线末尾才暴露的路径错误，
成本是十几分钟的构建时间 + 一次失败发布。

---

## D-010 随包 README 不得写死版本号

**缺口**：`release-assets/README.txt` 是**静态文件**、每版都原样上传，
但首行写死了「PromptCraft **0.1.0**」。
从 v0.3.x 一路到 v0.4.1，它一直随包发着一个过期了三个大版本的版本号 ——
v0.4.0 那一版也是这么发出去的。

**采用值**：去掉版本号，首行改为「PromptCraft - Windows 10/11 64 位便携版」，
并加一句说明：版本号请看压缩包文件名与程序属性。

**理由**：静态文件里写死的版本**必然过期**；
随包发出去的东西在陈述一件不真实的事 —— 这正是本项目
「把失败显示成成功」那一族问题的变体，只是这次过期的是版本号而不是状态。

**回归测试**：`evaluation/test_release_workflow.py`
的 `test_shipped_readme_does_not_carry_a_stale_version`
（断言随包 README 里不出现任何 `x.y.z` 形式的版本号）。
