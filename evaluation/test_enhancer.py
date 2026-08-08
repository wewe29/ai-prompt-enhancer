"""enhancer.py 与 Rust 源码的等价性测试。

关键测试会直接读取 src-tauri/src/provider.rs，从源码中提取 SYSTEM_PROMPT
与 SYSTEM_PROMPT_VERSION 原文，与我们 Python 端的常量做逐字比较——
任何复刻偏差都会在这里暴露。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 允许从 evaluation 目录直接运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))

import enhancer  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_RS = PROJECT_ROOT / "src-tauri" / "src" / "provider.rs"


def _extract_rust_string(source: str, const_name: str) -> str:
    """提取 Rust 源码中 `const NAME: &str = ...;` 的字符串内容（兼容普通与 raw 字符串）。"""
    pattern = re.compile(
        rf'const {const_name}: &str = (?:r#"(?P<raw>.*?)"#|"(?P<normal>.*?)")', re.DOTALL
    )
    match = pattern.search(source)
    assert match, f"未在 provider.rs 中找到 {const_name}"
    return match.group("raw") if match.group("raw") is not None else match.group("normal")


def _rust_system_prompt() -> str:
    return _extract_rust_string(PROVIDER_RS.read_text(encoding="utf-8"), "SYSTEM_PROMPT")


def _rust_version() -> str:
    return _extract_rust_string(PROVIDER_RS.read_text(encoding="utf-8"), "SYSTEM_PROMPT_VERSION")


def test_system_prompt_byte_identical():
    assert enhancer.SYSTEM_PROMPT == _rust_system_prompt(), "SYSTEM_PROMPT 与 provider.rs 不一致！"


def test_system_prompt_version_identical():
    assert enhancer.SYSTEM_PROMPT_VERSION == _rust_version(), "SYSTEM_PROMPT_VERSION 不一致！"


def test_build_user_message_matches_rust_template():
    """验证 user message 模板与 build_body 的 format 字符串结构一致（占位符/换行）。"""
    msg = enhancer.build_user_message(
        "解释这段代码",
        target_model="豆包",
        verbosity="standard",
        custom_instructions=None,
        clarification_round=0,
        profile_summary=[],
        context_text="",
        clarification_answers=[],
        attachments=[],
    )
    expected_prefix = (
        f"系统提示词版本：{enhancer.SYSTEM_PROMPT_VERSION}\n"
        "目标模型：豆包\n"
        "详细程度：标准（适度补充）\n"
        "自定义详细要求：无\n"
        "澄清轮次：0/3\n"
        "用户偏好摘要：\n\n\n"
        "原始提示词：\n解释这段代码\n"
        "\n上下文：\n\n\n"
        "本轮澄清回答：\n\n\n"
        "附件参考资料：\n"
    )
    assert msg == expected_prefix, f"user message 模板偏差：\n{msg!r}"


def test_build_user_message_with_attachments_and_answers():
    msg = enhancer.build_user_message(
        "原文",
        target_model="千问",
        verbosity="deep",
        custom_instructions="要简洁",
        clarification_round=2,
        profile_summary=["偏好中文"],
        context_text="背景",
        clarification_answers=[{"question_id": "q1", "answer": "回答1"}],
        attachments=[{"name": "a.txt", "text": "内容"}],
    )
    assert '<attachment name="a.txt">\n内容\n</attachment>' in msg
    assert "q1: 回答1" in msg
    assert "自定义详细要求：要简洁" in msg
    assert "澄清轮次：2/3" in msg
    assert "用户偏好摘要：\n偏好中文" in msg


def test_redact_sensitive_matches_rust_case():
    # 与 security.rs 测试用例 redacts_credentials 等价
    redacted = enhancer.redact_sensitive("key=sk-abcdefghijklmnop password: secret")
    assert "abcdefghijklmnop" not in redacted
    assert "secret" not in redacted
    assert "[REDACTED_API_KEY_1]" in redacted
    assert "[REDACTED_PASSWORD_1]" in redacted


def test_parse_result_strips_fence():
    raw = '```json\n{"primary_prompt":"第一行\\n第二行","status":"ready"}\n```'
    assert enhancer.parse_result(raw)["primary_prompt"] == "第一行\n第二行"


def test_parse_result_with_leading_text():
    raw = '说明文字\n{"primary_prompt":"p","status":"ready"}'
    assert enhancer.parse_result(raw)["primary_prompt"] == "p"


def test_parse_result_rejects_no_json():
    try:
        enhancer.parse_result("完全没有 JSON")
    except ValueError as exc:
        assert "没有返回结构化结果" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_validate_result_ok():
    result = {"primary_prompt": "有效提示词", "suggestions": [
        {"id": f"s{i}"} for i in range(5)
    ], "questions": []}
    enhancer.validate_result(result)  # 不应抛错


def test_validate_result_suggestions_must_be_5():
    result = {"primary_prompt": "p", "suggestions": [{"id": "s1"}], "questions": []}
    try:
        enhancer.validate_result(result)
    except ValueError as exc:
        assert "恰好 5 个" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_validate_result_placeholder():
    result = {"primary_prompt": "请解释 XXX", "suggestions": [{"id": f"s{i}"} for i in range(5)], "questions": []}
    try:
        enhancer.validate_result(result)
    except ValueError as exc:
        assert "占位符" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_validate_result_questions_limit():
    result = {"primary_prompt": "p", "suggestions": [{"id": f"s{i}"} for i in range(5)],
              "questions": [{"id": f"q{i}"} for i in range(4)]}
    try:
        enhancer.validate_result(result)
    except ValueError as exc:
        assert "超过 3 个澄清问题" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_validate_result_duplicate_ids():
    result = {"primary_prompt": "p", "suggestions": [{"id": "dup"} for _ in range(5)], "questions": []}
    try:
        enhancer.validate_result(result)
    except ValueError as exc:
        assert "重复建议" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_calculate_cost_matches_rust():
    # 与 provider.rs tests::calculates_configured_cost 等价
    assert abs(enhancer.calculate_cost(1_000, 2_000, 0.001, 0.002) - 0.005) < 1e-9


def test_estimate_tokens_rounding():
    # 与 Rust: ((chars as f64) / 1.8).ceil()
    assert enhancer.estimate_tokens("abcde") == 3  # 5 / 1.8 ≈ 2.78 → ceil 3
    assert enhancer.estimate_tokens("ab") == 2     # 2 / 1.8 ≈ 1.11 → ceil 2
