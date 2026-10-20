"""生成 v0.4.0 门禁的 committed fixture。

**这些 fixture 是 report.py 真实聚合出来的 summary，不是手搓的假数据。**
生成方式：构造原始 payload → 调 `report._aggregate()` → 落盘。

用法:
    python evaluation/_make_v040_fixtures.py

产物（全部提交入库，release_gate.py 直接消费）:
    docs/evaluation-data-v040/fixtures/summary_pass.json
    docs/evaluation-data-v040/fixtures/summary_missing_cost.json
    docs/evaluation-data-v040/fixtures/summary_denominator_lost.json
    docs/evaluation-data-v040/fixtures/summary_single_run.json
    docs/evaluation-data-v040/fixtures/summary_v040_gates_missing.json

本脚本 0 次 API 调用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import report as report_mod  # noqa: E402

FIXTURES = ROOT / "docs" / "evaluation-data-v040" / "fixtures"

DIMS = ("accuracy", "completeness", "relevance", "clarity")


def _judge(deltas, winner="enhanced"):
    return {
        "original": {d: 0 for d in DIMS},
        "enhanced": {d: 2 for d in DIMS},
        "deltas": deltas,
        "winner": winner,
        "reason": "fixture",
    }


def _sample(sid, scenario, ambiguity, orig_len, enh_len, *, judge=True,
            control=False, agree=True, ctrl_delta=0.0):
    s = {
        "id": sid,
        "scenario": scenario,
        "ambiguity_level": ambiguity,
        "original": "o" * orig_len,
        "enhanced_text": "e" * enh_len,
        "enhanced": {"primary_prompt": "e" * enh_len, "delivery_status": "complete"},
        "results": {},
    }
    r = {
        "original_output": "o" * orig_len,
        "enhanced_output": "e" * enh_len,
    }
    if judge:
        r["judge"] = _judge({d: 2 for d in DIMS})
    if control:
        # C−B（增强 vs 填充）必须有正增益，否则 C-B 质量提升门槛必然 FAIL
        r["judge_control"] = _judge({d: ctrl_delta for d in DIMS}, winner="tie")
        r["judge_padded_vs_orig"] = _judge({d: 2 for d in DIMS})
    if agree:
        r["agreement"] = {"winner_agreement": True, "dim_corr": {d: 0.5 for d in DIMS}}
    s["results"]["t1"] = r
    return s


def _payload(n_samples=60, *, judge_all=True, control=True, orig_len=100):
    samples = []
    scenarios = ("编程", "办公写作", "问答", "数据分析", "翻译", "创意")
    for i in range(n_samples):
        amb = ("clear", "medium", "severe")[i % 3]
        samples.append(_sample(
            f"s{i+1:02d}", scenarios[i % len(scenarios)], amb,
            orig_len, orig_len * 2,
            judge=judge_all or (i % 4 != 0),
            control=control,
            ctrl_delta=2.0,
        ))
    return {
        "meta": {
            "generated_at": "2026-10-20T00:00:00",
            "targets": ["t1"],
            "judge_model": "fixture-judge",
            "enhance_model": "fixture-enhancer",
            "estimated_cost": 0.0,
            "human_review_count": n_samples,
        },
        "samples": samples,
    }


def _v040_gates(flip_clear=0.22, translate=0.75, severe=0.95, runs=2):
    """v0.4.0 效果门槛的测量值（喂给 release_gate 的 v040_gates 段）。

    n 取值满足规范推导出的要求：
      G4 需 n≥5（区分 11% 与 60%），R≥2
      G5 需 n≥68（区分 73% 与 85%），R≥2；且 CI 半宽要小到能证成 ≥85%
      G6 需 n≥61（0 命中的 95% 率上界 ≤5%）
      G7 需 n≥61（实测 360 次运行）
    """
    return {
        "flip_rate_clear": {"value": flip_clear, "n": 40, "runs": runs},
        "flip_rate_medium": {"value": 0.15, "n": 40, "runs": runs},
        "light_rate_clear": {"value": 0.55, "n": 40, "runs": runs},
        "blind_hit_rate_translate": {"value": translate, "n": 27, "runs": runs},
        "blind_hit_rate_severe": {"value": severe, "n": 68, "runs": runs},
        "redundant_question_count": {"value": 0, "n": 61, "runs": 1},
        "delivery_drift_count": {"value": 0, "n": 360, "runs": 1},
    }


def _write(name, agg, v040_gates=None):
    payload = dict(agg)
    if v040_gates is not None:
        payload["v040_gates"] = v040_gates
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fixture] {path.relative_to(ROOT)}  ({path.stat().st_size} bytes)")


def main() -> int:
    # 1) 全部通过：所有比较都判了、膨胀比有样本、效果门槛达标
    agg = report_mod._aggregate(_payload())
    _write("summary_pass.json", agg, _v040_gates())

    # 2) 缺膨胀比样本：原文长度全为空 ⇒ cost_ratios 为空 ⇒ pad_ratio=None
    p = _payload()
    for s in p["samples"]:
        s["original"] = ""
    agg2 = report_mod._aggregate(p)
    _write("summary_missing_cost.json", agg2, _v040_gates())

    # 3) 分母流失：1/4 的样本没有 judge 结果
    p3 = _payload(judge_all=False)
    agg3 = report_mod._aggregate(p3)
    _write("summary_denominator_lost.json", agg3, _v040_gates())

    # 4) 单次运行的效果门槛：runs=1 ⇒ 低于 min_runs ⇒ 必须 FAIL
    agg4 = report_mod._aggregate(_payload())
    _write("summary_single_run.json", agg4, _v040_gates(runs=1))

    # 5) 完全没有 v0.4.0 效果门槛测量数据 ⇒ 全部 active 门槛 FAIL（缺数据 fail-closed）
    agg5 = report_mod._aggregate(_payload())
    _write("summary_v040_gates_missing.json", agg5, None)

    print("[fixture] 全部由 report._aggregate() 真实产出，0 次 API 调用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
