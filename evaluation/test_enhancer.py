"""enhancer.py 与 Rust 源码的等价性测试。

关键测试会直接读取 src-tauri/src/prompts.rs，从源码中提取 SYSTEM_PROMPT
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
PROMPTS_RS = PROJECT_ROOT / "src-tauri" / "src" / "prompts.rs"


def _extract_rust_string(source: str, const_name: str) -> str:
    """提取 Rust 源码中 `const NAME: &str = ...;` 的字符串内容（兼容普通与 raw 字符串）。"""
    pattern = re.compile(
        rf'const {const_name}: &str = (?:r#"(?P<raw>.*?)"#|"(?P<normal>.*?)")', re.DOTALL
    )
    match = pattern.search(source)
    assert match, f"未在 prompts.rs 中找到 {const_name}"
    return match.group("raw") if match.group("raw") is not None else match.group("normal")


def _rust_system_prompt() -> str:
    return _extract_rust_string(PROMPTS_RS.read_text(encoding="utf-8"), "SYSTEM_PROMPT")


def _rust_version() -> str:
    return _extract_rust_string(PROMPTS_RS.read_text(encoding="utf-8"), "SYSTEM_PROMPT_VERSION")


def test_system_prompt_byte_identical():
    assert enhancer.SYSTEM_PROMPT == _rust_system_prompt(), "SYSTEM_PROMPT 与 prompts.rs 不一致！"


def test_system_prompt_version_identical():
    assert enhancer.SYSTEM_PROMPT_VERSION == _rust_version(), "SYSTEM_PROMPT_VERSION 不一致！"


def _rust_user_message_template() -> str:
    """从 src-tauri/src/provider.rs 取出 user message 的 format 模板。

    F2：这条断言原先比的是**测试自己写死的一份拷贝** ——
    改 Rust 模板它不会红，Python 侧漂移它也不会红。
    真正的做法是**读对方源码**：把 Rust 的 format 串取出来，
    用同一组值渲染，与 Python 侧逐字比对。
    """
    source = (PROMPTS_RS.parent / "provider.rs").read_text(encoding="utf-8")
    marker = "系统提示词版本：{SYSTEM_PROMPT_VERSION}"
    index = source.find(marker)
    assert index >= 0, "provider.rs 里找不到 user message 模板"
    # 取**整行**（模板是单行字面量），再取首尾两个引号之间的内容
    line_start = source.rfind("\n", 0, index) + 1
    line_end = source.find("\n", index)
    line = source[line_start:line_end]
    start = line.find('"')
    end = line.rfind('"')
    assert start >= 0 and end > start, f"provider.rs 的模板不是字符串字面量：{line!r}"
    template = line[start + 1 : end]
    # Rust 的具名占位符 {SYSTEM_PROMPT_VERSION} 换成位置占位符，
    # 让它与 Python 侧的 format 串可直接比对。
    return template.replace("{SYSTEM_PROMPT_VERSION}", "{}").replace("\\n", "\n")


def test_build_user_message_matches_rust_template():
    """user message 模板必须与 Rust 侧**逐字一致**（读 provider.rs 原文比对）。"""
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
    template = _rust_user_message_template()
    rendered = template.format(
        enhancer.SYSTEM_PROMPT_VERSION,  # 系统提示词版本
        "豆包",                      # target_model
        "标准（适度补充）",            # verbosity_label
        "无",                        # custom_instructions 默认值
        0,                           # clarification_round
        "",                          # profile_summary.join("\n")
        "解释这段代码",                # original_text
        "",                          # context_text
        "",                          # answers_text
        "",                          # attachments_text
    )
    assert msg == rendered, f"user message 模板与 provider.rs 不一致：\nPython: {msg!r}\nRust:   {rendered!r}"


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


def test_validate_result_suggestions_0_to_5():
    for count in range(0, 6):
        result = {"primary_prompt": "p", "suggestions": [{"id": f"s{i}"} for i in range(count)], "questions": []}
        enhancer.validate_result(result)  # 0-5 条均不应抛错

    result = {"primary_prompt": "p", "suggestions": [{"id": f"s{i}"} for i in range(6)], "questions": []}
    try:
        enhancer.validate_result(result)
    except ValueError as exc:
        assert "超过 5 条" in str(exc)
    else:
        raise AssertionError("应当抛出异常")


def test_normalize_result_does_not_invent_missing_verdicts():
    """B2：缺失的档位与交付状态**不得**被补成默认值。

    此前 Python 侧把缺失的 `enhancement_level` 补成 "light"、缺失的
    `delivery_status` 补成 "complete" —— 模型没给判断，系统替他断言
    「轻度增强」。Rust 侧已改为 `Option`（缺失即 None），**两层必须一致**。
    """
    ready = enhancer.normalize_result({"primary_prompt": "p"})
    assert ready.get("enhancement_level") is None, "缺失的档位不得被补成 light"
    assert ready.get("delivery_status") is None, "缺失的交付状态不得被补成 complete"
    assert ready["status"] == "ready"
    assert ready["questions"] == [], "数组字段仍然补空"

    preserved = enhancer.normalize_result(
        {"primary_prompt": "p", "enhancement_level": "none", "delivery_status": "partial"}
    )
    assert preserved["enhancement_level"] == "none"
    assert preserved["delivery_status"] == "partial"


def test_normalize_result_matches_rust_option_semantics():
    """F1 / R4：Python 复刻品必须与 Rust 的 Option 语义一致 —— 读 Rust 源码断言。

    这条测试**读 `src-tauri/src/models.rs` 原文**，不是断言自己这一侧的
    硬编码期望值：Rust 侧若把字段改回 `String` + serde default，
    这里必须红。
    """
    rust = (Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "models.rs").read_text(
        encoding="utf-8"
    )
    for field in ("delivery_status", "enhancement_level"):
        assert f"pub {field}: Option<String>" in rust, (
            f"Rust 侧 {field} 应为 Option<String>（缺失即 None）；"
            f"若已改回带 serde default 的 String，Python 侧不得再补默认值"
        )
        assert f"default_delivery_status" not in rust or field != "delivery_status", (
            "Rust 侧不应再有把缺失交付状态补成 complete 的 default 函数"
        )
    assert "fn default_enhancement_level" not in rust, (
        "Rust 侧不应再有把缺失档位补成 light 的 default 函数"
    )


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
