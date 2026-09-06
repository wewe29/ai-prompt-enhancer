# Changelog

本项目遵循语义化版本。发布日期使用 `YYYY-MM-DD`。

## [0.3.2] - 2026-09-05

### 可信效果验证（重新执行正式评测）

- 按迭代规划 §4.1 固定规则重新执行正式评测：`samples_v30.yaml` 60 条样本（清晰/中等模糊/严重模糊 各 20）× 4 个目标模型（plan_doubao / plan_glm / plan_doubao_pro / plan_glm_5_2）× repeats=3 × A/B/C 三组对照（原始 / 长度填充 / 增强），第二裁判（glm-5.2）全程开启；共 2160 组目标生成、2880 次裁判调用。
- 修复目标模型空回复：火山方舟 plan 端点的推理型部署在 `max_tokens: 4096` 下思考段即可耗尽预算、正文为空（v0.3.0 正式评测四个 plan 目标各丢失 4 组对比即由此导致），四个 plan 目标 `max_tokens` 提升至 8192（`evaluation/config.yaml`）。
- 新增 `evaluation/v032_driver.py`：`prefetch`（一次性预生成全部增强与提示词级裁判缓存，供各目标进程复用）、`slice`（样本集确定性 round-robin 切片）、`merge`（按 目标×切片 并行运行后合并 samples.json 重新出统一报告）、`extract`（提取失败清单/超时比例/交付状态等报告必需事实）。本轮正式评测以 4 目标 × 3 切片共 12 个并行进程执行，耗时约 4 小时（顺序执行预计 15 小时以上）。
- 新增 `evaluation/v032_blind_review.py`：人工盲评导出/揭盲比对工具。按 目标×难度 分层抽取 20% 有效对比（每目标 12 个、共 48 个单元），盲态下仅见匿名回答 A/B（随机顺序），评审完成后揭盲与自动裁判比对胜者一致率与方向一致性；`blind_key.json`（揭盲映射）与评审包分离存放。
- 人工盲评结论：按 目标×难度 分层抽取 48/222（21.6%）单元盲态评审（评审者仅见匿名回答 A/B，揭盲映射分离存放）。揭盲后人工判定 增强更优 0 / 原文更优 27 / 接近 21，与自动裁判非平局胜者一致率仅 26.9%——**人工盲评与自动评测方向不一致**；主要冲突模式是人工判"接近"的 21 个单元中 17 个被自动裁判判了非平局（14 个判增强组胜），证实 LLM 裁判对更长、结构化更强的回答存在系统性打分偏好，自动评测的量化胜率不可用于产品结论。
- `release_gate.py` 判定：**未通过**（有效交付率 100% 通过；严重模糊 C−B +0.38 < +0.8、中等模糊 C−B +0.12 < +0.4、清晰任务明显变差率 41.1% > 5%、膨胀比 ×4.63 > ×2.0、双裁判一致率 67.6% < 75%、人工盲评方向不一致）。与 v0.3.0 基准（+0.74 / −0.07 / 36.5% / ×5.04 / 62.7%）方向一致，结论稳定。
- 本轮评测效果结论（定性，README 已同步）：增强回答更长更结构化，但人工盲评未显示质量优势；已写清楚的任务优先直接使用原文（过度改动风险两个口径一致确认）；信息严重缺失的任务可尝试增强但需逐项审核。完整报告：`docs/PromptCraft-v0.3.2-评测报告.md`，关键数据：`docs/evaluation-data-v032/`。
- 评测覆盖率披露：720 个 rep 单元完成 649（90.1%）；plan_doubao / plan_doubao_pro 全部完成，plan_glm 缺 37、plan_glm_5_2 缺 54——glm 系部署对最重样本的长思考（>16k token / >8 分钟）在多轮重试后仍超时或空回复，属目标侧限制；缺失单元不计入统计。运行日志统计生成超时 321 次、空正文回复 270 次。

## [0.3.1] - 2026-09-02

### 发布稳定性

