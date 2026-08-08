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
    pub assumptions: Vec<Assumption>,
    #[serde(default)]
    pub questions: Vec<ClarifyingQuestion>,
    #[serde(default)]
    pub changes: Vec<PromptChange>,
    #[serde(default)]
    pub suggestions: Vec<Suggestion>,
    #[serde(default)]
    pub risk_flags: Vec<RiskFlag>,
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

fn pending_state() -> String { "pending".to_string() }

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
    #[serde(default, skip_serializing)]
    pub api_key: Option<String>,
}

fn default_models() -> Vec<String> {
    vec!["deepseek-chat".into()]
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            base_url: "https://api.deepseek.com".into(),
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
    pub month_total: f64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BackendEvent {
    Delta { data: String },
    Status { data: String },
    Result { result: EnhancementResult },
    Usage { usage: UsageRecord },
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
    pub monthly_limit: f64,
    #[serde(default)]
    pub profile_rules: serde_json::Value,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            clear_clipboard: false,
            profile_enabled: true,
            custom_target_url: String::new(),
            monthly_warning_limit: 8.0,
            monthly_limit: 10.0,
            profile_rules: serde_json::Value::Array(Vec::new()),
        }
    }
}

fn default_warning_limit() -> f64 { 8.0 }
fn default_true() -> bool { true }
