# PromptCraft v0.4.1 — 发布记录

> **本版不宣称任何效果数字。** 它是 v0.4.2 效果线的**前置**：
> 只装机器可判的东西，不产生任何效果测量值。
> v0.4.0 的 41 条「把失败显示成成功」闭环结论保持不变。

- **tag**：`v0.4.1`（annotated）→ commit `5031cbf`
- **分支**：`release/v0.4.1` → 已并入 `main`（两者同指 `5031cbf`）
- **Release**：<https://github.com/wewe29/ai-prompt-enhancer/releases/tag/v0.4.1>
- **发布时间**：2026-10-21T13:40:48Z
- **工作流**：`Release` run `39607795454` → success；`CI` run `39607784634` → success

---

## 1. 交付了什么

| 交付物 | 位置 | 说明 |
|---|---|---|
| 提示词 `promptcraft-v2.5.0` | `src-tauri/src/prompts.rs` | 第 8 条之后追加「问题自检」段 + 4 条典型反例（**只追加**） |
| 三处同步 | `evaluation/enhancer.py` / `src/App.tsx` | Rust↔Python 逐字断言；Rust↔TS 跨源断言（**前端此前无任何保护**） |
| 样本标注 | `evaluation/samples_v30.yaml` | 20 条 severe 全部新增 `must_not_question`；8 条有标注、12 条留空 |
| 第六维判定 | `evaluation/verify_enhance.py` | `redundant_question`，「无标注-不判定」**不计入通过** |
| 门禁事实 | `evaluation/gate_spec_v040.json` | v040.1 → v040.2：新增 `n_unit`、G5/G6 的 `known_gap` |
| 发布管线 | `.github/workflows/release.yml` | 按 tag 取发布说明，新版本不必再改工作流 |
| 偏离登记 | `docs/DEVIATIONS.md` | 10 条 |

---

## 2. 验收结果（全部本地跑通，且在 CI 上再跑一遍）

| 命令 | 结果 |
|---|---|
| `cargo test --manifest-path src-tauri\Cargo.toml` | **74 passed / 0 failed**（v0.4.0 为 70） |
| `npm test` | **87 passed**（10 个文件；v0.4.0 为 84） |
| `py -3 -m pytest evaluation -q` | **309 passed**（v0.4.0 为 279） |
| `cargo fmt --all -- --check` | 通过（退出码 0） |
| `npm run build`（`tsc && vite build`） | 通过（退出码 0） |
| `py -3 scripts\check-fail-open.py` | **OPEN 0 / FIXED 41 / UNVERIFIED 0**（退出码 0） |
| `py -3 evaluation\check_gate_behavior.py` | 全部 committed fixture 判定符合预期（退出码 0） |
| `py -3 evaluation\gate_spec_report.py` | 退出码 0，G5/G6 的 `known_gap` 出现在报告里 |

---

## 3. 过程中被推翻的三个判断（都留了痕）

1. **「本地全绿就等于没问题」——不成立。**
   第一次打 tag 时 CI 与发布工作流**双双红**，而本机 `npm test` 全绿：
   `src/promptVersion.test.ts` 用了 `node:fs`，而项目**从未装过 `@types/node`** ⇒
   `tsc` 报 TS2307 ⇒ `npm run build` 失败。
   **vitest 不做类型检查，`tsc` 才做** ⇒ 「本机 `npm test` 绿」不等于「能构建」。
   已把 `npm run build` 补进发布前必跑清单。

2. **「模型输出排在前面更安全」——恰好相反。**
   `parse_result` **不过滤未知字段** ⇒ 模型有可能吐出一个同名的
   `must_not_question` / `must_preserve` / `original`。
   若数据集真值排在模型输出**之前**，模型就能把自己的判据换掉 ——
   判定照样出数，只是那个数是模型写的。
   现抽出 `build_record()`，真值一律排在 `**result` 之后，并配**敌意模型输出**的回归测试。

3. **「发布管线不算代码，不需要门禁」——不成立。**
   `body_path` 曾参数化成 `steps.target.outputs.version`（= `0.4.1`，去 v），
   而文件名的约定是 `RELEASE_NOTES-<tag>.md`（带 v）
   ⇒ 会在**构建完十几分钟之后**的发布步才失败，本地任何测试都发现不了。
   现新增 `evaluation/test_release_workflow.py`（6 条）把这条路径变成**本机可判定**，
   并已做**反向对照**（改回错误口径立刻有 2 条转红）。

另修：随包 `README.txt` 自 v0.3.x 起一直写死「PromptCraft **0.1.0**」，
现去掉版本号（版本由 zip 文件名与 exe 版本信息承载），并加测试钉住。

---

## 4. 发布产物核对（三方一致）

| 项 | 值 |
|---|---|
| 产物 | `PromptCraft-0.4.1-windows-x64-portable.zip` |
| 字节数 | 9,308,238（与 GitHub 记录一致） |
| SHA-256 | `322dcb5151837cbda2ef745ca3ac670b3d42f85d55fb4817353dced46e3dbd92` |
| 三方比对 | 实际下载文件 = 随包 `SHA256-zip.txt` = GitHub 资产 digest → **一致** |
| 附件 | zip + `SHA256-zip.txt` + `README.txt` |
| Release 状态 | `isDraft=false`、`isPrerelease=false`、`/releases/latest` → **v0.4.1** |
| 发布说明正文 | 3,514 字符，含「本版本不宣称任何效果数字」声明；**效果宣称扫描 0 命中** |

---

## 5. 边界（这一版**没有**做什么）

- **没有**任何效果测量值：`v040_gates` 未产生任何一项。
- **没有**宣称命中率、整体质量、档位稳定性。
  档位稳定性那三条门槛（G1/G2/G3）已在 v0.4.0 作废，本版一句未提。
- **没有**跑 `verify_enhance.py`（需 API Key 且花钱，且本版不宣称效果）。
- 第六维是**保守的下限**：子串匹配只抓字面复现，改写过的问法会漏报
  ⇒ 门槛 6 是下限证据，门槛 4/5 的人工盲评才是权威度量。

---

## 6. 下一步（v0.4.2 阶段 A：**只做核算，不跑评测**）

**先做可达性核算，再决定跑不跑。** 三处缺口已登记在
`evaluation/gate_spec_v040.json` 的 `known_gap` 里：

| 门槛 | 要求 | 现有样本集 | 结论 |
|---|---|---|---|
| G4 | n≥5 **问题**，R≥2 | 翻译场景 9 个问题 | 可达（但 v2.5.0 后可能减少） |
| G5 | n≥68 **问题**，R≥2 | severe 20 条 × 3 问 = **60 个问题** | **不可达** |
| G6 | n≥61 **标注样本** | severe 总体 **20 条** | **结构性不可达** |

且 **v0.4.1 的提示词改动会让 severe 档转澄清的条数变少**（自检规则第 3 条明写
「不要为了凑问题而提问」）⇒ 可评问题数只会更少 ⇒ 缺口不会自己消失。

⇒ 先跑一次 v2.5.0 的 `verify_enhance`，统计
`n_units` / `n_questions` / `n_translate_questions`，产出缺口表，
再在「扩样本」与「降口径」之间做决定。**核算未完成前不许开跑。**
