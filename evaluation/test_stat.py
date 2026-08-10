"""T6/T7 测试：Wilson CI、二项检验、裁判一致性（Spearman）与 review 导出。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import judge as judge_mod  # noqa: E402
import report as report_mod  # noqa: E402
import review_export  # noqa: E402

DIMS = ("accuracy", "completeness", "relevance", "clarity")


def test_wilson_ci_zero_k_low_is_zero():
    low, high = report_mod.wilson_ci(0, 10)
    assert low == 0.0
    assert high > 0.0


def test_wilson_ci_sanity():
    low, high = report_mod.wilson_ci(5, 10)
    assert low == pytest.approx(0.237, abs=0.05)
    assert high == pytest.approx(0.763, abs=0.05)


def test_wilson_ci_monotonic_with_k():
    lo1, _ = report_mod.wilson_ci(2, 10)
    lo2, _ = report_mod.wilson_ci(5, 10)
    assert lo2 > lo1


def test_binomial_one_sided_zero_wins():
    assert report_mod.binomial_one_sided(0, 10) == pytest.approx(2 ** -10, abs=1e-9)


def test_binomial_one_sided_sanity():
    p = report_mod.binomial_one_sided(6, 4)
    assert 0.2 < p < 0.6
    assert 0.0 < report_mod.binomial_one_sided(10, 0) < 1.0


def _judge_result(deltas, winner="enhanced"):
    return {
        "original": {d: 0 for d in DIMS},
        "enhanced": {d: 0 for d in DIMS},
        "deltas": deltas,
        "winner": winner,
        "reason": "x",
    }


def test_judge_agreement_identical_inputs():
    a = _judge_result({"accuracy": 1, "completeness": 2, "relevance": 3, "clarity": 4})
    ag = judge_mod.judge_agreement(a, a)
    assert ag["winner_agreement"] is True
    assert all(v == 1.0 for v in ag["dim_corr"].values())


def test_judge_agreement_winner_disagreement():
    a = _judge_result({"accuracy": 1, "completeness": 2, "relevance": 3, "clarity": 4}, winner="enhanced")
    b = _judge_result({"accuracy": 1, "completeness": 2, "relevance": 3, "clarity": 4}, winner="original")
    ag = judge_mod.judge_agreement(a, b)
    assert ag["winner_agreement"] is False


def test_spearman_monotonic_perfect():
    assert judge_mod._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert judge_mod._spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0


def test_spearman_tie_handling():
    rho = judge_mod._spearman([1, 1, 2, 3], [5, 5, 6, 7])
    assert -1.0 <= rho <= 1.0


def _payload():
    return {
        "meta": {
            "judge_model": "judge-m",
            "targets": ["t1"],
            "enhance_model": "enh-m",
            "estimated_cost": 0.0,
        },
        "samples": [
            {
                "id": "s1", "scenario": "A", "original": "p1", "enhanced_text": "p1e",
                "results": {"t1": {
                    "original_output": "o1", "enhanced_output": "e1",
                    "judge": _judge_result({"accuracy": 2, "completeness": 2, "relevance": 2, "clarity": 2}),
                }},
            },
            {
                "id": "s2", "scenario": "B", "original": "p2", "enhanced_text": "p2e",
                "results": {"t1": {
                    "original_output": "o2", "enhanced_output": "e2",
                    "judge": _judge_result(
                        {"accuracy": -1, "completeness": 0, "relevance": 1, "clarity": 2}, winner="tie"
                    ),
                }},
            },
        ],
    }


def _write_payload(tmp_path, name="results"):
    results_dir = tmp_path / name
    results_dir.mkdir()
    (results_dir / "samples.json").write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    return results_dir


def test_review_export_writes_expected_structure(tmp_path):
    results_dir = _write_payload(tmp_path)
    out = tmp_path / "review_set.json"

    review_export.export_cmd(results_dir, out, ratio=1.0, seed=42)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2
    item = next(i for i in data if i["sample_id"] == "s1")
    assert set(item) == {"sample_id", "target", "original_answer", "enhanced_answer", "judge"}
    assert item["sample_id"] == "s1" and item["target"] == "t1"
    assert item["original_answer"] == "o1" and item["enhanced_answer"] == "e1"
    assert item["judge"]["winner"] == "enhanced"
    assert set(item["judge"]["dims"]) == set(DIMS)


def test_review_export_ratio_sampling(tmp_path):
    results_dir = _write_payload(tmp_path, "results2")
    out = tmp_path / "review_set50.json"

    review_export.export_cmd(results_dir, out, ratio=0.5, seed=1)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 1


def test_review_export_compare(tmp_path):
    review = [{
        "sample_id": "s1", "target": "t1", "original_answer": "o1", "enhanced_answer": "e1",
        "judge": {
            "dims": {"accuracy": 2, "completeness": 2, "relevance": 2, "clarity": 2},
            "winner": "enhanced", "reason": "",
        },
    }]
    human = [{
        "sample_id": "s1", "target": "t1", "winner": "enhanced",
        "dims": {"accuracy": 2, "completeness": 3, "relevance": 2, "clarity": 1},
    }]
    review_path = tmp_path / "review.json"
    human_path = tmp_path / "human.json"
    out = tmp_path / "agreement.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    human_path.write_text(json.dumps(human, ensure_ascii=False), encoding="utf-8")

    summary = review_export.compare_cmd(review_path, human_path, out)

    assert summary["matched"] == 1
    assert summary["winner_agreement_rate"] == 1.0
    assert summary["per_dim_mean_abs_diff"]["completeness"] == pytest.approx(1.0)
    assert summary["per_dim_mean_abs_diff"]["accuracy"] == pytest.approx(0.0)
    assert json.loads(out.read_text(encoding="utf-8")) == summary


def test_aggregate_judge_agreement_and_stats():
    payload = _payload()
    judge = payload["samples"][0]["results"]["t1"]["judge"]
    payload["samples"][0]["results"]["t1"]["agreement"] = judge_mod.judge_agreement(judge, judge)
    payload["samples"][0]["results"]["t1"]["judge2"] = judge

    agg = report_mod._aggregate(payload)

    ja = agg["summary"]["judge_agreement"]
    assert ja["n"] == 1
    assert ja["winner_agreement"] == 1.0
    assert set(ja["dim_corr"]) == set(DIMS)

    s = agg["summary"]
    assert s["win_rate_ci"][0] <= s["win_rate_ci"][1]
    assert 0.0 <= s["win_rate_ci"][0] <= 1.0
    assert 0.0 < s["binomial_p"] <= 1.0


def test_aggregate_no_agreement_skips_field():
    agg = report_mod._aggregate(_payload())
    assert "judge_agreement" not in agg["summary"]


def test_render_markdown_contains_ci_and_agreement(tmp_path):
    payload = _payload()
    judge = payload["samples"][0]["results"]["t1"]["judge"]
    payload["samples"][0]["results"]["t1"]["agreement"] = judge_mod.judge_agreement(judge, judge)
    report_mod.generate(tmp_path, payload)

    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "95% CI:" in md
    assert "p=" in md
    assert "裁判一致性" in md
    assert (tmp_path / "samples.json").exists()
