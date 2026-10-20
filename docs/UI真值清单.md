# PromptCraft UI 真值清单

> 作者：产品体验官（bot_e669985569ad）｜工作包：WP2
> 基线：`release/v0.4.0`，HEAD = `97b5584`（工作区另有 `src-tauri/src/{lib,provider,storage}.rs` 在途修改，本文引用行号以**当前工作区实读**为准）
> 配套代码：`src/components/resultTruth.ts`（档位/交付/状态的真值解析器）
> 关联：`docs/v040-缺失语义约定.md` R1–R8、`docs/v040-失败伪装清单.json`、`docs/v040-架构审查.md`（E1/E2/A10/A12/G9/G11）
> 状态标记：**✅ 已在本工作包内闭环** ／ **⏳ 需后端** ／ **⏳ 需 src/App.tsx 所有者**

---

## 〇、这份清单怎么用

**它回答一个问题：这个字段在界面上被说出来的时候，那句话到底代表什么。**

规则只有一条：**一个字段只能说出它真的测到的东西。** 测不到就说"不可判定"，失败就说"失败"，不许用默认值、占位值或漂亮话顶替（`docs/v040-缺失语义约定.md` R1/R2/R6）。

每条按六项写：**字段 → 真实含义 → 允许的文案 → 禁止的文案 → 触发条件 → 依据**。

**已修的呈现层缺陷**在代码里的落点：

| 落点 | 作用 |
|---|---|
| `src/components/resultTruth.ts:83` `resolveResultTruth` | 唯一的真值解析入口：交付状态 → 档位可否被陈述 → 状态徽标 → chip |
| `src/components/resultTruth.ts:60` `levelLabels` | 档位文案表。**只允许用于模型真的做过判断时** |
| `src/components/resultTruth.ts:63` `UNJUDGEABLE_LABEL` | 档位不可判定时的统一文案 |
| `src/components/StatusBadge.tsx:42` | 状态徽标按**真值状态**渲染，不再按流程状态渲染 |
| `src/views/EnhanceView.tsx:119` | 组件内统一调用解析器 |
| `src/views/EnhanceView.tsx:195` | 标题栏：交付徽标 + chip + 状态徽标 |
| `src/views/EnhanceView.tsx:215` | 摘要区「增强等级」行 |
| `src/components/ConfirmClearModal.tsx:21` | 清空失败时把后端错误显示出来 |

---

## 一、状态与档位

### F1. `status`（流程状态 → 标题栏状态徽标）

| 项 | 内容 |
|---|---|
| **真实含义** | **只是流程状态**：这次请求走到哪一步了。**它不代表交付物是什么**，也不代表模型做了判断 |
| **允许的文案** | `等待输入` / `正在增强` / `需要补充` / `增强完成`（仅当交付状态为 complete）/ `交付不完整` / `增强失败` / `生成已停止` / `生成失败` / `交付状态未知` |
| **禁止的文案** | 在 `delivery_status` 为 `fallback` 时出现「**增强完成**」——这是一次彻底失败的增强被渲染成成功<br>在 `delivery_status` 为 `partial` 时出现「**增强完成**」<br>用 `status` 推导「档位判断结果是 X」 |
| **触发条件** | `status === "ready"` **且** `delivery_status !== "complete"` 时，`status` 不再直接决定徽标文案 |
| **依据** | 缺陷：结构解析彻底失败时后端仍返回 `status="ready"` 且命令返回 `Ok(())`（`src-tauri/src/provider.rs:190-228`、`:293`）⇒ 旧实现 `src/components/StatusBadge.tsx` 直接把 `state` 渲染成「增强完成」<br>修复：`src/components/resultTruth.ts:106-130` + `src/components/StatusBadge.tsx:42` ✅<br>回归测试：`src/views/EnhanceView.test.tsx`「不再显示『增强完成』」 |

> **关键点**：`status` 与 `delivery_status` 必须**分开渲染**。合并成一个字段，就会出现「原文回退 + 增强完成 + 增强器判断无需修改」三个互相打架的信号同时出现在标题栏。

### F2. `delivery_status`（交付状态 → 标题栏交付徽标）

