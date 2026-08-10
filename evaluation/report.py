"""报告生成：raw JSON + report.md + report.html。

输入是 run_eval 聚合后的完整数据，输出到 results/<timestamp>/。
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from judge import DIMENSIONS, DIMENSION_LABELS, PROMPT_JUDGE_DIMS  # evaluation/ 目录已在 sys.path 中

DIMENSION_ORDER = ["accuracy", "completeness", "relevance", "clarity", "novelty"]

# 判定"改进"的阈值：任一维度提升 ≥ 2 分视为有实质改进
IMPROVE_THRESHOLD = 2


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：字符数 / 1.8（中英混合经验值，向上取整）。"""
    return math.ceil(len(text) / 1.8)


def _mean_of_dims(dims: dict[str, Any]) -> float:
    """各维度 delta 均分：dims 为 {dim: stat} 时取各维度 mean 的平均。"""
    vals = [dims[d]["mean"] for d in DIMENSION_ORDER if d in dims]
    return sum(vals) / len(vals) if vals else 0.0


def _delta_cell(st: dict[str, Any]) -> str:
    """统计单元格渲染：无数据（旧数据缺该维度）显示 "-"。"""
    return "-" if st["n"] == 0 else f"{st['mean']:+.2f}"


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for proportion k/n; returns (low, high)."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def binomial_one_sided(wins: int, losses: int) -> float:
    """单侧二项检验：X~Binomial(wins+losses, 0.5)，返回观察方向上的尾概率。

    wins <= losses 时 p = P(X <= wins)，否则 p = P(X >= wins)。
    """
    n = wins + losses
    if n <= 0:
        return 1.0
    if wins <= losses:
        return sum(math.comb(n, i) for i in range(wins + 1)) / (2 ** n)
    return sum(math.comb(n, i) for i in range(wins, n + 1)) / (2 ** n)


def classify_magnitude(mean_delta: float) -> str:
    """按平均 Delta 分类：方向（improve/regress/tie）+ 幅度（minor/moderate/major）。

    |mean_delta| ≥ 3 → major；≥ 1 → moderate；否则 minor。正号归 improve，负号归 regress。
    """
    magnitude = (
        "major" if abs(mean_delta) >= 3.0
        else "moderate" if abs(mean_delta) >= 1.0
        else "minor"
    )
    if mean_delta > 0:
        return f"improve-{magnitude}"
    if mean_delta < 0:
        return f"regress-{magnitude}"
    return f"tie-{magnitude}"


