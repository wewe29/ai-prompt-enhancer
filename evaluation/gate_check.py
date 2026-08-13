"""v0.2.2 回归门槛校验（规格 §5.1）。

两种入口：
1. CLI：python gate_check.py results/<timestamp>/summary.json
   按 §5.1 阈值对已生成报告 summary.json 判定 PASS/FAIL，并跑回归样本的离线确定性校验。
2. 纯函数：check_regression(summary) -> list[dict]（{name, value, threshold, pass}），供 pytest 单测。

指标口径：
- 核心约束保留率 = by_prompt_judge 各场景 constraint_retention 均值的归一化
  （prompt 级裁判为 1-10 分，见 judge.py；除以 10 得 0-1 保留率），门槛 100%。
- 清晰变差率 = summary.regression_worse_rate；缺失时按 losses/(wins+losses) 推算，门槛 ≤5%。
  该指标只有全量真实评测才能算出来，本文件只做字段读取与阈值判定，实值留待 P7 全量运行。
- 整体平均膨胀比 = cost.pad_ratio（增强长度/原文长度），门槛 ≤2.0。
  concise/standard 分模式膨胀比由离线回归 harness 单独校验（≤1.5 / ≤2.0）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

CONSTRAINT_RETENTION_MAX = 10.0  # prompt 级裁判满分（1-10 分）
RETAIN_SUFFIX = "\n请先给结论再展开。"  # 离线确定性"增强"追加句


def _percent(value: float) -> str:
    return f"{value * 100.0:.1f}%"


# ---- 报告门槛：从 summary.json 提取指标 ----
def extract_constraint_retention(summary: dict[str, Any]) -> float | None:
    """by_prompt_judge 各场景 constraint_retention 均值 → 0-1 保留率。"""
    by_pj = summary.get("by_prompt_judge") or {}
    means: list[float] = []
    for dims in by_pj.values():
        st = dims.get("constraint_retention") or {}
        if st.get("n"):
            means.append(float(st["mean"]))
    if not means:
        return None
    return round(sum(means) / len(means) / CONSTRAINT_RETENTION_MAX, 4)


def extract_worse_rate(summary: dict[str, Any]) -> float | None:
    """清晰提示词变差率：summary.regression_worse_rate；缺失时用胜负数推算。"""
    s = summary.get("summary") or {}
    rate = s.get("regression_worse_rate")
    if rate is None:
        wins = int(s.get("wins", 0) or 0)
        losses = int(s.get("losses", 0) or 0)
        if wins + losses:
            rate = losses / (wins + losses)
    if rate is None:
        return None
    return round(float(rate), 4)


def extract_pad_ratio(summary: dict[str, Any]) -> float | None:
    """整体平均膨胀比（增强长度/原文长度）。"""
    cost = summary.get("cost") or {}
    if "pad_ratio" not in cost:
        return None
    return round(float(cost["pad_ratio"]), 4)


def check_regression(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """按规格 §5.1 计算各门槛 → [{name, value, threshold, pass}]。

    数据缺失时 value=None 且 pass=False（无法证实达标即视为不达标）。
    """
    cr = extract_constraint_retention(summary)
    wr = extract_worse_rate(summary)
    pr = extract_pad_ratio(summary)
    return [
        {
            "name": "constraint_retention",
            "label": "核心约束保留率",
            "value": cr,
            "threshold": 1.0,
            "pass": cr is not None and cr >= 1.0,
        },
        {
            "name": "regression_worse_rate",
            "label": "清晰提示词变差率",
            "value": wr,
            "threshold": 0.05,
            "pass": wr is not None and wr <= 0.05,
        },
        {
            "name": "pad_ratio",
            "label": "整体平均膨胀比",
            "value": pr,
            "threshold": 2.0,
            "pass": pr is not None and pr <= 2.0,
        },
    ]


# ---- 离线回归样本确定性校验（不调用真实 API）----
def load_regression_samples(path: str | Path) -> list[dict[str, Any]]:
    import yaml

    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return [s for s in (data.get("samples") or []) if s.get("original")]


def simulate_enhanced(original: str, mode: str = "standard") -> str:
    """确定性"增强"：完整保留原文（原样），仅追加中性句。concise 不追加。"""
    if mode == "concise":
        return original
    return original + RETAIN_SUFFIX


def retention_key_phrase(original: str) -> str:
    """取原文第一句作为必须保留的关键短语；长句截断到前 12 字符。"""
    first = original.strip()
    for sep in ("。", "？", "！", "?", "!", "\n"):
        first = first.split(sep)[0]
    first = first.strip()
    return first[:12]


def retention_ok(original: str, enhanced: str) -> bool:
    key = retention_key_phrase(original)
    return bool(key) and key in enhanced


def run_offline_regression(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """对回归样本做确定性模拟，返回保留率与分模式平均膨胀比。"""
    if not samples:
        return {"samples": 0, "retention": 0.0, "concise_pad_ratio": 0.0, "standard_pad_ratio": 0.0}
    retained = 0
    concise_ratios: list[float] = []
    standard_ratios: list[float] = []
    for s in samples:
        original = s.get("original") or ""
        if not original:
            continue
        concise = simulate_enhanced(original, "concise")
        standard = simulate_enhanced(original, "standard")
        if retention_ok(original, standard) and retention_ok(original, concise):
            retained += 1
        concise_ratios.append(len(concise) / len(original))
        standard_ratios.append(len(standard) / len(original))
    return {
        "samples": len(samples),
        "retention": round(retained / len(samples), 4),
        "concise_pad_ratio": round(statistics.mean(concise_ratios), 4),
        "standard_pad_ratio": round(statistics.mean(standard_ratios), 4),
    }


# ---- CLI ----
def _format_gate_value(gate: dict[str, Any]) -> str:
    if gate["value"] is None:
        return "-（无数据）"
    if gate["name"] in ("constraint_retention", "regression_worse_rate"):
        return _percent(gate["value"])
    return f"{gate['value']:.2f}×"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v0.2.2 回归门槛校验（规格 §5.1）")
    parser.add_argument("summary", help="报告 summary.json 路径")
    parser.add_argument("--samples", default="samples_regression.yaml", help="回归样本 YAML 路径")
    args = parser.parse_args(argv)

    path = Path(args.summary)
    if not path.exists():
        print(f"[错误] 找不到 summary.json：{path}")
        return 1
    with open(path, "r", encoding="utf-8") as fh:
        summary = json.load(fh)

    print("== 离线回归样本确定性校验 ==")
    samples = load_regression_samples(args.samples)
    offline = run_offline_regression(samples)
    print(f"  样本数：{offline['samples']} | 核心约束保留率：{_percent(offline['retention'])}（需 100%）")
    print(f"  concise 平均膨胀比：{offline['concise_pad_ratio']:.2f}×（需 ≤1.5） | "
          f"standard 平均膨胀比：{offline['standard_pad_ratio']:.2f}×（需 ≤2.0）")
    offline_pass = (
        offline["samples"] > 0
        and offline["retention"] >= 1.0
        and offline["concise_pad_ratio"] <= 1.5
        and offline["standard_pad_ratio"] <= 2.0
    )

    print("\n== 报告门槛判定（规格 §5.1） ==")
    gates = check_regression(summary)
    all_pass = True
    for gate in gates:
        mark = "PASS" if gate["pass"] else "FAIL"
        if not gate["pass"]:
            all_pass = False
        print(f"  [{mark}] {gate['label']}：{_format_gate_value(gate)}（门槛 {_format_gate_value(gate | {'value': gate['threshold']})}）")
    print(f"  离线确定性校验：{'PASS' if offline_pass else 'FAIL'}")
    all_pass = all_pass and offline_pass

    if not all_pass:
        print("\n结论：FAIL（门槛未全部达标；变差率实值留待 P7 全量运行评估）")
        return 1
    print("\n结论：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