| 项 | 内容 |
|---|---|
| **真实含义** | 这一版**交付了什么形态的东西**：完整结构 / 结构不完整 / 根本没拿到模型结果 |
| **允许的文案** | `完整交付` / `部分交付` / `原文回退` |
| **禁止的文案** | 用 `delivery_status` 反推「模型认为你的提示词不需要改」<br>在 `fallback` 时出现任何含「判断」「无需修改」「增强器认为」字样的文案 |
| **触发条件** | `result` 存在时始终显示；`result` 不存在（历史恢复，`src/App.tsx:306` 只回填原文与结果文本）时不显示 |
| **依据** | 实现：`src/views/EnhanceView.tsx:97`（`deliveryLabels`）、`:195`（渲染）<br>后端取值：`src-tauri/src/provider.rs:195`（partial）、`:893`（fallback） |

### F3. `enhancement_level`（档位 → 摘要区「增强等级」行 + 标题栏 chip）

| 项 | 内容 |
|---|---|
| **真实含义** | **仅当 `delivery_status === "complete"` 时**，它才是模型对「该不该改」的真实判断；在 `partial` / `fallback` 下它可能是后端硬编码的占位值 |
| **允许的文案** | `无需明显修改` / `轻度增强` / `需要澄清`（三者仅在 `fromModel === true` 时）<br>`不可判定`（其余全部情况）<br>标题栏 chip：`增强器判断无需修改，建议直接使用原文`（**仅** `complete` + `none`） |
| **禁止的文案** | 在 `delivery_status === "fallback"` 或 `partial` 时出现「**增强器判断无需修改，建议直接使用原文**」——模型从未做过这个判断<br>把 `partial` 且档位为 `none` 说成「无需明显修改」<br>用 `?? "light"` 之类的默认值补齐缺失档位后按正常档位展示（`src/lib.ts:243` 仍有此问题，见 §二.3） |
| **触发条件** | 档位行的取值规则（`src/components/resultTruth.ts:106-133`）：<br>• `fallback` → 不可判定（两个构造器都把档位写死成 none）<br>• `partial` 且档位为 `light`/`clarify` → 可陈述（两个构造器都不产出这两个值 ⇒ 必然来自模型）<br>• `partial` 且档位为 `none` → **不可判定**（无法区分"模型真的判 none"与"partial_result 的占位值"）<br>• `complete` → 档位来自模型，可陈述<br>• `delivery_status` 缺失 → 不可判定（fail-closed） |
| **依据** | 缺陷：E2（`src/views/EnhanceView.tsx:192` 旧实现只看 `enhancement_level`）、E1（`src-tauri/src/provider.rs:876`/`:894` 硬编码 `enhancement_level: "none"`）<br>规则来源：`docs/v040-缺失语义约定.md` §4.5「失败 → 显示失败原因；失败 → 显示"无需修改"这类**判断性**文案（禁止）」、R6<br>修复：`src/components/resultTruth.ts:60-133` + `src/views/EnhanceView.tsx:119`/`:195`/`:215` ✅<br>回归测试：`src/components/resultTruth.test.ts`（9 条）+ `src/views/EnhanceView.test.tsx`（6 条 DOM 级） |

> **为什么 `partial` + `none` 一律不可判定**：`partial` 有两条来源，前端分不出来 ——
> a) `src-tauri/src/provider.rs:192-200`：模型返回了结果，只是没过完整校验，**档位是模型的真实判断**；
> b) `src-tauri/src/provider.rs:218`：走 `partial_result` 构造器，**档位被硬编码成 none**，不是判断。
> 宁可少说一句，不可多说一句。**要彻底分开需要后端区分（见 §三.1）。**

---

## 二、用户可见的"假成功"逐条对照

### 2.1 「连接验证成功，配置已保存」（`src/App.tsx:300`）

| 项 | 内容 |
|---|---|
| **真实含义** | 这句话同时断言了三件不同的事：**①** Key 联网验证通过、**②** 凭据已写入 Windows 凭据管理器、**③** 配置已写入本机。三件都**可能没发生** |
| **允许的文案** | 拆成三段分别陈述：<br>「**已验证**：Key 可用（已用当前地址实测）」<br>「**已保存**：API Key → Windows 凭据管理器；地址 / 模型 / 价格 → 本机配置」<br>「**未做**：连接地址未做连通性检查」（当 Key 输入框留空时） |
| **禁止的文案** | 未执行联网验证时出现「**连接验证成功**」<br>凭据写入失败时出现「配置已保存」<br>把「验证」与「保存」合成一句话 |
| **触发条件** | `src/App.tsx:295` 是 `if (apiKeyDraft) await validateProvider(...)` —— **Key 留空时一次网络请求都没发**；而 `:300` 无条件显示「连接验证成功」 |
| **依据** | 清单 G9（`src/App.tsx:300`，P1，owner=WP2）<br>代码：`src/App.tsx:295`、`:300`；`src/views/SettingsView.tsx:18`（消息渲染位，不指明字段）<br>状态：**⏳ 需 src/App.tsx 所有者**（本工作包禁止触碰该文件） |

