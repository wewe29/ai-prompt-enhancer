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

PROMPT_JUDGE_SYSTEM_PROMPT = """你是提示词质量评测裁判。请对"增强后的提示词"本身进行评分(不是回答),四个维度各 1-10 分(整数):
- structure(结构清晰度): 是否明确分层了角色/任务/约束/输出格式
- constraint_retention(约束保留率): 原始提示词的约束是否被完整保留,遗漏则扣分
- info_gain(信息增益): 是否补充了合理的隐含需求;编造原文没有的内容则扣分
- over_enhancement(过度增强): 是否添加了原始没有的约束或偏见(高分=越过度)
只输出一个 JSON 对象,不要输出任何其他内容,格式:
{"structure":1,"constraint_retention":1,"info_gain":1,"over_enhancement":1,"reason":"一句话评价"}"""

PROMPT_JUDGE_DIMS = ["structure", "constraint_retention", "info_gain", "over_enhancement"]

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
    judge_model: str | None = None,
) -> dict[str, Any]:
    """对同一提示词的两组回答进行裁判，返回结构化对比结果。

    judge_model 可指定与 cfg["judge"]["model"] 不同的模型（第二裁判交叉验证时使用）。
    """
    jcfg = cfg.get("judge", {})
    model = judge_model or jcfg.get("model", "deepseek-chat")
    swapped = bool(jcfg.get("randomize_order", True)) and random.random() < 0.5
    prompt_a, answer_a, prompt_b, answer_b = _assign_labels(
        sample, original_output, enhanced_output, swapped
    )

    user_message = _build_judge_message(sample, prompt_a, answer_a, prompt_b, answer_b)
    body = {
        "model": model,
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


def judge_agreement(a: dict, b: dict) -> dict:
    """a/b 为两次 judge_pair 结果；返回 winner 一致性与四维 Spearman 相关。

    单条对比在每个维度上只有一组 delta 值，无法按维度单独求相关，
    因此对两个裁判的四维 delta 向量整体计算 Spearman ρ（四维相关），
    并以同一 ρ 填入各维度键下，便于报告端按维度聚合均值。
    """
    winner_agreement = bool(a.get("winner") == b.get("winner"))
    x = [float(a["deltas"][dim]) for dim in DIMENSIONS]
    y = [float(b["deltas"][dim]) for dim in DIMENSIONS]
    rho = _spearman(x, y)
    return {"winner_agreement": winner_agreement, "dim_corr": {dim: rho for dim in DIMENSIONS}}


def judge_prompt_level(
    original: str,
    enhanced: str,
    cfg: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    """对增强后的 prompt 打分；返回四指标 + reason，或 {"error": ...}。"""
    jcfg = cfg.get("judge", {})
    body = {
        "model": jcfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": PROMPT_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"原始提示词：\n{original}\n\n增强后的提示词：\n{enhanced}\n",
            },
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
            parsed = _parse_prompt_judge_result(content)
            if getattr(response, "usage", None):
                usage = {
                    "input_tokens": response.usage.prompt_tokens or 0,
                    "output_tokens": response.usage.completion_tokens or 0,
                }
            break
        except (ValueError, openai.APIError) as exc:
            last_error = exc
    if parsed is None:
        return {"error": f"提示词裁判解析失败：{last_error}"}

    result: dict[str, Any] = {dim: parsed[dim] for dim in PROMPT_JUDGE_DIMS}
    result["reason"] = str(parsed.get("reason", "")).strip()
    result["usage"] = usage or {"input_tokens": 0, "output_tokens": 0}
    return result


# ---- 内部工具 ----

def _rank(values: list[float]) -> list[float]:
    """平均秩：并列值共享名次的平均值。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    denom = (sxx * syy) ** 0.5
    if not denom:
        return 0.0
    return max(-1.0, min(1.0, sxy / denom))


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman 秩相关：平均秩处理并列后对秩做 Pearson 相关（无 scipy 依赖）。"""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    return _pearson(_rank(x), _rank(y))


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


def _parse_prompt_judge_result(content: str) -> dict[str, Any]:
    parsed = enhancer.parse_result(content)
    for dim in PROMPT_JUDGE_DIMS:
        value = parsed.get(dim)
        if not isinstance(value, (int, float)):
            raise ValueError(f"提示词裁判结果缺少 {dim} 或非数字")
        score = int(round(float(value)))
        if not 1 <= score <= 10:
            raise ValueError(f"提示词裁判 {dim} 越界：{value}")
        parsed[dim] = score
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
