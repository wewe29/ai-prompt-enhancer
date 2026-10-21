"""v0.4.2 可达性核算工具的行为级测试（`evaluation/v042_reachability.py`）。

这些断言全部喂**构造出来的记录**，不联网、不花钱。
重点是三件事：

1. **分母口径不能错**：G4/G5 的 n 是**问题条数**，G6 的 n 是**标注样本数**。
2. **未标注样本不得进入 G6 的分母**（否则门禁会造出虚假绿灯）。
3. **外推是外推**：新增样本数按实测「每单元问题数」算，且必须标明不保证。

全部离线。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v042_reachability as rc  # noqa: E402


def _rec(
    rid: str,
    level: str = "severe",
    scenario: str = "编程",
    questions: list[str] | None = None,
    must_not_question: list[str] | None = None,
    error: str = "",
) -> dict:
    return {
        "id": rid,
        "ambiguity_level": level,
        "scenario": scenario,
        "original": "原始文本",
        "questions": [{"text": q} for q in (questions or [])],
        "must_not_question": must_not_question or [],
        "error": error,
    }


# =====================================================================
# 实测规模
# =====================================================================
def test_counts_questions_not_samples_for_g4_g5():
    """G4/G5 的分母是**问题条数**：3 条各 3 问 = 9 个问题，不是 3。"""
    records = [
        _rec("a", questions=["q1", "q2", "q3"]),
        _rec("b", questions=["q1", "q2", "q3"]),
        _rec("c", questions=["q1", "q2", "q3"]),
    ]
    m = rc.measure(records)
    assert m["severe_units_with_questions"] == 3
    assert m["severe_questions"] == 9
    assert m["questions_per_unit"] == 3.0


def test_severe_units_without_questions_are_counted_separately():
    """未转澄清的 severe 是**独立观察值**（自检规则生效的证据），不能混进分母。"""
    records = [
        _rec("a", questions=["q1", "q2", "q3"]),
        _rec("b", questions=[]),          # 判了 none/light，没转澄清
        _rec("c", questions=[]),
    ]
    m = rc.measure(records)
    assert m["severe_total"] == 3
    assert m["severe_units_with_questions"] == 1
    assert m["severe_units_without_questions"] == 2
    assert m["severe_questions"] == 3


def test_translate_questions_counted_separately():
    """G4 只看翻译场景的问题数。"""
    records = [
        _rec("t1", scenario="翻译", questions=["q1", "q2", "q3"]),
        _rec("t2", scenario="翻译", questions=["q1", "q2", "q3"]),
        _rec("p1", scenario="编程", questions=["q1", "q2", "q3"]),
    ]
    m = rc.measure(records)
    assert m["translate_units_with_questions"] == 2
    assert m["translate_questions"] == 6
    assert m["severe_questions"] == 9


def test_error_records_excluded():
    """出错的样本没有可评内容，不得进分母。"""
    records = [
        _rec("a", questions=["q1", "q2", "q3"]),
        _rec("b", questions=["q1"], error="HTTP 500: boom"),
    ]
    m = rc.measure(records)
    assert m["error_count"] == 1
    assert m["judged_count"] == 1
    assert m["severe_questions"] == 3


# =====================================================================
# G6 的分母：只有**有标注**的样本才算
# =====================================================================
def test_g6_denominator_counts_only_labeled_samples():
    """未标注样本**不进 G6 的分母** —— 这是防假绿灯的关键。"""
    records = [
        _rec("a", questions=["q1"], must_not_question=["原文是什么"]),
        _rec("b", questions=["q1"], must_not_question=["代码在哪里"]),
        _rec("c", questions=["q1"]),                       # 无标注
        _rec("d", questions=["q1"], must_not_question=[]),  # 空列表 = 无标注
    ]
    m = rc.measure(records)
    assert m["labeled_samples"] == 2


def test_g6_upper_bound_is_three_over_n():
    """0 命中的 95% 率上界 ≈ 3/n。n=8 ⇒ 37.5%。"""
    records = [
        _rec(f"s{i}", questions=["q1"], must_not_question=["原文是什么"])
        for i in range(8)
    ]
    report = rc.build_report(records, None)
    g6 = next(r for r in report["gates"] if r["gate"] == "G6")
    assert g6["measured_n"] == 8
    assert g6["zero_event_upper_bound"] == pytest.approx(0.375)
    assert g6["reachable"] is False, "n=8 < 61 ⇒ G6 在现有标注量下不可认证"


def test_g6_hit_count_uses_the_judge_not_a_reimplementation():
    """命中数复用 verify_enhance 的判定函数，不另写一份匹配逻辑。"""
    hit = _rec("a", questions=["要翻译的原文是什么？"], must_not_question=["原文是什么"])
    miss = _rec("b", questions=["翻译成哪种语言？"], must_not_question=["原文是什么"])
    m = rc.measure([hit, miss])
    assert m["labeled_samples"] == 2
    assert m["redundant_hits"] == 1


# =====================================================================
# 门槛对账
# =====================================================================
def test_gate_table_uses_required_n_from_derivation():
    """要求的 n 必须来自现场推导，不能手写。"""
    records = [_rec(f"s{i}", questions=["q1", "q2", "q3"]) for i in range(20)]
    report = rc.build_report(records, None)
    g5 = next(r for r in report["gates"] if r["gate"] == "G5")
    assert g5["required_n"] == 68
    assert g5["required_runs"] == 2
    assert g5["n_unit"] == "questions"
    assert g5["measured_n"] == 60
    assert g5["reachable"] is False
    assert g5["gap"] == -8


def test_g5_extrapolation_of_extra_samples():
    """按实测「每单元问题数」外推还需要多少 severe 样本。"""
    records = [_rec(f"s{i}", questions=["q1", "q2", "q3"]) for i in range(20)]
    report = rc.build_report(records, None)
    # 68 个问题 / 每单元 3 个 = 23 条 ⇒ 还差 3 条
    assert report["g5_extra_severe_samples_needed"] == 3


def test_extrapolation_returns_none_without_units():
    """一条都没转澄清 ⇒ 无法外推（不能给出一个假装精确的数字）。"""
    report = rc.build_report([_rec("a", questions=[])], None)
    assert report["g5_extra_severe_samples_needed"] is None


# =====================================================================
# 输入兼容
# =====================================================================
def test_load_records_accepts_three_shapes(tmp_path):
    """三种产物结构都要能读（旧产物、stability 产物、当前产物）。"""
    rec = _rec("a", questions=["q1"])
    current = tmp_path / "cur.json"
    current.write_text(
        '{"_identity": {"fields": {"prompt_version": "promptcraft-v2.5.0"}}, "records": [%s]}'
        % ('{"id": "a", "ambiguity_level": "severe", "scenario": "编程", "questions": [{"text": "q1"}], "must_not_question": [], "error": ""}'),
        encoding="utf-8",
    )
    old = tmp_path / "old.json"
    old.write_text(
        '[{"id": "a", "ambiguity_level": "severe", "scenario": "编程", "questions": [{"text": "q1"}], "must_not_question": [], "error": ""}]',
        encoding="utf-8",
    )
    stability = tmp_path / "stab.json"
    stability.write_text(
        '{"records": [{"id": "a", "ambiguity_level": "severe", "scenario": "编程", "questions": [{"text": "q1"}], "must_not_question": [], "error": ""}]}',
        encoding="utf-8",
    )

    for path in (current, old, stability):
        records, _ = rc.load_records(path)
        assert len(records) == 1, path.name
        assert records[0]["id"] == "a"

    _, identity = rc.load_records(current)
    assert identity["fields"]["prompt_version"] == "promptcraft-v2.5.0"


def test_missing_file_is_a_clear_error(tmp_path, capsys):
    assert rc.main(["--raw", str(tmp_path / "nope.json")]) == 2
