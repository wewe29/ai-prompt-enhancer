# PromptCraft v0.2.1-v0.3.0 迭代实施规格

> 文档状态：可直接实施  
> 基于版本：PromptCraft 0.2.0  
> 适用项目：`F:\ai-prompt-enhancer`  
> 编写日期：2026-08-12

## 1. 文档目的

本文件是下一阶段的完整实施依据。执行者应先阅读现有代码和本文件，再按阶段顺序修改；不得跳过可靠性修复，直接继续增加模型、画像或界面功能。

本轮迭代的核心目标不是继续扩写提示词，而是让 PromptCraft 能够稳定判断：

1. 当前提示词是否真的需要增强。
2. 应该轻度修改、提出澄清，还是基本保留原文。
3. 即使模型没有返回标准 JSON，用户是否仍然能拿到可用结果。
4. 增强带来的质量收益是否能够通过可信评测证明。

## 2. 当前基线与已知问题

### 2.1 已有能力

- Tauri 2 + React 19 + TypeScript + Rust Windows 桌面应用。
- DeepSeek API、自定义模型、流式生成、停止和自动重试。
- 最多三轮澄清、假设、修改明细和可选建议。
- 本地附件提取、敏感信息检查、SQLCipher 历史和 Windows 凭据管理器。
- 提示词任务类型识别、画像预设、本地预检和键盘快捷键。
- Python 评测框架、模型横向基准、长度控制组、双裁判、置信区间和回归样本。

### 2.2 已验证的主要问题

1. 最新 22 条模型基准中，大量样本因“没有返回结构化结果”或“JSON 无法解析”失败，只有约一半进入有效评分。
2. Rust 后端把“必须恰好返回 5 条建议”等非核心字段当成整次增强成功的硬条件；主提示词即使可用，也会被整体丢弃。
3. 前端收到后端错误后进入 `error`，用户只能重新生成，不能直接保留原文继续工作。
4. 现有结果只有 `ready` 与 `needs_clarification`，无法区分“无需修改”“轻度修改”“降级结果”。
5. 现有本地预检对所有任务统一检查受众、格式和约束，容易对代码、翻译和简单问答给出不相关提示。
6. 评测体系功能已较丰富，但真实有效样本率不足，因此不能用平均得分或模型排名作为产品结论。
7. F 盘上 Vite、Cargo 和 pytest 的缓存目录存在权限问题，导致本地测试命令可能把环境故障误报成代码故障。

## 3. 总体实施原则

### 3.1 阶段顺序

必须按以下顺序实施：

1. `v0.2.1`：增强链路可靠性和失败降级。
2. `v0.2.2`：增强必要性判断和结果解释。
3. `v0.3.0`：可信效果评测与发布结论。
4. `v0.4.0`：画像自动学习等个性化能力，当前不实施。

只有上一阶段的验收门槛全部通过，才能进入下一阶段。

### 3.2 不得破坏的行为

- 原始提示词、上下文和附件在失败时必须保留。
- API Key 继续仅存 Windows 凭据管理器，不进入数据库、日志、导出文件或前端持久状态。
- 旧历史记录和旧版导入包必须仍可读取。
- 附件临时内容仍按既有规则自动清理。
- 最多三轮澄清、停止生成、撤销/重做和手动编辑继续可用。
- 不在本地合成看似由模型生成的事实、假设或建议。

## 4. v0.2.1：增强链路可靠性

### 4.1 目标与完成门槛

本阶段解决“模型有输出，但因结构问题整次不可用”。完成后：

- 100 条混合提示词中，完整结构结果成功率不低于 95%。
- “完整结果 + 部分结果 + 原文回退”的可交付率必须达到 100%。
- 任意解析失败都不能清空原文、上下文或附件列表。
- 非核心字段缺失不能让有效的 `primary_prompt` 整体失败。
- 每次失败必须产生稳定错误码，操作日志中仍只记录时间、模型、耗时和错误码。

### 4.2 新增结果字段

在 Rust `EnhancementResult`、前端 `EnhancementResult` 和 Zod schema 中同步增加：

```text
delivery_status: "complete" | "partial" | "fallback"
enhancement_level: "none" | "light" | "clarify"
notices: string[]
```

