# PromptCraft v0.3.4 评审报告

> 评审基线：`F:\ai-prompt-enhancer` 分支 `main`，HEAD `5886b6b`（`Merge release/v0.3.4`），tag `v0.3.4`。
> 评审日期：2026-09-30
> 评审范围：仅 v0.3.4「安全和数据可靠性加固」，对照《PromptCraft 后续迭代实施规划》§6。
> 评审模型：仅当前单一模型（无第二裁判、无人工盲评、无外部评审服务）。

---

## 1. 方法与局限（先读本章）

### 1.1 双层证据分离

本报告的每条结论都标注了证据层级，两层不可混淆：

| 层级 | 含义 | 复核方式 |
|---|---|---|
| **事实层** | git 差异、源码行号、命令实测退出码、运行时输出 | 第三方可原样复现 |
| **判断层** | 需求覆盖度判定、风险定级、测试缺口识别、根因归因 | 仅当前模型，未经第二方交叉验证 |

凡标注「**已验证**」的，表示有可复现的实测证据；标注「**已推断**」的，表示基于读码的静态判断，未实际触发。

### 1.2 单模型评审的硬性局限

本轮评审**只使用当前模型**，因此必须明确以下能力缺失：

- **无模型间独立性。** 不能声称任何形式的「双裁判一致率」，不能复现或验证 v0.3.2 的人工盲评方向。
- **无统计显著性。** 严重度定级是单模型的主观判断，未经第二方交叉验证，可能存在系统性偏差。
- **不得借效果结论。** v0.3.2 评测的 `release_gate.py` 判定「未通过」（人工盲评 0/48 判增强更优，与自动裁判方向不一致），该结论**仅属于 v0.3.2**。本报告不以任何方式将其重新包装为 v0.3.4 的效果证据。
- **无外部事实核验。** 本报告不联网，所有事实均来自本地仓库；不核验任何外部事实主张。
- **无法验证 GUI 行为。** 未做人工界面操作验证，涉及界面的判断为读码推断。

### 1.3 结论分级与严重度定义

每条需求映射到五档之一，**不使用笼统的「通过/不通过」**：

`已实现且有测试证据` / `已实现但测试不足` / `部分实现` / `未实现` / `不适用（无实现面）`

| 严重度 | 判据 |
|---|---|
| **阻断** | 违反用户规划红线，或导致凭据泄露 / 数据不可恢复损失 |
| **高** | 可被真实用户路径触发，造成安全或数据可靠性后果 |
| **中** | 需求未完整满足，或存在绕过 / 边界失效 |
| **低** | 文档、命名或语义不一致，无实际危害 |

---

## 2. v0.3.4 变更基线（事实层）

`git diff f0efe0f 5886b6b`：

```
 CHANGELOG.md                 |  14 ++
 package.json                 |   2 +-
 src-tauri/Cargo.lock         |   2 +-
 src-tauri/Cargo.toml         |   2 +-
 src-tauri/src/attachments.rs |  94 ++++++++++
 src-tauri/src/provider.rs    |  70 +++++++-
 src-tauri/src/security.rs    |  32 ++++
 src-tauri/src/storage.rs     | 397 +++++++++++++++++++++++++++++++++++++++----
 src-tauri/tauri.conf.json    |   2 +-
 9 files changed, 569 insertions(+), 46 deletions(-)
```

核心改动集中于 `storage.rs`(+397)、`attachments.rs`(+94)、`provider.rs`(+70)、`security.rs`(+32)。

### 2.1 测试基线实测

| 项目 | 结果 | 证据 |
|---|---|---|
| 新增 Rust 测试 | **23 个**（非 CHANGELOG 声称的 24） | `git diff` 逐行统计 `+#[test]`：storage 10 / attachments 7 / provider 3 / security 3 = 23 |
| `cargo test --lib` | **43 passed, 0 failed** | 现场执行 |
| `cargo fmt --all -- --check` | **通过**（exit 0） | 现场执行 |
| 前端测试 | 55 passed | `v034_tests.log` |
| pytest | 165 passed | `v034_tests.log` |

