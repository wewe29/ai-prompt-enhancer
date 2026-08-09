"""导出"自包含评测数据包"：可直接复制给任意外部 AI 做进一步分析。用完即删。"""
import json
import sys
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
    report_md = Path(report_dir) / "report.md" if report_dir else BASE / "results" / "2026-08-08_134852" / "report.md"
    if not report_md.exists():
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


def main() -> None:
    answers = json.loads((BASE / "answers.json").read_text(encoding="utf-8"))["answers"]
    samples = {s["id"]: s for s in load_samples()}
    scores_map = parse_scores_from_report()

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
        "## 一、整体结论",
        "- 样本：6 条测试提示词 × 2 模型 = 12 组对比。",
        "- 增强后更优（任一维度提升 ≥2 分）：1 组（8.3%）；胜/平/负 = 4/3/5（增强更优/持平/原始更优）。",
        "- 四维平均 Delta（增强 − 原始）：准确性 -0.08、完整性 -0.25、相关性 -0.08、清晰度 +0.0。",
        "- 分场景：问答类任务增强提升最明显（平均 +1.5）；编程/数据分析/创意类反而略降。",
        "- 分模型：千问微正（+0.1~+0.3），豆包微负（-0.2~-0.3）。",
        "- 初步解读：增强提示词对「信息不足/结构要求模糊」的提示词价值大；对已具体清晰的提示词可能引入冗余。",
        "",
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
    for sid, per in answers.items():
        for target, _pair in per.items():
            detail = scores_map.get(sid, {}).get(target)
            if not detail:
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
    lines.append("> 说明：表中『原→增』表示该维度 原始版得分 → 增强版得分。裁判为 DeepSeek（deepseek-chat）。")
    lines.append("")

    out = BASE / "results" / "manual" / "评测数据包.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成：{out}（{out.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
