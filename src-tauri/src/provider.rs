use crate::{
    models::{BackendEvent, EnhancementRequest, EnhancementResult, UsageRecord},
    prompts::{SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION, verbosity_label},
    security::redact_sensitive,
    AppState,
};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::HashSet, time::Instant};
use tauri::{ipc::Channel, State};
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
    validate_request(&request)?;
    let api_key = state.storage.api_key()?;
    let config = state.storage.provider_config()?;
    if request.model.trim().is_empty() { return Err("所选模型没有可用的 API model_id".into()); }

    let token = CancellationToken::new();
    {
        let mut cancellation = state.cancellation.lock().await;
        if let Some(previous) = cancellation.replace(token.clone()) { previous.cancel(); }
    }
    let started = Instant::now();
    let body = build_body(&request);
    let endpoint = format!("{}/chat/completions", config.base_url.trim_end_matches('/'));
    on_event.send(BackendEvent::Status { data: "requesting".into() }).map_err(|error| error.to_string())?;

    let mut completed = None;
    for attempt in 0..2 {
        if attempt == 1 {
            on_event.send(BackendEvent::Status { data: "retrying_structure".into() }).map_err(|error| error.to_string())?;
        }
        let sent = state.client.post(&endpoint)
            .bearer_auth(&api_key)
            .header("Content-Type", "application/json")
            .json(&body)
            .send().await;
        let response = match sent {
            Ok(candidate) if candidate.status().is_success() => candidate,
            Ok(candidate) => {
                let status = candidate.status();
                let message = candidate.text().await.unwrap_or_default();
                if status.is_server_error() && attempt == 0 { continue; }
                return Err(map_http_error(status.as_u16(), &message));
            }
            Err(error) if attempt == 0 && (error.is_connect() || error.is_timeout()) => continue,
            Err(error) => return Err(format!("网络请求失败，输入内容仍已保留：{error}")),
        };
        let (raw, wire_usage) = match consume_stream(response, &on_event, token.clone()).await {
            Ok(consumed) => consumed,
            Err(_) if attempt == 0 && !token.is_cancelled() => continue,
            Err(error) => return Err(format!("{error}，已自动重试一次")),
        };
        if token.is_cancelled() { return Ok(()); }

        let parsed = parse_result(&raw).and_then(|mut result| {
            if request.clarification_round >= 3 {
                result.questions.clear();
                result.status = "ready".into();
            }
            validate_result(&result)?;
            Ok(result)
        });
        match parsed {
            Ok(result) => {
                completed = Some((result, wire_usage));
                break;
            }
            Err(_) if attempt == 0 => continue,
            Err(error) => return Err(format!("{error}，已自动重试一次")),
        }
    }
    let (result, wire_usage) = completed.ok_or_else(|| "DeepSeek 服务暂时不可用，已自动重试一次".to_string())?;

    let input_tokens = wire_usage.prompt_tokens.max(estimate_tokens(&request.original_text));
    let output_tokens = wire_usage.completion_tokens.max(estimate_tokens(&result.primary_prompt));
    let estimated_cost = calculate_cost(input_tokens, output_tokens, config.input_price, config.output_price);
    let month_total = state.storage.record_usage(
        &request.model, input_tokens, output_tokens, estimated_cost,
        started.elapsed().as_millis() as u64, "success", None,
    )?;
    on_event.send(BackendEvent::Result { result }).map_err(|error| error.to_string())?;
    on_event.send(BackendEvent::Usage { usage: UsageRecord { input_tokens, output_tokens, estimated_cost, month_total } }).map_err(|error| error.to_string())?;
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
            let Some(data) = line.strip_prefix("data:").map(str::trim) else { continue };
            if data == "[DONE]" { continue; }
            let Ok(event) = serde_json::from_str::<Value>(data) else { continue };
            if let Some(delta) = event.pointer("/choices/0/delta/content").and_then(Value::as_str) {
                raw.push_str(delta);
                if let Some(current) = partial_primary_prompt(&raw) {
                    if let Some(suffix) = current.strip_prefix(&preview) {
                        if !suffix.is_empty() {
                            channel.send(BackendEvent::Delta { data: suffix.to_string() }).map_err(|error| error.to_string())?;
                        }
                    }
                    preview = current;
                }
            }
            if let Some(value) = event.get("usage") {
                if let Ok(parsed) = serde_json::from_value::<WireUsage>(value.clone()) { usage = parsed; }
            }
        }
    }
    Ok((raw, usage))
}