- 便携 ZIP 一律不进版本库：`.gitignore` 恢复忽略 `release/*.zip`，已入库的 0.3.0 ZIP 从索引移除（本地保留，GitHub 上已发布的 v0.3.0 资产不受影响）；`bootstrap.yml` 标注为 v0.3.0 专用冻结，v0.3.1 起改为本地 `gh release create` 直接上传。
- 测试/构建临时目录统一为扁平的 `%TEMP%\PromptCraft-*`：`PromptCraft-vite-<pid>`（进程退出即清理）、`PromptCraft-pytest-<pid>`（会话结束即清理）、`PromptCraft-cargo-target`（持久保留 Rust 增量编译缓存；改名会使下一次构建全量重编译一次）。
- `evaluation/conftest.py`（新增）：直跑 `python -m pytest evaluation -q` 时 basetemp 重定向到 `%TEMP%\PromptCraft-pytest-<pid>` 并在会话结束清理；显式 `--basetemp` 时不干预。
- `build-check.ps1`：新增 `-Clean` 开关一键清空全部 `PromptCraft-*` 目录；启动时清扫陈旧临时目录（vite/pytest >7 天、cargo target >30 天）；pytest basetemp 在 `finally` 中清理。
- 错误分类：preflight 环境预检（node/npm/python/cargo/git/node_modules/OpenSSL），退出码 0=通过、1=代码错误（CODE）、2=环境错误（ENVIRONMENT）；阶段输出带 `(CODE|ENVIRONMENT)` 标注与 SUMMARY 汇总。
- OpenSSL 探测增强：按顺序探测 `OPENSSL_DIR`、`%ProgramFiles%\OpenSSL-Win64`、`%ProgramFiles(x86)%\OpenSSL-Win64`、`%ProgramFiles%\OpenSSL`、`%LOCALAPPDATA%\Programs\OpenSSL-Win64`，以 `include\openssl\ssl.h` 判定命中；缺失时列出已探测路径、winget/setx 安装命令与 README「OpenSSL 依赖」指引。
- `build-portable.ps1`：打包前校验 `package.json` / `Cargo.toml` / `tauri.conf.json` / `Cargo.lock` 四处版本一致；校验 exe `ProductVersion`；重建前清空旧目录；打包后自动生成 `release-assets/SHA256-zip.txt`（sha256sum 格式）。
- 新增 `scripts/smoke-portable.ps1` 便携版端到端冒烟：全新目录解压、凭据备份/恢复（Windows 凭据管理器 `deepseek-api-key.PromptCraft`）、流式生成/停止/重新生成/复制/历史恢复/API 失败保留原文/临时目录残留检查（CDP 自动化），人工核对项在结束时打印。
- workspace sanity 阶段对 ZIP 改为 `git ls-files` 索引硬检查（原 `git status` 正则检查在 ignore 修复后恒为空，属假安全）。
- `evaluation/requirements.txt` 全量锁定为本地 venv 验证通过的精确版本（含传递依赖）。此前 `openai>=1.40.0` 等浮动约束使 CI 每次安装最新版，上游破坏性更新导致 CI 的 "Run evaluation unit tests" 自 2026-08-14 起持续失败（本地始终通过）；另注意该文件必须保持纯 ASCII——pip 对无 BOM 文件按系统本地编码解码，中文注释在中文 Windows 上会让 `pip install -r` 直接报 GBK 解码错误。

## [0.3.0] - 2026-08-14

### 增强必要性判断与最小干预

- 系统提示词 v2.1.0:模型先判断增强等级(none/light/clarify);建议放宽为 0-5 条,不再要求恰好 5 条。
- 任务感知的发送前快速检查:按 7 类任务(代码/创意/写作/问答/数据分析/翻译/其他)分别检查,最多 3 条提示,零成本。
- 结果区只读摘要:增强等级、长度变化、修改摘要(由 changes 推导)、事实来源。
- 变更接受/拒绝重建规则:只替换首个完全匹配;anchor 未命中时提示"原文已被编辑"而非静默改动。

### 可信效果评测(v0.3.0 基准)

- 60 条正式样本:clear/medium/severe 三档模糊 × 6 场景,含 must_preserve/must_not_add/expected_behavior。
- A/B/C 三组对照(原始/长度填充/增强)默认开启;`--repeats N` 重复实验;每次对比记录延迟/估算 token/错误码。
- 目标模型与裁判/增强器解耦(剔除同部署模型);第二裁判默认开启并报告一致率。
- 报告新增:按模糊等级分项、C−B/B−A 对照、发布门槛横幅("不可用于产品结论")与 `release_gate.py` 门槛判定。
- 正式评测(60×4)结论(定性):严重模糊任务增强帮助显著;中等/清晰任务增益有限,部分清晰提示词存在回退风险——按发布门槛规则,README 仅作定性描述。

