"""LLM-as-judge：对同一提示词的原始/增强两组回答做四维度打分对比。

四维度：准确性(accuracy)、完整性(completeness)、相关性(relevance)、清晰度(clarity)。
每次调用前随机互换 A/B 标签消除位置偏差，解析后按实际映射还原。
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

import openai

import enhancer  # evaluation/ 目录已在 sys.path 中

DIMENSIONS = ["accuracy", "completeness", "relevance", "clarity"]
DIMENSION_LABELS = {
    "accuracy": "准确性",
    "completeness": "完整性",
    "relevance": "相关性",
    "clarity": "清晰度",
}

JUDGE_SYSTEM_PROMPT = """你是公正的提示词评测裁判。同一任务分别使用"提示词A"和"提示词B"得到回答A与回答B。
请分别对两个回答按四个维度各打 1-10 分（整数）：
- 准确性：事实、数据、代码、措辞是否正确、无幻觉
- 完整性：是否覆盖任务的全部显式要求与隐含需求
- 相关性：是否紧扣任务目标、无离题与冗余
- 清晰度：结构、组织、语言是否易读
要求：先对每个回答独立评分，再综合比较；A/B 的先后顺序不代表质量高低。
只输出一个 JSON 对象，不要输出任何其他内容，格式：
{"a":{"accuracy":1,"completeness":1,"relevance":1,"clarity":1},"b":{"accuracy":1,"completeness":1,"relevance":1,"clarity":1},"winner":"a|b|tie","reason":"一句话理由"}"""


class JudgeError(Exception):
    """裁判调用失败"""


def judge_pair(
    sample: dict[str, Any],
    original_output: str,
    enhanced_output: str,
    cfg: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    """对同一提示词的两组回答进行裁判，返回结构化对比结果。"""
    jcfg = cfg.get("judge", {})
    swapped = bool(jcfg.get("randomize_order", True)) and random.random() < 0.5
    prompt_a, answer_a, prompt_b, answer_b = _assign_labels(
        sample, original_output, enhanced_output, swapped
    )

    user_message = _build_judge_message(sample, prompt_a, answer_a, prompt_b, answer_b)
    body = {
        "model": jcfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": float(jcfg.get("temperature", 0)),
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    client = openai.OpenAI(api_key=api_key, base_url=cfg["enhancer"]["base_url"].rstrip("/"))

    parsed: dict[str, Any] | None = None
    last_error: Exception | None = None
    usage: dict[str, int] | None = None
    for _ in range(2):
        try:
            response = client.chat.completions.create(**body)
            content = response.choices[0].message.content or ""
            parsed = _parse_judge_result(content)
            if getattr(response, "usage", None):
                usage = {
                    "input_tokens": response.usage.prompt_tokens or 0,
                    "output_tokens": response.usage.completion_tokens or 0,
                }
            break
        except (ValueError, openai.APIError) as exc:
            last_error = exc
    if parsed is None:
        raise JudgeError(f"裁判解析失败：{last_error}")

    if swapped:
        enhanced_scores, original_scores = dict(parsed["a"]), dict(parsed["b"])
    else:
        original_scores, enhanced_scores = dict(parsed["a"]), dict(parsed["b"])
    for side in (original_scores, enhanced_scores):
        for dim in DIMENSIONS:
            side[dim] = int(side[dim])
    actual_winner = _remap_winner(parsed.get("winner", "tie"), swapped)

    return {
        "original": original_scores,
        "enhanced": enhanced_scores,
        "deltas": {
            dim: enhanced_scores[dim] - original_scores[dim] for dim in DIMENSIONS
        },
        "winner": actual_winner,
        "reason": str(parsed.get("reason", "")).strip(),
        "swapped": swapped,
        "heuristics": {
            "original": _heuristics(original_output),
            "enhanced": _heuristics(enhanced_output),
        },
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


# ---- 内部工具 ----

def _assign_labels(sample, original_output, enhanced_output, swapped):
    if swapped:
        return (sample.get("enhanced", ""), enhanced_output,
                sample.get("original", ""), original_output)
    return (sample.get("original", ""), original_output,
            sample.get("enhanced", ""), enhanced_output)


def _build_judge_message(sample, prompt_a, answer_a, prompt_b, answer_b) -> str:
    return (
        f"任务：\n{sample.get('original', '')}\n\n"
        f"提示词A：\n{prompt_a}\n\n"
        f"回答A：\n{answer_a}\n\n"
        f"提示词B：\n{prompt_b}\n\n"
        f"回答B：\n{answer_b}\n"
    )


def _parse_judge_result(content: str) -> dict[str, Any]:
    parsed = enhancer.parse_result(content)
    for side in ("a", "b"):
        scores = parsed.get(side)
        if not isinstance(scores, dict):
            raise ValueError(f"裁判结果缺少 {side} 侧评分")
        for dim in DIMENSIONS:
            value = scores.get(dim)
            if not isinstance(value, (int, float)):
                raise ValueError(f"裁判结果 {side}.{dim} 缺失或非数字")
            scores[dim] = int(round(float(value)))
    if parsed.get("winner") not in ("a", "b", "tie"):
        raise ValueError(f"裁判 winner 非法：{parsed.get('winner')}")
    return parsed


def _remap_winner(winner: str, swapped: bool) -> str:
    if winner == "tie":
        return "tie"
    if swapped:
        # swapped 时 a=enhanced, b=original
        return "enhanced" if winner == "a" else "original"
    return "original" if winner == "a" else "enhanced"


def _heuristics(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "code_blocks": len(re.findall(r"```", text)) // 2,
        "list_items": len(re.findall(r"(?m)^\s*[-*•]|\d+[.、]", text)),
        "paragraphs": len([p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]),
    }