> `v034_tests.log` 开头记录的 `cargo fmt` 失败是**历史快照**，当前 HEAD 已通过；该日志未被 git 跟踪，不是权威证据。

### 2.2 评审基线声明

工作区存在未提交改动：`release-assets/SHA256-zip.txt` 被改写为 0.3.4 哈希，`src-tauri/Cargo.toml` 存在 CRLF 警告性改动。**本报告以 `5886b6b` 的已发布代码为基线**，不评价工作区脏状态。

---

## 3. 需求覆盖矩阵（15 条 + 4 条红线）

### 3.1 核心需求逐条判定

| # | 需求 | 源码位置 | 测试 | 通过 | 判定 | 风险 |
|---|---|---|---|---|---|---|
| 1 | API Key 不出现在日志/错误提示/导出包 | `provider.rs:768`（`map_http_error` 过 `redact_sensitive`）、`storage.rs:601`（导出不含 Key）、`storage.rs:150-152`（落库前剥离） | `http_error_messages_never_echo_api_key`、`export_and_stored_config_never_contain_api_key` | ✅ | **已实现且有测试证据** | — |
| 2 | API Key 只存 Windows 凭据管理器 | `storage.rs:143-149`（`Entry::set_password`）、`models.rs:140`（`skip_serializing`） | `export_and_stored_config_never_contain_api_key` | ✅ | **已实现**（但见缺陷 A 导入侧绕过） | **高** |
| 3 | 敏感检测不静默修改用户原文 | `security.rs:15-26`（仅替换凭据值，前后文保留） | `redact_preserves_plain_original_text`、`redact_masks_credential_values_without_touching_surroundings`、`build_body_masks_credentials_without_rewriting_original` | ✅ | **已实现且有测试证据** | — |
| 4 | 附件只作参考资料，不作系统指令 | `provider.rs` `build_body`（附件进 user message 的 `<attachment>` 段）、`prompts.rs:37`（明确降权措辞） | `build_body_treats_attachments_as_reference_only` | ✅ | **已实现且有测试证据** | — |
| 5 | 附件处理后自动删除临时文件 | `attachments.rs:13-50`（全程原地读取，**不产生临时文件**） | `extraction_leaves_no_temp_files_behind` | ✅ | **不适用（以更强方式满足）** | — |
| 6 | 附件删除失败时给出提示 | **无对应代码路径** | 无 | — | **不适用（无实现面）** | 低 |
| 7 | 空/超大/损坏 PDF·DOCX 安全失败 | `attachments.rs:20-22`（10MB 上限）、`:41-43`（空文本）、`:32`、`:69`、`:71` | 6 个测试全覆盖 | ✅ | **已实现且有测试证据** | — |
| 8 | 断网·超时·限流·余额不足·模型不存在 有稳定错误码 | `provider.rs:789-798` | `provider.rs:1135-1142`（8 条断言） | ⚠️ | **部分实现**（缺独立超时码，见 D-4） | 中 |
| 9 | ZIP 导入阻止路径穿越 | `storage.rs:366-369`（`enclosed_name()`） | `import_rejects_path_traversal` | ✅ | **已实现且有测试证据** | — |
| 10 | ZIP 导入限制文件数与总大小 | `storage.rs:348-377`（≤100MB 压缩后、≤8 文件、≤100MB 解压后）、`:487-503`（单条目 ≤8MB 硬截断） | `import_rejects_oversized_entry`、`import_rejects_unknown_entries_and_bad_manifest` | ✅ | **已实现且有测试证据** | — |
| 11 | 单条历史删除正常 | `storage.rs:219-228` | `history_roundtrip_and_single_delete` | ✅ | **已实现且有测试证据** | — |
| 12 | 彻底清空删除历史·设置·费用记录·凭据 | `storage.rs:261-293`（事务删三表 + 凭据，失败明确报错） | `clear_all_wipes_history_settings_usage_and_credential` | ⚠️ | **已实现但测试不足**（见 D-5） | 中 |
| 13 | 导入导出兼容旧版本数据 | `models.rs` 全字段 `#[serde(default)]`；`storage.rs:380`（schema 严格校验） | `import_accepts_legacy_minimal_archive` | ✅ | **已实现且有测试证据** | — |
| 14 | 达上限后按配置清理旧记录 | `storage.rs:23`（**硬编码常量**）、`:448-477` | `capacity_enforcement_deletes_oldest_unpinned_first` | ⚠️ | **部分实现**（不可配置，见 D-6） | 中 |
| 15 | 置顶记录不能被容量清理 | `storage.rs:467`（`WHERE pinned=0`） | `capacity_enforcement_never_deletes_pinned_records` | ❌ | **未实现**（生产不可达，见缺陷 B） | **高** |

