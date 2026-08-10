"""模型横向评测报告：聚合计算 + report_benchmark.md 生成。

payload 结构（与 summary.json 一致）：
  {"meta": {...},
   "models": [每模型聚合结果（排名/维度均分/延迟/输出长度/胜率/综合分）],
   "details": [每(模型×提示词)详情（scores/latency_s/output_len/error）]}

aggregate() 为纯函数，供 model_benchmark.py 与测试复用；
generate() 在 payload 缺 "models" 时自动先聚合再渲染。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from bench_judge import DIMENSIONS, DIMENSION_LABELS  # evaluation/ 目录已在 sys.path 中

REPORT_NAME = "report_benchmark.md"

DEFAULT_WEIGHTS = {"quality": 0.6, "relevance": 0.2, "speed": 0.2}
LATENCY_CAP_S = 120.0
QUALITY_DIMS = ("accuracy", "completeness", "clarity")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：字符数 / 1.8（中英混合经验值，向上取整）。"""
    return math.ceil(len(text) / 1.8)


def compute_composite(
    scores: dict[str, Any],
    latency_s: float | None,
    weights: dict[str, float] | None = None,
) -> float:
    """综合分 = 0.6×质量均分 + 0.2×相关性 + 0.2×速度分；速度分 = 1 - min(latency,120)/120。"""
    if not scores:
        return 0.0
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    quality = sum(scores.get(d, 0) for d in QUALITY_DIMS) / len(QUALITY_DIMS)
    relevance = scores.get("relevance", 0)
    speed = 1.0 - min(float(latency_s or LATENCY_CAP_S), LATENCY_CAP_S) / LATENCY_CAP_S
    return w["quality"] * quality + w["relevance"] * relevance + w["speed"] * speed


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _cost_block(payload: dict[str, Any]) -> list[str]:
    """原始 prompt vs 增强 prompt 的 token 成本对比块（按提示词平均）。

    meta.prompts 条目含 len（原始字符数）与 enhanced_len（增强字符数，可选）时生效。
    """
    prompts = payload.get("meta", {}).get("prompts") or []
    orig_lens: list[float] = []
    enh_lens: list[float] = []
    for p in prompts:
        o, e = p.get("len"), p.get("enhanced_len")
        if isinstance(o, (int, float)) and o > 0:
            orig_lens.append(float(o))
        if isinstance(e, (int, float)) and e > 0:
            enh_lens.append(float(e))
    if not orig_lens and not enh_lens:
        return []
    lines = ["### 成本与 token（原始 prompt vs 增强 prompt）\n"]
    if orig_lens:
        orig_mean = sum(orig_lens) / len(orig_lens)
        lines.append(f"- 平均原始 prompt 长度：**{orig_mean:.1f}** 字符（估算 {math.ceil(orig_mean / 1.8)} token）")
    if enh_lens:
        enh_mean = sum(enh_lens) / len(enh_lens)
        lines.append(f"- 平均增强 prompt 长度：**{enh_mean:.1f}** 字符（估算 {math.ceil(enh_mean / 1.8)} token）")
    if orig_lens and enh_lens:
        ratio = (sum(enh_lens) / len(enh_lens)) / (sum(orig_lens) / len(orig_lens))
        lines.append(f"- 增强膨胀比：**×{ratio:.2f}**（增强长度 / 原始长度）")
    lines.append("")
    return lines