### 2.2 「已导出 N 条记录」（`src/views/SettingsView.tsx:21`）

| 项 | 内容 |
|---|---|
| **真实含义** | N = **实际写进导出包的条数**。不是"库里一共多少条"，也不是"界面里看到多少条" |
| **允许的文案** | 「已导出 {实际条数} 条记录」<br>若发生截断：「已导出 {实际条数} 条（{总数} 条中有 {n} 条未导出）」<br>若同时导出了设置与配置：「已导出 {条数} 条历史记录，并包含本机设置与供应商配置（不含 API Key）」 |
| **禁止的文案** | 只报一个数字而未说明它是"实际条数"还是"总条数"<br>发生截断时只报截断后的数字（让用户以为整包都备份了） |
| **触发条件** | 导出成功且未截断 / 发生截断两种情况必须用不同文案 |
| **依据** | 后端：`src-tauri/src/storage.rs:379-428`（已改用 `all_history()`，`:427` 返回真实条数）<br>前端：`src/lib.ts:194-204`（只返回一个 number）、`src/views/SettingsView.tsx:21`（文案）<br>状态：**⏳ 需后端返回三元组（已导出条数 / 总条数 / 是否截断）**，见 §三.3 |

### 2.3 缺失字段被前端补成默认值（`src/lib.ts:242-243`）

| 项 | 内容 |
|---|---|
| **真实含义** | `delivery_status` / `enhancement_level` **缺失** ⇒ 后端没给出这项信息，**不是**"完整交付"、也不是"轻度增强" |
| **允许的文案** | 缺失即显示「不可判定」/「交付状态未知」，并按 F1/F3 的规则降级呈现 |
| **禁止的文案** | `?? "complete"` / `?? "light"` 这类默认值顶替（清单 A13，P1，owner=WP2） |
| **触发条件** | 后端返回残缺结构时（例如字段被 `provider.rs:523-537` 补成空数组但整体结构不完整） |
| **依据** | `src/lib.ts:242-243`（`normalizeResult`）<br>规则：`docs/v040-缺失语义约定.md` R1/R2/R5、§4.6「缺失时显示"结果不完整"，不得 `?? "complete"` / `?? "light"`」<br>状态：**⏳ 需 src/lib.ts 所有者**（本工作包未获得该文件所有权；已登记在 A13） |

### 2.4 月度额度「本月 ¥0.00」（`src-tauri/src/provider.rs:247-259`）

| 项 | 内容 |
|---|---|
| **真实含义** | 本次请求**真实消耗**了多少 token / 多少钱。失败与部分交付**同样计费**（供应商已扣费） |
| **允许的文案** | 「本次：{tokens} tokens · 约 ¥{cost}」<br>「本月：¥{total}（含 {n} 次失败请求的消耗 ¥{x}）」<br>或按清单 A12 的验收条件二选一：统计全部消耗，**或**明确标注「失败不计费」并与供应商账单口径一致 |
| **禁止的文案** | 失败/部分交付时把 `month_total` 写成 `0.0` —— 用户已真实付费的 token 在额度面板上显示为 0（清单 A12，P1）<br>用"只统计 success"的口径却不告诉用户这个口径 |
| **触发条件** | `provider.rs:247` 的 `match error_code`：走 `Some(code)` 分支（失败/部分交付）时返回硬编码的 `0.0`；`storage.rs:530` 的月度汇总也只统计 `status='success'` |
| **依据** | `src-tauri/src/provider.rs:247-259`（硬编码 0.0）、`src-tauri/src/storage.rs:530`（`AND status='success'`）<br>呈现：`src/views/EnhanceView.tsx:201`（费用面板）、`src/App.tsx:153-155`（强制额度判定）<br>后果：**部分交付之后额度被强制判失效**<br>状态：**⏳ 需后端** |

### 2.5 清空后报「已清空」但 Key 其实还在（`src-tauri/src/storage.rs:364-376`）