### 3.2 红线合规（4 条）

| 红线 | 判定 | 证据 |
|---|---|---|
| 不联网搜索 | **合规** | 全仓库无搜索/浏览相关调用；`build_body` 固定 `SYSTEM_PROMPT`，无工具调用能力 |
| 不联网核验事实 | **合规** | `prompts.rs:13` 明确「只允许来自用户输入，不得改写、推断或虚构」 |
| 事实以用户提供内容为准 | **合规** | 同上；`facts` 字段要求逐字或近逐字摘自用户输入 |
| 不记录提示词·附件·回答正文 | **合规** | `src-tauri/src/*.rs` 全量扫描：`println!` / `eprintln!` / `log::` / `tracing::` / `dbg!` **0 命中**；前端 `src/**/*.{ts,tsx}` `console.*` **0 命中**。落库内容仅为用户主动保存的历史记录 |

---

## 4. 缺陷清单（按严重度）

### D-1 · 阻断 · ZIP 导入可向 Windows 凭据管理器植入任意 API Key（凭据投毒）

**证据层级：事实层（已验证，实测复现）**

**缺陷 A 的完整攻击链：**

1. `models.rs:140-141` — `ProviderConfig.api_key` 标注 `#[serde(default, skip_serializing)]`。
   `skip_serializing` **只阻止序列化（导出）方向，不阻止反序列化（导入）方向**。这是 serde 的既定语义，两者是独立开关。
2. `storage.rs:385` — `read_archive_json(&mut archive, "provider.json")` 以 `ProviderConfig` 为目标反序列化，攻击者包内 `"apiKey":"sk-..."` 会被完整读入 `provider.api_key`。
3. `storage.rs:395` — `self.save_provider_config(&provider)?`。
4. `storage.rs:144-149` — `save_provider_config` 检测到 `config.api_key` 非空，调用 `Entry::set_password` **写入真实的 Windows 凭据管理器**，服务名 `PromptCraft`，账户 `deepseek-api-key`。

**实测复现（本轮临时探针，已还原）：**

在 `storage.rs` 临时加入一个测试，构造攻击者包（`provider.json` 携带虚构的 `apiKey`，值记为 `sk-<探针虚构值>`），调用 `import_data`：

```
PROBE_RESULT=planted_key=sk-<探针虚构值>
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 43 filtered out
```

探针指定的密钥被真实写入凭据管理器并可读回。探针代码已通过 `git checkout -- src-tauri/src/storage.rs` 完整还原，`git status` 确认与 HEAD 一致，测试基线恢复至 43 passed。

> 报告不复述探针使用的具体 Key 字面量；该值为一次性虚构串，仅写入独立的 `PromptCraftTest-<uuid>` 测试服务，测试内已删除，未进入真实 `PromptCraft` 凭据。

**用户可达性（已验证）：**

