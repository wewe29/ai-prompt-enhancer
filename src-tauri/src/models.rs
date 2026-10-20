use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnhancementRequest {
    pub original_text: String,
    pub context_text: String,
    pub attachments: Vec<AttachmentInput>,
    pub model: String,
    pub target_model: String,
    pub verbosity: String,
    pub custom_instructions: Option<String>,
    pub clarification_round: u8,
    pub clarification_answers: Vec<ClarificationAnswer>,
    pub profile_summary: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttachmentInput {
    pub name: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClarificationAnswer {
    pub question_id: String,
    pub answer: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnhancementResult {
    pub status: String,
    #[serde(default)]
    pub task_type: String,
    pub primary_prompt: String,
    #[serde(default)]
    pub facts: Vec<String>,
    #[serde(default)]
    pub assumptions: Vec<Assumption>,
    #[serde(default)]
    pub questions: Vec<ClarifyingQuestion>,
    #[serde(default)]
    pub changes: Vec<PromptChange>,
    #[serde(default)]
    pub suggestions: Vec<Suggestion>,
    #[serde(default)]
    pub risk_flags: Vec<RiskFlag>,
    #[serde(default)]
    pub candidates: Vec<PromptCandidate>,
    /// 交付状态。**由后端决定**（模型给的值不可信）。
    ///
    /// `None` = 不可判定：前端必须显示「不可判定」，
    /// 不得当成"完整交付"。此前这里是 `String` 且缺省成 "complete"，
    /// 于是模型少给一个字段就被算作完整交付（A13 / E1 的同源问题）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_status: Option<String>,
    /// 增强档位。**只有模型真的给出判断时才有值**。
    ///
    /// 降级 / 回退路径必须是 `None`：把 "none" 塞进这个字段，
    /// 等于宣称"模型判断过无需修改"—— 而结构解析彻底失败时
    /// 根本没有过这次判断（E1）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enhancement_level: Option<String>,
    /// 降级路径自己的失败原因。**不是模型判断**。
    ///
    /// 降级必须携带自己的失败原因，不得复用 `enhancement_level`
    /// 这类"模型判断结果"字段来表达"结构解析失败"。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failure_reason: Option<DeliveryFailure>,
    #[serde(default)]
    pub notices: Vec<String>,
}

/// 交付失败原因。
///
/// 与 `EnhancementResult::enhancement_level` 是**两回事**：
/// 后者陈述模型做过的判断，前者陈述这次交付出了什么问题。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct DeliveryFailure {
    /// 机器可读失败码，例如 `STRUCTURE_FALLBACK`。
    pub code: String,
    /// 面向用户的一句话。
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Assumption {
    pub id: String,
    pub text: String,
    #[serde(default)]
    pub confirmed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClarifyingQuestion {
    pub id: String,
    pub text: String,
    pub why_needed: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptChange {
    pub id: String,
    #[serde(rename = "type")]
    pub change_type: String,
    #[serde(default)]
    pub before: String,
    pub after: String,
    pub reason: String,
    #[serde(default = "pending_state")]
    pub state: String,
}

fn pending_state() -> String {
    "pending".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Suggestion {
    pub id: String,
    pub kind: String,
    pub title: String,
    pub purpose: String,
    pub content: String,
    pub operation: String,
    #[serde(default)]
    pub anchor: String,
    #[serde(default)]
    pub applied: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskFlag {
    pub category: String,
    pub message: String,
    pub required_protection: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptCandidate {
    #[serde(default)]
    pub index: u32,
    pub text: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderConfig {
    pub base_url: String,
    pub has_api_key: bool,
    pub default_model: String,
    pub v4_flash_model_id: String,
    pub input_price: f64,
    pub output_price: f64,
    #[serde(default = "default_models")]
    pub models: Vec<String>,
    /// `skip_deserializing`：导入的数据包**永远不能**改写本机 API Key。
    /// 仅有 `skip_serializing` 只能挡住导出方向，攻击者仍可在 provider.json 里
    /// 植入 `apiKey` 字段，经 `import_data` → `save_provider_config` 写入真实凭据管理器。
    #[serde(default, skip_serializing, skip_deserializing)]
    pub api_key: Option<String>,
}

/// 默认 API 端点。读取供应商配置时若发现库里的地址非法，回落到这里。
pub const DEFAULT_BASE_URL: &str = "https://api.deepseek.com";

/// 导入结果报告。导入的一切"没做/少做/被忽略"都必须出现在这里，
/// 不允许只报一个成功的条数（v0.4.0 架构审查 P0-3）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportReport {
    /// 实际导入的历史记录条数。
    pub imported: usize,
    /// 因超过单包上限而**未**导入的条数（0 表示全部导入）。
    pub skipped: usize,
    /// 导入包里的 API 地址与本机不一致时为 true：
    /// 此时导入包提供的地址被忽略，仍使用本机当前地址。
    pub base_url_kept: bool,
}

fn default_models() -> Vec<String> {
    vec!["deepseek-chat".into()]
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            base_url: DEFAULT_BASE_URL.into(),
            has_api_key: false,
            default_model: "deepseek-chat".into(),
            v4_flash_model_id: "deepseek-v4-flash".into(),
            input_price: 0.001,
            output_price: 0.002,
            models: default_models(),
            api_key: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageRecord {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub estimated_cost: f64,
    /// 本月累计费用。
    ///
    /// `None` = **不可判定**（用量记录没写进数据库，或读取失败）。
    /// 绝不能回落成 0.0：0.0 会被前端当成"本月一分没花"，
    /// 强制额度拦截随之失效 —— 用户以为有额度，实际已经超了（A12）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub month_total: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BackendEvent {
    Delta { data: String },
    Status { data: String },
    Result { result: EnhancementResult },
    Usage { usage: UsageRecord },
    /// 事件通道上的失败。
    ///
    /// B5：此前 Rust 侧没有这个变体，而 TS 侧声明了 `"error"`，
    /// 于是 `lib.ts` 的 `if (event.type === "error")` 分支**永远不可达** ——
    /// 契约说谎，事件通道上的失败处理形同虚设。
    /// 现在这个变体存在，而且 provider.rs 的失败路径**真的**会发它。
    /// 命令的 Err 返回仍然是权威失败通道；这个事件让前端在流式中途
    /// 就能知道"这次增强失败了"，而不是只看到一个迟到的命令错误。
    Error { code: String, message: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtractedAttachment {
    pub text: String,
    pub kind: String,
    pub chars: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryRecord {
    pub id: String,
    pub title: String,
    pub original: String,
    pub enhanced: String,
    pub created_at: String,
    pub model: String,
    pub target: String,
    #[serde(default, rename = "deliveryStatus")]
    pub delivery_status: Option<String>,
    #[serde(default, rename = "enhancementLevel")]
    pub enhancement_level: Option<String>,
    #[serde(default, rename = "promptVersion")]
    pub prompt_version: Option<String>,
    /// 置顶标记：置顶记录永不被 90 天时间清理与容量清理删除。
    #[serde(default)]
    pub pinned: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub clear_clipboard: bool,
    #[serde(default = "default_true")]
    pub profile_enabled: bool,
    #[serde(default)]
    pub custom_target_url: String,
    #[serde(default = "default_warning_limit")]
    pub monthly_warning_limit: f64,
    #[serde(default)]
    pub monthly_limit: f64,
    #[serde(default = "default_max_history_mb")]
    pub max_history_mb: i64,
    #[serde(default)]
    pub profile_rules: serde_json::Value,
}

/// 历史容量上限的默认/回落值（MB）。
pub fn default_history_mb() -> i64 {
    64
}

fn default_max_history_mb() -> i64 {
    default_history_mb()
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            clear_clipboard: false,
            profile_enabled: true,
            custom_target_url: String::new(),
            monthly_warning_limit: 8.0,
            monthly_limit: 10.0,
            max_history_mb: default_history_mb(),
            profile_rules: serde_json::Value::Array(Vec::new()),
        }
    }
}

fn default_warning_limit() -> f64 {
    8.0
}
fn default_true() -> bool {
    true
}
