"""release_gate.py 的单元测试：合成 summary 判定发布门槛。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_gate import compute_gates  # noqa: E402


def _dim(mean: float) -> dict:
    return {"n": 10, "mean": mean}


def _pass_summary() -> dict:
    return {
        "delivery_rate": 0.97,
        "control": {
            "by_ambiguity": {
                "severe": {"c_minus_b": {d: _dim(9.0) for d in ("accuracy", "completeness", "clarity")}},
                "medium": {"c_minus_b": {d: _dim(9.0) for d in ("accuracy", "completeness", "clarity")}},
            }
        },
        "by_ambiguity": {"clear": {"wins": 40, "ties": 10, "losses": 1}},
        "cost": {"pad_ratio": 1.8},
        "judge_agreement": {"winner_agreement": 0.8},
    }


def _fail_summary() -> dict:
    return {
        "delivery_rate": 0.88,
        "control": {"by_ambiguity": {}},
        "by_ambiguity": {},
        "cost": {"pad_ratio": 2.6},
        "judge_agreement": {"winner_agreement": 0.6},
    }


def test_pass_summary_all_gates_pass():
    gates = compute_gates(_pass_summary())
    assert all(g["pass"] for g in gates), gates
    assert len(gates) == 6


def test_fail_summary_flags_multiple():
    gates = compute_gates(_fail_summary())
    failed = [g for g in gates if not g["pass"]]
    names = [g["name"] for g in failed]
    assert "总有效率 ≥ 95%" in names
    assert "严重模糊 C-B 质量提升 ≥ +0.8" in names
    assert "中等模糊 C-B 质量提升 ≥ +0.4" in names
    assert "清晰提示词明显变差率 ≤ 5%" in names
    assert "整体平均膨胀比 ≤ 2.0" in names
    assert "双裁判一致率 ≥ 75%" in names


def test_clear_worse_rate_boundary():
    s = _pass_summary()
    s["by_ambiguity"]["clear"] = {"wins": 19, "ties": 0, "losses": 1}
    gates = compute_gates(s)
    g = next(x for x in gates if "变差率" in x["name"])
    assert g["pass"] is True  # 1/20 = 5% 恰好等于阈值 → 允许

    s["by_ambiguity"]["clear"] = {"wins": 18, "ties": 0, "losses": 2}
    g = next(x for x in compute_gates(s) if "变差率" in x["name"])
    assert g["pass"] is False  # 2/20 = 10% > 5%


def test_missing_agreement_fails():
    s = _pass_summary()
    s.pop("judge_agreement", None)
    g = next(x for x in compute_gates(s) if "一致率" in x["name"])
    assert g["pass"] is False
    assert g["value"] == "缺失"