`src/views/SettingsView.tsx` 有真实的「导入数据」按钮 → `importLocalData()`（`src/lib.ts:187-198`）→ `command<number>("import_data", { path: picked })` → `lib.rs` 的 `import_data` 命令。这不是死代码，**普通用户在设置页点两下即可触发**。

**后果：**

用户导入一个来源不明的 zip（朋友分享、网上下载、邮件附件）后，DeepSeek Key 被静默替换为攻击者指定的值。此后用户所有提示词增强请求都携带攻击者的 Key —— 攻击者可获取用户全部提示词内容（可能含公司源码、客户资料），且**费用转移至攻击者账户**。用户界面仅显示「已导入 N 条记录」，**没有任何凭据被更改的提示**。

**为什么现有测试没抓到：**

`import_accepts_legacy_minimal_archive`（`storage.rs:711-740`）构造的 provider 恰好**不含 `apiKey` 字段**，因此走的是 `api_key = None` 分支，完全没触达投毒路径。这是典型的「测试覆盖了正常路径，遗漏了攻击路径」。

**评级理由：阻断。** 该缺陷使「API Key 只保存到 Windows 凭据管理器」这条 v0.3.4 核心安全目标在导入侧被完全绕过，且直接导致凭据泄露与费用转移——命中严重度定义的「凭据泄露」判据。

---

### D-2 · 高 · 置顶保护在生产环境完全不可达

**证据层级：事实层（已验证）**

`enforce_history_capacity`（`storage.rs:448-477`）的删除条件是：

```sql
DELETE FROM history WHERE id = (SELECT id FROM history WHERE pinned=0 ORDER BY created_at ASC LIMIT 1)
```

`housekeeping` 的时间清理（`storage.rs:434`）同样带 `pinned=0`。

**但 `pinned` 在整个仓库只存在于 `storage.rs` 内部：**

- `HistoryRecord` 结构体（`models.rs:186-200`）**没有 `pinned` 字段**。
- 全仓库 `git grep -n 'pinned'` 在 `src/` 下**零命中**（前端无置顶按钮、无置顶图标）。
- `lib.rs` 的 17 个 `#[tauri::command]` 中**没有任何一个能写入 `pinned=1`**。
- 唯一写 `pinned=1` 的地方是测试 `capacity_enforcement_never_deletes_pinned_records`（`storage.rs:773`）的裸 SQL。

**后果：**

需求 15「置顶记录不能被容量清理」在真实使用中**无法被触发** —— 因为用户根本无法置顶任何记录。测试 `capacity_enforcement_never_deletes_pinned_records` 通过了，但它证明的是一个**用户无法到达的状态**，属于虚假信心：绿色测试掩盖了功能缺失。

这不是「测试不足」，而是**功能未实现但测试给出了已实现的假象**，故判为「未实现」而非「已实现但测试不足」。

---

### D-3 · 高 · 测试污染真实 Windows 凭据管理器，与代码注释的自我声明相矛盾

**证据层级：事实层（已验证，实测统计）**

`storage.rs:526-531` 的注释明确声明：

> 每个测试使用独立临时目录与独立凭据服务名，绝不触碰真实的 PromptCraft 凭据。

前半句为真（服务名确实是 `PromptCraftTest-<uuid>` 随机隔离），**后半句不成立**：

```
cmdkey /list 实测：
  deepseek-api-key.PromptCraftTest-*  →  7 个残留测试 API Key 凭据
  database-key.PromptCraftTest-*      → 73 个残留测试数据库密钥凭据
  （另有 database-key.PromptCraft 等真实凭据同列表共存）
```

临时目录同样残留 18 个（`PromptCraft-test-*` 10 个、`PromptCraft-attach-*` 7 个）。

**根因（已推断）：** 测试使用 `std::env::temp_dir().join(format!("PromptCraft-test-{}", Uuid::new_v4()))` 创建唯一目录，但**从不删除**；`test_storage()` 创建的唯一凭据服务名也**从不清理**。每跑一次 `cargo test` 就向真实 Windows 凭据管理器追加约 10 条永久凭据条目。