字段含义：

- `delivery_status=complete`：JSON 完整解析且核心结构有效。
- `delivery_status=partial`：提取到了可用主提示词，但建议、修改明细或其他附属字段不完整。
- `delivery_status=fallback`：没有可靠提取出增强主提示词，使用原始提示词作为输出。
- `enhancement_level=none`：原文已足够清楚，基本保留原文。
- `enhancement_level=light`：只做必要的局部增强。
- `enhancement_level=clarify`：缺少会改变结果的关键信息，需要澄清。
- `notices`：面向用户的非阻塞说明，例如“模型未返回完整建议，主提示词仍可使用”。

兼容规则：

- Rust 字段全部加 `#[serde(default)]`。
- 旧结果缺少 `delivery_status` 时前端默认 `complete`。
- 旧结果缺少 `enhancement_level` 时：`needs_clarification` 映射为 `clarify`，其余映射为 `light`。
- 旧结果缺少 `notices` 时默认空数组。
- 不修改旧字段名称，不删除旧字段。

### 4.3 后端解析流水线

修改 `src-tauri/src/provider.rs`，把当前单一 `parse_result` 拆成明确的四步流水线。

#### 第一步：直接解析

按顺序尝试：

1. 对完整字符串执行 `serde_json::from_str`。
2. 去除首尾 Markdown 围栏后再次解析。
3. 使用字符串感知的平衡花括号扫描器提取第一个完整 JSON 对象，再解析。

平衡扫描器必须正确处理：

- JSON 字符串内部的 `{` 和 `}`。
- `\"` 转义引号。
- `\\` 转义反斜杠。
- JSON 前后的解释文字。
- 一个未闭合的尾部 JSON；未闭合时不得越界或 panic。

禁止仅使用“第一个 `{` 到最后一个 `}`”作为最终算法，因为多余说明或多个对象会导致错误截取。

#### 第二步：字段归一化

JSON 成功解析成 `serde_json::Value` 后，先归一化再反序列化：

- `status` 非法或缺失时，根据 `questions` 是否非空推导。
- `task_type` 非法时改为 `other`。
- 数组字段缺失、为 `null` 或类型错误时改为空数组，并写入 `notices`。
- `suggestions` 超过 5 条时只保留前 5 条。
- `suggestions` 少于 5 条时保留已有条目，不在本地编造缺失建议。
- 建议或修改项缺少 ID 时使用稳定前缀加序号补齐，例如 `s1`、`c1`。
- 删除 ID 重复的后续条目，并写入 `notices`。
- `questions` 超过 3 条时保留前三条。
- `primary_prompt` 首尾空白必须清理，但不能改写正文。

#### 第三步：核心校验与完整校验分离

新增两个函数：

```text
validate_core_result(result)
validate_complete_result(result)
```

核心校验只检查：

- `primary_prompt` 非空。
- `primary_prompt` 不包含未解释的 `XXX` 占位符。
- `status` 合法。
- `questions` 不超过 3 条。

完整校验检查：

- suggestions 是否恰好 5 条。
- suggestions 的 kind、operation 和 ID 是否有效。
- changes 的 before 是否能在原始提示词中找到；`before` 为空时只允许 `add_context`、`add_constraint`、`format` 或 `safety`。
- task_type、enhancement_level 是否合法。

处理规则：

- 核心校验和完整校验都通过：`delivery_status=complete`。
- 核心校验通过、完整校验失败：`delivery_status=partial`，仍发送 Result 事件。
- 核心校验失败：进入结构修复重试。

#### 第四步：结构修复重试与回退

第一次核心校验失败后，第二次请求不能简单重复原请求。第二次请求使用专门的结构修复消息：

```text
上一次输出没有形成可解析的 PromptCraft JSON。请重新生成同一任务的结果。
只输出一个完整 JSON 对象，不要解释，不要代码围栏。
必须优先保证 status、task_type、primary_prompt、questions 可用；
其他数组无法确定时可以返回空数组。
```

修复请求仍携带原始任务信息，但不把可能含敏感信息的完整错误日志写入磁盘。

如果第二次仍无法通过核心校验：