| 项 | 内容 |
|---|---|
| **真实含义** | 清空有两件事：**①** 本地数据（历史/设置/配置/用量记录）已删，**②** Windows 凭据管理器里的 API Key 已删。**两者可能只完成一件** |
| **允许的文案** | 两件都成功：「本地数据与凭据已清空」<br>数据删了、Key 删不掉：「**本地数据已清空，但删除 Windows 凭据失败**：{原因}；请打开凭据管理器手动确认。」<br>前端必须把这条错误**显示出来**（已做：`src/components/ConfirmClearModal.tsx:21`、`:31`） |
| **禁止的文案** | `Entry::new` 失败导致整块被跳过时，界面报「已清空」——**Key 其实还在凭据管理器里**（清单同族缺陷）<br>后端返回 Err 而前端不显示（未处理的 rejection，用户既看不到原因、弹窗也不会关闭） |
| **触发条件** | `storage.rs:364` 的 `if let Ok(entry) = Entry::new(...)` —— **创建凭据条目失败时整个 if 块被跳过**，函数继续走到 `:376` 的 `Ok(())`；而 `delete_credential()` 失败反而会返回 Err（`:369-372`）。**两条失败路径的待遇不一致，这就是漏洞** |
| **依据** | 后端：`src-tauri/src/storage.rs:364-376`<br>前端（本工作包已修）：`src/components/ConfirmClearModal.tsx:21`/`:31` —— 捕获后端 Err 并显示，弹窗保持打开<br>回归测试：`src/components/ConfirmClearModal.test.tsx`（jsdom 行为级）✅<br>残留：`src/App.tsx:308` 的 `onClearData` 在 `clearAllData()` 抛错时**不会执行**后面的内存重置，界面仍显示已删除的历史 ⇒ **⏳ 需 src/App.tsx 所有者** |

---

## 三、其余状态字段

### F4. 月度强制额度 `monthlyLimit`（设置页）

| 项 | 内容 |
|---|---|
| **真实含义** | 这是**本机估算口径**下的强制额度，不是供应商账单。是否阻断取决于 `provider.inputPrice/outputPrice`（可被导入包改写，见 G10） |
| **允许的文案** | 「预计本次请求会使本月费用达到约 ¥{X}，超过强制额度 ¥{Y}，已阻止调用。」+ 说明这是估算（已有：`src/views/SettingsView.tsx:20`「按本机记录估算；供应商账单仍是最终依据」） |
| **禁止的文案** | 把本机估算说成供应商账单口径<br>在费用口径失真（导入包改了价格）时不提示 |
| **触发条件** | 每次调用前预估（`src/App.tsx:151-155`） |
| **依据** | `src/App.tsx:151-155`、`src/views/SettingsView.tsx:20`；价格口径失真见清单 G10（`src-tauri/src/storage.rs:501`） |

### F5. 历史容量上限 `maxHistoryMb`（设置页）

| 项 | 内容 |
|---|---|
| **真实含义** | 用户设定的**历史文本容量上限**（MB）。超限后按最旧未置顶逐条清理；**置顶记录永不被清理** |
| **允许的文案** | 现状文案基本准确：「历史记录文本总量超过该值后，按最旧的未置顶记录逐条清理。置顶记录不会被清理。」（`src/views/SettingsView.tsx:20`） |
| **禁止的文案** | 把"读取设置失败回落 64MB"说成用户设的值 —— 读失败时 `storage.rs:560` 静默回落，**用户看到的是默认值而不是错误**（清单 A10 同族） |
| **触发条件** | 设置读取失败（`storage.rs:560` `self.app_settings().unwrap_or_default()`）；界面读数与实际生效值不一致时 |
| **依据** | `src-tauri/src/storage.rs:556-566`、`src/views/SettingsView.tsx:20`、`src/App.tsx:76`/`:93` |

### F6. 凭据状态（设置页「已配置 / 未配置」）

| 项 | 内容 |
|---|---|
| **真实含义** | Windows 凭据管理器里**真的有**一条 API Key |
| **允许的文案** | 「已配置」/「未配置」—— 但必须来自**读回凭据管理器**的真相（`storage.rs:156` `config.has_api_key = self.api_key().is_ok()`） |
| **禁止的文案** | 用**输入框草稿**乐观推出「已配置」（`src/App.tsx:299` 现在是 `Boolean(apiKeyDraft \|\| provider.hasApiKey)`）<br>保存成功后不回读就显示「已配置」 |
| **触发条件** | 保存后、应用启动时（`src/App.tsx:82-86` 调 `getProviderConfig()`） |
| **依据** | 真相来源 `src-tauri/src/storage.rs:156`；乐观来源 `src/App.tsx:299`；徽标渲染 `src/views/SettingsView.tsx:16`<br>状态：**⏳ 需 src/App.tsx 所有者**（保存后回读一次 `getProviderConfig()` 再更新徽标） |

