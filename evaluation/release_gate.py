"""v0.3.0 发布门槛判定（规格 §6.7）。

用法：python release_gate.py --summary <results>/<ts>/summary.json
退出码：0=全部硬门槛通过；1=存在不达标项（打印原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _quality(mean_map: dict[str, Any]) -> float:
    """三维质量均分：accuracy/completeness/clarity 的均值（缺失维度跳过）。"""
    dims = [mean_map[d]["mean"] for d in ("accuracy", "completeness", "clarity") if d in mean_map and mean_map[d].get("n", 0) > 0]
    return sum(dims) / len(dims) if dims else 0.0


def _nested(summary: dict[str, Any]) -> dict[str, Any]:
    """summary.json 顶层可能为完整聚合(含 summary 子对象);归一化到数据视图。"""
    if "delivery_rate" in summary or "judge_agreement" in summary:
        return summary
    sub = summary.get("summary") or {}
    merged = dict(summary)
    merged.update(sub)
    return merged


def compute_gates(summary_in: dict[str, Any]) -> list[dict[str, Any]]:
    summary = _nested(summary_in)
    gates: list[dict[str, Any]] = []

    # v0.4.0 代码审查 H3：report.py 会算出 summary["gate"]（含 4 条 gate_reasons：
    # 有效率<90%、主要场景样本<8、双裁判一致率<70%、**人工校准样本<有效比较的 20%**）
    # 并在 report.md 里打印"⚠️ 本轮结果不可用于产品结论"。但本函数此前完全不读它，
    # 自己重算 6 条且不含人工校准与场景样本量。
    # 结果：同一份 summary.json 可以同时出现"不可用于产品结论"和"全部硬门槛通过"。
    # 现在把它作为**第一条**门槛，两边不一致时直接 FAIL。
    pre = summary.get("gate")
    if not isinstance(pre, dict) or "pass" not in pre:
        gates.append({
            "name": "report.py 前置门槛（数据可用性）",
            "value": "缺失",
            "threshold": "pass=true",
            "pass": False,
        })
    else:
        reasons = pre.get("gate_reasons") or pre.get("reasons") or []
        gates.append({
            "name": "report.py 前置门槛（数据可用性）",
            "value": f"{len(reasons)} 条阻断：" + ("；".join(str(r) for r in reasons) if reasons else "无"),
            "threshold": "0 条阻断",
            "pass": bool(pre.get("pass")),
        })

    effective = float(summary.get("delivery_rate", 0.0))
    gates.append({
        "name": "总有效率 ≥ 95%",
        "value": f"{effective:.1%}",
        "threshold": "95%",
        "pass": effective >= 0.95,
    })

    control = summary.get("control") or {}
    amb_control = control.get("by_ambiguity") or {}

    def _level_gain(level: str) -> float | None:
        cmb = amb_control.get(level, {}).get("c_minus_b")
        if not cmb:
            return None
        return _quality(cmb)

    severe_gain = _level_gain("severe")
    if severe_gain is None:
        gates.append({"name": "严重模糊 C-B 质量提升 ≥ +0.8", "value": "缺失", "threshold": "+0.8", "pass": False})
    else:
        gates.append({"name": "严重模糊 C-B 质量提升 ≥ +0.8", "value": f"{severe_gain:+.2f}", "threshold": "+0.8", "pass": severe_gain >= 0.8})

    medium_gain = _level_gain("medium")
    if medium_gain is None:
        gates.append({"name": "中等模糊 C-B 质量提升 ≥ +0.4", "value": "缺失", "threshold": "+0.4", "pass": False})
    else:
        gates.append({"name": "中等模糊 C-B 质量提升 ≥ +0.4", "value": f"{medium_gain:+.2f}", "threshold": "+0.4", "pass": medium_gain >= 0.4})

    by_amb = summary.get("by_ambiguity") or {}
    clear = by_amb.get("clear")
    if not clear or (clear.get("wins", 0) + clear.get("losses", 0)) == 0:
        gates.append({"name": "清晰提示词明显变差率 ≤ 5%", "value": "缺失", "threshold": "5%", "pass": False})
    else:
        worse = clear.get("losses", 0) / (clear.get("wins", 0) + clear.get("losses", 0))
        gates.append({"name": "清晰提示词明显变差率 ≤ 5%", "value": f"{worse:.1%}", "threshold": "5%", "pass": worse <= 0.05})

    # v0.4.0 代码审查 H2：这里是 6 个门槛里**唯一 fail-open** 的一条。
    # 原写法 `float(cost.get("pad_ratio", 0.0))` 让 cost 整个缺失时 pad=0.0，
    # 于是 `0.0 <= 2.0` → PASS。也就是说"没测到膨胀比"被算成"膨胀完美"。
    # 而它恰好是防"增强器乱加字"的那道闸——缺失时必须判 FAIL，与其余 5 条一致。
    cost = summary.get("cost") or {}
    pad = cost.get("pad_ratio")
    if pad is None:
        gates.append({"name": "整体平均膨胀比 ≤ 2.0", "value": "缺失", "threshold": "×2.0", "pass": False})
    else:
        pad = float(pad)
        gates.append({"name": "整体平均膨胀比 ≤ 2.0", "value": f"×{pad:.2f}", "threshold": "×2.0", "pass": pad <= 2.0})

    agreement = (summary.get("judge_agreement") or {}).get("winner_agreement")
    if agreement is None:
        gates.append({"name": "双裁判一致率 ≥ 75%", "value": "缺失", "threshold": "75%", "pass": False})
    else:
        gates.append({"name": "双裁判一致率 ≥ 75%", "value": f"{agreement:.0%}", "threshold": "75%", "pass": agreement >= 0.75})

    return gates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PromptCraft v0.3.0 发布门槛判定")
    parser.add_argument("--summary", required=True, help="summary.json 路径")
    args = parser.parse_args(argv)

    path = Path(args.summary)
    if not path.exists():
        print(f"[gate] 找不到 {path}")
        return 1
    summary = json.loads(path.read_text(encoding="utf-8"))

    gates = compute_gates(summary)
    all_pass = True
    print("=== PromptCraft v0.3.0 发布门槛 ===")
    for g in gates:
        mark = "PASS" if g["pass"] else "FAIL"
        all_pass = all_pass and g["pass"]
        print(f"  [{mark}] {g['name']}: 值={g['value']} (阈值 {g['threshold']})")
    if not all_pass:
        print("\n[gate] 未达标，README 仅允许定性描述（禁止“提升 X%”）。")
        return 1
    print("\n[gate] 全部硬门槛通过，可写入量化效果数字。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