def generate(results_dir: Path, data: dict[str, Any]) -> Path:
    """生成全部输出文件，返回报告目录。"""
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = results_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    _write_raw_files(raw_dir, data)
    (results_dir / "samples.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (results_dir / "summary.json").write_text(
        json.dumps(_aggregate(data), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (results_dir / "report.md").write_text(_render_markdown(data), encoding="utf-8")
    (results_dir / "report.html").write_text(_render_html(data), encoding="utf-8")
    return results_dir


# ---- raw 文件 ----
def _write_raw_files(raw_dir: Path, data: dict[str, Any]) -> None:
    for sample in data.get("samples", []):
        sample_id = sample["id"]
        persona = sample.get("persona") or ""
        persona_seg = f"_{persona}" if persona else ""
        enhanced = sample.get("enhanced") or {}
        (raw_dir / f"enhance_{sample_id}{persona_seg}.json").write_text(
            json.dumps(enhanced, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for target_id, result in (sample.get("results") or {}).items():
            for variant in ("original", "enhanced"):
                payload = {
                    "sample_id": sample_id,
                    "target": target_id,
                    "variant": variant,
                    "prompt": sample.get("original" if variant == "original" else "enhanced_text", ""),
                    "output": result.get(f"{variant}_output", ""),
                    "error": result.get(f"{variant}_error"),
                }
                (raw_dir / f"{sample_id}{persona_seg}_{target_id}_{variant}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )


# ---- 聚合 ----
def _aggregate(data: dict[str, Any]) -> dict[str, Any]:
    by_dim: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    improved, total, wins, ties, losses = 0, 0, 0, 0, 0
    by_target: dict[str, dict[str, list[float]]] = {}
    by_scenario: dict[str, dict[str, list[float]]] = {}
    by_persona: dict[str, dict[str, list[float]]] = {}
    by_magnitude: dict[str, dict[str, int]] = {
        "improve": {"minor": 0, "moderate": 0, "major": 0},
        "regress": {"minor": 0, "moderate": 0, "major": 0},
    }
    by_prompt_judge: dict[str, dict[str, list[float]]] = {}
    agreement_winners: list[float] = []
    agreement_rhos: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    cost_orig_chars: list[float] = []
    cost_enh_chars: list[float] = []
    cost_orig_tokens: list[float] = []
    cost_enh_tokens: list[float] = []
    cost_ratios: list[float] = []
    cost_quality_deltas: list[float] = []
    ctrl_enh_vs_padded: dict[str, dict[str, list[float]]] = {}
    ctrl_padded_vs_orig: dict[str, dict[str, list[float]]] = {}
    ctrl_ratios: list[float] = []

    for sample in data.get("samples", []):
        scenario = sample.get("scenario", "未分类")
        persona = sample.get("persona")
        for target_id, result in (sample.get("results") or {}).items():
            judge = result.get("judge")
            if not judge:
                continue
            total += 1
            original, enhanced = judge["original"], judge["enhanced"]
            deltas = judge.get("deltas", {})
            orig_text = sample.get("original") or ""
            enh_text = sample.get("enhanced_text") or ""
            cost_orig_chars.append(float(len(orig_text)))
            cost_enh_chars.append(float(len(enh_text)))
            cost_orig_tokens.append(float(estimate_tokens(orig_text)))
            cost_enh_tokens.append(float(estimate_tokens(enh_text)))
            if orig_text:
                cost_ratios.append(len(enh_text) / len(orig_text))
            if deltas:
                cost_quality_deltas.append(sum(deltas.values()) / len(deltas))

            jc = result.get("judge_control")
            jpv = result.get("judge_padded_vs_orig")
            if jc:
                jc_deltas = jc.get("deltas", {})
                for dim in DIMENSIONS:
                    if dim in jc_deltas:
                        ctrl_enh_vs_padded.setdefault(scenario, {}).setdefault(dim, []).append(jc_deltas[dim])
            if jpv:
                jpv_deltas = jpv.get("deltas", {})
                for dim in DIMENSIONS:
                    if dim in jpv_deltas:
                        ctrl_padded_vs_orig.setdefault(scenario, {}).setdefault(dim, []).append(jpv_deltas[dim])
            if (jc or jpv) and orig_text:
                ctrl_ratios.append(len(enh_text) / len(orig_text))
            if any(v >= IMPROVE_THRESHOLD for v in deltas.values()):
                improved += 1
            winner = judge.get("winner", "tie")
            wins += winner == "enhanced"
            losses += winner == "original"
            ties += winner == "tie"
            mean_delta = (sum(deltas.values()) / len(deltas)) if deltas else 0.0
            magnitude = classify_magnitude(mean_delta).split("-", 1)[1]
            if winner == "enhanced":
                by_magnitude["improve"][magnitude] += 1
            elif winner == "original":
                by_magnitude["regress"][magnitude] += 1
            for dim in DIMENSIONS:
                if dim not in deltas:
                    continue
                by_dim[dim].append(deltas[dim])
                by_target.setdefault(target_id, {d: [] for d in DIMENSIONS})[dim].append(deltas[dim])
                by_scenario.setdefault(scenario, {d: [] for d in DIMENSIONS})[dim].append(deltas[dim])
                if persona:
                    by_persona.setdefault(persona, {d: [] for d in DIMENSIONS})[dim].append(deltas[dim])

            ag = result.get("agreement")
            if ag and ag.get("dim_corr"):
                agreement_winners.append(1.0 if ag.get("winner_agreement") else 0.0)
                for dim in DIMENSIONS:
                    rho = ag["dim_corr"].get(dim)
                    if isinstance(rho, (int, float)):
                        agreement_rhos[dim].append(float(rho))

        pj = sample.get("prompt_judge")
        if pj and not pj.get("error"):
            for dim in PROMPT_JUDGE_DIMS:
                by_prompt_judge.setdefault(scenario, {}).setdefault(dim, []).append(float(pj.get(dim, 0)))

    def stat(series: list[float]) -> dict[str, float]:
        n = len(series)
        if not n:
            return {"n": 0, "mean": 0.0}
        return {"n": n, "mean": round(sum(series) / n, 2)}

    summary: dict[str, Any] = {
        "total": total,
        "improved": improved,
        "improved_pct": round(100.0 * improved / total, 1) if total else 0.0,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate_ci": [round(v, 4) for v in wilson_ci(wins, total)],
        "binomial_p": round(binomial_one_sided(wins, losses), 4),
    }
    if agreement_winners:
        summary["judge_agreement"] = {
            "winner_agreement": round(sum(agreement_winners) / len(agreement_winners), 4),
            "n": len(agreement_winners),
            "dim_corr": {
                dim: round(sum(vals) / len(vals), 4) if vals else 0.0
                for dim, vals in agreement_rhos.items()
                if vals
            },
        }

    cost: dict[str, Any] = {}
    if cost_orig_chars:
        orig_tokens_mean = round(sum(cost_orig_tokens) / len(cost_orig_tokens), 1)
        enh_tokens_mean = round(sum(cost_enh_tokens) / len(cost_enh_tokens), 1)
        cost = {
            "orig_chars_mean": round(sum(cost_orig_chars) / len(cost_orig_chars), 1),
            "enh_chars_mean": round(sum(cost_enh_chars) / len(cost_enh_chars), 1),
            "orig_tokens_mean": orig_tokens_mean,
            "enh_tokens_mean": enh_tokens_mean,
            "pad_ratio": round(sum(cost_ratios) / len(cost_ratios), 2) if cost_ratios else 0.0,
        }
        delta_tokens = enh_tokens_mean - orig_tokens_mean
        if delta_tokens > 0 and cost_quality_deltas:
            cost["quality_gain_per_1k_tokens"] = round(
                (sum(cost_quality_deltas) / len(cost_quality_deltas)) / (delta_tokens / 1000.0), 2
            )
        else:
            cost["quality_gain_per_1k_tokens"] = None

    by_control: dict[str, Any] = {
        "enhanced_vs_padded": {
            sc: {dim: stat(vals) for dim, vals in dims.items()} for sc, dims in ctrl_enh_vs_padded.items()
        },
        "padded_vs_original": {
            sc: {dim: stat(vals) for dim, vals in dims.items()} for sc, dims in ctrl_padded_vs_orig.items()
        },
        "pad_ratio": round(sum(ctrl_ratios) / len(ctrl_ratios), 2) if ctrl_ratios else 0.0,
    }

    return {
        "dimensions": {dim: stat(by_dim[dim]) for dim in DIMENSIONS},
        "by_target": {tid: {dim: stat(vals) for dim, vals in dims.items()} for tid, dims in by_target.items()},
        "by_scenario": {sc: {dim: stat(vals) for dim, vals in dims.items()} for sc, dims in by_scenario.items()},
        "by_persona": {pid: {dim: stat(vals) for dim, vals in dims.items()} for pid, dims in by_persona.items()},
        "by_prompt_judge": {sc: {dim: stat(vals) for dim, vals in dims.items()} for sc, dims in by_prompt_judge.items()},
        "by_magnitude": by_magnitude,
        "by_control": by_control,
        "cost": cost,
        "summary": summary,
    }


# ---- Markdown ----
def _render_markdown(data: dict[str, Any]) -> str:
    agg = _aggregate(data)
    lines: list[str] = []
    meta = data.get("meta", {})
    lines.append("# PromptCraft 提示词增强前后效果评测报告\n")
    lines.append(f"- 生成时间：{meta.get('generated_at', '')}")
    lines.append(f"- 样本数：{len(data.get('samples', []))}")
    lines.append(f"- 目标模型：{', '.join(meta.get('targets', [])) or '无'}")
    lines.append(f"- 增强模型：{meta.get('enhance_model', '')} | 裁判模型：{meta.get('judge_model', '')}")
    lines.append(f"- 预估 API 成本：${meta.get('estimated_cost', 0):.4f}\n")

    lines.append("## 一、整体结论\n")
    summary = agg["summary"]
    lines.append(f"- 有效对比样本：**{summary['total']}** 组")
    lines.append(f"- 增强后更优（任一维度 +{IMPROVE_THRESHOLD} 分）：**{summary['improved']}** 组（{summary['improved_pct']}%）")
    win_line = f"- 胜负关系：胜（增强更优）**{summary['wins']}** / 平 **{summary['ties']}** / 负（原始更优）**{summary['losses']}**"
    if summary["total"] > 0:
        ci = summary.get("win_rate_ci", (0.0, 0.0))
        win_line += (
            f"（胜率 {100.0 * summary['wins'] / summary['total']:.1f}%，"
            f"95% CI: [{ci[0]:.3f}, {ci[1]:.3f}]，p={summary.get('binomial_p', 1.0):.4f}）"
        )
        if summary["total"] < 30:
            win_line += "（样本数较少，结论仅供参考）"
    lines.append(win_line + "\n")
    ja = summary.get("judge_agreement")
    if ja:
        dim_corr = "；".join(
            f"{DIMENSION_LABELS[d]} ρ={ja['dim_corr'].get(d, 0.0):.2f}" for d in DIMENSION_ORDER
        )
        lines.append(
            f"- 裁判一致性：双裁判胜者一致率 **{100.0 * ja['winner_agreement']:.1f}%**"
            f"（n={ja['n']}）｜{dim_corr}\n"
        )
    lines.append("| 维度 | 平均 Delta（增强 − 原始） |")
    lines.append("|---|---|")
    for dim in DIMENSION_ORDER:
        st = agg["dimensions"][dim]
        cell = "-" if st["n"] == 0 else f"{'+' if st['mean'] >= 0 else ''}{st['mean']}"
        lines.append(f"| {DIMENSION_LABELS[dim]} | {cell} |")
    lines.append("")

    cost = agg.get("cost") or {}
    if cost:
        lines.append("### 成本与 token\n")
        lines.append(f"- 平均原始提示词长度：**{cost['orig_chars_mean']:.1f}** 字符（估算 {cost['orig_tokens_mean']:.0f} token）")
        lines.append(f"- 平均增强提示词长度：**{cost['enh_chars_mean']:.1f}** 字符（估算 {cost['enh_tokens_mean']:.0f} token）")
        lines.append(f"- 增强膨胀比：**×{cost['pad_ratio']:.2f}**（增强长度 / 原始长度）")
        gain = cost.get("quality_gain_per_1k_tokens")
        if gain is not None:
            mark = "+" if gain >= 0 else ""
            lines.append(f"- 每千 token 质量提升：**{mark}{gain:.2f}** 分（每增加 1000 token）")
        lines.append("")

    if summary["total"] > 0:
        by_mag = agg["by_magnitude"]
        lines.append("| 改进幅度 | 增强更优 | 原始更优 |")
        lines.append("|---|---|---|")
        for mag in ("minor", "moderate", "major"):
            lines.append(f"| {mag} | {by_mag['improve'][mag]} | {by_mag['regress'][mag]} |")
        lines.append("")

    if agg["by_scenario"]:
        lines.append("### 按场景分项\n")
        lines.append("| 场景 | " + " | ".join(DIMENSION_LABELS[d] for d in DIMENSION_ORDER) + " |")
        lines.append("|---|" + "---|" * len(DIMENSION_ORDER))
        for scenario, dims in agg["by_scenario"].items():
            row = [_delta_cell(dims[d]) for d in DIMENSION_ORDER]
            lines.append(f"| {scenario} | " + " | ".join(row) + " |")
        lines.append("")

    if agg["by_target"]:
        lines.append("### 按目标模型分项\n")
        lines.append("| 目标模型 | " + " | ".join(DIMENSION_LABELS[d] for d in DIMENSION_ORDER) + " |")
        lines.append("|---|" + "---|" * len(DIMENSION_ORDER))
        for tid, dims in agg["by_target"].items():
            row = [_delta_cell(dims[d]) for d in DIMENSION_ORDER]
            lines.append(f"| {tid} | " + " | ".join(row) + " |")
        lines.append("")

    if agg["by_persona"]:
        persona_labels = {
            s["persona"]: s.get("persona_label") or s["persona"]
            for s in data.get("samples", [])
            if s.get("persona")
        }
        lines.append("### 按用户画像分项\n")
        lines.append("| 用户画像 | " + " | ".join(DIMENSION_LABELS[d] for d in DIMENSION_ORDER) + " |")
        lines.append("|---|" + "---|" * len(DIMENSION_ORDER))
        for pid, dims in agg["by_persona"].items():
            row = [_delta_cell(dims[d]) for d in DIMENSION_ORDER]
            lines.append(f"| {persona_labels.get(pid, pid)} | " + " | ".join(row) + " |")
        lines.append("")

    prompt_judged = [
        s for s in data.get("samples", [])
        if s.get("prompt_judge") and not s["prompt_judge"].get("error")
    ]
    if prompt_judged:
        lines.append("### 增强质量(prompt 级)\n")
        lines.append("| 场景 | 结构清晰度 | 约束保留率 | 信息增益 | 过度增强(均分) |")
        lines.append("|---|---|---|---|---|")
        for scenario, dims in agg["by_prompt_judge"].items():
            row = [f"{dims[d]['mean']:.2f}" for d in PROMPT_JUDGE_DIMS]
            lines.append(f"| {scenario} | " + " | ".join(row) + " |")
        lines.append("")
        over = [s for s in prompt_judged if s["prompt_judge"].get("over_enhancement", 0) >= 7]
        if over:
            lines.append("**过度干预候选清单**（over_enhancement ≥ 7）：\n")
            for s in over:
                pj = s["prompt_judge"]
                lines.append(f"- {s['id']}（{s.get('scenario', '未分类')}）over_enhancement={pj.get('over_enhancement')}：{pj.get('reason', '')}")
            lines.append("")

    by_control = agg.get("by_control") or {}
    if by_control.get("enhanced_vs_padded") or by_control.get("padded_vs_original"):
        lines.append("## 长度控制组\n")
        lines.append("| 场景 | enhanced−padded 四维 delta 均分 | padded−original 四维 delta 均分 | 平均填充比(增强长度/原文长度) |")
        lines.append("|---|---|---|---|")
        scenarios = sorted(set(by_control.get("enhanced_vs_padded", {})) | set(by_control.get("padded_vs_original", {})))
        for scenario in scenarios:
            m1 = _mean_of_dims(by_control.get("enhanced_vs_padded", {}).get(scenario, {}))
            m2 = _mean_of_dims(by_control.get("padded_vs_original", {}).get(scenario, {}))
            lines.append(f"| {scenario} | {m1:+.2f} | {m2:+.2f} | {by_control.get('pad_ratio', 0.0):.2f}× |")
        lines.append("")
        lines.append("> 提示：若 enhanced−padded ≈ 0 且 padded−original > 0,说明增益主要来自加字而非改写\n")

    lines.append("## 二、逐样本对比详情\n")
    for sample in data.get("samples", []):
        heading = f"### {sample['id']}（{sample.get('scenario', '')}）"
        if sample.get("persona_label"):
            heading += f" · {sample['persona_label']}"
        lines.append(heading + "\n")
        lines.append("**原始提示词：**\n")
        lines.append("```text\n" + sample["original"] + "\n```\n")
        enhanced = sample.get("enhanced") or {}
        enhanced_text = sample.get("enhanced_text") or enhanced.get("primary_prompt", "")
        lines.append("**增强后提示词：**\n")
        if enhanced.get("error"):
            lines.append(f"> 增强失败：{enhanced['error']}\n")
        else:
            lines.append("```text\n" + enhanced_text + "\n```\n")
            status = enhanced.get("status", "")
            if status == "needs_clarification":
                questions = enhanced.get("questions", [])
                if questions:
                    lines.append("> 增强器提出了澄清问题：")
                    for q in questions:
                        lines.append(f"> - {q.get('text', '')}")
                    lines.append("")
            suggestions = enhanced.get("suggestions", [])
            if suggestions:
                lines.append("> 增强器附带 5 条可选建议：" + "、".join(s.get("title", "") for s in suggestions) + "\n")
        for target_id, result in (sample.get("results") or {}).items():
            lines.append(f"#### 目标模型：{target_id}\n")
            judge = result.get("judge")
            if judge:
                o, e = judge["original"], judge["enhanced"]
                lines.append("| 维度 | 原始回答 | 增强回答 | Delta |")
                lines.append("|---|---|---|---|")
                for dim in DIMENSION_ORDER:
                    delta = judge["deltas"].get(dim)
                    if delta is None:
                        lines.append(f"| {DIMENSION_LABELS[dim]} | - | - | - |")
                    else:
                        lines.append(
                            f"| {DIMENSION_LABELS[dim]} | {o.get(dim, '-')} | {e.get(dim, '-')} | {delta:+.0f} |"
                        )
                winner_label = {"enhanced": "增强版", "original": "原始版", "tie": "平局"}.get(judge["winner"], judge["winner"])
                lines.append(f"\n**裁判结论：** {winner_label} 更优。{judge.get('reason', '')}\n")
            for variant, label in (("original", "原始版回答"), ("enhanced", "增强版回答")):
                output = result.get(f"{variant}_output", "")
                error = result.get(f"{variant}_error")
                lines.append(f"**{label}：**")
                if error:
                    lines.append(f"> 获取失败：{error}")
                elif output:
                    lines.append("```text\n" + output[:4000] + ("\n…（已截断）" if len(output) > 4000 else "") + "\n```")
                else:
                    lines.append("> 无输出")
                lines.append("")
    lines.append("---\n")
    lines.append("*本报告由 evaluation/run_eval.py 自动生成。详细原始数据见 raw/ 与 summary.json。*")
    return "\n".join(lines)


# ---- HTML ----
def _fill_missing_dims_for_html(sample: dict[str, Any]) -> None:
    """HTML 模板按 DIMENSION_ORDER 渲染全部维度；旧缓存缺 novelty 时以 relevance 回退补齐显示。"""
    for result in (sample.get("results") or {}).values():
        for key in ("judge", "judge2", "judge_control", "judge_padded_vs_orig"):
            judge = result.get(key)
            if not isinstance(judge, dict):
                continue
            o, e = judge.get("original"), judge.get("enhanced")
            if not isinstance(o, dict) or not isinstance(e, dict):
                continue
            for side in (o, e):
                for dim in DIMENSION_ORDER:
                    if dim not in side:
                        side[dim] = side.get("relevance", 0)
            deltas = judge.setdefault("deltas", {})
            for dim in DIMENSION_ORDER:
                if dim not in deltas:
                    deltas[dim] = int(e.get(dim, 0)) - int(o.get(dim, 0))


def _render_html(data: dict[str, Any]) -> str:
    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"))
    template = env.get_template("report.html.j2")
    agg = _aggregate(data)
    for sample in data.get("samples", []):
        sample["original_text"] = sample["original"]
        enhanced = sample.get("enhanced") or {}
        sample["enhanced_text"] = sample.get("enhanced_text") or enhanced.get("primary_prompt", "")
        sample["enhance_error"] = enhanced.get("error")
        sample["enhance_status"] = enhanced.get("status")
        sample["enhance_questions"] = enhanced.get("questions", [])
        sample["enhance_suggestions"] = enhanced.get("suggestions", [])
        _fill_missing_dims_for_html(sample)
    return template.render(
        data=data,
        agg=agg,
        dims=DIMENSION_ORDER,
        dim_labels=DIMENSION_LABELS,
        improve_threshold=IMPROVE_THRESHOLD,
        winner_label={"enhanced": "增强版", "original": "原始版", "tie": "平局"},
    )


def timestamp_dir(root: Path) -> Path:
    """创建 results/<yyyy-mm-dd_HHMMSS>/ 目录。"""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return root / stamp