- 若流式原文中能够可靠提取 `primary_prompt` 字符串，构造 `partial` 结果。
- 若不能提取，构造 `fallback` 结果，`primary_prompt` 等于用户原始提示词。
- fallback 的 `suggestions`、`changes`、`questions`、`assumptions` 为空。
- `notices` 写入“增强服务未返回可用结构，本次已保留原始提示词”。
- 后端发送 Result 事件，不抛出会让前端丢失可交付结果的异常。
- 操作日志错误码记录 `STRUCTURE_FALLBACK`。

### 4.4 错误码规范

内部统一使用以下稳定错误码：

```text
REQUEST_INVALID
AUTH_FAILED
BALANCE_INSUFFICIENT
MODEL_NOT_FOUND
RATE_LIMITED
NETWORK_FAILED
STREAM_INTERRUPTED
STRUCTURE_RETRY
STRUCTURE_PARTIAL
STRUCTURE_FALLBACK
USER_CANCELLED
STORAGE_FAILED
```

要求：

- UI 展示中文说明，不直接展示内部堆栈。
- 操作日志只保存错误码，不保存提示词、响应正文或 API Key。
- HTTP 状态到错误码的映射集中管理，不能分散在多个组件中。
- 用户取消不显示红色错误，应显示“生成已停止，已保留当前内容”。

### 4.5 前端降级体验

修改 `src/App.tsx`、`src/types.ts`、`src/schemas.ts` 和增强视图。

#### 状态处理

- `complete`：保持当前正常完成流程。
- `partial`：状态为 `ready`，显示黄色非阻塞提示，复制和编辑按钮可用。
- `fallback`：输出框显示原始提示词，状态文案显示“已保留原文”，复制按钮可用。
- 调用异常且后端没有发送 Result 时，前端也应把当前输出作为可恢复草稿；若当前输出为空，则显示原文。
- fallback 结果默认不写入“成功增强”历史；历史记录应标注为 `fallback`，避免后续统计误认为成功。

#### 新增按钮

增强结果区增加：

- `恢复原文`：把输出恢复为本次请求开始时的原始提示词，并进入撤销栈。
- `仅保留必要修改`：仅在存在 accepted/pending changes 时显示，拒绝所有非 safety 类型且未明确接受的修改，再根据 change 规则重建输出。
- `重新生成`：沿用当前输入和配置重新调用，不清空原文和上下文。

#### notices 展示

- notices 显示在结果区顶部，不使用模态框。
- 同一 notice 文案去重。
- `partial` 和 `fallback` 必须有至少一条 notice。

### 4.6 v0.2.1 测试要求

Rust 单元测试至少新增：

1. 直接 JSON 能解析。
2. Markdown 围栏 JSON 能解析。
3. JSON 前后有解释文字能解析。
4. 字符串内包含花括号能正确扫描。
5. 缺少 suggestions 但有 primary_prompt 时返回 partial。
6. suggestions 超过 5 条会截断。
7. 重复 ID 会去重。
8. questions 超过 3 条会截断。
9. 完全不可解析时返回 fallback 原文。
10. fallback 不包含模型虚构建议。

前端测试至少新增：

1. 旧版结果可被 schema 兼容读取。
2. partial 结果可复制和编辑。
3. fallback 显示原文和提示信息。
4. 恢复原文进入撤销栈。
5. 重新生成不会清除上下文和附件。
6. 用户停止生成不显示为失败。

评测测试至少新增：

1. 增强结果失败时单条样本不终止整批评测。
2. 报告分别统计 complete、partial、fallback 和 hard failure。
3. 模型基准不得把增强失败复制成三个目标模型各一次失败；增强阶段失败只计一次。

## 5. v0.2.2：增强必要性判断与最小干预

### 5.1 目标与完成门槛

- 已经写清楚的回归样本中，核心约束保留率达到 100%。
- 已清晰提示词的明显变差率不超过 5%。
- `concise` 模式平均膨胀比不超过 1.5 倍。
- `standard` 模式平均膨胀比不超过 2.0 倍。
- 创意任务默认不加入编号清单、表格或验收模板。
- 所有澄清问题都必须说明为什么会改变最终结果。

### 5.2 增强等级判定

系统提示词升级为 `promptcraft-v2.1.0`，要求模型先判断增强等级：