**后果：**

- 凭据管理器被测试垃圾长期污染，且**随每次测试运行持续累积**（73 个库密钥即多次运行的累积结果）。
- 干扰用户在凭据管理器中的正常查阅体验。
- 若将来引入「按服务名前缀批量清理」等逻辑，这些残留会参与匹配。

**评级理由：高。** 与 v0.3.4「凭据管理」核心主题直接相关，且是**每次运行必然发生**的确定性副作用。

---

### D-4 · 中 · 错误码缺少独立的「超时」分类

**证据层级：事实层（读码确认）**

用户规划 §6 要求：「网络断开、超时、限流、余额不足、模型不存在都有稳定错误码」——共 5 类。

`error_code_for_status`（`provider.rs:789-798`）实现：

| HTTP 状态 | 错误码 |
|---|---|
| 401 / 403 | `AUTH_FAILED` |
| 402 | `BALANCE_INSUFFICIENT` |
| 400 / 404 | `MODEL_NOT_FOUND` |
| 429 | `RATE_LIMITED` |
| 其他 | `NETWORK_FAILED` |

`provider.rs:100` 的传输层错误处理：

```rust
Err(error) if attempt == 0 && (error.is_connect() || error.is_timeout()) => continue,
Err(error) => { ... "NETWORK_FAILED" ... }
```

`is_timeout()` 与 `is_connect()` **被合并进同一个 `NETWORK_FAILED`**。需求列举的 5 类场景中，「超时」未被单独区分，实际只有 4 个独立码。

**后果：** 用量记录（`usage_records.error_code`）无法区分「网络不通」与「服务端响应超时」。对用户而言二者的处置建议不同（前者检查网络/代理，后者应减小 payload 或稍后重试）。这属于**可观测性缺陷**，非安全缺陷。

其余错误码（`STREAM_INTERRUPTED`、`STRUCTURE_RETRY`、`STRUCTURE_PARTIAL`、`STRUCTURE_FALLBACK`、`USER_CANCELLED`）实现完整并有测试固化。

---

### D-5 · 中 · 「彻底清空」的测试断言存在恒真漏洞

**证据层级：事实层（已验证）**

`clear_all_wipes_history_settings_usage_and_credential`（`storage.rs:578-598`）对「设置已清空」的断言是：

```rust
assert!(
    settings.profile_rules.as_array().map(|items| items.is_empty()).unwrap_or(true)
);
```

`AppSettings::default()` 的 `profile_rules` 本身就是 `serde_json::Value::Array(Vec::new())`（`models.rs:139-152`）。测试在清空前调用的是 `save_app_settings(&AppSettings::default())` —— 存入的就是空数组。

因此该断言在**删除逻辑完全失效**的情况下同样会通过：`app_settings()` 查不到记录时返回 `unwrap_or_default()`，默认值的 `profile_rules` 依然是空数组。

该测试真实证明的只有「`api_key()` 调用失败」和「历史列表为空」。**「设置已清空」这一项实际未被验证**。

**后果：** 属于虚假信心测试。若将来 `clear_all_data` 的 `DELETE FROM app_settings` 被误删，此测试不会变红。

---

### D-6 · 中 · 容量上限为硬编码常量，不满足「按配置清理」

**证据层级：事实层（已验证）**

用户规划需求 14 原文：「达到最大占用空间后**按配置**清理旧记录」。

实现（`storage.rs:23`）：

```rust
const MAX_HISTORY_CONTENT_BYTES: i64 = 64 * 1024 * 1024;
```

全仓库检索 `MAX_HISTORY_CONTENT_BYTES` / `max_history` / `max_storage`：**仅 2 处命中，均在 `storage.rs`（定义处与调用处 `:442`）**。前端设置页（`SettingsView.tsx`）只有 `monthlyWarningLimit` / `monthlyLimit` / `inputPrice` / `outputPrice` / `clearClipboard` 五项，**无任何容量设置项**。`AppSettings` 结构体亦无对应字段。

