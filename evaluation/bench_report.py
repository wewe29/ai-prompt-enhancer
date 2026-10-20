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

DEFAULT_WEIGHTS = {"quality": 0.7, "relevance": 0.2, "speed": 0.1}
LATENCY_CAP_S = 120.0
QUALITY_DIMS = ("accuracy", "completeness", "clarity")
COMPOSITE_DIMS = ("accuracy", "completeness", "relevance", "clarity", "novelty")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：字符数 / 1.8（中英混合经验值，向上取整）。"""
    return math.ceil(len(text) / 1.8)


def composite_gaps(
    scores: dict[str, Any] | None,
    latency_s: float | None,
    weights: dict[str, float] | None = None,
    scenario: str = "",
    scenario_weights: dict[str, dict[str, float]] | None = None,
) -> list[str]:
    """返回导致 composite **不可评**的缺失项（空列表 = 可评）。

    清单 C2 / C3：任何参与排序键的指标**不得由别的指标顶替**，也不得用 0 顶替。
    缺哪一项，这里就点名哪一项 —— 排除原因必须落在产物里（R2）。
    """
    if not scores:
        return ["缺少全部维度打分"]
    gaps: list[str] = []
    sw = (scenario_weights or {}).get(scenario)
    if sw:
        wmap = {
            d: float(v)
            for d, v in sw.items()
            if isinstance(v, (int, float)) and float(v) != 0
        }
        if sum(wmap.values()) > 0:
            for dim in COMPOSITE_DIMS:
                if wmap.get(dim, 0.0) == 0:
                    continue
                if scores.get(dim) is None:
                    gaps.append(f"缺少参与加权的维度 {dim}")
            return gaps
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    if w.get("quality"):
        for dim in QUALITY_DIMS:
            if scores.get(dim) is None:
                gaps.append(f"缺少质量维度 {dim}")
    if w.get("relevance") and scores.get("relevance") is None:
        gaps.append("缺少相关性维度 relevance")
    if w.get("speed") and latency_s is None:
        gaps.append("缺少延迟 latency_s")
    return gaps


def compute_composite(
    scores: dict[str, Any],
    latency_s: float | None,
    weights: dict[str, float] | None = None,
    scenario: str = "",
    scenario_weights: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """综合分计算。**缺数据 ⇒ None（不可评），绝不回落成 0.0**（清单 C2 / C3）。

    场景权重命中（scenario_weights 含该场景且权重和为正值）时：
      composite = Σ (w_i / Σw) × dim_i，dim 取 accuracy/completeness/relevance/clarity/novelty。
      参与加权的维度缺失 ⇒ composite = None（该条目不可评）。
      **不再用 relevance 顶替缺失的 novelty** —— 此前"新颖性"实际测的是"相关性"，
      而且这个顶替发生在**聚合侧**，而 composite 正是模型排名的排序键。
    未命中时：
      composite = q_w×质量均分(准确/完整/清晰) + r_w×相关性 + s_w×速度分；
      速度分 = 1 - min(latency,120)/120。缺任何一项 ⇒ None。
    """
    if composite_gaps(scores, latency_s, weights, scenario, scenario_weights):
        return None
    sw = (scenario_weights or {}).get(scenario)
    if sw:
        wmap = {
            d: float(v)
            for d, v in sw.items()
            if isinstance(v, (int, float)) and float(v) != 0
        }
        total_w = sum(wmap.values())
        if total_w > 0:
            total = 0.0
            for dim in COMPOSITE_DIMS:
                w = wmap.get(dim, 0.0)
                if w == 0:
                    continue
                total += (w / total_w) * float(scores[dim])
            return total
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    quality = sum(scores[d] for d in QUALITY_DIMS) / len(QUALITY_DIMS)
    relevance = float(scores["relevance"])
    speed = 1.0 - min(float(latency_s), LATENCY_CAP_S) / LATENCY_CAP_S
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
    scenario_weights: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """按模型聚合：各维度均分、平均延迟、平均输出长度、胜率、综合分，按综合分降序排名。

    weights 为 quality/relevance/speed 回退权重（缺省 0.7/0.2/0.1）；
    scenario_weights 为 {场景: {维度: 权重}}（未传时从 payload.meta.scenario_weights 读取），
    命中场景的 (模型×提示词) 条目改用纯维度加权综合分。
    场景来源：detail.scenario 优先，其次 meta.prompts[id].scenario。
    缺失的维度（如旧缓存的 novelty）不参与均分，只按条目实际含有的维度收集。

    details 中的每条会补充 composite（该条综合分）与 winner（该提示词上是否严格最高）。
    有 error 或无 scores 的条目不参与评分聚合。
    """
    if scenario_weights is None:
        scenario_weights = payload.get("meta", {}).get("scenario_weights") or {}
    prompt_scenarios = {
        str(p.get("id")): str(p.get("scenario") or "")
        for p in (payload.get("meta", {}).get("prompts") or [])
    }

    def scenario_of(item: dict[str, Any]) -> str:
        return str(item.get("scenario") or prompt_scenarios.get(item.get("prompt_id", ""), ""))

    per_model: dict[str, dict[str, Any]] = {}
    per_prompt_composite: dict[str, dict[str, float]] = {}

    for item in payload.get("details", []):
        mid = item.get("model_id", "")
        if item.get("error") or not item.get("scores"):
            per_model.setdefault(mid, {})
            continue
        bucket = per_model.setdefault(mid, {})
        scores = item["scores"]
        gaps = composite_gaps(
            scores, item.get("latency_s"), weights, scenario_of(item), scenario_weights
        )
        composite = (
            None
            if gaps
            else compute_composite(
                scores, item.get("latency_s"), weights, scenario_of(item), scenario_weights
            )
        )
        bucket["judged"] = bucket.get("judged", 0) + 1
        for dim in DIMENSIONS:
            if dim in scores:
                bucket.setdefault("dims", {}).setdefault(dim, []).append(float(scores[dim]))
        if all(scores.get(d) is not None for d in QUALITY_DIMS):
            bucket.setdefault("qualities", []).append(
                sum(scores[d] for d in QUALITY_DIMS) / len(QUALITY_DIMS)
            )
        else:
            # 质量均分只收"三维齐全"的条目；缺维度的条目单独计数（R2）
            bucket.setdefault("quality_missing", []).append(item.get("prompt_id", ""))
        if composite is None:
            # C2：不可评的条目**不进** composites，也不参与该提示词的胜者判定。
            # 排除原因必须留痕（gaps），否则读者只看到"这个模型没上榜"。
            bucket.setdefault("unevaluable", []).append({
                "prompt_id": item.get("prompt_id", ""),
                "gaps": gaps,
            })
        else:
            bucket.setdefault("composites", []).append(composite)
            per_prompt_composite.setdefault(item.get("prompt_id", ""), {})[mid] = composite
        if item.get("latency_s") is not None:
            bucket.setdefault("latencies", []).append(float(item["latency_s"]))
        if item.get("output_len") is not None:
            bucket.setdefault("output_lens", []).append(int(item["output_len"]))

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
        judged = bucket.get("judged", 0)
        composites = bucket.get("composites", [])
        unevaluable = bucket.get("unevaluable", [])
        wins = sum(1 for pid, wmid in prompt_winners.items() if wmid == mid)
        # C2：任一条目不可评 ⇒ 该模型**不可评**：综合分是排名的排序键，
        # 用"剩下的条目"算出来的综合分会让缺失的条目凭空消失（与 A6 同一个病）。
        model_composite = round(_mean(composites), 3) if composites and not unevaluable else None
        models.append({
            "id": mid,
            "label": label_map.get(mid, mid),
            "dims": {d: round(_mean(dims.get(d, [])), 2) for d in DIMENSIONS if dims.get(d)},
            "quality_mean": round(_mean(bucket.get("qualities", [])), 2),
            "latency_mean": round(_mean(bucket.get("latencies", [])), 2),
            "output_len_mean": round(_mean(bucket.get("output_lens", [])), 1),
            "win_rate": round(wins / judged, 3) if judged else None,
            "wins": wins,
            "judged_prompts": judged,
            "evaluable_prompts": len(composites),
            "unevaluable_prompts": len(unevaluable),
            "unevaluable_gaps": sorted({g for u in unevaluable for g in u["gaps"]}),
            "quality_missing_prompts": len(bucket.get("quality_missing", [])),
            "composite": model_composite,
            "evaluable": model_composite is not None,
        })

    # 排名只给**可评**的模型；不可评的排在后面、rank=None（清单 C2）。
    # 绝不把不可评的模型按 0 分塞进排名 —— 那会让"没测到"看起来像"排最后"。
    evaluable = [m for m in models if m["composite"] is not None]
    unevaluable = [m for m in models if m["composite"] is None]
    evaluable.sort(key=lambda m: (m["composite"], m["id"]), reverse=True)
    prev_composite: float | None = None
    for idx, m in enumerate(evaluable):
        m["rank"] = idx + 1 if m["composite"] != prev_composite else evaluable[idx - 1]["rank"]
        prev_composite = m["composite"]
    for m in unevaluable:
        m["rank"] = None
    models = evaluable + unevaluable

    details: list[dict[str, Any]] = []
    for item in payload.get("details", []):
        entry = dict(item)
        mid = item.get("model_id", "")
        if not item.get("error") and item.get("scores"):
            composite = compute_composite(
                item["scores"], item.get("latency_s"), weights, scenario_of(item), scenario_weights
            )
            entry["composite"] = None if composite is None else round(composite, 3)
            entry["winner"] = prompt_winners.get(item.get("prompt_id", "")) == mid
            if composite is None:
                entry["composite_gaps"] = composite_gaps(
                    item["scores"], item.get("latency_s"), weights, scenario_of(item), scenario_weights
                )
        else:
            entry["composite"] = None
            entry["winner"] = False
        details.append(entry)

    result: dict[str, Any] = {"models": models, "details": details}
    enhance_failures = payload.get("enhance_failures")
    if enhance_failures:
        result["enhance_failures"] = enhance_failures
    return result


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
    lines.append(f"- 接口协议：{meta.get('protocol', '')} | {meta.get('base_url', '')}")
    sw = meta.get("scenario_weights") or {}
    sw_desc = "、".join(sw) if sw else "（未配置）"
    lines.append(
        f"- 综合分：场景命中 scenario_weights（{sw_desc}）→ Σ 各维度×场景权重（归一化）；"
        f"未命中 → {DEFAULT_WEIGHTS['quality']}×质量均分（准确/完整/清晰）+ "
        f"{DEFAULT_WEIGHTS['relevance']}×相关性 + {DEFAULT_WEIGHTS['speed']}×速度分\n"
    )

    lines.append("## 一、总览表\n")
    lines.append("| 排名 | 模型 | 质量均分 | 相关性 | 延迟(s) | 综合分 |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    for m in models:
        # C2 / C3：不可评的模型照常列出，但排名与综合分写「不可评」——
        # 把它们按 0 分塞进排名，会让"没测到"看起来像"排最后"。
        rank_txt = m["rank"] if m.get("rank") is not None else "不可评"
        composite_txt = f"{m['composite']:.3f}" if m.get("composite") is not None else "不可评"
        lines.append(
            f"| {rank_txt} | {m.get('label', m['id'])} | {m['quality_mean']:.2f} | "
            f"{m['dims'].get('relevance', 0):.2f} | {m['latency_mean']:.2f} | {composite_txt} |"
        )
    lines.append("")
    unevaluable_models = [m for m in models if m.get("unevaluable_gaps")]
    if unevaluable_models:
        lines.append("> **不可评模型**（缺失维度参与排序键 ⇒ 不参与排名）：")
        for m in unevaluable_models:
            lines.append(
                f"> - {m.get('label', m['id'])}：{m.get('unevaluable_prompts', 0)} 条不可评"
                f"（{'；'.join(m['unevaluable_gaps'])}）"
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
        lines.append("| 模型 | 准确性 | 完整性 | 相关性 | 清晰度 | 新颖度 | 延迟(s) | 综合分 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for item in items:
            label = item.get("model_label", item.get("model_id", ""))
            if item.get("error") or not item.get("scores"):
                lines.append(f"| {label} | 失败 | - | - | - | - | - | - |")
                continue
            scores = item["scores"]
            mark = " ★" if item.get("winner") else ""
            composite = item.get("composite")
            composite_txt = f"{composite:.3f}" if isinstance(composite, (int, float)) else "不可评"
            latency = item.get("latency_s")
            latency_txt = f"{latency:.2f}" if isinstance(latency, (int, float)) else "-"
            lines.append(
                f"| {label}{mark} | {scores.get('accuracy', 0)} | {scores.get('completeness', 0)} | "
                f"{scores.get('relevance', 0)} | {scores.get('clarity', 0)} | "
                f"{scores.get('novelty', '-')} | "
                f"{latency_txt} | {composite_txt} |"
            )
            if item.get("composite_gaps"):
                lines.append(
                    f"| ↳ 不可评原因 | {'；'.join(item['composite_gaps'])} | | | | | | |"
                )
        lines.append("")

    lines.append("## 三、逐模型小结\n")
    for m in models:
        dims = m.get("dims", {})
        lines.append(f"### {m.get('label', m['id'])}\n")
        ranked_dims = sorted(dims.items(), key=lambda kv: kv[1], reverse=True)
        strong = "、".join(f"{DIMENSION_LABELS[d]}（{v:.2f}）" for d, v in ranked_dims[:2] if v)
        weak = "、".join(f"{DIMENSION_LABELS[d]}（{v:.2f}）" for d, v in ranked_dims[-2:] if v)
        composite = m.get("composite")
        composite_txt = f"{composite:.3f}" if isinstance(composite, (int, float)) else "不可评"
        win_rate = m.get("win_rate")
        win_txt = f"{win_rate * 100:.1f}%" if isinstance(win_rate, (int, float)) else "不可判定"
        rank_txt = m["rank"] if m.get("rank") is not None else "不可评"
        lines.append(f"- 排名第 {rank_txt} | 综合分 {composite_txt} | 胜率 {win_txt}"
                     f"（{m.get('wins', 0)}/{m.get('judged_prompts', 0)}）")
        if m.get("unevaluable_gaps"):
            lines.append(
                f"- 不可评原因：{'；'.join(m['unevaluable_gaps'])}"
                f"（{m.get('unevaluable_prompts', 0)}/{m.get('judged_prompts', 0)} 条不可评）"
            )
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
    enhance_failures = payload.get("enhance_failures") or []
    lines.append("## 四、失败清单\n")
    if not failures and not enhance_failures:
        lines.append("> 无失败条目。\n")
    for item in failures:
        lines.append(
            f"- [{item.get('prompt_id', '')} × {item.get('model_id', '')}] {item.get('error', '未知错误')}"
        )
    for item in enhance_failures:
        lines.append(
            f"- [{item.get('prompt_id', '')}] {item.get('error', '未知错误')}"
            f"（影响 {item.get('model_count', 0)} 个模型）"
        )
    lines.append("")
    lines.append("---\n")
    lines.append("*本报告由 evaluation/model_benchmark.py 自动生成。详细数据见 summary.json。*")
    return "\n".join(lines)