def aggregate(
    payload: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """按模型聚合：四维度均分、平均延迟、平均输出长度、胜率、综合分，按综合分降序排名。

    details 中的每条会补充 composite（该条综合分）与 winner（该提示词上是否严格最高）。
    有 error 或无 scores 的条目不参与评分聚合。
    """
    per_model: dict[str, dict[str, Any]] = {}
    per_prompt_composite: dict[str, dict[str, float]] = {}

    for item in payload.get("details", []):
        mid = item.get("model_id", "")
        if item.get("error") or not item.get("scores"):
            per_model.setdefault(mid, {})
            continue
        bucket = per_model.setdefault(mid, {})
        scores = item["scores"]
        composite = compute_composite(scores, item.get("latency_s"), weights)
        for dim in DIMENSIONS:
            bucket.setdefault("dims", {}).setdefault(dim, []).append(float(scores.get(dim, 0)))
        bucket.setdefault("composites", []).append(composite)
        if item.get("latency_s") is not None:
            bucket.setdefault("latencies", []).append(float(item["latency_s"]))
        if item.get("output_len") is not None:
            bucket.setdefault("output_lens", []).append(int(item["output_len"]))
        per_prompt_composite.setdefault(item.get("prompt_id", ""), {})[mid] = composite

    prompt_winners: dict[str, str] = {}
    for pid, comps in per_prompt_composite.items():
        if not comps:
            continue
        best = max(comps.values())
        winners = [mid for mid, c in comps.items() if c == best]
        if len(winners) == 1:  # 严格最高：并列时无人获胜
            prompt_winners[pid] = winners[0]

    models: list[dict[str, Any]] = []
    label_map = {
        m.get("id", ""): m.get("label", m.get("id", ""))
        for m in (payload.get("meta", {}).get("models") or [])
    }
    for mid, bucket in per_model.items():
        dims = bucket.get("dims", {})
        quality_mean = _mean(
            [
                (sum(dims[d][i] for d in QUALITY_DIMS) / len(QUALITY_DIMS))
                for i in range(len(dims.get("accuracy", [])))
            ]
        )
        judged = len(bucket.get("composites", []))
        wins = sum(1 for pid, wmid in prompt_winners.items() if wmid == mid)
        models.append({
            "id": mid,
            "label": label_map.get(mid, mid),
            "dims": {d: round(_mean(dims.get(d, [])), 2) for d in DIMENSIONS},
            "quality_mean": round(quality_mean, 2),
            "latency_mean": round(_mean(bucket.get("latencies", [])), 2),
            "output_len_mean": round(_mean(bucket.get("output_lens", [])), 1),
            "win_rate": round(wins / judged, 3) if judged else 0.0,
            "wins": wins,
            "judged_prompts": judged,
            "composite": round(_mean(bucket.get("composites", [])), 3),
        })

    models.sort(key=lambda m: (m["composite"], m["id"]), reverse=True)
    prev_composite: float | None = None
    for idx, m in enumerate(models):
        m["rank"] = idx + 1 if m["composite"] != prev_composite else models[idx - 1]["rank"]
        prev_composite = m["composite"]

    details: list[dict[str, Any]] = []
    for item in payload.get("details", []):
        entry = dict(item)
        mid = item.get("model_id", "")
        if not item.get("error") and item.get("scores"):
            entry["composite"] = round(
                compute_composite(item["scores"], item.get("latency_s"), weights), 3
            )
            entry["winner"] = prompt_winners.get(item.get("prompt_id", "")) == mid
        else:
            entry["composite"] = None
            entry["winner"] = False
        details.append(entry)

    return {"models": models, "details": details}


def generate(out_dir: Path, payload: dict[str, Any]) -> Path:
    """生成 report_benchmark.md，返回报告文件路径。"""
    if "models" not in payload:
        payload = {**payload, **aggregate(payload)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_NAME
    path.write_text(_render_markdown(payload), encoding="utf-8")
    return path


def _render_markdown(payload: dict[str, Any]) -> str:
    meta = payload.get("meta", {})
    models = payload.get("models", [])
    details = payload.get("details", [])
    lines: list[str] = []

    lines.append("# 模型横向评测报告\n")
    lines.append(f"- 生成时间：{meta.get('generated_at', '')}")
    lines.append(f"- 参评模型：{len(models)} 个 | 提示词数：{meta.get('prompt_count', 0)}")
    labels = ", ".join(m.get("label", m.get("id", "")) for m in models) or "无"
    lines.append(f"- 模型列表：{labels}")
    lines.append(f"- 增强模型：{meta.get('enhancer_model', '')} | 裁判模型：{meta.get('judge_model', '')}")
    lines.append(f"- 接口协议：{meta.get('protocol', '')} | {meta.get('base_url', '')}\n")

    lines.append("## 一、总览表\n")
    lines.append("| 排名 | 模型 | 质量均分 | 相关性 | 延迟(s) | 综合分 |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    for m in models:
        lines.append(
            f"| {m['rank']} | {m.get('label', m['id'])} | {m['quality_mean']:.2f} | "
            f"{m['dims'].get('relevance', 0):.2f} | {m['latency_mean']:.2f} | {m['composite']:.3f} |"
        )
    lines.append("")

    lines.extend(_cost_block(payload))

    lines.append("## 二、按提示词详情\n")
    by_prompt: dict[str, list[dict[str, Any]]] = {}
    for item in details:
        by_prompt.setdefault(item.get("prompt_id", ""), []).append(item)
    if not by_prompt:
        lines.append("> 无有效评测条目。\n")
    for pid, items in by_prompt.items():
        lines.append(f"### {pid}\n")
        lines.append("| 模型 | 准确性 | 完整性 | 相关性 | 清晰度 | 延迟(s) | 综合分 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for item in items:
            label = item.get("model_label", item.get("model_id", ""))
            if item.get("error") or not item.get("scores"):
                lines.append(f"| {label} | 失败 | - | - | - | - | - |")
                continue
            scores = item["scores"]
            mark = " ★" if item.get("winner") else ""
            lines.append(
                f"| {label}{mark} | {scores.get('accuracy', 0)} | {scores.get('completeness', 0)} | "
                f"{scores.get('relevance', 0)} | {scores.get('clarity', 0)} | "
                f"{item.get('latency_s', 0):.2f} | {item.get('composite', 0):.3f} |"
            )
        lines.append("")

    lines.append("## 三、逐模型小结\n")
    for m in models:
        dims = m.get("dims", {})
        lines.append(f"### {m.get('label', m['id'])}\n")
        ranked_dims = sorted(dims.items(), key=lambda kv: kv[1], reverse=True)
        strong = "、".join(f"{DIMENSION_LABELS[d]}（{v:.2f}）" for d, v in ranked_dims[:2] if v)
        weak = "、".join(f"{DIMENSION_LABELS[d]}（{v:.2f}）" for d, v in ranked_dims[-2:] if v)
        lines.append(f"- 排名第 {m['rank']} | 综合分 {m['composite']:.3f} | 胜率 {m['win_rate'] * 100:.1f}%"
                     f"（{m.get('wins', 0)}/{m.get('judged_prompts', 0)}）")
        lines.append(f"- 优势维度：{strong or '无'}")
        lines.append(f"- 短板维度：{weak or '无'}")
        lines.append(f"- 平均延迟：{m['latency_mean']:.2f}s | 平均输出长度：{m['output_len_mean']:.0f} 字符")
        reasons = [
            item.get("reason")
            for item in details
            if item.get("model_id") == m["id"] and not item.get("error") and item.get("reason")
        ]
        if reasons:
            lines.append("- 裁判评语摘录：")
            for reason in reasons[:3]:
                lines.append(f"  - {reason}")
        lines.append("")

    failures = [item for item in details if item.get("error")]
    lines.append("## 四、失败清单\n")
    if not failures:
        lines.append("> 无失败条目。\n")
    for item in failures:
        lines.append(
            f"- [{item.get('prompt_id', '')} × {item.get('model_id', '')}] {item.get('error', '未知错误')}"
        )
    lines.append("")
    lines.append("---\n")
    lines.append("*本报告由 evaluation/model_benchmark.py 自动生成。详细数据见 summary.json。*")
    return "\n".join(lines)