#### none

满足以下条件时使用：

- 目标明确。
- 关键输入或上下文已给出。
- 交付物明确。
- 任务不依赖缺失信息。

输出规则：

- `primary_prompt` 基本保留原文。
- changes 可为空。
- 不为了凑格式而增加无关要求。
- 可以给 0-5 条可选建议，不再把恰好 5 条作为核心成功条件。

#### light

适用于只缺少 1-3 个能明显改善结果的要素，例如受众、篇幅、输出格式或验收标准。

输出规则：

- 只补必要内容。
- 优先局部改写，不重写整段。
- 不重复原文已经写明的约束。

#### clarify

适用于缺失信息会让正确答案发生实质变化，例如：

- “这是什么意思”但没有提供对象。
- “帮我改代码”但没有代码或错误。
- “写个方案”但没有主题或目标。
- 要查询数字、课程、事件，但没有可靠来源且软件不联网核验。

输出规则：

- 最多 3 个问题。
- 同时提供基于明确假设的临时提示词。
- 不使用 `XXX`、`待补充` 等裸占位符。

### 5.3 任务感知的本地预检

重构 `src/preflight.ts`，先做轻量任务分类，再按任务类型检查。

最低分类集合与后端一致：

```text
code | creative | writing | qa | data | translation | other
```

规则：

- code：检查是否有代码/错误、运行环境、预期行为；不强制检查受众。
- translation：检查原文、目标语言、是否保留格式；不强制检查输出结构。
- creative：检查用途、受众、风格边界；不提示验收标准。
- writing：检查读者、用途、语气、篇幅。
- qa：检查问题对象和事实边界；简单知识问题不强制要求格式。
- data：检查字段、口径、时间范围和期望指标。
- other：只提示目标、上下文和交付物中真正缺失的项。

本地预检只给建议，不阻止发送。每次最多显示 3 条，按影响程度排序。

### 5.4 修改结果解释

结果区增加以下只读摘要：

```text
增强等级：无需明显修改 / 轻度增强 / 需要澄清
长度变化：原文 N 字 -> 结果 M 字（x.xx 倍）
修改摘要：补充了哪些类型的信息
事实来源：仅使用用户提供内容 / 包含明确假设
```

修改摘要由已有 `changes` 推导，不额外调用模型：

- `add_context` -> 补充背景
- `add_constraint` -> 补充约束
- `format` -> 明确输出形式
- `safety` -> 添加风险保护
- `clarify` -> 澄清表达
- `remove_redundancy` -> 精简重复内容

### 5.5 变更接受/拒绝的重建规则

当前基于字符串替换的方式必须满足以下规则：

- `before` 非空时，只替换第一个完全匹配片段。
- 找不到 anchor 或 before 时，不得静默修改其他位置；显示“原文已被编辑，无法自动应用该项”。
- insert 建议只能追加到段落末尾或明确 anchor 后，不能随机插入。
- 每次接受、拒绝、恢复原文和手动编辑都进入统一撤销栈。
- 切换候选或重新生成时保留上一版为历史版本，不能覆盖且无法恢复。

### 5.6 v0.2.2 测试要求

创建任务类型表驱动测试，至少覆盖用户早期提供的典型请求：

- “中暑症状表现”。
- “这是什么意思”。
- “学生，码农，办公，了解项目和代码”。
- “扫描我目前装的 skill”。
- “只保留 brainstorming，删除其他 superpowers skill”。
- “opencode 右侧窗口怎么关”。
- “再找几篇真实的爱情帖子”。
- “河北科技学院大三课程”。

测试只检查行为边界，不把某一段固定增强文案当作唯一正确答案。重点断言：

- 是否正确识别任务类型和风险。
- 是否编造事实。
- 是否该澄清时给出问题。
- 是否保留原始意图。
- 是否避免无意义扩写。

## 6. v0.3.0：可信效果评测

### 6.1 目标

回答三个问题：

1. PromptCraft 对什么类型和什么模糊程度的任务有帮助。
2. 提升来自有效改写，还是仅仅因为提示词变长。
3. 对已经清晰的提示词，PromptCraft 是否引入回退。

### 6.2 样本集

正式评测至少 60 条，固定版本进入仓库：

