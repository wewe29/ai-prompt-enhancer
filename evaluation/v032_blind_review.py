"""v0.3.2 人工盲评辅助：从合并结果中导出盲评包，回收答案后揭盲与自动裁判比对。

流程（对应迭代规划 §4.1「至少 20% 样本进行人工盲评」）：
  1. export  —— 从合并 samples.json 按 目标×难度 分层抽样，生成盲评包
                blind_packet.md（只有任务 + 匿名回答A/B）与 blind_key.json（揭盲映射，评审时不得查看）；
  2. compare —— 人工（盲态）填好 blind_answers.json 后，揭盲并与自动裁判结论比对，
                输出 agreement.json（胜者一致率、四维平均绝对差、方向一致性判定）。

盲评单位 = 一个（样本 × 目标）对比（与报告统计口径一致）。回答取该对比首个成功 rep 的输出。
评审者盲态下仅见 anonymous A/B；blind_key.json 保存随机映射，compare 阶段才揭盲。

用法：
  python v032_blind_review.py export --samples results/<merged>/samples.json \
      --per-target 12 --seed 42 --outdir blind_review_v032
  python v032_blind_review.py compare --review blind_review_v032/blind_answers.json \
      --key blind_review_v032/blind_key.json --samples results/<merged>/samples.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

DIMS = ["accuracy", "completeness", "relevance", "clarity"]
DIFF_ORDER = {"clear": 0, "medium": 1, "severe": 2}


def load_units(samples_path: Path) -> list[dict[str, Any]]:
    """收集全部有效对比单元：(sample_id, target, difficulty, task, original_output, enhanced_output, judge)。"""
    data = json.loads(samples_path.read_text(encoding="utf-8"))
    units: list[dict[str, Any]] = []
    for sample in data.get("samples", []):
        for target_id, result in (sample.get("results") or {}).items():
            judge = result.get("judge")
            if not judge or not isinstance(judge, dict):
                continue
            original_out = result.get("original_output") or ""
            enhanced_out = result.get("enhanced_output") or ""
            if not original_out or not enhanced_out:
                continue
            units.append({
                "sample_id": sample.get("id", ""),
                "target": target_id,
                "difficulty": sample.get("ambiguity_level", "unknown"),
                "scenario": sample.get("scenario", ""),
                "task": sample.get("original", ""),
                "original_output": original_out,
                "enhanced_output": enhanced_out,
                "judge_winner": judge.get("winner", "tie"),
                "judge_deltas": {d: (judge.get("deltas") or {}).get(d, 0) for d in DIMS},
            })
    return units


def cmd_export(args: argparse.Namespace) -> int:
    units = load_units(Path(args.samples))
    if not units:
        print("[错误] 没有可用的已裁判对比")
        return 1
    rng = random.Random(args.seed)
    # 目标×难度分层：每目标 per_target 条，难度内均匀
    by_target: dict[str, list[dict[str, Any]]] = {}
    for u in units:
        by_target.setdefault(u["target"], []).append(u)
    selected: list[dict[str, Any]] = []
    for tid in sorted(by_target):
        pool = by_target[tid]
        by_diff: dict[str, list[dict[str, Any]]] = {}
        for u in pool:
            by_diff.setdefault(u["difficulty"], []).append(u)
        per_diff = max(1, args.per_target // max(1, len(by_diff)))
        for diff in sorted(by_diff, key=lambda d: DIFF_ORDER.get(d, 9)):
            cand = sorted(by_diff[diff], key=lambda u: u["sample_id"])
            take = min(per_diff, len(cand))
            selected.extend(rng.sample(cand, take))
        if len([s for s in selected if s["target"] == tid]) > args.per_target:
            extra = [s for s in selected if s["target"] == tid][args.per_target:]
            selected = [s for s in selected if s not in extra]
    rng.shuffle(selected)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    key_rows: list[dict[str, Any]] = []
    packet: list[str] = [
        "# PromptCraft v0.3.2 人工盲评包",
        "",
        f"- 生成 seed：{args.seed}｜盲评单元数：{len(selected)}",
        "- 每个单元给出任务与两个匿名回答（A/B 随机顺序）。请盲态判定更优回答并按 1-10 打分。",
        "",
    ]
    for i, u in enumerate(selected, 1):
        swap = rng.random() < 0.5
        ans_a, ans_b = (u["enhanced_output"], u["original_output"]) if swap else (u["original_output"], u["enhanced_output"])
        key_rows.append({
            "item": i,
            "sample_id": u["sample_id"],
            "target": u["target"],
            "difficulty": u["difficulty"],
            "enhanced_label": "A" if swap else "B",
            "judge_winner": u["judge_winner"],
            "judge_deltas": u["judge_deltas"],
        })
        packet += [
            f"## 单元 {i}",
            "",
            f"- 任务（原始提示词）：",
            "",
            "```",
            u["task"].strip(),
            "```",
            "",
            "### 回答 A",
            "",
            "```",
            ans_a.strip(),
            "```",
            "",
            "### 回答 B",
            "",
            "```",
            ans_b.strip(),
            "```",
            "",
            "> 我的判定：胜者(A/B/平)＝__｜A 四维(准确/完整/相关/清晰)＝_/_/_/_｜B 四维＝_/_/_/_｜理由＝__",
            "",
        ]
    (outdir / "blind_packet.md").write_text("\n".join(packet), encoding="utf-8")
    (outdir / "blind_key.json").write_text(json.dumps(key_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    template = [{"item": k["item"], "winner": "", "dims_a": {}, "dims_b": {}, "reason": ""} for k in key_rows]
    (outdir / "blind_answers.json").write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export] 盲评单元 {len(selected)} 个 → {outdir/'blind_packet.md'}")
    print(f"[export] 揭盲映射（评审期间勿查看）：{outdir/'blind_key.json'}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    key_rows = json.loads(Path(args.key).read_text(encoding="utf-8"))
    answers = {a["item"]: a for a in json.loads(Path(args.review).read_text(encoding="utf-8"))}
    key_map = {k["item"]: k for k in key_rows}
    agree = disagree = tie_cases = 0
    abs_diffs: list[float] = []
    per_dim_abs: dict[str, list[float]] = {d: [] for d in DIMS}
    rows: list[dict[str, Any]] = []
    direction_conflicts: list[dict[str, Any]] = []
    for item, k in sorted(key_map.items()):
        a = answers.get(item)
        if not a or not a.get("winner"):
            print(f"[警告] 单元 {item} 未填写，跳过")
            continue
        human_winner = str(a["winner"]).strip().lower()
        enhanced_label = k["enhanced_label"]
        # 揭盲：把盲态胜者映射到 enhanced/original/tie
        if human_winner in ("a", "b"):
            human_mapped = "enhanced" if human_winner == enhanced_label else "original"
        else:
            human_mapped = "tie"
        judge_winner = k["judge_winner"]
        if human_mapped == "tie" or judge_winner == "tie":
            tie_cases += 1
        elif human_mapped == judge_winner:
            agree += 1
        else:
            disagree += 1
            direction_conflicts.append({"item": item, "sample_id": k["sample_id"], "target": k["target"],
                                        "difficulty": k["difficulty"], "human": human_mapped, "judge": judge_winner})
        # 四维绝对差：human(enhanced - original) vs judge deltas
        dims_a = a.get("dims_a") or {}
        dims_b = a.get("dims_b") or {}
        human_delta = {}
        for d in DIMS:
            try:
                da = float(dims_a.get(d, 0))
                db = float(dims_b.get(d, 0))
            except (TypeError, ValueError):
                continue
            # 盲态 delta 是 A−B；映射到 enhanced−original
            delta = (da - db) if enhanced_label == "A" else (db - da)
            human_delta[d] = delta
            abs_diffs.append(abs(delta - k["judge_deltas"].get(d, 0)))
            per_dim_abs[d].append(abs(delta - k["judge_deltas"].get(d, 0)))
        rows.append({"item": item, "sample_id": k["sample_id"], "target": k["target"],
                     "difficulty": k["difficulty"], "human_winner": human_mapped,
                     "judge_winner": judge_winner, "human_deltas": human_delta,
                     "judge_deltas": k["judge_deltas"], "reason": a.get("reason", "")})
    n = agree + disagree
    winner_agreement = (agree / n) if n else None
    decisive = agree + disagree
    direction_consistent = bool(decisive and (agree / decisive) >= 0.5)
    out = {
        "reviewed": len(rows),
        "non_tie_pairs": decisive,
        "winner_agreement_non_tie": round(winner_agreement, 4) if winner_agreement is not None else None,
        "tie_or_vs_tie_cases": tie_cases,
        "direction_consistent": direction_consistent,
        "dims_mad_vs_judge": {d: round(sum(v) / len(v), 3) if v else None for d, v in per_dim_abs.items()},
        "overall_mad": round(sum(abs_diffs) / len(abs_diffs), 3) if abs_diffs else None,
        "conflicts": direction_conflicts,
        "rows": rows,
    }
    outpath = Path(args.out) if args.out else Path(args.review).with_name("agreement.json")
    outpath.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[compare] 盲评 {len(rows)} 单元｜非平局判定 {decisive} 个")
    print(f"[compare] 与自动裁判胜者一致率（非平局）: {winner_agreement:.1%}" if winner_agreement is not None
          else "[compare] 无有效非平局对比")
    print(f"[compare] 四维平均绝对差: {out['dims_mad_vs_judge']}｜总体 {out['overall_mad']}")
    print(f"[compare] 方向一致性: {'一致' if direction_consistent else '不一致'}")
    if direction_conflicts:
        print(f"[compare] 冲突单元: {[(c['item'], c['sample_id'], c['target']) for c in direction_conflicts]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="v0.3.2 人工盲评辅助")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="导出盲评包")
    p_exp.add_argument("--samples", required=True, help="合并后的 samples.json")
    p_exp.add_argument("--per-target", type=int, default=12, help="每目标抽取的单元数（默认 12 ≈ 总量 20%）")
    p_exp.add_argument("--seed", type=int, default=42)
    p_exp.add_argument("--outdir", default="blind_review_v032")
    p_exp.set_defaults(fn=cmd_export)

    p_cmp = sub.add_parser("compare", help="揭盲并与自动裁判比对")
    p_cmp.add_argument("--review", required=True, help="人工填写的 blind_answers.json")
    p_cmp.add_argument("--key", required=True, help="blind_key.json（揭盲映射）")
    p_cmp.add_argument("--samples", required=True, help="合并后的 samples.json（保留参数以备扩展）")
    p_cmp.add_argument("--out", default=None, help="agreement 输出路径")
    p_cmp.set_defaults(fn=cmd_compare)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
