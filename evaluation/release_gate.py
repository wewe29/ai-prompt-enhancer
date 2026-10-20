"""v0.3.0 发布门槛判定（规格 §6.7）+ v0.4.0 验收门禁（gate_spec_v040.json）。

用法：python release_gate.py --summary <results>/<ts>/summary.json
退出码：0=全部硬门槛通过；1=存在不达标项（打印原因）。

判定纪律（`docs/v040-缺失语义约定.md` R1–R8）
============================================
- **缺数据一律 fail-closed**：任何门槛拿不到测量值 ⇒ 判 FAIL，不放行。
- **输出只有 PASS / FAIL**。"不可判定"是 FAIL 的**原因标签**，不是第三种输出。
- **分母必须显式**：应有（expected）与实有（judged）比较数同时报；
  分母流失低于阈值 ⇒ FAIL（清单 A6）。
- **比率型门槛按区间判定**：点估计落在阈值另一侧不足以翻盘；
  区间跨越阈值 ⇒ FAIL（不可判定），见 `gate_decidability.decide_rate`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from gate_decidability import decide_rate, derive_requirements  # noqa: E402

SPEC_PATH = HERE / "gate_spec_v040.json"


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
    #
    # v0.4.0 代码审查 P1-6（上游同步）：report.py 现在在无比值样本时写
    # `pad_ratio: None`（此前写 0.0）。H2 只在**下游**把 None 判 FAIL，
    # 上游仍写 0.0 ⇒ fail-open 被下移一层、门槛照样 PASS。
    # 现在上游写 None，这里再把"键缺失"与"键存在但为 None"分开报，
    # 两种形态都必须 FAIL —— 缺数据就是缺数据，不是"膨胀完美"。
    cost = summary.get("cost") or {}
    if "pad_ratio" not in cost:
        gates.append({"name": "整体平均膨胀比 ≤ 2.0", "value": "缺失", "threshold": "×2.0", "pass": False})
    elif cost["pad_ratio"] is None:
        gates.append({"name": "整体平均膨胀比 ≤ 2.0", "value": "无样本（未测到膨胀比）", "threshold": "×2.0", "pass": False})
    else:
        pad = float(cost["pad_ratio"])
        gates.append({"name": "整体平均膨胀比 ≤ 2.0", "value": f"×{pad:.2f}", "threshold": "×2.0", "pass": pad <= 2.0})

    agreement = (summary.get("judge_agreement") or {}).get("winner_agreement")
    if agreement is None:
        gates.append({"name": "双裁判一致率 ≥ 75%", "value": "缺失", "threshold": "75%", "pass": False})
    else:
        gates.append({"name": "双裁判一致率 ≥ 75%", "value": f"{agreement:.0%}", "threshold": "75%", "pass": agreement >= 0.75})

    gates.append(_denominator_gate(summary))
    gates.extend(compute_spec_gates(summary))
    return gates


def _denominator_gate(summary: dict[str, Any]) -> dict[str, Any]:
    """分母完整性门槛（清单 A6 / 缺失语义约定 R2、R4）。

    "应有多少"与"实有多少"必须同时在产物里；实有/应有 低于阈值 ⇒ FAIL。
    缺这两个数 ⇒ FAIL（fail-closed）：没有分母的比率一律不可判定。
    """
    comparison = summary.get("comparison") or {}
    expected = comparison.get("expected_total")
    judged = comparison.get("judged_total")
    excluded = comparison.get("excluded")
    if expected is None or judged is None:
        return {"name": "比较样本完整性（实有/应有 ≥ 95%）", "value": "缺失",
                "threshold": "95%", "pass": False}
    if expected <= 0:
        return {"name": "比较样本完整性（实有/应有 ≥ 95%）", "value": "应有 0 组",
                "threshold": "95%", "pass": False}
    rate = judged / expected
    ok = rate >= 0.95
    excl_txt = "、".join(f"{k}={v}" for k, v in (excluded or {}).items() if v) or "无"
    return {
        "name": "比较样本完整性（实有/应有 ≥ 95%）",
        "value": f"{judged}/{expected} = {rate:.1%}（已排除：{excl_txt}）",
        "threshold": "95%",
        "pass": ok,
    }


def compute_spec_gates(summary_in: dict[str, Any],
                       spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """v0.4.0 验收门禁（规范 `evaluation/gate_spec_v040.json`）。

    测量数据从 summary 的 `v040_gates` 段读取：
        "v040_gates": {"<metric>": {"value": <比率或计数>, "n": <条数>, "runs": <独立运行轮数>}}

    判定三重保险：
      1. 规范里 status != "active" 的门槛**不参与放行判定**（已作废），
         但由调用方单独列出，避免"作废"被当成"通过"。
      2. n < required_n ⇒ FAIL（样本量不足）。
      3. runs < required_runs ⇒ FAIL（独立运行轮次不足 —— 单次运行的结果不可进结论）。
      4. 其余按 `decide_rate` 的区间规则判定。
    """
    if spec is None:
        with SPEC_PATH.open(encoding="utf-8") as f:
            spec = json.load(f)
    summary = _nested(summary_in)
    measured = summary.get("v040_gates") or {}
    reqs = {r["id"]: r for r in derive_requirements(spec)}

    gates: list[dict[str, Any]] = []
    for g in spec.get("gates", []):
        if g.get("status") != "active":
            continue
        req = reqs.get(g["id"], {})
        entry = measured.get(g["metric"]) or {}
        value = entry.get("value")
        n = entry.get("n")
        runs = entry.get("runs")
        req_n = req.get("required_n", 0)
        req_r = req.get("required_runs", 1) or 1
        name = f"[{g['id']}] {g['name']}"

        if value is None:
            gates.append({"name": name, "value": "缺失", "threshold": _thr_text(g),
                          "pass": False})
            continue
        if n is None or n < req_n:
            gates.append({"name": name,
                          "value": f"{value}（n={n}）",
                          "threshold": _thr_text(g) + f"，需 n≥{req_n}",
                          "pass": False})
            continue
        if runs is None or runs < req_r:
            gates.append({"name": name,
                          "value": f"{value}（runs={runs}）",
                          "threshold": _thr_text(g) + f"，需 R≥{req_r}",
                          "pass": False})
            continue

        if req.get("kind") == "count":
            ok = float(value) <= float(g["threshold"])
            gates.append({"name": name, "value": f"{value}（n={n}）",
                          "threshold": _thr_text(g) + f"，需 n≥{req_n}",
                          "pass": ok})
            continue

        tau = float(spec["noise_models"][g["noise_model"]]["tau"])
        decided = decide_rate(float(value), int(n), float(g["threshold"]),
                              g["direction"], tau=tau, runs=int(runs),
                              alpha=float(spec.get("alpha", 0.05)))
        gates.append({"name": name, "value": decided["value"],
                      "threshold": _thr_text(g) + f"，需 n≥{req_n} 且 R≥{req_r}",
                      "pass": decided["pass"]})
    return gates


def _thr_text(g: dict[str, Any]) -> str:
    thr = g["threshold"]
    if g.get("count_unit") == "samples":
        return f"≤{thr:.0f} 条"
    return f"{g['direction']} {thr:.0%}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PromptCraft 发布门槛判定（fail-closed）")
    parser.add_argument("--summary", required=True, help="summary.json 路径")
    args = parser.parse_args(argv)

    path = Path(args.summary)
    if not path.exists():
        print(f"[gate] 找不到 {path}")
        return 1
    summary = json.loads(path.read_text(encoding="utf-8"))

    gates = compute_gates(summary)
    all_pass = True
    print("=== PromptCraft 发布门槛（发布质量 + v0.4.0 验收）===")
    for g in gates:
        mark = "PASS" if g["pass"] else "FAIL"
        all_pass = all_pass and g["pass"]
        print(f"  [{mark}] {g['name']}: 值={g['value']} (阈值 {g['threshold']})")

    # 作废的门槛必须显式出现 —— "作废"不等于"通过"，也不能悄悄消失。
    with SPEC_PATH.open(encoding="utf-8") as f:
        spec = json.load(f)
    voided = [g for g in spec.get("gates", []) if g.get("status") != "active"]
    if voided:
        print("\n--- 已作废的 v0.4.0 验收门槛（不参与放行判定）---")
        for g in voided:
            print(f"  [作废] [{g['id']}] {g['name']}")
            print(f"         原因: {g.get('void_reason', '')}")
            print(f"         重建: {g.get('rebuild_requirement', '')}")

    if not all_pass:
        print("\n[gate] 结论：FAIL（存在未达标门槛；README 仅允许定性描述，禁止“提升 X%”）。")
        return 1
    print("\n[gate] 结论：PASS（全部硬门槛通过，可写入量化效果数字）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
