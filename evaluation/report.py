"""报告生成：raw JSON + report.md + report.html。

输入是 run_eval 聚合后的完整数据，输出到 results/<timestamp>/。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from judge import DIMENSIONS, DIMENSION_LABELS  # evaluation/ 目录已在 sys.path 中

DIMENSION_ORDER = ["accuracy", "completeness", "relevance", "clarity"]

# 判定"改进"的阈值：任一维度提升 ≥ 2 分视为有实质改进
IMPROVE_THRESHOLD = 2


def generate(results_dir: Path, data: dict[str, Any]) -> Path:
    """生成全部输出文件，返回报告目录。"""
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = results_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    _write_raw_files(raw_dir, data)
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
            if any(v >= IMPROVE_THRESHOLD for v in deltas.values()):
                improved += 1
            winner = judge.get("winner", "tie")
            wins += winner == "enhanced"
            losses += winner == "original"
            ties += winner == "tie"
            for dim in DIMENSIONS:
                by_dim[dim].append(deltas.get(dim, 0))
                by_target.setdefault(target_id, {}).setdefault(dim, []).append(deltas.get(dim, 0))
                by_scenario.setdefault(scenario, {}).setdefault(dim, []).append(deltas.get(dim, 0))
                if persona:
                    by_persona.setdefault(persona, {}).setdefault(dim, []).append(deltas.get(dim, 0))

    def stat(series: list[float]) -> dict[str, float]:
        n = len(series)
        if not n:
            return {"n": 0, "mean": 0.0}
        return {"n": n, "mean": round(sum(series) / n, 2)}

    return {
        "dimensions": {dim: stat(by_dim[dim]) for dim in DIMENSIONS},
        "by_target": {tid: {dim: stat(vals) for dim, vals in dims.items()} for tid, dims in by_target.items()},
        "by_scenario": {sc: {dim: stat(vals) for dim, vals in dims.items()} for sc, dims in by_scenario.items()},
        "by_persona": {pid: {dim: stat(vals) for dim, vals in dims.items()} for pid, dims in by_persona.items()},
        "summary": {
            "total": total,
            "improved": improved,
            "improved_pct": round(100.0 * improved / total, 1) if total else 0.0,
            "wins": wins,
            "ties": ties,
            "losses": losses,
        },
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
    lines.append(f"- 胜负关系：胜（增强更优）**{summary['wins']}** / 平 **{summary['ties']}** / 负（原始更优）**{summary['losses']}**\n")
    lines.append("| 维度 | 平均 Delta（增强 − 原始） |")
    lines.append("|---|---|")
    for dim in DIMENSION_ORDER:
        st = agg["dimensions"][dim]
        mark = "+" if st["mean"] >= 0 else ""
        lines.append(f"| {DIMENSION_LABELS[dim]} | {mark}{st['mean']} |")
    lines.append("")

    if agg["by_scenario"]:
        lines.append("### 按场景分项\n")
        lines.append("| 场景 | " + " | ".join(DIMENSION_LABELS[d] for d in DIMENSION_ORDER) + " |")
        lines.append("|---|" + "---|" * len(DIMENSION_ORDER))
        for scenario, dims in agg["by_scenario"].items():
            row = [f"{dims[d]['mean']:+.2f}" for d in DIMENSION_ORDER]
            lines.append(f"| {scenario} | " + " | ".join(row) + " |")
        lines.append("")

    if agg["by_target"]:
        lines.append("### 按目标模型分项\n")
        lines.append("| 目标模型 | " + " | ".join(DIMENSION_LABELS[d] for d in DIMENSION_ORDER) + " |")
        lines.append("|---|" + "---|" * len(DIMENSION_ORDER))
        for tid, dims in agg["by_target"].items():
            row = [f"{dims[d]['mean']:+.2f}" for d in DIMENSION_ORDER]
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
            row = [f"{dims[d]['mean']:+.2f}" for d in DIMENSION_ORDER]
            lines.append(f"| {persona_labels.get(pid, pid)} | " + " | ".join(row) + " |")
        lines.append("")

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
                    delta = judge["deltas"][dim]
                    lines.append(f"| {DIMENSION_LABELS[dim]} | {o[dim]} | {e[dim]} | {delta:+.0f} |")
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