- 20 条清晰提示词：目标、背景、约束和格式基本完整。
- 20 条中等模糊提示词：缺少 1-2 个关键要素。
- 20 条严重模糊提示词：必须澄清或明确假设。

每一层均覆盖：编程、办公写作、问答、数据分析、翻译和创意。每个场景每个模糊等级至少 3 条；剩余条目优先分配给编程、办公和数据分析，因为它们是目标用户主要场景。

每条样本必须包含：

```text
id
scenario
ambiguity_level: clear | medium | severe
original
must_preserve: string[]
must_not_add: string[]
expected_behavior: none | light | clarify
```

### 6.3 三组对照

每个样本生成三种提示词：

- A：原始提示词。
- B：长度填充组，只追加中性要求，使长度接近增强版，不改变任务信息。
- C：PromptCraft 增强提示词。

长度填充要求：

- B 与 C 字符数差异控制在 ±5%。
- 只能使用固定中性句，不加入具体领域知识。
- 报告主比较为 C 对 B；A 对 B 仅用于判断“加字效应”。

### 6.4 重复实验

- 每个目标模型、每个变体至少生成 3 次。
- 编程、翻译和数据分析温度使用 0.2。
- 问答使用 0.4。
- 写作使用 0.5。
- 创意使用 0.7。
- 同一比较内模型版本、温度和最大输出 token 必须相同。
- 保存模型 ID、执行时间、耗时、token 和错误码。

### 6.5 裁判与人工校准

- 主裁判与增强模型不能使用同一个模型部署。
- 第二裁判默认开启，不再作为可选项。
- A/B/C 标签随机化，裁判看不到“原始”或“增强”字样。
- 至少抽取 20% 有效比较进行人工盲评。
- 人工评审文件只包含任务、三个提示词和三个回答，不包含系统内部标签。

自动评分维度：

- 准确性。
- 完整性。
- 相关性。
- 清晰度。
- 创意任务额外评分新颖度。

提示词级评分：

- 约束保留率。
- 信息增益。
- 过度增强程度。
- 假设透明度。
- 可直接执行程度。

### 6.6 报告必须显示的指标

- 总样本数、计划比较数、有效比较数和有效率。
- complete、partial、fallback、hard failure 数量和比例。
- 各模糊等级的胜/平/负、均值、标准差和 95% 置信区间。
- 各任务类型的结果。
- 各目标模型的结果。
- C-B 与 B-A 的质量差值。
- 原文/填充/增强的字符数与 token。
- 每增加 1000 token 的质量收益。
- 清晰提示词的变差率。
- 双裁判一致率和维度相关性。
- 人工与自动裁判一致率。
- 所有失败样本清单和错误码。

以下条件不满足时，报告首页必须显示“本轮结果不可用于产品结论”：

- 有效率低于 90%。
- 任一主要场景有效样本少于 8 条。
- 双裁判胜者一致率低于 70%。
- 人工校准样本少于有效比较的 20%。

### 6.7 产品发布门槛

只有同时满足以下条件，README 才能写入效果数字：

- 总有效率不低于 95%。
- 严重模糊任务 C-B 的平均质量提升至少 +0.8/10。
- 中等模糊任务 C-B 的平均质量提升至少 +0.4/10。
- 清晰任务明显变差率不超过 5%。
- 整体平均膨胀比不超过 2.0。
- 双裁判一致率不低于 75%。
- 人工盲评方向与自动评测方向一致。

未达到门槛时，README 只允许使用定性描述，不允许写“提升 X%”。

## 7. 构建、测试和发布工程

### 7.1 临时目录权限

不得要求开发者手工删除整个 `node_modules` 或 Cargo target。

前端：

- 在测试脚本中为 Vite 配置项目外的可写临时目录。
- 推荐使用系统临时目录下的 `promptcraft-vite-{进程ID}`。
- 测试结束尽力清理，清理失败只告警，不影响测试结果。
- `npm test` 的失败输出必须能区分“测试失败”和“临时目录不可写”。

Python：

- pytest 默认使用仓库外的可写 `--basetemp`。
- CI 可保留默认缓存，本地 F 盘脚本使用 `-p no:cacheprovider`。
- 新增统一脚本，避免用户记忆长命令。

