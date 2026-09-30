# PromptCraft v0.3.5 — 评审缺陷修复与增强能力验证

v0.3.5 修复了 v0.3.4 代码评审发现的 **8 个缺陷**（含 1 个阻断级），新增**历史置顶**与**容量上限可配置**两项用户可见功能，并附上一份基于 60 条样本的**增强能力验证报告**。

---

## 一、必须优先知道的安全修复

### 🔴 阻断级：ZIP 导入可静默替换你的 API Key（凭据投毒）

**这是本次最重要的修复，请务必升级。**

**问题**：`ProviderConfig.api_key` 原本只标注了 `skip_serializing`（序列化时跳过），但**没有**阻止反序列化。攻击者构造的 `provider.json` 里只要带一个 `apiKey` 字段，就能经 `import_data` → `save_provider_config` 写入**你真实的 Windows 凭据管理器**。

**后果**：你导入一个来源不明的 ZIP（朋友分享的、网上下载的、邮件附件）之后，DeepSeek Key 被静默换成攻击者的。此后你所有提示词内容都带着攻击者的 Key 发出去——**公司源码、客户资料可能全部泄露**，费用也转移到他账户。界面只显示「已导入 N 条记录」，没有任何凭据被更改的提示。

**修复**：
- `api_key` 增加 `skip_deserializing`，导入方向直接忽略该字段
- `import_data` 内再显式 `provider.api_key = None` 一次（纵深防御，防止将来有人误删 serde 属性后重新打开这个洞）
- 导入包也不能把历史容量上限设成 0/负数来让清理永久失效

**回归测试**：`import_never_plants_api_key_into_credential_store` —— 先写入用户自己的 Key，再导入带 `apiKey` 的攻击包，断言 Key 原样保留。

> 评审阶段已用探针实测复现该缺陷（攻击者 Key 被真实写入凭据管理器并可读回），修复后回归测试通过。完整证据见评审报告。

### 🟠 高：置顶功能此前生产环境完全不可达

`enforce_history_capacity` 依赖 `WHERE pinned=0` 保护置顶记录，但 `HistoryRecord` 结构体**根本没有 `pinned` 字段**，全仓库没有任何 tauri command 或前端入口能置顶。唯一能置顶的地方是测试里的裸 SQL——**那个绿色测试证明的是用户根本到不了的状态**。

**修复**：全链路贯通 `HistoryRecord.pinned` → `set_history_pinned` 存储方法 → Tauri 命令 → 历史页置顶按钮（置顶项排最前并高亮）。测试也改用公开 API 而非裸 SQL。

### 🟠 高：测试污染你真实的 Windows 凭据管理器

`test_storage()` 只在创建时保证隔离、**从不清理**。每跑一次 `cargo test` 就往你的凭据管理器追加一批 `PromptCraftTest-<uuid>` 条目并留下临时目录。**本机实测一度累积 162 条。**

**修复**：`TestSandbox` 在 `Drop` 中串行化清理（Windows 凭据管理器并发删改会静默失败，故用进程级锁 + 退避重试）。实测连续运行增量归零，历史 162 条已清空。真实 `PromptCraft` 凭据未受影响。

> 本轮发布前又发现该修复尚不完整：凭据的**写入**也需加锁，否则并发测试的删除会落在别的测试 set 与 get 之间，造成约 3/4 概率的假失败。已补齐并连跑 6 次验证全绿。

---

## 二、其余修复（中低危）

| 修复 | 说明 |
|---|---|
| **超时独立错误码** | `is_timeout()` 与 `is_connect()` 此前都归入 `NETWORK_FAILED`，用量记录无法区分「网络不通」和「响应超时」。现区分 `TIMEOUT` / `NETWORK_UNREACHABLE`。 |
| **清空测试恒真断言** | 原测试存入 `AppSettings::default()`（其 `profile_rules` 本就是空数组）后再断言为空，删除逻辑完全失效时同样会通过。现存入全字段非默认值并逐字段断言。 |
| **容量上限可配置** | 新增 `AppSettings.max_history_mb`（默认 64 MB，夹取 1..=1024，非法值回落默认），设置页新增输入项；同步修正设置页长期显示的、早已失效的「最大 500 MB」文案。 |
| **容量计量口径** | `enforce_history_capacity` 用 SQLite `LENGTH()`，对 TEXT 返回**字符数**而非字节数，与常量声明的字节语义不符（中文下实际占用可达名义值约 3 倍）。改用 `LENGTH(CAST(x AS BLOB))`，并补中文样本对照测试。 |
| **测试计数修正** | v0.3.4 的 CHANGELOG 与提交信息称「24 个安全测试」，实测为 23 个。 |