**判定：部分实现。** 清理机制本身完整且有测试（`capacity_enforcement_deletes_oldest_unpinned_first` 验证按最旧未置顶逐条删除、幂等），但「按配置」这一约束未实现。

**附带的 UI 不一致：** `SettingsView.tsx` 仍显示「历史保留 90 天，**最大 500 MB**」，而代码早已改为 64 MB 文本量上限。用户看到的说明与实际行为不符。

---

### D-7 · 中低 · `LENGTH()` 语义与常量名不符，中文场景下偏差约 3 倍

**证据层级：事实层（已推断，未构造边界数据实测）**

`storage.rs:457`：

```sql
SELECT COALESCE(SUM(LENGTH(title) + LENGTH(original) + LENGTH(enhanced)), 0) FROM history
```

SQLite 的 `LENGTH()` 对 TEXT 参数返回**字符数**（characters），而非字节数。而常量名 `MAX_HISTORY_CONTENT_BYTES` 及其注释（`storage.rs:22-23`）明确声明的是**字节数**语义：

```rust
// 历史记录文本内容总量上限（title+original+enhanced 的字节数），超出后按最旧未置顶清理
const MAX_HISTORY_CONTENT_BYTES: i64 = 64 * 1024 * 1024;
```

**后果：** 中文内容的 UTF-8 编码为 3 字节/字，因此 64「字符」上限的实际占用可达约 192 MB，是名义值的 3 倍。这会让容量清理的触发点显著晚于设计预期，对以中文提示词为主的本项目而言偏差具有实际意义。

未构造临界数据实测，故标为「已推断」。修复方向应为改用 `LENGTH(CAST(x AS BLOB))`，但本轮不修改代码。

---

### D-8 · 低 · CHANGELOG 声称的测试数量与事实不符

`CHANGELOG.md` v0.3.4 段及提交信息均称「**24 new security tests**」，实测为 **23 个**（`git diff` 逐行统计 `+#[test]`：storage 10 + attachments 7 + provider 3 + security 3 = 23）。

偏差 1 个，无功能影响，但属于发布文档的准确性问题，会误导后续维护者评估测试覆盖强度。

---

### D-9 · 低 · 附件需求 5/6 的判定需避免误读

需求 5「附件处理完成后自动删除临时文件」与需求 6「附件删除失败时给出提示」在本版本中**没有实现面**——`extract`（`attachments.rs:13-50`）全程对原文件原地读取，不产生任何临时文件。

`CHANGELOG.md` 已诚实说明「附件为原地读取，不产生任何临时文件……无清理失败面」，`extraction_leaves_no_temp_files_behind` 测试验证了处理前后目录内容完全一致。

**判定：需求 5 为「不适用（以更强方式满足）」，需求 6 为「不适用（无实现面）」。** 不得记为「已通过」——这会掩盖「若未来 v0.4.0 引入临时文件，删除失败提示需同步实现」这一前瞻性依赖。

---

## 5. 测试质量评估

### 5.1 优点

- **ZIP 加固测试扎实。** 路径穿越、压缩包炸弹（构造 9MB 声明解压内容触发 8MB 硬上限）、未知条目、manifest 版本校验，四个维度均有独立测试，且断言检查具体错误文案而非仅 `is_err()`。
- **凭据遮蔽测试精确到语义层。** 不只验证「密钥消失」，还固定了「只替换值本身、前后文逐字保留」与「纯文本原文逐字通过」两个行为，能防止未来正则改动引入误删。
- **测试隔离设计正确。** `Storage::open(&dir, &credential_service)` 的可测试化重构（`storage.rs:47`）使每个测试拥有独立临时目录与独立凭据服务名，**避免了污染真实 `PromptCraft` 凭据**这一最严重后果。方向完全正确，缺陷 D-3 只是收尾清理缺失，不否定该重构的价值。

### 5.2 缺陷