Rust：

- 本地检查脚本把 `CARGO_TARGET_DIR` 指向可写缓存目录；优先从环境变量读取。
- `build-env.ps1` 检查 OpenSSL 时输出探测过的目录。
- 未找到 OpenSSL 时给出安装和设置命令，不继续执行 Cargo。

### 7.2 统一验证命令

新增或修改 `build-check.cmd`，使以下命令一次完成全部检查：

```powershell
build-check.cmd test
```

执行顺序：

1. TypeScript/Vitest 测试。
2. 前端生产构建。
3. Python 评测单元测试。
4. Rust fmt check。
5. Rust test。
6. Rust clippy。
7. 检查工作区是否意外产生应提交的缓存或密钥文件。

任何步骤失败立即停止，并输出具体阶段名称。

### 7.3 CI

GitHub Actions 增加 Python 评测测试：

```text
python -m pip install -r evaluation/requirements.txt
python -m pytest evaluation -q
```

CI 不运行真实 API 或网页评测。所有外部调用必须 mock。

### 7.4 Release

- ZIP 不提交到源码 Git 历史，改为 GitHub Release 附件。
- `.gitignore` 恢复忽略 `release/*.zip`；Release 文件通过发布流程上传。
- 每次发布生成 SHA-256，并在 Release Notes 中列出。
- 0.2.1 Release Notes 明确写“可靠性修复”，不宣称未经复测的质量提升。
- 发布前确认本地分支与 GitHub `main` 同步，不能长期保留未推送的大批提交。

## 8. 数据与历史兼容

### 8.1 历史记录

历史记录新增可选字段：

```text
deliveryStatus
enhancementLevel
promptVersion
```

旧记录缺失字段时正常展示。fallback 历史必须显示“原文回退”，不能显示为成功增强。

### 8.2 导入导出

- 导出包 schema 升级时保留版本字段。
- 导入旧包时填充默认值。
- 导入未知未来版本时拒绝写入，并显示明确提示。
- 导出包继续排除 API Key、附件临时文件和操作正文日志。

## 9. 实施提交顺序

建议每一步独立提交，禁止把全部改动堆在一个提交中：

1. `refactor: split core and complete enhancement validation`
2. `fix: recover partial structured enhancement results`
3. `feat: add fallback delivery status and notices`
4. `feat: expose enhancement level and result metrics`
5. `refactor: make preflight checks task aware`
6. `test: cover structured output recovery and fallback`
7. `feat(eval): add ambiguity-level evaluation dataset`
8. `feat(eval): require repeated control-group evaluation`
9. `chore: make local test caches use writable directories`
10. `ci: run frontend rust and evaluation test suites`
11. `docs: document v0.2.1 reliability release`

每个提交后至少运行相关子系统测试；阶段结束运行完整验证命令。

## 10. 完成定义

### v0.2.1 完成定义

- 结构恢复、partial 和 fallback 全部实现。
- 前后端 schema 兼容旧数据。
- 相关单元测试全部通过。
- 100 条结构压力测试可交付率 100%。
- 生成失败时用户仍可复制原文。
- 操作日志不记录敏感正文。

### v0.2.2 完成定义

- none/light/clarify 行为完整。
- 本地预检按任务类型工作。
- 结果区显示增强等级、长度变化和修改摘要。
- 清晰提示词回归集达到规定门槛。

### v0.3.0 完成定义

- 60 条正式样本、三组对照、三次重复全部跑通。
- 有效率、裁判一致率和人工校准达到门槛。
- 报告能明确指出适用场景和不适用场景。
- README 中的效果描述与报告证据一致。

## 11. 当前明确不做

以下内容推迟到可靠性和效果验证完成后：

- 自动控制豆包、千问等网页并自动发送。
- 云端账户、同步和统一付费。
- 截图 OCR、Excel、PPT、压缩包和整个项目目录解析。
- 自动联网搜索或事实核验。
- 复杂的隐式画像学习、跨会话自动推断和 30 天衰减。
- 更多模型排行榜或更复杂的评测可视化。

这些功能不是当前核心瓶颈。当前唯一优先事项是：让每次增强都有可交付结果，并证明它在合适的任务上确实优于不增强。
