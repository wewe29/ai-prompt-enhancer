"""门禁可判定性模块的单元测试：统计功效计算与 PASS/FAIL 判定规则。

全部 0 次 API 调用。每条断言都配**反向对照**，确保断言不是恒真的
（把输入改坏，结论必须相反）。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_decidability import (  # noqa: E402
    ALPHA,
    POWER,
    decide_rate,
    derive_requirements,
    half_width,
    required_n_for_runs,
    required_n_two_proportions,
    required_runs,
    z,
)

SPEC_PATH = Path(__file__).resolve().parent / "gate_spec_v040.json"


# ---------------------------------------------------------------- z
def test_z_known_values():
    assert z(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert z(0.95) == pytest.approx(1.644854, abs=1e-4)
    assert z(0.80) == pytest.approx(0.841621, abs=1e-4)


# ------------------------------------------------- 样本量（二项）
def test_required_n_grows_as_gap_shrinks():
    """间距越小，需要的条数越多 —— 单调性。"""
    n_far = required_n_two_proportions(0.55, 0.30)
    n_near = required_n_two_proportions(0.33, 0.30)
    assert n_near > n_far * 10, "3 个百分点的间距必须比 25 个百分点的间距贵一个数量级"


def test_required_n_matches_hand_calculation():
    """G1：55% → 30%，n = [z·√(0.55·0.45) + z·√(0.30·0.70)]² / 0.25²。"""
    expect = ((z(0.95) * math.sqrt(0.55 * 0.45) + z(POWER) * math.sqrt(0.30 * 0.70)) ** 2) / 0.25 ** 2
    assert required_n_two_proportions(0.55, 0.30) == math.ceil(expect)


def test_required_n_rejects_identical_proportions():
    with pytest.raises(ValueError):
        required_n_two_proportions(0.30, 0.30)


# ------------------------------------------------- 轮次（运行间噪声）
def test_required_runs_impossible_when_binomial_exceeds_budget():
    """间距 3 点、τ=0.41 ⇒ 加轮次也补不回来 ⇒ None。"""
    assert required_runs(gap=0.03, tau=0.41, p_bar=0.315, n=1493, alpha=ALPHA) is None


def test_required_runs_finite_when_gap_is_large_enough():
    r = required_runs(gap=0.49, tau=0.41, p_bar=0.355, n=20, alpha=ALPHA)
    assert r is not None and r > 0


def test_required_n_for_runs_is_monotone_decreasing_in_runs():
    """轮次越多，所需的条数越少（且轮次不足时根本无解）。"""
    ns = {r: required_n_for_runs(0.25, 0.41, 0.425, r, ALPHA)
          for r in (2, 10, 100)}
    assert ns[100] is not None, "R=100 时必须可行"
    assert ns[100] < 200, "R=100 时所需条数应远小于 200"
    assert ns[2] is None or ns[2] > ns[100]


def test_required_n_for_runs_none_when_runs_insufficient():
    """τ 项单独就吃掉预算时，再多加条数也没用。"""
    assert required_n_for_runs(0.03, 0.41, 0.315, 1, ALPHA) is None


# ------------------------------------------------- 区间与判定
def test_half_width_shrinks_with_n_and_runs():
    h0 = half_width(0.3, 20, 0.41, 1)
    h1 = half_width(0.3, 200, 0.41, 1)
    h2 = half_width(0.3, 20, 0.41, 10)
    assert h1 < h0
    assert h2 < h0


def test_decide_missing_value_is_fail():
    d = decide_rate(None, 100, 0.30, "<=", tau=0.41, runs=3)
    assert d["pass"] is False
    assert d["value"] == "缺失"


def test_decide_missing_n_is_fail():
    d = decide_rate(0.2, None, 0.30, "<=", tau=0.41, runs=3)
    assert d["pass"] is False
    assert "n 缺失" in d["value"]


def test_decide_missing_runs_is_fail():
    """单次运行的结果不可进结论 ⇒ FAIL。"""
    d = decide_rate(0.2, 100, 0.30, "<=", tau=0.41, runs=None)
    assert d["pass"] is False
    assert "轮次" in d["value"]


def test_decide_confidently_below_threshold_passes():
    """τ=0.41 时运行间噪声是主导项 ⇒ 需要足够多的独立轮次才可能证成达标。"""
    assert decide_rate(0.10, 200, 0.30, "<=", tau=0.41, runs=10)["pass"] is False
    d = decide_rate(0.10, 200, 0.30, "<=", tau=0.41, runs=20)
    assert d["pass"] is True
    assert "上界" in d["reason"]


def test_decide_confidently_above_threshold_fails():
    d = decide_rate(0.50, 200, 0.30, "<=", tau=0.41, runs=30)
    assert d["pass"] is False
    assert "下界" in d["reason"]


def test_decide_straddling_interval_is_fail_not_pass():
    """区间跨越阈值 ⇒ FAIL。这是 fail-closed 的关键：**没有第三种输出**。"""
    d = decide_rate(0.28, 20, 0.30, "<=", tau=0.41, runs=1)
    assert d["pass"] is False
    assert "跨越阈值" in d["reason"]


def test_decide_greater_than_direction():
    """direction='>=' 时方向相反。"""
    ok = decide_rate(0.90, 200, 0.85, ">=", tau=0.0, runs=2)
    assert ok["pass"] is True
    bad = decide_rate(0.60, 200, 0.85, ">=", tau=0.0, runs=2)
    assert bad["pass"] is False


def test_decide_rejects_bad_direction():
    with pytest.raises(ValueError):
        decide_rate(0.5, 10, 0.3, "==", tau=0.0, runs=1)


# ------------------------------------------------- 规范推导
def test_derive_requirements_matches_committed_spec():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in derive_requirements(spec)}
    assert set(rows) == {g["id"] for g in spec["gates"]}
    # 作废的三条必须被标成不可判定，且给出量化理由
    for gid in ("G1", "G2", "G3"):
        assert rows[gid]["status"] == "void"
        assert rows[gid]["decidable"] is False
        assert rows[gid]["required_runs"] is None
    # G1 即使 n→∞ 也要 42 轮独立运行
    assert rows["G1"]["required_runs_asymptotic"] == 42
    # G2 需要 2870 轮 —— 实务上不可行
    assert rows["G2"]["required_runs_asymptotic"] >= 1000
    # G4/G5 是 active 且可判定
    assert rows["G4"]["status"] == "active" and rows["G4"]["decidable"] is True
    assert rows["G5"]["status"] == "active" and rows["G5"]["decidable"] is True
    # G5 的区分样本量必须明显大于 G4（间距更小）
    assert rows["G5"]["required_n"] > rows["G4"]["required_n"]


def test_derive_requirements_is_deterministic():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    a = derive_requirements(spec)
    b = derive_requirements(spec)
    assert a == b, "推导必须可复现（规范里不能有手写数字漂移）"


def test_committed_spec_has_no_hand_written_requirements():
    """规范文件只存事实；required_n / required_runs 必须由代码推导。"""
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    for g in spec["gates"]:
        assert "required_n" not in g, f"{g['id']} 手写了 required_n"
        assert "required_runs" not in g, f"{g['id']} 手写了 required_runs"
