"""导出"自包含评测数据包"：可直接复制给任意外部 AI 做进一步分析。用完即删。"""
import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent
TARGET_LABELS = {"doubao": "豆包", "qwen": "千问(Qwen)", "deepseek": "DeepSeek"}
DIM_LABELS = {"accuracy": "准确性", "completeness": "完整性", "relevance": "相关性", "clarity": "清晰度"}


def load_samples() -> list[dict]:
    with open(BASE / "samples" / "samples.yaml", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("samples", [])


def parse_scores_from_report(report_dir: str | None = None) -> dict:
    """从已生成的 report.md 解析逐条评分（样本 → 目标 → 评分明细）。"""
    if report_dir is None:
        # 自动选择最新报告目录
        dirs = sorted((BASE / "results").glob("2026-*"), reverse=True)
        report_dir = str(dirs[0]) if dirs else None
    # 找不到报告目录时**不回退到任何硬编码路径**：
    # 旧实现回退到 results/2026-08-08_134852/report.md —— 那是某一天的历史产物，
    # 静默读它会让人以为导出的数据包对应当前这一轮。
    if not report_dir:
        print("[警告] 找不到任何报告目录，评分表将为空（缺失数会如实报出）", file=sys.stderr)
        return {}
    report_md = Path(report_dir) / "report.md"
    if not report_md.exists():
        print(f"[警告] 报告不存在：{report_md}（评分表将为空）", file=sys.stderr)
        return {}
    text = report_md.read_text(encoding="utf-8")
    result: dict = {}
    import re
    # 样本块
    sample_blocks = re.split(r"(?m)^### ([a-z_0-9]+)（", text)
    # 结构: ["", sid, 内容, sid, 内容...]
    for i in range(1, len(sample_blocks), 2):
        sid = sample_blocks[i]
        body = sample_blocks[i + 1]
        result[sid] = {}
        target_blocks = re.split(r"(?m)^#### 目标模型：(\w+)\n", body)
        for j in range(1, len(target_blocks), 2):
            tid = target_blocks[j]
            tbody = target_blocks[j + 1]
            scores = {}
            for m in re.finditer(r"\| (\S+) \| (\d+) \| (\d+) \| ([+-]?\d+) \|", tbody):
                scores[m.group(1)] = {"original": int(m.group(2)), "enhanced": int(m.group(3)),
                                      "delta": int(m.group(4))}
            winner_m = re.search(r"裁判结论：\*\*\s*(\S+)\s*更优。([^\n]*)", tbody)
            result[sid][tid] = {
                "scores": scores,
                "winner": winner_m.group(1) if winner_m else "?",
                "reason": winner_m.group(2).strip() if winner_m else "",
            }
    return result


def _conclusion_lines(answers: dict, scores_map: dict, samples: dict) -> tuple[list[str], dict]:
    """**由数据算出**的整体结论。

    D6：此前「一、整体结论」整段是**源码里的字面量**（组数、占比、胜平负、
    四个维度的 Delta、分场景与分模型的结论）——**不随 `scores_map` 或
    `answers` 计算**。换任何数据，结论段都不变；导出的数据包于是带着一份
    与数据无关的结论。

    现在这一段全部由数据算出；**数据不足时明说「不可判定」，不编数字**。
    """
    pairs = [(sid, target) for sid, per in answers.items() for target in (per or {})]
    scored = [(sid, target) for sid, target in pairs if scores_map.get(sid, {}).get(target)]
    missing = [p for p in pairs if p not in scored]

    stats = {
        "samples": len(answers),
        "pairs": len(pairs),
        "scored_pairs": len(scored),
        "missing_pairs": len(missing),
    }

    lines = [
        "## 一、整体结论",
        "",
    ]

    if not scored:
        # 没有可评分的对比 ⇒ 任何数字都是编的
        lines.append("- **不可判定**：没有解析出任何可用的评分（详见下方「缺失」一节）。")
        lines.append(f"  - 对比组数：{len(pairs)}；其中**有评分的：0**；**缺失评分的：{len(missing)}**。")
        lines.append("- 本节不给出任何统计数字 —— 在没有数据时给出数字，等于编造。")
        return lines, stats

    # 逐组判定：以裁判结论为准，缺失时以四维平均 delta 为准
    wins = ties = losses = 0
    dim_deltas: dict[str, list[float]] = {dim: [] for dim in DIM_LABELS}
    by_scenario: dict[str, list[float]] = {}
    by_target: dict[str, list[float]] = {}
    for sid, target in scored:
        detail = scores_map[sid][target]
        scores = detail.get("scores") or {}
        deltas = []
        for dim, label in DIM_LABELS.items():
            entry = scores.get(label) or scores.get(dim) or {}
            delta = entry.get("delta")
            if isinstance(delta, (int, float)):
                dim_deltas[dim].append(float(delta))
                deltas.append(float(delta))
        mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
        winner = str(detail.get("winner", "")).strip()
        if "增强" in winner:
            wins += 1
        elif "原始" in winner:
            losses += 1
        else:
            if mean_delta > 0:
                wins += 1
            elif mean_delta < 0:
                losses += 1
            else:
                ties += 1
        scenario = (samples.get(sid) or {}).get("scenario", "未标注")
        by_scenario.setdefault(scenario, []).append(mean_delta)
        by_target.setdefault(target, []).append(mean_delta)

    stats.update({"wins": wins, "ties": ties, "losses": losses})
    lines.append(
        f"- 样本：{stats['samples']} 条 × 每条 {len(answers) and max((len(p) for p in answers.values()), default=0)} "
        f"个模型 = {stats['pairs']} 组对比；其中**有评分的 {stats['scored_pairs']} 组**"
        + (f"，**缺失评分的 {stats['missing_pairs']} 组**（见第三节）" if missing else "")
        + "。"
    )
    lines.append(
        f"- 增强后更优 / 持平 / 原始更优 = **{wins} / {ties} / {losses}**"
        f"（占已评分 {stats['scored_pairs']} 组）。"
    )
    dim_text = []
    for dim, label in DIM_LABELS.items():
        vals = dim_deltas[dim]
        dim_text.append(
            f"{label} {sum(vals) / len(vals):+.2f}" if vals else f"{label} 不可判定"
        )
    lines.append("- 四维平均 Delta（增强 − 原始）：" + "、".join(dim_text) + "。")

    scen_text = [
        f"{sc} {sum(v) / len(v):+.1f}" for sc, v in sorted(by_scenario.items())
    ]
    if scen_text:
        lines.append("- 分场景平均 Delta：" + "、".join(scen_text) + "。")
    tgt_text = [
        f"{TARGET_LABELS.get(t, t)} {sum(v) / len(v):+.1f}" for t, v in sorted(by_target.items())
    ]
    if tgt_text:
        lines.append("- 分模型平均 Delta：" + "、".join(tgt_text) + "。")
    lines.append("")
    return lines, stats


def main() -> int:
    answers = json.loads((BASE / "answers.json").read_text(encoding="utf-8"))["answers"]
    samples = {s["id"]: s for s in load_samples()}
    scores_map = parse_scores_from_report()

    conclusion_lines, stats = _conclusion_lines(answers, scores_map, samples)

    lines = [
        "# 提示词增强前后效果评测 · 数据包（自包含，可直接供外部 AI 分析）",
        "",
        "## 评测背景",
        "- 目的：对比「原始提示词」与「经 PromptCraft 增强后的提示词」分别驱动大模型回答的效果差异。",
        "- 被测模型：豆包（字节跳动）、千问 Qwen（阿里）。",
        "- 评测方式：同一提示词的原始版/增强版分别发给同一模型，由 AI 裁判按四个维度 1-10 分打分：",
        "  准确性（事实/代码/措辞是否正确无幻觉）、完整性（是否覆盖任务全部要求）、",
        "  相关性（是否紧扣目标无离题）、清晰度（结构/组织/语言易读）。",
        "- 注意：本轮为人工测试，DeepSeek 网页版暂未采集数据。",
        "",
        *conclusion_lines,
        "## 二、逐条对比数据",
        "",
    ]

    for idx, (sid, per) in enumerate(answers.items(), 1):
        sample = samples.get(sid, {})
        enhanced = json.loads((BASE / "results" / "_cache" / f"enhance_{sid}.json").read_text(encoding="utf-8")) \
            if (BASE / "results" / "_cache" / f"enhance_{sid}.json").exists() else {}
        lines.append(f"### {idx}. {sid}（{sample.get('scenario', '')}）")
        lines.append("")
        lines.append("**任务（原始提示词）：**")
        lines.append("```")
        lines.append(sample.get("original", ""))
        lines.append("```")
        lines.append("")
        lines.append("**增强后提示词：**")
        lines.append("```")
        lines.append(enhanced.get("primary_prompt", ""))
        lines.append("```")
        lines.append("")
        for target, pair in per.items():
            lines.append(f"#### {TARGET_LABELS.get(target, target)}")
            for variant in ("original", "enhanced"):
                label = "原始版回答" if variant == "original" else "增强版回答"
                content = pair.get(variant, "")
                lines.append(f"**{label}（{len(content)} 字）：**")
                lines.append("```")
                lines.append(content)
                lines.append("```")
                lines.append("")
    lines.append("## 三、逐条评分表（AI 裁判输出）")
    lines.append("")
    lines.append("| 样本 | 模型 | 准确性(原→增) | 完整性(原→增) | 相关性(原→增) | 清晰度(原→增) | 胜者 | 裁判理由 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    missing_rows: list[str] = []
    for sid, per in answers.items():
        for target, _pair in per.items():
            detail = scores_map.get(sid, {}).get(target)
            if not detail:
                # 解析不出评分的样本：**静默从评分表消失**是"把失败显示成成功"的
                # 另一种形态 —— 读者会以为这些样本根本没被评，而实际上是被漏掉了。
                missing_rows.append(f"{sid} / {TARGET_LABELS.get(target, target)}")
                continue
            s = detail["scores"]
            row = [
                sid, TARGET_LABELS.get(target, target),
                f"{s.get('准确性', {}).get('original', '-')}→{s.get('准确性', {}).get('enhanced', '-')}",
                f"{s.get('完整性', {}).get('original', '-')}→{s.get('完整性', {}).get('enhanced', '-')}",
                f"{s.get('相关性', {}).get('original', '-')}→{s.get('相关性', {}).get('enhanced', '-')}",
                f"{s.get('清晰度', {}).get('original', '-')}→{s.get('清晰度', {}).get('enhanced', '-')}",
                detail.get("winner", "?"),
                (detail.get("reason", "") or "")[:40],
            ]
            lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    if missing_rows:
        lines.append(f"### 缺失（{len(missing_rows)} 组）")
        lines.append("")
        lines.append("以下对比组**没有解析出评分**，它们不在上表中（不是「没被评」，是「被漏掉了」）：")
        lines.append("")
        for item in missing_rows:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("> 说明：表中『原→增』表示该维度 原始版得分 → 增强版得分。裁判为 DeepSeek（deepseek-chat）。")
    lines.append("")

    # 产物名带身份（避免每次覆盖上一次的导出）
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = BASE / "results" / "manual" / f"评测数据包_{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成：{out}（{out.stat().st_size} 字节）")
    print(
        f"统计：对比组 {stats['pairs']}｜有评分 {stats['scored_pairs']}｜缺失 {stats['missing_pairs']}"
    )
    if missing_rows:
        print(f"[警告] {len(missing_rows)} 组对比没有解析出评分，已在产物里逐条列出（不静默丢弃）")
    if not stats["scored_pairs"]:
        # 整表为空却退出 0 —— 读者会以为"导出成功"就是"数据齐全"
        print("[错误] 没有任何可评分的对比，导出的数据包没有结论。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
