"""LLM 裁判：对单个回答按五维度 1-10 打分。

与 judge.py（双回答对比裁判）不同，本模块只评判一个回答；
解析失败时内部重试一次，仍失败返回 {"error": ...}。
"""
from __future__ import annotations

from typing import Any

import bench_api  # evaluation/ 目录已在 sys.path 中
import enhancer  # 复用 parse_result（trim + 剥 ```json 围栏 + 截取 JSON 区间）

DIMENSIONS = ["accuracy", "completeness", "relevance", "clarity", "novelty"]
DIMENSION_LABELS = {
    "accuracy": "准确性",
    "completeness": "完整性",
    "relevance": "相关性",
    "clarity": "清晰度",
    "novelty": "新颖度",
}

JUDGE_SYSTEM_PROMPT = """你是公正的模型评测裁判。给定一个任务提示词及其回答，请对回答从五个维度各打 1-10 分（整数）：
- 准确性(accuracy)：事实、数据、代码、措辞是否正确、无幻觉
- 完整性(completeness)：是否覆盖任务的全部显式要求与隐含需求
- 相关性(relevance)：是否紧扣任务目标、无离题与冗余
- 清晰度(clarity)：结构、组织、语言是否易读
- novelty(新颖度)：是否有新意、不落俗套(创意类任务尤其重要;结构化任务给 5 分为中性)
要求：先独立评分再给出结论。只输出一个 JSON 对象，不要输出任何其他内容，格式：
{"accuracy":1,"completeness":1,"relevance":1,"clarity":1,"novelty":1,"reason":"一句话理由"}"""


def judge_answer(
    task: str,
    prompt: str,
    answer: str,
    api_cfg: dict[str, Any],
    judge_cfg: dict[str, Any],
    api_key: str,
    timeout_s: float = 120,
) -> dict[str, Any]:
    """对单个回答五维度打分，返回 {accuracy, completeness, relevance, clarity, novelty, reason}。

    解析失败内部重试一次；仍失败返回 {"error": ...}。
    API 层异常（RateLimited/Timeout/HttpError/NetworkError）向上抛出，由
    call_with_retry 处理重试。
    """
    jcfg = judge_cfg or {}
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"任务：\n{task}\n\n提示词：\n{prompt}\n\n回答：\n{answer}",
        },
    ]
    model = jcfg.get("model") or ""
    temperature = float(jcfg.get("temperature", 0))
    max_tokens = int(jcfg.get("max_tokens", 2048))

    last_error: BaseException | None = None
    for _attempt in range(2):
        try:
            result = bench_api.call_chat(
                base_url=api_cfg.get("base_url", ""),
                api_key=api_key,
                protocol=api_cfg.get("protocol", "anthropic"),
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=float(timeout_s),
            )
            if result.error:
                raise ValueError(result.error)
            return _parse_scores(result.text)
        except (ValueError, TypeError, KeyError) as exc:
            last_error = exc
    return {"error": f"裁判输出解析失败：{last_error}"}


def _parse_scores(text: str) -> dict[str, Any]:
    """解析裁判 JSON：五个维度必须是 1-10 的整数，reason 缺失时置空。

    novelty 缺失（旧缓存/模型未输出）时回退取 relevance，保证旧数据兼容。
    """
    parsed = enhancer.parse_result(text)
    scores: dict[str, Any] = {}
    for dim in DIMENSIONS:
        value = parsed.get(dim)
        if not isinstance(value, (int, float)) and dim == "novelty":
            value = parsed.get("relevance")
        if not isinstance(value, (int, float)):
            raise ValueError(f"裁判结果缺少 {dim} 或非数字")
        scores[dim] = int(round(float(value)))
        if not 1 <= scores[dim] <= 10:
            raise ValueError(f"裁判结果 {dim}={scores[dim]} 超出 1-10 范围")
    scores["reason"] = str(parsed.get("reason", "")).strip()
    return scores