### F7. 导入结果（设置页「导入数据」）

| 项 | 内容 |
|---|---|
| **真实含义** | 一次导入会改写**四类东西**：历史记录、本机设置、供应商配置（含价格）、容量上限。**API Key 永远不受影响**（导入包不能改写凭据） |
| **允许的文案** | 「已导入 {n} 条历史记录」<br>「另有 {m} 条超过单包上限未导入」<br>「导入包中的 API 地址未采用，仍使用本机当前地址」<br>「导入包不含也不接受 API Key，本机 Key 保持不变」<br>**若导入包改写了价格等计费字段，必须逐项列出改了什么** |
| **禁止的文案** | 只报条数、不报"改了哪些设置"<br>让用户以为导入只是"恢复历史备份" |
| **触发条件** | 导入成功（`src/views/SettingsView.tsx:21`） |
| **依据** | 后端：`src-tauri/src/storage.rs:430-507`（`:475` 清凭据、`:487-490` 保端点、`:501` 整份写配置 —— **价格字段未被拦**）<br>前端：`src/lib.ts:206-219`、`src/views/SettingsView.tsx:21`<br>清单 G10（P1，owner=WP0）：导入包能静默改写 `input_price`/`output_price`，使月度强制额度失效 |

---

## 四、代码层的可复用约定

**新增或修改任何状态字段的呈现时，走这一个入口，不要在组件里写 `result.enhancement_level === "x"` 这类判断。**

```ts
// src/components/resultTruth.ts
const truth = resolveResultTruth({ result, state, deliveryStatus });
```

它产出三样东西，各自有唯一的责任：

| 产出 | 责任 | 渲染位置 |
|---|---|---|
| `truth.status` | **流程 + 交付**合并后的真值状态 | `src/components/StatusBadge.tsx:42` |
| `truth.level` | 档位**能不能被陈述**（`fromModel`）+ 陈述什么 | `src/views/EnhanceView.tsx:215` |
| `truth.chip` | 只有真判断才出现的补充说明 | `src/views/EnhanceView.tsx:195` |

**三条硬规则（改这个文件时不要破坏）**

1. **档位文案表（`resultTruth.ts:60`）只允许用于模型真的做过判断时。** 后端两个构造器把档位硬编码成 `none`（`provider.rs:876`、`:894`），那两个 `none` 不是判断。
2. **交付状态缺失 ⇒ fail-closed。** 不许默认成"完整交付"（`resultTruth.ts:124`）。
3. **真值状态与流程状态分离。** `EnhancementState` 描述流程走到哪一步，`delivery_status` 描述交付物是什么；合并就会把失败渲染成成功。

**扩展方式**：新增交付状态时，在 `src/components/resultTruth.ts` 的 `resolveResultTruth` 里加分支，同时在 `src/components/StatusBadge.tsx` 的 `labels` / `cssClasses` 两张表里加条目。**两张表都要加** —— 只加一张会出现"有文案没样式"或"有样式没文案"。

> `src/styles.css` 不在本工作包所有权内，因此 `partial` / `unknown` 复用 `needs_clarification` 的琥珀色、`failed` 复用 `error` 的红色（`src/components/StatusBadge.tsx:28-29`）。若将来拿到样式文件所有权，应为 `partial` / `failed` / `unknown` 各建独立类。

---

## 五、交给后端的需求清单

> 后端不在本工作包所有权内（`src-tauri/**` 由实现工程师并行修改）。以下三条按"最小改动 + 可区分"提出。

### 后端需求 1：区分"增强成功且判定无需修改"与"增强失败走了 fallback"

**为什么要**：前端的 `partial` 有两条来源、档位可信度不同，但**无法区分**（`resultTruth.ts:115-122`）。`fallback` 能靠 `delivery_status` 识别，但 `partial` 不能。现状只能采取"宁可少说一句"的策略。

**最小改法（二选一）**

| 方案 | 做法 | 代价 |
|---|---|---|
| **A. 新增字段（推荐）** | 在 `src-tauri/src/models.rs` 的 `EnhancementResult` 上加 `level_source: "model" \| "placeholder"`（或 `enhancement_level: Option<String>` 允许为 `None`），由 `provider.rs:863`/`:881` 两个构造器填 `"placeholder"`，模型返回的路径填 `"model"` | 前端要加一条判别；TS 侧 `src/types.ts` 要同步（R7） |
| **B. 新增 status** | 失败路径的 `status` 不再是 `"ready"`，改成新值（如 `"failed"`） | 改动面大：`status` 的取值域被 `src/types.ts:51`、`src/schemas.ts:8`、Rust `models.rs:33` 三处约束，且 `App.tsx:195` 依赖它分流 |