| 编号 | 问题 |
|---|---|
| D-3 | 测试凭据与临时目录从不清理，每次运行污染真实凭据管理器 |
| D-5 | 清空测试的「设置已清空」断言恒真，虚假信心 |
| D-2 | 置顶测试证明的是用户无法到达的状态，虚假信心 |
| D-1 | 兼容性测试恰好避开 `apiKey` 字段，遗漏投毒路径 |

**共性问题：测试的「存在」远多于「有效」。** 23 个新增测试中，至少 3 个（D-2、D-5 关联场景、以及对 D-1 的遗漏）存在覆盖假象。这提示 v0.3.1 建立的可信测试基线在新增安全测试时未被同等严格执行。

---

## 6. 未覆盖风险（单模型评审未能证明的事项）

以下事项**本轮无法给出结论**，不应被默认为无风险：

1. **GUI 端到端行为未验证。** 全部结论基于读码与 Rust 层测试。实际界面中的置顶入口、数据导入提示、错误提示展示均未人工操作确认。
2. **SQLCipher 数据库密钥管理未审计。** `get_or_create_database_key`（`storage.rs:507-519`）在凭据不存在时生成随机密钥并保存。若用户清除凭据但保留数据库文件，将永久无法解密（无恢复路径）。此行为是否可接受需产品决策。
3. **导出的 `provider.json` 含 `baseUrl`。** 导入时 `save_provider_config` 会写入包内的 `baseUrl`，可将 API 请求导向任意地址。结合 D-1，恶意包可同时替换 Key 与 Base URL，使增强请求内容直接发往攻击者服务器。**此项已推断，未实测。**
4. **`clear_all_data` 不删除 `database-key` 凭据。** 属设计选择（否则数据库永久无法打开），但用户「彻底清空」的预期与实际残留存在语义落差。
5. **v0.4.0 / v0.5.0 兼容性。** 按规划范围未评审。仅提示：`pinned` 字段（缺陷 B）若在后续版本补齐，需注意 `HistoryRecord` 结构变更会同时影响 `save_history` / `list_history` / 导入导出三处 SQL 与 TS 类型。
6. **未评估 `evaluation/` 目录 165 个 pytest。** 该目录属 v0.3.2 评测基础设施，不在 v0.3.4 评审范围。

---

## 7. 结论与放行建议

### 7.1 总体判定

**不建议将 v0.3.4 视为「安全加固已完成」放行。**

v0.3.4 在**正向实现**上质量良好：ZIP 加固、附件安全失败、错误信息防泄露、凭据遮蔽精度、旧版导入兼容等需求均有对应实现与测试，代码质量明显高于一般迭代。

但**安全评审的价值不在于「已做了什么」，而在于「攻击面是否闭合」**。本轮发现：

- **1 个阻断级缺陷（D-1）**：v0.3.4 立项时最核心的「API Key 只保存到 Windows 凭据管理器」目标，在导入路径上被完整绕过，且用户可达、无提示、现有测试未覆盖。这不是边角问题，而是该版本核心安全承诺的直接反例。
- **2 个高危缺陷（D-2、D-3）**：置顶功能生产不可达却显示测试通过；测试每次运行污染真实凭据管理器，与代码注释的自我声明矛盾。
- **4 个中低危缺陷（D-4 ~ D-8）** 与 2 项需避免误读的需求判定（D-9）。

### 7.2 需求覆盖统计

| 判定档位 | 数量 | 对应需求 |
|---|---|---|
| 已实现且有测试证据 | 8 | 1, 3, 4, 7, 9, 10, 11, 13 |
| 已实现（受缺陷影响） | 1 | 2（受 D-1 绕过） |
| 不适用（更强方式满足） | 2 | 5, 6 |
| 部分实现 | 2 | 8, 14 |
| 未实现 | 1 | 15 |
| 已实现但测试不足 | 1 | 12 |
| **合计** | **15** | — |

4 条红线**全部合规**。

