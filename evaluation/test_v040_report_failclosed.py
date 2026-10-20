"""v0.4.0 Phase B · 失败伪装清单 A 家族的行为级回归测试。

覆盖 A4 / A5 / A6 / A7 / A8 / A9 —— 断言的是**行为**（缺数据时产物里到底是什么），
不是"某行代码不见了"。

为什么要有这层测试：门禁脚本 `scripts/check-fail-open.py` 是**子串探针**，
只能挡住"原地不改"的缺陷；换了个写法、bug 还在的缺陷它挡不住。
这层测试挡的正是后者。全部离线，不调用任何 API。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_gate  # noqa: E402
import report as report_mod  # noqa: E402

DIMS = ("accuracy", "completeness", "relevance", "clarity")


def _judge(deltas, winner="enhanced"):
    return {
        "original": {d: 0 for d in DIMS},
        "enhanced": {d: 0 for d in DIMS},
        "deltas": deltas,
        "winner": winner,
        "reason": "x",
    }


def _sample(sample_id, *, judge=None, enhanced=None, original="orig"):
    """构造一个样本。enhanced=None 表示**没有** enhanced 字段（缺结果）。"""
    sample = {
        "id": sample_id,
        "scenario": "A",
        "original": original,
        "enhanced_text": "enh",
        "results": {"t1": {"judge": judge}},
    }
    if enhanced is not None:
        sample["enhanced"] = enhanced
    return sample


def _payload(samples):
    return {"meta": {"targets": ["t1"]}, "samples": samples}


# ---------------------------------------------------------------- A4 交付状态

def test_unknown_delivery_status_does_not_count_as_complete():
    """A4：拼写异常/新增状态必须进 unknown 桶，不得归 complete。"""
    payload = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complate"}),
        _sample("s2", enhanced={"primary_prompt": "p", "delivery_status": "complete"}),
    ])
    summary = report_mod._aggregate(payload)["summary"]
    assert summary["delivery"]["unknown"] == 1
    assert summary["delivery"]["complete"] == 1
    assert summary["delivery_rate"] is None, "存在未知交付状态时，有效交付率不可判定"


def test_missing_delivery_status_is_unknown_not_complete():
    """A4 + R1：缺状态是缺数据，不是「完整交付」。"""
    payload = _payload([
        _sample("s1", enhanced={"primary_prompt": "p"}),
    ])
    summary = report_mod._aggregate(payload)["summary"]
    assert summary["delivery"]["unknown"] == 1
    assert summary["delivery"]["complete"] == 0
    assert summary["delivery_rate"] is None


def test_markdown_shows_unknown_bucket_and_undeterminable_rate(tmp_path):
    """A4：报告里必须看得见 unknown 桶，且有效率写「不可判定」。"""
    payload = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complate"}),
    ])
    report_mod.generate(tmp_path, payload)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "状态未知/缺失（unknown）" in md
    assert "不可判定" in md


# ---------------------------------------------------------------- A5 交付分母

def test_sample_without_result_still_counts_in_delivery_denominator():
    """A5：失败样本不得退出分母 —— 否则「失败越多、比率越好看」。"""
    payload = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complete"}),
        _sample("s2", enhanced={"error": "增强服务返回空结果"}),
        _sample("s3"),  # 完全没有 enhanced 字段
    ])
    summary = report_mod._aggregate(payload)["summary"]
    assert summary["delivery"]["hard_failure"] == 2
    assert summary["delivery"]["complete"] == 1
    assert summary["delivery_rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_delivery_rate_gets_worse_when_a_failure_is_added():
    """A5 的后果方向：分母固定 ⇒ 加一个失败样本，有效率必须下降。"""
    base = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complete"}),
        _sample("s2", enhanced={"primary_prompt": "p", "delivery_status": "complete"}),
    ])
    with_failure = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complete"}),
        _sample("s2", enhanced={"error": "boom"}),
    ])
    rate_base = report_mod._aggregate(base)["summary"]["delivery_rate"]
    rate_fail = report_mod._aggregate(with_failure)["summary"]["delivery_rate"]
    assert rate_base == 1.0
    assert rate_fail == 0.5, "失败样本必须拉低有效率，而不是退出分母"


# ---------------------------------------------------------------- A6 比较分母

def test_judge_failed_sample_stays_in_the_denominator():
    """A6：裁判失败不再静默出分母 —— 它留在分母里，只是没有胜负。"""
    payload = _payload([
        _sample("s1", judge=_judge({d: 4 for d in DIMS})),
        _sample("s2", judge=None),
    ])
    summary = report_mod._aggregate(payload)["summary"]
    assert summary["total"] == 2, "进入比较的样本总数必须包含裁判失败的那条"
    assert summary["comparison"]["judged_total"] == 1
    assert summary["comparison"]["excluded"]["judge_failed"] == 1
    assert summary["wins"] == 1
    assert summary["improved_pct"] == pytest.approx(50.0), (
        "比率的分母必须是进入比较的样本总数（2），而不是存活子集（1）"
    )


def test_more_judge_failures_make_rate_worse_not_better():
    """A6 的后果方向：裁判失败越多，比率越难看。"""
    judged_only = _payload([
        _sample("s1", judge=_judge({d: 4 for d in DIMS})),
    ])
    with_failure = _payload([
        _sample("s1", judge=_judge({d: 4 for d in DIMS})),
        _sample("s2", judge=None),
    ])
    rate_clean = report_mod._aggregate(judged_only)["summary"]["improved_pct"]
    rate_dirty = report_mod._aggregate(with_failure)["summary"]["improved_pct"]
    assert rate_clean == 100.0
    assert rate_dirty < rate_clean, "裁判失败不得让比率变好看"


# ---------------------------------------------------------------- A7 幅度分类

def test_missing_deltas_does_not_enter_magnitude_buckets():
    """A7：裁判给了胜负但没给维度分 ⇒ 不进幅度分类（0.0 会被归进 minor）。"""
    payload = _payload([
        _sample("s1", judge=_judge({}, winner="enhanced")),
        _sample("s2", judge=_judge({d: 4 for d in DIMS}, winner="enhanced")),
    ])
    agg = report_mod._aggregate(payload)
    magnitude = agg["by_magnitude"]
    assert magnitude["improve"]["minor"] == 0, "缺维度分的样本不得被当成「改动很小」"
    assert magnitude["improve"]["major"] == 1
    assert agg["summary"]["comparison"]["missing_deltas"] == 1, "缺口必须留痕（R2）"


# ---------------------------------------------------------------- A8 零样本

def test_zero_sample_ratios_are_none_not_zero():
    """A8：0 个样本与「比率真的是 0」必须可区分。"""
    summary = report_mod._aggregate({"meta": {}, "samples": []})["summary"]
    assert summary["improved_pct"] is None
    assert summary["regression_failure_rate"] is None
    assert summary["regression_worse_rate"] is None
    assert summary["delivery_rate"] is None


def test_zero_sample_markdown_says_undeterminable(tmp_path):
    """A8：报告里不得把缺数据渲染成 0.0%。"""
    report_mod.generate(tmp_path, {"meta": {}, "samples": []})
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "不可判定" in md
    assert "0.0%" not in md, "缺数据不得渲染成 0.0%（读者会读成「测过了，结果是 0」）"


def test_report_gate_blocks_when_delivery_rate_is_undeterminable():
    """A4 / A8：交付率不可判定时，报告自带的前置门槛必须阻断。"""
    payload = _payload([
        _sample("s1", enhanced={"primary_prompt": "p", "delivery_status": "complate"}),
    ])
    summary = report_mod._aggregate(payload)["summary"]
    assert summary["gate"]["pass"] is False
    assert any("不可判定" in reason for reason in summary["gate"]["reasons"])


# ---------------------------------------------------------------- A9 维度相关性

def test_unmeasured_dimension_is_absent_from_dim_corr():
    """A9 + C4：未测量的维度不得出现在 dim_corr 里；逐维度相关性必须**真算**。

    dim_corr 的来源已从「把 agreement 块里的全局 ρ 抄成 5 份」改成
    **跨多条对比、逐维度独立计算**（judge_agreement_across）。所以：
      * 样本对不足 2 的维度 ⇒ 不可判定，**不出现在** dim_corr 里（也不是 ρ=0）；
      * 样本对够的维度 ⇒ 出现，且是它自己的 ρ。
    """
    # s1 有 4 个维度；s2 缺 relevance ⇒ relevance 只有 1 对样本
    payload = _payload([
        _sample("s1", judge=_judge({"accuracy": 1, "completeness": 2, "clarity": 3, "relevance": 4})),
        _sample("s2", judge=_judge({"accuracy": 2, "completeness": 4, "clarity": 6})),
    ])
    for sample in payload["samples"]:
        result = sample["results"]["t1"]
        result["judge2"] = _judge(result["judge"]["deltas"])
        result["agreement"] = {
            "winner_agreement": True,
            "dim_corr": {},
            "delta_rho": 1.0,
            "rho_n_dims": 4,
        }
    summary = report_mod._aggregate(payload)["summary"]
    dim_corr = summary["judge_agreement"]["dim_corr"]
    # 有 2 对样本的维度：出现，且是它自己的 ρ（两个裁判完全一致 ⇒ ρ=1）
    assert dim_corr.get("accuracy") == pytest.approx(1.0)
    assert dim_corr.get("completeness") == pytest.approx(1.0)
    # 只有 1 对样本的维度：不可判定，不得出现在结果里
    assert "relevance" not in dim_corr, "样本对不足的维度不得出现在 dim_corr 里"


def test_markdown_marks_unmeasured_dimension(tmp_path):
    """A9：报告里未测量的维度写「未测量」，不得渲染成 ρ=0.00。"""
    payload = _payload([
        _sample("s1", judge=_judge({d: 4 for d in DIMS})),
    ])
    payload["samples"][0]["results"]["t1"]["agreement"] = {
        "winner_agreement": True,
        "dim_corr": {"accuracy": 0.8},
    }
    report_mod.generate(tmp_path, payload)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "未测量" in md
    assert "ρ=0.00" not in md


# ---------------------------------------------------------------- 下游门槛 fail-closed

def test_release_gate_fails_when_delivery_rate_is_undeterminable():
    """A4 / A8：有效交付率不可判定时，发布门槛必须 FAIL（不得回落成 0.0）。"""
    summary = {
        "delivery_rate": None,
        "gate": {"pass": True, "reasons": []},
        "control": {},
        "by_ambiguity": {},
        "cost": {"pad_ratio": 1.0},
        "judge_agreement": {"winner_agreement": 0.9},
        "comparison": {"expected_total": 10, "attempted_total": 10, "judged_total": 10},
    }
    gates = {g["name"]: g for g in release_gate.compute_gates(summary)}
    gate = gates["总有效率 ≥ 95%"]
    assert gate["pass"] is False
    assert "不可判定" in gate["value"]


def test_release_gate_fails_when_delivery_rate_key_is_absent():
    """A4 / A8 + R1：键整个缺失同样是不可判定，不是 0%。"""
    summary = {
        "gate": {"pass": True, "reasons": []},
        "control": {},
        "by_ambiguity": {},
        "cost": {"pad_ratio": 1.0},
        "judge_agreement": {"winner_agreement": 0.9},
        "comparison": {"expected_total": 10, "attempted_total": 10, "judged_total": 10},
    }
    gates = {g["name"]: g for g in release_gate.compute_gates(summary)}
    gate = gates["总有效率 ≥ 95%"]
    assert gate["pass"] is False
    assert "不可判定" in gate["value"]
