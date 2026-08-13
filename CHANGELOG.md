# Changelog

本项目遵循语义化版本。发布日期使用 `YYYY-MM-DD`。

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