### 7.3 修复优先级建议（本轮不执行修复）

| 优先级 | 缺陷 | 建议方向 |
|---|---|---|
| **P0** | D-1 凭据投毒 | 导入时**显式丢弃** `provider.api_key`（导入前 `provider.api_key = None`），或对 `api_key` 同时加 `skip_deserializing`；补一条含 `apiKey` 字段的攻击包回归测试 |
| **P1** | D-3 凭据污染 | 为测试实现 `Drop` 清理：删除凭据服务条目 + 递归删除临时目录 |
| **P1** | D-2 置顶不可达 | 补齐 `HistoryRecord.pinned` 字段 + 前端置顶入口 + `save_history`/`list_history` SQL 透传；或明确将需求 15 移出 v0.3.4 范围并在 CHANGELOG 说明 |
| **P2** | D-5 恒真断言 | 存入非默认 `profile_rules`（如含一条规则）后再断言为空 |
| **P2** | D-6 容量配置 | 增加 `AppSettings.max_history_content_mb` 并接入设置页；同步修正 UI 的「最大 500 MB」文案 |
| **P2** | D-4 超时码 | 增加 `TIMEOUT` 独立错误码，与 `is_connect()` 分支 |
| **P3** | D-7 `LENGTH()` 语义 | 改用 `LENGTH(CAST(x AS BLOB))` 或修正常量命名与注释 |
| **P3** | D-8 测试计数 | 将 CHANGELOG 与提交信息中的 24 修正为 23 |

### 7.4 方法论声明

本报告的**事实层结论均可由第三方原样复现**（git 差异、行号引用、命令退出码、探针输出）。

本报告的**判断层（严重度定级、需求覆盖判定、修复优先级排序）由单一模型给出，未经第二方交叉验证**，可能存在系统性偏差。报告中已尽最大努力区分「已验证」与「已推断」，请据此判断各条结论的可信度。

本报告**未使用**任何第二裁判、人工盲评或外部评审服务，因此**不构成**对 v0.3.4 增强效果的统计显著性结论，也**不重新引用** v0.3.2 的评测结果作为 v0.3.4 的效果证据。

---

## 附录 A：关键证据复现命令

```powershell
# 评审基线
git -C F:\ai-prompt-enhancer log --oneline -1        # 5886b6b
git -C F:\ai-prompt-enhancer diff --stat f0efe0f 5886b6b

# 新增测试数（23 而非 24）
git -C F:\ai-prompt-enhancer diff f0efe0f 5886b6b -- src-tauri/src/ |
  Select-String '^\+\s*#\[test\]'

# 测试基线
cargo test --manifest-path F:\ai-prompt-enhancer\src-tauri\Cargo.toml --lib
cargo fmt --manifest-path F:\ai-prompt-enhancer\src-tauri\Cargo.toml --all -- --check

# D-1：api_key 仅 skip_serializing（不阻止反序列化）
Select-String -Path 'F:\ai-prompt-enhancer\src-tauri\src\models.rs' -Pattern 'skip_serializing'

# D-2：pinned 仅存在于 storage.rs，前端与 tauri command 均无
git -C F:\ai-prompt-enhancer grep -n 'pinned' -- src/

# D-3：测试凭据残留
cmdkey /list | Select-String 'PromptCraftTest'

# D-6：容量上限不可配置
Select-String -Path 'F:\ai-prompt-enhancer\src-tauri\src\storage.rs' -Pattern 'MAX_HISTORY_CONTENT_BYTES'
```

## 附录 B：评审未做的事

- 未修改任何产品代码（D-1 探针已完整还原，`git status` 确认干净）
- 未删除或改动 `comparison-site/`、`website/` 等未跟踪文件
- 未进入 v0.4.0 / v0.5.0 功能范围
- 未联网核验任何事实
- 未对 v0.3.2 评测结论作任何重新解读
- 未在本报告中包含任何 API Key、凭据内容或用户隐私数据