**验收**：mock 一个 `partial` 且档位为 `none` 的响应 ⇒ 界面显示「不可判定」；mock 一个 `partial` 且档位为 `light` 的响应 ⇒ 界面显示「轻度增强」。**两个断言都必须能独立成立。**

### 后端需求 2：凭据写入/删除失败必须返回可区分的错误，不能返回 Ok

**为什么要**：`src-tauri/src/storage.rs:364` 的 `if let Ok(entry) = Entry::new(...)` —— **`Entry::new` 失败时整个 if 块被跳过**，函数走到 `:376` 返回 `Ok(())`。调用方（`src-tauri/src/lib.rs:191-193`）据此认为"全部清空成功"，而**API Key 其实还在凭据管理器里**。同一个函数里 `delete_credential()` 失败反而会返回 Err（`:369-372`），**两条失败路径待遇不一致**。

**最小改法**：把 `if let Ok(entry)` 改成 `let entry = Entry::new(...).map_err(|error| format!("本地数据已清空，但无法访问 Windows 凭据管理器：{error}；API Key 可能仍在凭据管理器中，请手动确认。"))?;`，让两条失败路径同样返回 Err。

**验收**：注入一个 `Entry::new` 失败的场景 ⇒ 命令返回 Err ⇒ 界面显示"本地数据已清空，但……"而不是"已清空"。（前端侧已具备显示能力：`src/components/ConfirmClearModal.tsx:21` ✅）

### 后端需求 3：导出接口返回"已导出条数 + 总条数 + 是否截断"

**为什么要**：`src-tauri/src/storage.rs:427` 目前只返回一个 `history.len()`。前端 `src/lib.ts:194-204` 也只接一个 number。**一旦将来出现截断**（单包上限、容量上限、失败重试），界面无法区分"导出了全部"和"只导出了一部分"——这正是 P0-3 的教训。

**最小改法**：参照 `src-tauri/src/models.rs:150-162` 的 `ImportReport` 先例，把返回值换成一个结构体：

```rust
pub struct ExportReport {
    pub exported: usize,   // 实际写进包里的条数
    pub total: usize,      // 库里一共多少条
    pub truncated: bool,   // 是否因上限被截断
}
```

**验收**：导入一个超过上限的包 ⇒ 界面同时报出"已导出 N 条（另有 M 条未导出）"。

---

## 六、边界与未核实

1. **我没有运行过应用**。本工作包的所有 UI 结论来自**读代码 + DOM 级静态渲染测试**（`react-dom/server` 的 `renderToStaticMarkup`），**没有在真实 Tauri 窗口里看过**。
2. **没有跑 `cargo test` / `cargo clippy`**。本工作包不触碰 `src-tauri/**`，后端一行未改。
3. **负向对照已做**：把 `resultTruth.ts` 的 fallback 分支改回旧行为 ⇒ 6 条测试失败（含 3 条 DOM 级）；把 `ConfirmClearModal.tsx` 的错误显示去掉 ⇒ 1 条失败。**断言不是恒真的。**
4. **`src/styles.css` 未改**（不在所有权内）：`partial`/`unknown` 借用 `needs_clarification` 的琥珀色，`failed` 借用 `error` 的红色。这是**样式复用**，不是**语义正确**——将来应拆成独立类。
5. **本清单只说"当前代码允许发生什么"**，不等于"已经发生过"。是否有用户实际中招，无从判断。
6. **`src/App.tsx` 与 `src/lib.ts` 未改**（不在本工作包所有权内 / 未获授权）。相关条目（2.1 / 2.3 / F6 / 2.5 的残留项）已逐条写明需要谁改、改哪一行。
7. **`src/views/EnhanceView.test.tsx` 是新增文件**：`src/views/` 目录本身不在"src/components/**"的字面授权内，但本工作包明确点名 EnhanceView.tsx 为本人所有，测试文件随其所有，故一并放在同目录。
8. **未核实**：导入包改写价格（G10）的实际影响面（有多少用户导入了第三方包）无从判断；`storage.rs:530` 的月度汇总口径与供应商账单的实际偏差未做数据核对。