fn build_body(request: &EnhancementRequest) -> Value {
    let attachments = request.attachments.iter().map(|attachment| {
        format!("<attachment name=\"{}\">\n{}\n</attachment>", attachment.name, redact_sensitive(&attachment.text))
    }).collect::<Vec<_>>().join("\n\n");
    let answers = request.clarification_answers.iter().map(|answer| {
        format!("{}: {}", answer.question_id, redact_sensitive(&answer.answer))
    }).collect::<Vec<_>>().join("\n");
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

fn parse_result(raw: &str) -> Result<EnhancementResult, String> {
    let trimmed = raw.trim().trim_start_matches("```json").trim_start_matches("```").trim_end_matches("```").trim();
    let start = trimmed.find('{').ok_or_else(|| "模型没有返回结构化结果，请重新生成".to_string())?;
    let end = trimmed.rfind('}').ok_or_else(|| "模型结果未完整结束，请重新生成".to_string())?;
    serde_json::from_str(&trimmed[start..=end]).map_err(|_| "模型返回的结构无法解析，输入内容仍已保留，请重新生成".to_string())
}

fn validate_request(request: &EnhancementRequest) -> Result<(), String> {
    if request.original_text.trim().is_empty() { return Err("原始提示词不能为空".into()); }
    if request.attachments.len() > 5 { return Err("一次最多使用 5 个附件".into()); }
    let total = request.context_text.chars().count() + request.attachments.iter().map(|item| item.text.chars().count()).sum::<usize>();
    if total > 100_000 { return Err("上下文和附件总文本超过 100,000 字符".into()); }
    if request.clarification_round > 3 { return Err("澄清轮次不能超过 3 轮".into()); }
    Ok(())
}

fn validate_result(result: &EnhancementResult) -> Result<(), String> {
    if result.primary_prompt.trim().is_empty() { return Err("模型没有生成主提示词".into()); }
    if result.suggestions.len() != 5 { return Err("模型未返回恰好 5 个可选建议，请重新生成".into()); }
    if result.questions.len() > 3 { return Err("模型返回了超过 3 个澄清问题，请重新生成".into()); }
    if result.primary_prompt.contains("XXX") { return Err("模型结果包含未替换占位符，请重新生成".into()); }
    let unique = result.suggestions.iter().map(|item| item.id.as_str()).collect::<HashSet<_>>();
    if unique.len() != result.suggestions.len() { return Err("模型返回了重复建议，请重新生成".into()); }
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
        if !escaped && character == '"' { break; }
        buffer.push(character);
        if character == '\\' && !escaped { escaped = true; } else { escaped = false; }
    }
    if escaped { buffer.pop(); }
    serde_json::from_str::<String>(&format!("\"{}\"", buffer)).ok()
}

fn estimate_tokens(text: &str) -> u64 {
    ((text.chars().count() as f64) / 1.8).ceil() as u64
}

fn calculate_cost(input_tokens: u64, output_tokens: u64, input_price: f64, output_price: f64) -> f64 {
    input_tokens as f64 / 1000.0 * input_price + output_tokens as f64 / 1000.0 * output_price
}

fn map_http_error(status: u16, body: &str) -> String {
    let detail = serde_json::from_str::<Value>(body).ok()
        .and_then(|value| value.pointer("/error/message").and_then(Value::as_str).map(str::to_string))
        .unwrap_or_else(|| body.chars().take(180).collect());
    match status {
        401 | 403 => format!("API Key 无效或没有模型权限：{detail}"),
        402 => format!("DeepSeek 账户余额不足：{detail}"),
        429 => format!("DeepSeek 请求限流，请稍后重试：{detail}"),
        400 => format!("DeepSeek 拒绝了请求参数，请检查模型 ID：{detail}"),
        _ => format!("DeepSeek 返回错误 {status}：{detail}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_partial_primary_prompt() {
        let raw = r#"{"primary_prompt":"第一行\n第二行","status":"ready"}"#;
        assert_eq!(partial_primary_prompt(raw).as_deref(), Some("第一行\n第二行"));
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
        value.attachments = (0..6).map(|index| crate::models::AttachmentInput {
            name: format!("{index}.txt"),
            text: "x".into(),
        }).collect();
        assert_eq!(validate_request(&value).unwrap_err(), "一次最多使用 5 个附件");
    }

    #[test]
    fn validates_result_shape_and_placeholders() {
        let suggestion = crate::models::Suggestion {
            id: "s".into(), kind: "goal".into(), title: "目标".into(), purpose: "用途".into(),
            content: "内容".into(), operation: "insert".into(), anchor: String::new(), applied: false,
        };
        let mut result = EnhancementResult {
            status: "ready".into(), task_type: "other".into(), primary_prompt: "有效提示词".into(), assumptions: Vec::new(),
            questions: Vec::new(), changes: Vec::new(),
            suggestions: (0..5).map(|index| crate::models::Suggestion { id: format!("s{index}"), ..suggestion.clone() }).collect(),
            risk_flags: Vec::new(),
        };
        assert!(validate_result(&result).is_ok());
        result.primary_prompt = "请解释 XXX".into();
        assert!(validate_result(&result).unwrap_err().contains("占位符"));
    }

    #[test]
    fn calculates_configured_cost() {
        assert!((calculate_cost(1_000, 2_000, 0.001, 0.002) - 0.005).abs() < f64::EPSILON);
    }
}
