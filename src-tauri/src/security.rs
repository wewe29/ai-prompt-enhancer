use once_cell::sync::Lazy;
use regex::Regex;

static API_KEY: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\bsk-[A-Za-z0-9_-]{12,}\b").expect("api key regex"));
static PASSWORD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(password|passwd|密码)(\s*[:=：]\s*)\S+").expect("password regex")
});
static PRIVATE_KEY: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
        .expect("private key regex")
});
static ID_CARD: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\d{17}[0-9Xx]\b").expect("id regex"));

pub fn redact_sensitive(input: &str) -> String {
    let mut output = API_KEY
        .replace_all(input, "[REDACTED_API_KEY_1]")
        .into_owned();
    output = PASSWORD
        .replace_all(&output, "$1$2[REDACTED_PASSWORD_1]")
        .into_owned();
    output = PRIVATE_KEY
        .replace_all(&output, "[REDACTED_PRIVATE_KEY_1]")
        .into_owned();
    output
}

pub fn findings(input: &str) -> Vec<String> {
    let mut result = Vec::new();
    if API_KEY.is_match(input) {
        result.push("credential:api_key".into());
    }
    if PASSWORD.is_match(input) {
        result.push("credential:password".into());
    }
    if PRIVATE_KEY.is_match(input) {
        result.push("credential:private_key".into());
    }
    if ID_CARD.is_match(input) {
        result.push("privacy:id_card".into());
    }
    if input.contains("公司源码") || input.contains("商业机密") || input.contains("未公开")
    {
        result.push("privacy:confidential".into());
    }
    if input.contains("忽略之前") || input.contains("忽略系统") || input.contains("泄露系统提示词")
    {
        result.push("injection:instruction_override".into());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_credentials() {
        let input = "key=sk-abcdefghijklmnop password: secret";
        let redacted = redact_sensitive(input);
        assert!(!redacted.contains("abcdefghijklmnop"));
        assert!(!redacted.contains("secret"));
        assert!(redacted.contains("[REDACTED_API_KEY_1]"));
    }
}
