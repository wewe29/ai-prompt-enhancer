"""v0.3.2 人工盲评「揭盲映射」回归单测（离线、确定性、无真实 API）。

覆盖 v0.4.0 定位到的 P1-10 同族缺陷：v032_blind_review.cmd_compare 曾把人工胜者
转成**小写**（.lower()）却与**大写**的 enhanced_label 直接比较，"a" == "A" 恒为 False，
于是任何非平局人工判断都被记成 "original"（48 条实测里 enhanced 出现 0 次）。

本测试文件用「人工与裁判结论完全一致」的构造数据钉死这个不变量：
只要人的判断和裁判一致，一致率必须是 1.0、方向一致性必须是 True。
旧实现在这组数据上会得到 0.0 / False，因此本测试对旧实现必然失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v032_blind_review as blind  # noqa: E402

DIMS = blind.DIMS


def _key(item: str, *, enhanced_label: str, judge_winner: str = "enhanced",
        judge_deltas: dict | None = None) -> dict:
    return {
        "item": item,
        "sample_id": f"s-{item}",
        "target": "t1",
        "difficulty": "clear",
        "enhanced_label": enhanced_label,
        "judge_winner": judge_winner,
        "judge_deltas": judge_deltas or {d: 0.0 for d in DIMS},
    }


def _answer(item: str, winner: str, *, dims_a=None, dims_b=None) -> dict:
    return {
        "item": item,
        "winner": winner,
        "dims_a": dims_a if dims_a is not None else {d: 3 for d in DIMS},
        "dims_b": dims_b if dims_b is not None else {d: 3 for d in DIMS},
        "reason": "test",
    }


def _run_compare(tmp_path: Path, keys: list[dict], answers: list[dict]) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key_path = tmp_path / "blind_key.json"
    review_path = tmp_path / "blind_answers.json"
    out_path = tmp_path / "agreement.json"
    key_path.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    review_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        key=str(key_path), review=str(review_path), samples="unused", out=str(out_path)
    )
    assert blind.cmd_compare(args) == 0
    return json.loads(out_path.read_text(encoding="utf-8"))


# ---- 核心不变量：人判与裁判一致时，一致率必须是 1.0 ----
def test_consistent_human_judgement_agrees_fully(tmp_path):
    """人工判断与裁判结论逐条一致 -> 非平局一致率 1.0、方向一致 True。

    旧实现（.lower() 比较大写标签）会得到 0.0 / False，本用例对其必然失败。
    """
    keys = [
        _key("i1", enhanced_label="A", judge_winner="enhanced"),
        _key("i2", enhanced_label="B", judge_winner="enhanced"),
        _key("i3", enhanced_label="A", judge_winner="original"),
    ]
    answers = [
        _answer("i1", "A"),
        _answer("i2", "B"),
        _answer("i3", "B"),
    ]
    out = _run_compare(tmp_path, keys, answers)

    assert out["non_tie_pairs"] == 3
    assert out["winner_agreement_non_tie"] == 1.0
    assert out["direction_consistent"] is True
    assert out["conflicts"] == []


# ---- 大小写：人工填小写 / 大写都要能正确揭盲 ----
@pytest.mark.parametrize("human_input", ["a", "A", " a ", "a "])
def test_lowercase_human_winner_maps_to_enhanced(tmp_path, human_input):
    """人工填小写胜者时，命中 enhanced_label 必须映射成 enhanced。"""
    keys = [_key("i1", enhanced_label="A", judge_winner="enhanced")]
    answers = [_answer("i1", human_input)]
    out = _run_compare(tmp_path, keys, answers)

    assert out["rows"][0]["human_winner"] == "enhanced"
    assert out["winner_agreement_non_tie"] == 1.0


def test_lowercase_enhanced_label_in_key_still_maps(tmp_path):
    """揭盲表里 enhanced_label 写成小写时也必须能对上（防御性）。"""
    keys = [_key("i1", enhanced_label="a", judge_winner="enhanced")]
    answers = [_answer("i1", "A")]
    out = _run_compare(tmp_path, keys, answers)

    assert out["rows"][0]["human_winner"] == "enhanced"
    assert out["winner_agreement_non_tie"] == 1.0


# ---- 映射三分支 ----
def test_mismatch_maps_to_original(tmp_path):
    keys = [_key("i1", enhanced_label="A", judge_winner="original")]
    answers = [_answer("i1", "B")]
    out = _run_compare(tmp_path, keys, answers)

    assert out["rows"][0]["human_winner"] == "original"
    assert out["winner_agreement_non_tie"] == 1.0


@pytest.mark.parametrize("tie_input", ["tie", "平", "TIE", "  "])
def test_tie_input_maps_to_tie(tmp_path, tie_input):
    keys = [_key("i1", enhanced_label="A", judge_winner="enhanced")]
    answers = [_answer("i1", tie_input)]
    out = _run_compare(tmp_path, keys, answers)

    assert out["rows"][0]["human_winner"] == "tie"
    assert out["non_tie_pairs"] == 0
    assert out["tie_or_vs_tie_cases"] == 1
    assert out["winner_agreement_non_tie"] is None
    assert out["direction_consistent"] is False


def test_judge_tie_counts_as_tie_case(tmp_path):
    """裁判判平局时，无论人工判什么都不计入非平局分母。"""
    keys = [_key("i1", enhanced_label="A", judge_winner="tie")]
    answers = [_answer("i1", "A")]
    out = _run_compare(tmp_path, keys, answers)

    assert out["non_tie_pairs"] == 0
    assert out["tie_or_vs_tie_cases"] == 1


# ---- 四维 Delta 不受此 bug 影响（用大写标签且不依赖 winner）----
def test_dim_delta_follows_enhanced_label_not_winner(tmp_path):
    """enhanced_label=A 时 delta = A−B；enhanced_label=B 时 delta = B−A。"""
    dims_a = {d: 5 for d in DIMS}
    dims_b = {d: 3 for d in DIMS}

    keys_a = [_key("i1", enhanced_label="A", judge_winner="enhanced",
                   judge_deltas={d: 2.0 for d in DIMS})]
    out_a = _run_compare(tmp_path / "a", keys_a, [_answer("i1", "A", dims_a=dims_a, dims_b=dims_b)])
    assert out_a["rows"][0]["human_deltas"]["accuracy"] == 2.0
    assert out_a["overall_mad"] == pytest.approx(0.0)

    keys_b = [_key("i1", enhanced_label="B", judge_winner="enhanced",
                   judge_deltas={d: -2.0 for d in DIMS})]
    out_b = _run_compare(tmp_path / "b", keys_b, [_answer("i1", "A", dims_a=dims_a, dims_b=dims_b)])
    assert out_b["rows"][0]["human_deltas"]["accuracy"] == -2.0
    assert out_b["overall_mad"] == pytest.approx(0.0)


# ---- 旧 bug 的直接指纹：enhanced 必须真的能出现 ----
def test_enhanced_label_actually_reachable(tmp_path):
    """构造 20 条人工全判 enhanced 的数据，enhanced 计数必须是 20 而不是 0。

    旧实现在这组数据上给出 enhanced=0 —— 那是 bug 的指纹。
    """
    keys = [_key(f"i{i}", enhanced_label="A", judge_winner="enhanced") for i in range(20)]
    answers = [_answer(f"i{i}", "A") for i in range(20)]
    out = _run_compare(tmp_path, keys, answers)

    mapped = [r["human_winner"] for r in out["rows"]]
    assert mapped.count("enhanced") == 20
    assert mapped.count("original") == 0
