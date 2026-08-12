use crate::{
    AppState,
    models::{BackendEvent, EnhancementRequest, EnhancementResult, UsageRecord},
    prompts::{SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION, verbosity_label},
    security::redact_sensitive,
};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{collections::HashSet, time::Instant};
use tauri::{State, ipc::Channel};
use tokio_util::sync::CancellationToken;

#[derive(Default, Deserialize)]
struct WireUsage {
    #[serde(default)]
    prompt_tokens: u64,
    #[serde(default)]
    completion_tokens: u64,
}

pub async fn enhance(
    request: EnhancementRequest,
    on_event: Channel<BackendEvent>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if let Err(message) = validate_request(&request) {
        let _ = state.storage.record_usage(
            &request.model,
            estimate_tokens(&request.original_text),
            0,
            0.0,
            0,
            "error",
            Some("REQUEST_INVALID"),
        );
        return Err(message);
    }
    let api_key = state.storage.api_key()?;
    let config = state.storage.provider_config()?;
    if request.model.trim().is_empty() {
        return Err("所选模型没有可用的 API model_id".into());
    }

    let token = CancellationToken::new();
    {
        let mut cancellation = state.cancellation.lock().await;
        if let Some(previous) = cancellation.replace(token.clone()) {
            previous.cancel();
        }
    }
    let started = Instant::now();
    let endpoint = format!("{}/chat/completions", config.base_url.trim_end_matches('/'));
    on_event
        .send(BackendEvent::Status {
            data: "requesting".into(),
        })
        .map_err(|error| error.to_string())?;

    let mut outcome: Option<(EnhancementResult, WireUsage, Option<&'static str>)> = None;
    for attempt in 0..2 {
        if attempt == 1 {
            on_event
                .send(BackendEvent::Status {
                    data: "retrying_structure".into(),
                })
                .map_err(|error| error.to_string())?;
        }
        let body = if attempt == 1 {
            build_body_with_instruction(&request, Some(STRUCTURE_FIX_INSTRUCTION))
        } else {
            build_body(&request)
        };
        let sent = state
            .client
            .post(&endpoint)
            .bearer_auth(&api_key)
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await;
        let response = match sent {
            Ok(candidate) if candidate.status().is_success() => candidate,
            Ok(candidate) => {
                let status = candidate.status();
                let message = candidate.text().await.unwrap_or_default();
                if status.is_server_error() && attempt == 0 {
                    continue;
                }
                record_error_usage(
                    &state,
                    &request,
                    config.input_price,
                    config.output_price,
                    started.elapsed().as_millis() as u64,
                    error_code_for_status(status.as_u16()),
                );
                return Err(map_http_error(status.as_u16(), &message));
            }
            Err(error) if attempt == 0 && (error.is_connect() || error.is_timeout()) => continue,
            Err(error) => {
                record_error_usage(
                    &state,
                    &request,
                    config.input_price,
                    config.output_price,
                    started.elapsed().as_millis() as u64,
                    "NETWORK_FAILED",
                );
                return Err(format!("网络请求失败，输入内容仍已保留：{error}"));
            }
        };
        let (raw, wire_usage) = match consume_stream(response, &on_event, token.clone()).await {
            Ok(consumed) => consumed,
            Err(_) if attempt == 0 && !token.is_cancelled() => continue,
            Err(error) => {
                record_error_usage(
                    &state,
                    &request,
                    config.input_price,
                    config.output_price,
                    started.elapsed().as_millis() as u64,
                    "STREAM_INTERRUPTED",
                );
                return Err(format!("{error}，已自动重试一次"));
            }
        };
        if token.is_cancelled() {
            let input_tokens = wire_usage
                .prompt_tokens
                .max(estimate_tokens(&request.original_text));
            let output_tokens = wire_usage.completion_tokens.max(estimate_tokens(&raw));
            let estimated_cost = calculate_cost(
                input_tokens,
                output_tokens,
                config.input_price,
                config.output_price,
            );
            let _ = state.storage.record_usage(
                &request.model,
                input_tokens,
                output_tokens,
                estimated_cost,
                started.elapsed().as_millis() as u64,
                "cancelled",
                Some("USER_CANCELLED"),
            );
            return Ok(());
        }

        let parsed = parse_enhancement(&raw).and_then(|value| {
            let (value, notices) = normalize_value(value);
            let mut result = serde_json::from_value::<EnhancementResult>(value)
                .map_err(|_| "模型返回的结构无法解析，输入内容仍已保留，请重新生成".to_string())?;
            for notice in notices {
                if !result.notices.contains(&notice) {
                    result.notices.push(notice);
                }
            }
            if request.clarification_round >= 3 {
                result.questions.clear();
                result.status = "ready".into();
            }
            validate_core_result(&result)?;
            Ok(result)
        });
        match parsed {
            Ok(mut result) => {
                let error_code = match validate_complete_result(&result, &request.original_text) {
                    Ok(()) => None,
                    Err(_) => {
                        result.delivery_status = "partial".into();
                        result
                            .notices
                            .push("模型返回不完整的增强结构，主提示词仍可使用。".into());
                        Some("STRUCTURE_PARTIAL")
                    }
                };
                outcome = Some((result, wire_usage, error_code));
                break;
            }
            Err(_) if attempt == 0 => {
                record_error_usage(
                    &state,
                    &request,
                    config.input_price,
                    config.output_price,
                    started.elapsed().as_millis() as u64,
                    "STRUCTURE_RETRY",
                );
                continue;
            }
            Err(_) => {
                let result = match partial_primary_prompt(&raw) {
                    Some(prompt) => partial_result(prompt),
                    None => fallback_result(&request.original_text),
                };
                let code = if result.delivery_status == "partial" {
                    "STRUCTURE_PARTIAL"
                } else {
                    "STRUCTURE_FALLBACK"
                };
                outcome = Some((result, wire_usage, Some(code)));
                break;
            }
        }
    }
    let (result, wire_usage, error_code) =
        outcome.ok_or_else(|| "DeepSeek 服务暂时不可用，已自动重试一次".to_string())?;

    let input_tokens = wire_usage
        .prompt_tokens
        .max(estimate_tokens(&request.original_text));
    let output_tokens = wire_usage
        .completion_tokens
        .max(estimate_tokens(&result.primary_prompt));
    let estimated_cost = calculate_cost(
        input_tokens,
        output_tokens,
        config.input_price,
        config.output_price,
    );
    let duration_ms = started.elapsed().as_millis() as u64;
    let month_total = match error_code {
        Some(code) => {
            let _ = state.storage.record_usage(
                &request.model,
                input_tokens,
                output_tokens,
                estimated_cost,
                duration_ms,
                "error",
                Some(code),
            );
            0.0
        }
        None => state
            .storage
            .record_usage(
                &request.model,
                input_tokens,
                output_tokens,
                estimated_cost,
                duration_ms,
                "success",
                None,
            )
            .unwrap_or_else(|_| {
                let _ = state.storage.record_usage(
                    &request.model,
                    input_tokens,
                    output_tokens,
                    estimated_cost,
                    duration_ms,
                    "error",
                    Some("STORAGE_FAILED"),
                );
                0.0
            }),
    };
    on_event
        .send(BackendEvent::Result { result })
        .map_err(|error| error.to_string())?;
    on_event
        .send(BackendEvent::Usage {
            usage: UsageRecord {
                input_tokens,
                output_tokens,
                estimated_cost,
                month_total,
            },
        })
        .map_err(|error| error.to_string())?;
    Ok(())
}

async fn consume_stream(
    response: reqwest::Response,
    channel: &Channel<BackendEvent>,
    cancellation: CancellationToken,
) -> Result<(String, WireUsage), String> {
    let mut stream = response.bytes_stream();
    let mut pending = Vec::<u8>::new();
    let mut raw = String::new();
    let mut preview = String::new();
    let mut usage = WireUsage::default();

    loop {
        let next = tokio::select! {
            _ = cancellation.cancelled() => return Ok((raw, usage)),
            item = stream.next() => item,
        };
        let Some(chunk) = next else { break };
        let chunk = chunk.map_err(|error| format!("流式响应中断，已生成内容被保留：{error}"))?;
        pending.extend_from_slice(&chunk);
        while let Some(position) = pending.iter().position(|byte| *byte == b'\n') {
            let line = pending.drain(..=position).collect::<Vec<_>>();
            let line = String::from_utf8_lossy(&line).trim().to_string();
            let Some(data) = line.strip_prefix("data:").map(str::trim) else {
                continue;
            };
            if data == "[DONE]" {
                continue;
            }
            let Ok(event) = serde_json::from_str::<Value>(data) else {
                continue;
            };
            if let Some(delta) = event
                .pointer("/choices/0/delta/content")
                .and_then(Value::as_str)
            {
                raw.push_str(delta);
                if let Some(current) = partial_primary_prompt(&raw) {
                    if let Some(suffix) = current.strip_prefix(&preview) {
                        if !suffix.is_empty() {
                            channel
                                .send(BackendEvent::Delta {
                                    data: suffix.to_string(),
                                })
                                .map_err(|error| error.to_string())?;
                        }
                    }
                    preview = current;
                }
            }
            if let Some(value) = event.get("usage") {
                if let Ok(parsed) = serde_json::from_value::<WireUsage>(value.clone()) {
                    usage = parsed;
                }
            }
        }
    }
    Ok((raw, usage))
}

const STRUCTURE_FIX_INSTRUCTION: &str = "上一次输出没有形成可解析的 PromptCraft JSON。请重新生成同一任务的结果。只输出一个完整 JSON 对象，不要解释，不要代码围栏。必须优先保证 status、task_type、primary_prompt、questions 可用；其他数组无法确定时可以返回空数组。";

fn build_body(request: &EnhancementRequest) -> Value {
    build_body_with_instruction(request, None)
}

fn build_body_with_instruction(request: &EnhancementRequest, instruction: Option<&str>) -> Value {
    let attachments = request
        .attachments
        .iter()
        .map(|attachment| {
            format!(
                "<attachment name=\"{}\">\n{}\n</attachment>",
                attachment.name,
                redact_sensitive(&attachment.text)
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    let answers = request
        .clarification_answers
        .iter()
        .map(|answer| {
            format!(
                "{}: {}",
                answer.question_id,
                redact_sensitive(&answer.answer)
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    let user_message = format!(
        "系统提示词版本：{SYSTEM_PROMPT_VERSION}\n目标模型：{}\n详细程度：{}\n自定义详细要求：{}\n澄清轮次：{}/3\n用户偏好摘要：\n{}\n\n原始提示词：\n{}\n\n上下文：\n{}\n\n本轮澄清回答：\n{}\n\n附件参考资料：\n{}",
        request.target_model,
        verbosity_label(&request.verbosity),
        request.custom_instructions.as_deref().unwrap_or("无"),
        request.clarification_round,
        request.profile_summary.join("\n"),
        redact_sensitive(&request.original_text),
        redact_sensitive(&request.context_text),
        answers,
        attachments,
    );
    let user_message = match instruction {
        Some(instruction) => format!("{user_message}\n\n{instruction}"),
        None => user_message,
    };
    json!({
        "model": request.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.35,
        "stream": true,
        "stream_options": {"include_usage": true},
        "response_format": {"type": "json_object"}
    })
}

fn parse_enhancement(raw: &str) -> Result<Value, String> {
    let trimmed = raw.trim();
    if let Ok(value) = serde_json::from_str::<Value>(trimmed) {
        if value.is_object() {
            return Ok(value);
        }
    }
    let unfenced = strip_fences(trimmed);
    if let Ok(value) = serde_json::from_str::<Value>(unfenced) {
        if value.is_object() {
            return Ok(value);
        }
    }
    if let Some(object) = extract_balanced_object(trimmed) {
        if let Ok(value) = serde_json::from_str::<Value>(object) {
            if value.is_object() {
                return Ok(value);
            }
        }
    }
    Err("模型返回的结构无法解析，输入内容仍已保留，请重新生成".to_string())
}

fn strip_fences(raw: &str) -> &str {
    let trimmed = raw.trim();
    let after_open = trimmed
        .strip_prefix("```json")
        .or_else(|| trimmed.strip_prefix("```"))
        .unwrap_or(trimmed)
        .trim();
    after_open.strip_suffix("```").unwrap_or(after_open).trim()
}

fn extract_balanced_object(raw: &str) -> Option<&str> {
    let bytes = raw.as_bytes();
    let mut depth = 0usize;
    let mut start = None;
    let mut in_string = false;
    let mut escaped = false;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            index += 1;
            continue;
        }
        match byte {
            b'"' => in_string = true,
            b'{' => {
                if depth == 0 {
                    start = Some(index);
                }
                depth += 1;
            }
            b'}' if depth > 0 => {
                depth -= 1;
                if depth == 0 {
                    if let Some(begin) = start {
                        let candidate = &raw[begin..=index];
                        if serde_json::from_str::<Value>(candidate).is_ok() {
                            return Some(candidate);
                        }
                        start = None;
                    }
                }
            }
            _ => {}
        }
        index += 1;
    }
    None
}

fn normalize_value(mut value: Value) -> (Value, Vec<String>) {
    let mut notices = Vec::new();
    let Some(object) = value.as_object_mut() else {
        return (value, notices);
    };
    let status_valid = matches!(
        object.get("status").and_then(Value::as_str),
        Some("ready" | "needs_clarification")
    );
    if !status_valid {
        let questions_non_empty = object
            .get("questions")
            .and_then(Value::as_array)
            .map(|items| !items.is_empty())
            .unwrap_or(false);
        let derived = if questions_non_empty {
            "needs_clarification"
        } else {
            "ready"
        };
        object.insert("status".into(), Value::String(derived.into()));
        notices.push("模型未返回有效状态，已按内容自动判断".into());
    }
    const TASK_TYPES: [&str; 7] = [
        "code",
        "creative",
        "writing",
        "qa",
        "data",
        "translation",
        "other",
    ];
    let task_type_valid = object
        .get("task_type")
        .and_then(Value::as_str)
        .map(|kind| TASK_TYPES.contains(&kind))
        .unwrap_or(false);
    if !task_type_valid {
        object.insert("task_type".into(), Value::String("other".into()));
        notices.push("任务类型无法识别，已归入其他".into());
    }
    for field in [
        "assumptions",
        "questions",
        "changes",
        "suggestions",
        "risk_flags",
    ] {
        match object.get(field) {
            Some(Value::Array(_)) => {}
            _ => {
                object.insert(field.into(), Value::Array(Vec::new()));
                notices.push(format!("{field} 字段缺失或格式无效，已按空数组处理"));
            }
        }
    }
    if let Some(suggestions) = object.get_mut("suggestions").and_then(Value::as_array_mut) {
        if suggestions.len() > 5 {
            suggestions.truncate(5);
            notices.push("模型返回的建议超过 5 条，已保留前 5 条".into());
        }
        dedupe_ids(suggestions, "s", &mut notices);
    }
    if let Some(changes) = object.get_mut("changes").and_then(Value::as_array_mut) {
        dedupe_ids(changes, "c", &mut notices);
    }
    if let Some(questions) = object.get_mut("questions").and_then(Value::as_array_mut) {
        if questions.len() > 3 {
            questions.truncate(3);
            notices.push("模型返回的澄清问题超过 3 个，已保留前 3 个".into());
        }
    }
    if let Some(Value::String(prompt)) = object.get_mut("primary_prompt") {
        let trimmed = prompt.trim().to_string();
        if trimmed != *prompt {
            *prompt = trimmed;
        }
    }
    (value, notices)
}

fn dedupe_ids(items: &mut Vec<Value>, prefix: &str, notices: &mut Vec<String>) {
    let mut seen = HashSet::new();
    let mut next_index = 1;
    let mut index = 0;
    while index < items.len() {
        let mut id = items[index]
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if id.is_empty() {
            while seen.contains(&format!("{prefix}{next_index}")) {
                next_index += 1;
            }
            id = format!("{prefix}{next_index}");
            next_index += 1;
            if let Some(item) = items[index].as_object_mut() {
                item.insert("id".into(), Value::String(id.clone()));
            }
        }
        if seen.contains(&id) {
            notices.push(format!("{prefix} 前缀条目 {id} 重复，已删除后出现的重复项"));
            items.remove(index);
            continue;
        }
        seen.insert(id);
        index += 1;
    }
}

fn validate_request(request: &EnhancementRequest) -> Result<(), String> {
    if request.original_text.trim().is_empty() {
        return Err("原始提示词不能为空".into());
    }
    if request.attachments.len() > 5 {
        return Err("一次最多使用 5 个附件".into());
    }
    let total = request.context_text.chars().count()
        + request
            .attachments
            .iter()
            .map(|item| item.text.chars().count())
            .sum::<usize>();
    if total > 100_000 {
        return Err("上下文和附件总文本超过 100,000 字符".into());
    }
    if request.clarification_round > 3 {
        return Err("澄清轮次不能超过 3 轮".into());
    }
    Ok(())
}

fn validate_core_result(result: &EnhancementResult) -> Result<(), String> {
    if result.primary_prompt.trim().is_empty() {
        return Err("模型没有生成主提示词".into());
    }
    if result.primary_prompt.contains("XXX") {
        return Err("模型结果包含未替换占位符，请重新生成".into());
    }
    if !matches!(result.status.as_str(), "ready" | "needs_clarification") {
        return Err("模型返回了无效状态，请重新生成".into());
    }
    if result.questions.len() > 3 {
        return Err("模型返回了超过 3 个澄清问题，请重新生成".into());
    }
    Ok(())
}

fn validate_complete_result(result: &EnhancementResult, original: &str) -> Result<(), String> {
    if result.suggestions.len() != 5 {
        return Err("模型未返回恰好 5 个可选建议，请重新生成".into());
    }
    const SUGGESTION_KINDS: [&str; 5] = [
        "goal",
        "context",
        "format",
        "constraint",
        "alternate_intent",
    ];
    const OPERATIONS: [&str; 2] = ["insert", "replace"];
    for suggestion in &result.suggestions {
        if !SUGGESTION_KINDS.contains(&suggestion.kind.as_str()) {
            return Err(format!("建议包含无效类型：{}", suggestion.kind));
        }
        if !OPERATIONS.contains(&suggestion.operation.as_str()) {
            return Err(format!("建议包含无效操作：{}", suggestion.operation));
        }
    }
    let mut unique = HashSet::new();
    for suggestion in &result.suggestions {
        if suggestion.id.trim().is_empty() || !unique.insert(suggestion.id.as_str()) {
            return Err("模型返回了重复建议，请重新生成".into());
        }
    }
    const EMPTY_BEFORE_TYPES: [&str; 4] = ["add_context", "add_constraint", "format", "safety"];
    for change in &result.changes {
        if change.before.is_empty() {
            if !EMPTY_BEFORE_TYPES.contains(&change.change_type.as_str()) {
                return Err(
                    "空 before 的修改仅允许 add_context/add_constraint/format/safety".into(),
                );
            }
        } else if !original.contains(&change.before) {
            return Err("模型修改明细引用了原文不存在的片段，请重新生成".into());
        }
    }
    const TASK_TYPES: [&str; 7] = [
        "code",
        "creative",
        "writing",
        "qa",
        "data",
        "translation",
        "other",
    ];
    if !TASK_TYPES.contains(&result.task_type.as_str()) {
        return Err("模型返回了无效任务类型，请重新生成".into());
    }
    if !matches!(
        result.enhancement_level.as_str(),
        "none" | "light" | "clarify"
    ) {
        return Err("模型返回了无效增强等级，请重新生成".into());
    }
    Ok(())
}

fn partial_primary_prompt(raw: &str) -> Option<String> {
    let key = raw.find("\"primary_prompt\"")?;
    let after_key = &raw[key + "\"primary_prompt\"".len()..];
    let colon = after_key.find(':')?;
    let value = after_key[colon + 1..].trim_start();
    let encoded = value.strip_prefix('"')?;
    let mut escaped = false;
    let mut buffer = String::new();
    for character in encoded.chars() {
        if !escaped && character == '"' {
            break;
        }
        buffer.push(character);
        if character == '\\' && !escaped {
            escaped = true;
        } else {
            escaped = false;
        }
    }
    if escaped {
        buffer.pop();
    }
    serde_json::from_str::<String>(&format!("\"{}\"", buffer)).ok()
}

fn estimate_tokens(text: &str) -> u64 {
    ((text.chars().count() as f64) / 1.8).ceil() as u64
}

fn calculate_cost(
    input_tokens: u64,
    output_tokens: u64,
    input_price: f64,
    output_price: f64,
) -> f64 {
    input_tokens as f64 / 1000.0 * input_price + output_tokens as f64 / 1000.0 * output_price
}

fn map_http_error(status: u16, body: &str) -> String {
    let detail = serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|value| {
            value
                .pointer("/error/message")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_else(|| body.chars().take(180).collect());
    match status {
        401 | 403 => format!("API Key 无效或没有模型权限：{detail}"),
        402 => format!("DeepSeek 账户余额不足：{detail}"),
        429 => format!("DeepSeek 请求限流，请稍后重试：{detail}"),
        400 => format!("DeepSeek 拒绝了请求参数，请检查模型 ID：{detail}"),
        _ => format!("DeepSeek 返回错误 {status}：{detail}"),
    }
}

fn error_code_for_status(status: u16) -> &'static str {
    match status {
        401 | 403 => "AUTH_FAILED",
        402 => "BALANCE_INSUFFICIENT",
        400 | 404 => "MODEL_NOT_FOUND",
        429 => "RATE_LIMITED",
        _ => "NETWORK_FAILED",
    }
}

fn record_error_usage(
    state: &State<'_, AppState>,
    request: &EnhancementRequest,
    input_price: f64,
    output_price: f64,
    duration_ms: u64,
    code: &'static str,
) {
    let input_tokens = estimate_tokens(&request.original_text);
    let cost = calculate_cost(input_tokens, 0, input_price, output_price);
    let _ = state.storage.record_usage(
        &request.model,
        input_tokens,
        0,
        cost,
        duration_ms,
        "error",
        Some(code),
    );
}

fn partial_result(prompt: String) -> EnhancementResult {
    EnhancementResult {
        status: "ready".into(),
        task_type: "other".into(),
        primary_prompt: prompt,
        assumptions: Vec::new(),
        questions: Vec::new(),
        changes: Vec::new(),
        suggestions: Vec::new(),
        risk_flags: Vec::new(),
        delivery_status: "partial".into(),
        enhancement_level: "none".into(),
        notices: vec!["模型返回不完整的增强结构，主提示词仍可使用。".into()],
    }
}

fn fallback_result(original: &str) -> EnhancementResult {
    EnhancementResult {
        status: "ready".into(),
        task_type: "other".into(),
        primary_prompt: original.trim().to_string(),
        assumptions: Vec::new(),
        questions: Vec::new(),
        changes: Vec::new(),
        suggestions: Vec::new(),
        risk_flags: Vec::new(),
        delivery_status: "fallback".into(),
        enhancement_level: "none".into(),
        notices: vec!["增强服务未返回可用结构，本次已保留原始提示词".into()],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_partial_primary_prompt() {
        let raw = r#"{"primary_prompt":"第一行\n第二行","status":"ready"}"#;
        assert_eq!(
            partial_primary_prompt(raw).as_deref(),
            Some("第一行\n第二行")
        );
    }

    fn request() -> EnhancementRequest {
        EnhancementRequest {
            original_text: "解释这段代码".into(),
            context_text: String::new(),
            attachments: Vec::new(),
            model: "deepseek-chat".into(),
            target_model: "豆包".into(),
            verbosity: "standard".into(),
            custom_instructions: None,
            clarification_round: 0,
            clarification_answers: Vec::new(),
            profile_summary: Vec::new(),
        }
    }

    #[test]
    fn validates_request_limits() {
        let mut value = request();
        assert!(validate_request(&value).is_ok());
        value.original_text = "  ".into();
        assert_eq!(validate_request(&value).unwrap_err(), "原始提示词不能为空");

        value = request();
        value.attachments = (0..6)
            .map(|index| crate::models::AttachmentInput {
                name: format!("{index}.txt"),
                text: "x".into(),
            })
            .collect();
        assert_eq!(
            validate_request(&value).unwrap_err(),
            "一次最多使用 5 个附件"
        );
    }

    #[test]
    fn validates_result_shape_and_placeholders() {
        let suggestion = crate::models::Suggestion {
            id: "s".into(),
            kind: "goal".into(),
            title: "目标".into(),
            purpose: "用途".into(),
            content: "内容".into(),
            operation: "insert".into(),
            anchor: String::new(),
            applied: false,
        };
        let mut result = EnhancementResult {
            status: "ready".into(),
            task_type: "other".into(),
            primary_prompt: "有效提示词".into(),
            assumptions: Vec::new(),
            questions: Vec::new(),
            changes: Vec::new(),
            suggestions: (0..5)
                .map(|index| crate::models::Suggestion {
                    id: format!("s{index}"),
                    ..suggestion.clone()
                })
                .collect(),
            risk_flags: Vec::new(),
            delivery_status: "complete".into(),
            enhancement_level: "light".into(),
            notices: Vec::new(),
        };
        assert!(validate_core_result(&result).is_ok());
        assert!(validate_complete_result(&result, "解释这段代码").is_ok());
        result.primary_prompt = "请解释 XXX".into();
        assert!(
            validate_core_result(&result)
                .unwrap_err()
                .contains("占位符")
        );
    }

    #[test]
    fn calculates_configured_cost() {
        assert!((calculate_cost(1_000, 2_000, 0.001, 0.002) - 0.005).abs() < f64::EPSILON);
    }

    #[test]
    fn parses_direct_json() {
        let raw = r#"{"status":"ready","task_type":"qa","primary_prompt":"解释这段代码","questions":[],"suggestions":[]}"#;
        let value = parse_enhancement(raw).unwrap();
        assert_eq!(value["primary_prompt"], "解释这段代码");
    }

    #[test]
    fn parses_fenced_json() {
        let raw = "```json\n{\"status\":\"ready\",\"task_type\":\"qa\",\"primary_prompt\":\"解释这段代码\"}\n```";
        let value = parse_enhancement(raw).unwrap();
        assert_eq!(value["primary_prompt"], "解释这段代码");
    }

    #[test]
    fn parses_json_with_leading_and_trailing_prose() {
        let raw = "好的，以下是增强后的提示词：\n{\"status\":\"ready\",\"task_type\":\"qa\",\"primary_prompt\":\"解释这段代码\"}\n希望对你有帮助。";
        let value = parse_enhancement(raw).unwrap();
        assert_eq!(value["primary_prompt"], "解释这段代码");
    }

    #[test]
    fn scans_braces_inside_strings() {
        let raw = r#"前言 {"primary_prompt":"保留 {花括号} 和 }的文本","status":"ready"} 后记"#;
        let value = parse_enhancement(raw).unwrap();
        assert_eq!(value["primary_prompt"], "保留 {花括号} 和 }的文本");

        let raw = r#"{"a":"say \"hi\" {x}","b":{"c":1}}"#;
        let value = parse_enhancement(raw).unwrap();
        assert_eq!(value["a"], "say \"hi\" {x}");
        assert_eq!(value["b"]["c"], 1);
    }

    #[test]
    fn scanner_rejects_unterminated_json() {
        let raw = r#"{"primary_prompt":"未闭合的对象"#;
        assert!(parse_enhancement(raw).is_err());
        assert!(parse_enhancement("完全没有 JSON 的纯文本").is_err());
    }

    #[test]
    fn missing_suggestions_with_valid_primary_yields_partial() {
        let suggestion = crate::models::Suggestion {
            id: "s".into(),
            kind: "goal".into(),
            title: "目标".into(),
            purpose: "用途".into(),
            content: "内容".into(),
            operation: "insert".into(),
            anchor: String::new(),
            applied: false,
        };
        let mut result = EnhancementResult {
            status: "ready".into(),
            task_type: "other".into(),
            primary_prompt: "有效提示词".into(),
            assumptions: Vec::new(),
            questions: Vec::new(),
            changes: Vec::new(),
            suggestions: (0..3)
                .map(|index| crate::models::Suggestion {
                    id: format!("s{index}"),
                    ..suggestion.clone()
                })
                .collect(),
            risk_flags: Vec::new(),
            delivery_status: "complete".into(),
            enhancement_level: "light".into(),
            notices: Vec::new(),
        };
        assert!(validate_core_result(&result).is_ok());
        assert!(validate_complete_result(&result, "解释这段代码").is_err());

        result.suggestions = (0..5)
            .map(|index| crate::models::Suggestion {
                id: format!("s{index}"),
                ..suggestion.clone()
            })
            .collect();
        assert!(validate_complete_result(&result, "解释这段代码").is_ok());
    }

    #[test]
    fn truncates_suggestions_over_five() {
        let suggestions = (0..7)
            .map(|index| {
                json!({"id": format!("s{index}"), "kind": "goal", "title": "t", "purpose": "p", "content": "c", "operation": "insert"})
            })
            .collect::<Vec<Value>>();
        let value = json!({
            "status": "ready",
            "task_type": "qa",
            "primary_prompt": "提示词",
            "suggestions": suggestions,
        });
        let (normalized, notices) = normalize_value(value);
        assert_eq!(normalized["suggestions"].as_array().unwrap().len(), 5);
        assert!(notices.iter().any(|notice| notice.contains("前 5 条")));
    }

    #[test]
    fn dedupes_duplicate_ids() {
        let value = json!({
            "status": "ready",
            "task_type": "qa",
            "primary_prompt": "提示词",
            "suggestions": [
                {"id": "s1", "kind": "goal", "title": "t", "purpose": "p", "content": "c", "operation": "insert"},
                {"id": "s2", "kind": "context", "title": "t", "purpose": "p", "content": "c", "operation": "insert"},
                {"id": "s2", "kind": "format", "title": "t", "purpose": "p", "content": "c", "operation": "insert"},
                {"id": "s3", "kind": "goal", "title": "t", "purpose": "p", "content": "c", "operation": "insert"},
            ],
        });
        let (normalized, notices) = normalize_value(value);
        let ids = normalized["suggestions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["id"].as_str().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(ids, vec!["s1", "s2", "s3"]);
        assert!(notices.iter().any(|notice| notice.contains("重复")));
    }

    #[test]
    fn truncates_questions_over_three() {
        let questions = (0..5)
            .map(|index| json!({"id": format!("q{index}"), "text": "问题", "why_needed": "原因"}))
            .collect::<Vec<Value>>();
        let value = json!({
            "status": "needs_clarification",
            "task_type": "qa",
            "primary_prompt": "提示词",
            "questions": questions,
        });
        let (normalized, notices) = normalize_value(value);
        assert_eq!(normalized["questions"].as_array().unwrap().len(), 3);
        assert!(notices.iter().any(|notice| notice.contains("前 3 个")));
    }

    #[test]
    fn fallback_keeps_original_text() {
        let result = fallback_result("  保留原文提示词  ");
        assert_eq!(result.primary_prompt, "保留原文提示词");
        assert_eq!(result.delivery_status, "fallback");
        assert_eq!(result.enhancement_level, "none");
        assert!(
            result
                .notices
                .iter()
                .any(|notice| notice.contains("已保留原始提示词"))
        );
    }

    #[test]
    fn fallback_contains_no_fabricated_suggestions() {
        let result = fallback_result("原始提示词");
        assert!(result.suggestions.is_empty());
        assert!(result.changes.is_empty());
        assert!(result.questions.is_empty());
        assert!(result.assumptions.is_empty());
    }

    #[test]
    fn normalize_derives_status_from_questions() {
        let value = json!({
            "primary_prompt": "提示词",
            "questions": [{"id": "q1", "text": "问题", "why_needed": "原因"}],
        });
        let (normalized, _) = normalize_value(value);
        assert_eq!(normalized["status"], "needs_clarification");

        let value = json!({"primary_prompt": "提示词"});
        let (normalized, _) = normalize_value(value);
        assert_eq!(normalized["status"], "ready");
    }

    #[test]
    fn error_code_mapping_is_stable() {
        assert_eq!(error_code_for_status(401), "AUTH_FAILED");
        assert_eq!(error_code_for_status(403), "AUTH_FAILED");
        assert_eq!(error_code_for_status(402), "BALANCE_INSUFFICIENT");
        assert_eq!(error_code_for_status(400), "MODEL_NOT_FOUND");
        assert_eq!(error_code_for_status(404), "MODEL_NOT_FOUND");
        assert_eq!(error_code_for_status(429), "RATE_LIMITED");
        assert_eq!(error_code_for_status(500), "NETWORK_FAILED");
        assert_eq!(error_code_for_status(999), "NETWORK_FAILED");
    }
}
