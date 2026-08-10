"""人工复核工具：把已裁判的对比抽样导出为 review_set，并与人工打分比对一致性。

用法：
  python review_export.py export --results <results_dir> --out review_set.json [--ratio 0.1] [--seed 42]
  python review_export.py compare --review review_set.json --human human_scores.json --out agreement.json

- export：从结果目录（需包含新版 run_eval 生成的 samples.json）随机抽样已裁判对比，
  输出 [{sample_id, target, original_answer, enhanced_answer, judge: {dims, winner, reason}}]。
  dims 为各维度 delta（增强 − 原始，范围 −10..10），与 human_scores.json 中 dims 同语义。
- compare：human_scores.json = [{sample_id, target, winner: "enhanced"|"original"|"tie",
  dims: {accuracy:..,completeness:..,relevance:..,clarity:..}}]；
  按 (sample_id, target) 匹配，输出胜者一致率与四维平均绝对差。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

DIMS = ["accuracy", "completeness", "relevance", "clarity"]


def load_payload(results_dir: Path) -> dict[str, Any]:
    samples_file = Path(results_dir) / "samples.json"
    if not samples_file.exists():
        sys.exit(f"[错误] 找不到 {samples_file}（结果目录需由新版 run_eval 生成）")
    return json.loads(samples_file.read_text(encoding="utf-8"))


def judged_comparisons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """收集全部已裁判对比：{sample_id, target, original_answer, enhanced_answer, judge}。"""
    out: list[dict[str, Any]] = []
    for sample in payload.get("samples", []):
        for target_id, result in (sample.get("results") or {}).items():
            judge = result.get("judge")
            if not judge or not isinstance(judge, dict):
                continue
            deltas = judge.get("deltas") or {}
            out.append({
                "sample_id": sample.get("id", ""),
                "target": target_id,
                "original_answer": result.get("original_output", ""),
                "enhanced_answer": result.get("enhanced_output", ""),
                "judge": {
                    "dims": {dim: deltas.get(dim, 0) for dim in DIMS},
                    "winner": judge.get("winner", "tie"),
                    "reason": judge.get("reason", ""),
                },
            })
    return out


def export_cmd(results_dir: Path, out: Path, ratio: float = 0.1, seed: int = 42) -> None:
    """从结果目录抽样导出待人工复核的对比（固定 seed 可复现）。"""
    if not 0 < ratio <= 1:
        sys.exit(f"[错误] --ratio 必须在 (0, 1] 区间，当前 {ratio}")
    comparisons = judged_comparisons(load_payload(results_dir))
    if not comparisons:
        sys.exit("[错误] 结果目录中没有已裁判的对比")
    rng = random.Random(seed)
    rng.shuffle(comparisons)
    n = max(1, min(len(comparisons), round(ratio * len(comparisons))))
    subset = comparisons[:n]
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(subset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export] 已导出 {len(subset)}/{len(comparisons)} 条对比 → {out_path}（seed={seed}）")
    return subset


def compare_cmd(review_path: Path, human_path: Path, out: Path) -> dict[str, Any]:
    """人工打分 vs 裁判：胜者一致率 + 四维平均绝对差，写 agreement.json。"""
    review = json.loads(Path(review_path).read_text(encoding="utf-8-sig"))
    human = json.loads(Path(human_path).read_text(encoding="utf-8-sig"))
    human_by_key = {(h.get("sample_id", ""), h.get("target", "")): h for h in human}

    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_review = 0
    for r in review:
        h = human_by_key.get((r.get("sample_id", ""), r.get("target", "")))
        if h is None:
            unmatched_review += 1
        else:
            matched.append((r, h))
    unmatched_human = len(human) - len(matched)

    if not matched:
        summary: dict[str, Any] = {
            "matched": 0,
            "winner_agreement_rate": 0.0,
            "per_dim_mean_abs_diff": {dim: None for dim in DIMS},
            "review_total": len(review),
            "human_total": len(human),
            "unmatched_review": unmatched_review,
            "unmatched_human": unmatched_human,
        }
    else:
        agree = sum(1 for r, h in matched if r["judge"]["winner"] == h.get("winner"))
        per_dim_diff: dict[str, list[float]] = {dim: [] for dim in DIMS}
        for r, h in matched:
            hdims = h.get("dims") or {}
            jdims = r["judge"]["dims"]
            for dim in DIMS:
                if isinstance(hdims.get(dim), (int, float)) and isinstance(jdims.get(dim), (int, float)):
                    per_dim_diff[dim].append(abs(float(hdims[dim]) - float(jdims[dim])))
        summary = {
            "matched": len(matched),
            "winner_agreement_rate": round(agree / len(matched), 4),
            "per_dim_mean_abs_diff": {
                dim: round(sum(vals) / len(vals), 4) if vals else None
                for dim, vals in per_dim_diff.items()
            },
            "review_total": len(review),
            "human_total": len(human),
            "unmatched_review": unmatched_review,
            "unmatched_human": unmatched_human,
        }

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[compare] 匹配 {summary['matched']} 条，胜者一致率 "
        f"{summary['winner_agreement_rate']:.1%} → {out_path}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="人工复核：导出裁判对比 + 与人工打分比对一致性")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="从结果目录抽样导出待人工复核的对比")
    p_export.add_argument("--results", required=True, help="结果目录（需包含 samples.json）")
    p_export.add_argument("--out", required=True, help="输出 review_set.json 路径")
    p_export.add_argument("--ratio", type=float, default=0.1, help="抽样比例（默认 0.1）")
    p_export.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，可复现）")

    p_compare = sub.add_parser("compare", help="人工打分与裁判结果比对一致性")
    p_compare.add_argument("--review", required=True, help="review_set.json 路径")
    p_compare.add_argument("--human", required=True, help="human_scores.json 路径")
    p_compare.add_argument("--out", required=True, help="输出 agreement.json 路径")

    args = parser.parse_args(argv)
    if args.command == "export":
        export_cmd(Path(args.results), Path(args.out), ratio=args.ratio, seed=args.seed)
    else:
        compare_cmd(Path(args.review), Path(args.human), Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
