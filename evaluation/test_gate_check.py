"""v0.2.2 回归门槛校验单测（离线、确定性、无真实 API）。

覆盖：samples_regression.yaml 加载、模拟增强后的 100% 约束保留、concise/standard
膨胀比门槛，以及 check_regression 在合成 summary 上的阈值判定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_check  # noqa: E402

REGRESSION = Path(__file__).resolve().parent / "samples_regression.yaml"


def _synthetic_summary(by_pj=None, worse_rate=None, pad_ratio=None, wins=0, losses=0):
    s = {}
    if by_pj is not None:
        s["by_prompt_judge"] = by_pj
    cost = {}
    if pad_ratio is not None:
        cost["pad_ratio"] = pad_ratio
    if cost:
        s["cost"] = cost
    sub = {}
    if worse_rate is not None:
        sub["regression_worse_rate"] = worse_rate
    if wins or losses:
        sub["wins"] = wins
        sub["losses"] = losses
    if sub:
        s["summary"] = sub
    return s


# ---- 回归样本集 ----
def test_regression_samples_load_20():
    samples = gate_check.load_regression_samples(REGRESSION)
    assert len(samples) == 20
    for s in samples:
        assert s["id"]
        assert s["original"]


# ---- 检测器自检（v0.4.0 代码审查 H1 之后取代"离线确定性校验"）----
def test_detector_selfcheck_catches_all_corruptions():
    """检测器必须能检出两种坏结果，并正确放行原样保留。

    这三条替代原先的 test_offline_retention_is_100_percent：
    原实现拿 original 拼出 enhanced 再检它，判定恒为真，
    却以"核心约束保留率"的名义打印 100% 并计入发布判定。
    """
    samples = gate_check.load_regression_samples(REGRESSION)
    result = gate_check.run_detector_selfcheck(samples)
    assert result["samples"] == 20
    assert result["caught_empty_result"] == 20
    assert result["caught_removed_phrase"] == 20
    assert result["kept_intact"] == 20
    assert result["selfcheck_ok"] is True


def test_detector_selfcheck_fails_when_detector_is_blind():
    """反向对照：把 retention_ok 换成恒真，自检必须报失败。

    这条是本次修复的核心断言——它保证"检测器坏了"这件事不会再被漏掉。
    """
    samples = gate_check.load_regression_samples(REGRESSION)
    original_ok = gate_check.retention_ok
    try:
        gate_check.retention_ok = lambda o, e: True      # 模拟坏掉的检测器
        blind = gate_check.run_detector_selfcheck(samples)
        assert blind["selfcheck_ok"] is False
        assert blind["caught_empty_result"] == 0
    finally:
        gate_check.retention_ok = original_ok


def test_detector_selfcheck_rejects_empty_sample_set():
    """空样本集不能算自检通过（否则等于没检）。"""
    assert gate_check.run_detector_selfcheck([])["selfcheck_ok"] is False


def test_retention_ok_positive_and_negative():
    original = "帮我统计每月销售额。"
    assert gate_check.retention_ok(original, gate_check.simulate_enhanced(original, "standard"))
    assert gate_check.retention_ok(original, gate_check.simulate_enhanced(original, "concise"))
    assert not gate_check.retention_ok(original, "完全不同的增强结果")


def test_simulate_enhanced_modes():
    original = "原文内容"
    assert gate_check.simulate_enhanced(original, "concise") == original
    assert gate_check.simulate_enhanced(original, "standard") == original + gate_check.RETAIN_SUFFIX


def test_run_offline_regression_empty():
    assert gate_check.run_detector_selfcheck([])["samples"] == 0


# ---- check_regression：核心约束保留率 ----
def test_check_regression_constraint_retention_pass_at_100():
    summary = _synthetic_summary(by_pj={
        "编程": {"constraint_retention": {"n": 2, "mean": 10.0}},
        "写作": {"constraint_retention": {"n": 3, "mean": 10.0}},
    })
    gates = {g["name"]: g for g in gate_check.check_regression(summary)}
    g = gates["constraint_retention"]
    assert g["value"] == 1.0
    assert g["threshold"] == 1.0
    assert g["pass"] is True


def test_check_regression_constraint_retention_fail_below_100():
    summary = _synthetic_summary(by_pj={"编程": {"constraint_retention": {"n": 2, "mean": 9.0}}})
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["constraint_retention"]
    assert g["value"] == pytest.approx(0.9)
    assert g["pass"] is False


def test_check_regression_constraint_retention_skips_error_rows():
    summary = _synthetic_summary(by_pj={
        "编程": {"constraint_retention": {"n": 1, "mean": 10.0}},
        "写作": {"constraint_retention": {"error": "解析失败"}},
    })
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["constraint_retention"]
    assert g["value"] == 1.0
    assert g["pass"] is True


# ---- check_regression：清晰变差率 ----
def test_check_regression_worse_rate_pass_at_5_percent():
    summary = _synthetic_summary(worse_rate=0.05)
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["regression_worse_rate"]
    assert g["value"] == 0.05
    assert g["threshold"] == 0.05
    assert g["pass"] is True


def test_check_regression_worse_rate_fail_above_5_percent():
    summary = _synthetic_summary(worse_rate=0.10)
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["regression_worse_rate"]
    assert g["value"] == 0.10
    assert g["pass"] is False


def test_check_regression_worse_rate_falls_back_to_wins_losses():
    summary = _synthetic_summary(wins=98, losses=2)
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["regression_worse_rate"]
    assert g["value"] == pytest.approx(0.02)
    assert g["pass"] is True


def test_check_regression_exposes_worse_rate_metric_field():
    """变差率实值在离线下不可测，但门槛必须能从报告 summary 中读取该字段（P7 全量运行）。"""
    summary = _synthetic_summary(worse_rate=0.0)
    names = [g["name"] for g in gate_check.check_regression(summary)]
    assert "regression_worse_rate" in names
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["regression_worse_rate"]
    assert g["value"] == 0.0


# ---- check_regression：膨胀比 ----
def test_check_regression_pad_ratio_pass():
    summary = _synthetic_summary(pad_ratio=1.8)
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["pad_ratio"]
    assert g["value"] == 1.8
    assert g["threshold"] == 2.0
    assert g["pass"] is True


def test_check_regression_pad_ratio_fail():
    summary = _synthetic_summary(pad_ratio=2.5)
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["pad_ratio"]
    assert g["value"] == 2.5
    assert g["pass"] is False


def test_check_regression_pad_ratio_none_is_not_measurable():
    """P1-6：report.py 在无比值样本时写 `pad_ratio: None`。
    这个形态必须读成"测不到"（None，判 FAIL），而不是崩在 float(None) 上，
    更不能被当成 0.0（"膨胀完美"）。"""
    summary = _synthetic_summary()
    summary["cost"] = {"pad_ratio": None}
    g = {g["name"]: g for g in gate_check.check_regression(summary)}["pad_ratio"]
    assert g["value"] is None
    assert g["pass"] is False


# ---- 缺失数据 ----
def test_check_regression_missing_fields_report_not_measurable():
    gates = {g["name"]: g for g in gate_check.check_regression({})}
    assert set(gates) == {"constraint_retention", "regression_worse_rate", "pad_ratio"}
    for g in gates.values():
        assert g["value"] is None
        assert g["pass"] is False