---

## 三、新增用户可见功能

- **历史记录置顶**：历史页每条记录有置顶按钮，置顶项**永远不会被** 90 天时间清理与容量清理删除，列表中排最前并高亮。
- **历史容量上限可配置**：设置 → 费用控制 → 「历史容量上限（MB）」，默认 64 MB，范围 1–1024。超出后按**最旧的未置顶记录**逐条清理直到达标。

---

## 四、增强能力验证（v0.3.5 新增）

换用 **DeepSeek-V4.1-Flash** 底模重跑 60 条三档样本，**档位判断 60/60 全部恰当**：

| 提示词类型 | 数量 | 行为 | 结论 |
|---|---|---|---|
| 严重模糊 | 20 | **20/20 全部转澄清**，列出待确认清单 + 基于明确假设的临时方案，**不编造事实** | 增强决定性 |
| 中等模糊 | 20 | 17 条轻度补齐，3 条判定无需改动 | 有明显增强 |
| 清晰 | 20 | **11 条原样返回**，9 条仅轻量排版 | 克制得当 |

指令越权 **0 例**。

**结论：这个增强器知道自己什么时候不该动手。** 已经写清楚目标/背景/约束/格式的任务，直接用原文即可。

> ⚠️ **诚实边界**：增强由 DeepSeek-V4.1-Flash 承担、评审由 M3.1-Flash-Preview 承担（**两个模型分离，非自我评分**），但**没有做人工盲评**，不能等同于人的判断；也**不应与 v0.3.2 结论直接比较**（底模与评审方式均已变更）。详见 [增强能力验证报告](https://github.com/wewe29/ai-prompt-enhancer/blob/main/docs/PromptCraft-增强能力验证报告-v0.3.5.md)。

---

## 五、测试与质量

| 项目 | 结果 |
|---|---|
| Rust 单元/集成测试 | **49 passed, 0 failed**（连跑 6 次验证并发稳定性） |
| `cargo fmt --check` | 通过 |
| `cargo clippy --all-targets` | 0 error |
| 前端测试 | 55 passed |
| `npm run build` | 通过 |
| pytest | 165 passed |

v0.3.5 共新增 **6 个测试**（凭据投毒回归、导入不得禁用容量上限、置顶往返与排序、置顶不受容量清理、容量上限跟随设置、容量按字节计量）。

---

## 六、下载与安装

- **`PromptCraft-0.3.5-windows-x64-portable.zip`** — Windows 10/11 64-bit 便携版。解压完整 ZIP，双击 `PromptCraft.exe`。**不需要 Python / Node.js / 任何开发工具**。
- **可选**：同目录下的 `MicrosoftEdgeWebView2Setup.exe`（若系统缺 WebView2，首次启动时静默安装；Win11 23H2+ 可忽略）

## SHA-256 校验

```
c11f34c4666c80df80d7db6d93fb9d0cf8be20e9a77c8cbdb52e55b123df6fb6  PromptCraft-0.3.5-windows-x64-portable.zip
```

PowerShell 校验：

```powershell
Get-FileHash .\PromptCraft-0.3.5-windows-x64-portable.zip -Algorithm SHA256
```

## 升级建议

**如果你用过 v0.3.4 并导入过任何来源不明的 ZIP，请立即到设置页重新填写一次 DeepSeek API Key**，以确保凭据未被替换。

## 重要安全提示

- **本版未代码签名**，Windows SmartScreen 首次启动会弹安全警告。点 `More info` → `Run anyway`。
- **只从本仓库 Releases 页面下载**，并**校验 SHA-256**。
- 详见 [`PRIVACY.md`](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PRIVACY.md)：**不发送任何遥测，不上传任何崩溃报告**。

## 反馈

- 优先在 GitHub 提交 Issue（便于其他用户共同查看和验证）
- 也可发邮件至 3986351310@qq.com（**请勿附 API Key、密码、专有源码或其他敏感信息**）

## License

Copyright (C) 2026 wewe29

本项目使用 [GNU General Public License v3.0 only](https://github.com/wewe29/ai-prompt-enhancer/blob/main/LICENSE)。