## [0.2.1] - 2026-08-12

### 可靠性修复

- 解析流水线重构:直接 JSON → 去围栏 → 字符串感知平衡扫描,正确处理字符串内花括号/转义,不再用"首 `{` 到末 `}`"误截取。
- 核心校验与完整校验分离:`primary_prompt` 有效即可交付,不再因"恰好 5 条建议"等附属字段缺失而整体丢弃。
- 新增交付状态 `delivery_status`(complete/partial/fallback)与 `enhancement_level`(none/light/clarify)、`notices`;partial 保留主提示词,fallback 回退原文并保证可复制。
- 结构修复重试:第二次请求使用专门修复指令;仍失败时从流式内容提取或回退原文,始终发送结果,不抛异常。
- 稳定错误码(REQUEST_INVALID/AUTH_FAILED/RATE_LIMITED/STRUCTURE_FALLBACK 等)写入本地用量日志;日志不落提示词/响应正文/API Key。
- 前端降级体验:partial 黄色提示、fallback 显示"已保留原文"、新增 恢复原文/仅保留必要修改/重新生成 按钮、notices 非模态提示;失败时输入不丢失。
- 历史记录新增 deliveryStatus/enhancementLevel/promptVersion(可选,旧数据兼容),fallback 历史标注"原文回退"。
- 评测:单条增强失败不再终止整批;报告统计 complete/partial/fallback/hard_failure 与有效率;基准评测增强失败按提示词去重。
- 工程:Vite/pytest/Cargo 可写临时目录;`build-check.cmd test` 统一 7 步验证;CI 增加 Python 评测测试;`release/*.zip` 恢复忽略。

### 测试

- Rust:四步解析、归一化、partial/fallback、100 条结构压力测试(可交付率 100%,完整结构 95%)。
- 前端:旧结果兼容、partial/fallback 行为、恢复原文进撤销栈、重新生成不清输入。
- 评测:失败隔离、交付统计、基准失败去重。

## [0.2.0] - 2026-08-09

### Added

- 系统提示词 v2.0.0：任务类型识别（代码/创意/写作/问答解释/数据分析/翻译/其他）与差异化增强策略、最小干预原则、few-shot 示例、changes 局部改写。
- 增强结果展示任务类型徽标。
- 本地发送前快速检查（离线、零成本、仅提示）。
- 一键套用画像预设（纯AI小白/学生/普通办公员工/程序员）。
- 键盘快捷键（Ctrl+Enter/Ctrl+Z/Ctrl+Shift+Z·Ctrl+Y/Esc）。
- 自定义模型列表（设置页配置，增强页下拉选择）。

### Changed

- 前端重构：App.tsx 拆分为 views/ 与 components/，zod schema 统一校验。

### Evaluation

- 评测：用户画像模拟（--personas）、豆包/千问站点适配修复、v2 初步评测（23 组有效对比：8 胜 9 平 6 负，相关性 +0.09，待完整复测）。

### Fixed

- 其他：prompts.rs 提示词模块化、错误事件 reject 修复、mock 简化。

## [Unreleased]

### Planned

- 签名更新元数据和只提示下载的更新检查。
- 完整的隐式画像计分、跨会话证据和 30 天衰减。
- 更完整的端到端测试与可访问性检查。

## [0.1.0] - 2026-08-07

### Added

- Tauri 2、React、TypeScript 和 Rust 桌面应用。
- DeepSeek Chat 与 V4-Flash 提示词增强。
- 流式生成、停止、澄清、自动重试和结构校验。
- 修改接受或拒绝、撤销或重做、五个可选补充建议。
- 本地附件文本提取、敏感内容检测和强制凭据遮蔽。
- SQLCipher 历史、Windows 凭据管理器、导入导出和容量清理。
- 费用估算、提醒额度、强制额度和目标网页打开。
- Windows 便携构建脚本和零基础中文使用指南。

### Fixed

- 将 V4-Flash API 模型 ID 修正为 `deepseek-v4-flash`。
- 将流式请求总超时改为读取空闲超时，避免持续生成在 30 秒时被截断。
