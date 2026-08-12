"""v0.3.0 模糊等级评测样本集测试（规格 §6.2 / 计划 Phase 4）。

断言样本集本身的一致性（数量、分布、字段完整性、id 唯一、must_preserve 子串、行为映射），
以及 run_eval.load_samples 对新增字段（ambiguity_level 等）的透传。全部离线，不联网。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_eval as run_eval_mod  # noqa: E402

SAMPLES_PATH = Path(__file__).resolve().parent / "samples_v30.yaml"

SCENARIOS = {"编程", "办公写作", "问答", "数据分析", "翻译", "创意"}
LEVELS = {"clear", "medium", "severe"}
BEHAVIORS = {"none", "light", "clarify"}

REQUIRED_FIELDS = [
    "id",
    "scenario",
    "ambiguity_level",
    "original",
    "must_preserve",
    "must_not_add",
    "expected_behavior",
]


def _nonempty(v) -> bool:
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    return v is not None


@pytest.fixture(scope="module")
def samples():
    loaded = run_eval_mod.load_samples(SAMPLES_PATH)
    assert len(loaded) > 0, "load_samples 返回空，样本文件未加载"
    return loaded


def test_total_count(samples):
    assert len(samples) == 60


def test_level_distribution_exact_20_20_20(samples):
    counts = {}
    for s in samples:
        counts[s["ambiguity_level"]] = counts.get(s["ambiguity_level"], 0) + 1
    assert counts == {"clear": 20, "medium": 20, "severe": 20}


def test_every_level_scenario_cell_at_least_3(samples):
    from collections import Counter

    cells = Counter((s["ambiguity_level"], s["scenario"]) for s in samples)
    for level in LEVELS:
        for scenario in SCENARIOS:
            assert cells[(level, scenario)] >= 3, f"{level} × {scenario} = {cells[(level, scenario)]}"


def test_all_required_fields_present_and_nonempty(samples):
    for s in samples:
        for field in REQUIRED_FIELDS:
            assert _nonempty(s.get(field)), f"{s.get('id')} 字段 {field} 为空"


def test_ids_unique_and_v30_prefixed(samples):
    ids = [s["id"] for s in samples]
    assert len(ids) == len(set(ids)), "存在重复 id"
    for sid in ids:
        assert sid.startswith("v30_"), f"id 必须以 v30_ 开头：{sid}"


def test_scenario_values(samples):
    for s in samples:
        assert s["scenario"] in SCENARIOS, f"{s['id']} 非法场景：{s['scenario']}"


def test_ambiguity_level_values(samples):
    for s in samples:
        assert s["ambiguity_level"] in LEVELS, f"{s['id']} 非法模糊等级：{s['ambiguity_level']}"


def test_expected_behavior_values(samples):
    for s in samples:
        assert s["expected_behavior"] in BEHAVIORS, f"{s['id']} 非法行为：{s['expected_behavior']}"


def test_level_behavior_mapping(samples):
    for s in samples:
        lvl, beh = s["ambiguity_level"], s["expected_behavior"]
        if lvl == "clear":
            assert beh in {"none", "light"}, f"{s['id']} clear 应为 none|light"
        elif lvl == "medium":
            assert beh == "light", f"{s['id']} medium 应为 light"
        else:
            assert beh == "clarify", f"{s['id']} severe 应为 clarify"


def test_list_field_cardinality(samples):
    for s in samples:
        assert 2 <= len(s["must_preserve"]) <= 4, f"{s['id']} must_preserve 需 2-4 条"
        assert 1 <= len(s["must_not_add"]) <= 3, f"{s['id']} must_not_add 需 1-3 条"


def test_must_preserve_substrings_present_in_original(samples):
    for s in samples:
        for item in s["must_preserve"]:
            assert isinstance(item, str) and item in s["original"], (
                f"{s['id']} must_preserve 不在 original 中：{item!r}"
            )


# ---- load_samples 对新增字段的透传（回归）----
def test_load_samples_preserves_v30_fields(samples):
    for s in samples:
        assert "ambiguity_level" in s
        assert "must_preserve" in s
        assert "must_not_add" in s
        assert "expected_behavior" in s
        assert s["id"] == s["id"].strip()
        assert s["original"] == s["original"].strip()


def test_load_samples_pass_through_synthetic(tmp_path):
    y = tmp_path / "tiny.yaml"
    y.write_text(
        "samples:\n"
        "  - id: v30_tiny\n"
        "    scenario: 编程\n"
        "    ambiguity_level: severe\n"
        "    original: 帮我写个方案。\n"
        "    must_preserve: [方案]\n"
        "    must_not_add: [不要虚构主题]\n"
        "    expected_behavior: clarify\n",
        encoding="utf-8",
    )
    loaded = run_eval_mod.load_samples(y)
    assert len(loaded) == 1
    s = loaded[0]
    assert s["id"] == "v30_tiny"
    assert s["scenario"] == "编程"
    assert s["ambiguity_level"] == "severe"
    assert s["must_preserve"] == ["方案"]
    assert s["must_not_add"] == ["不要虚构主题"]
    assert s["expected_behavior"] == "clarify"
    assert s["original"] == "帮我写个方案。"


def test_load_samples_defaults_do_not_break_missing_keys(tmp_path):
    y = tmp_path / "legacy.yaml"
    y.write_text("samples:\n  - original: 只给原文\n", encoding="utf-8")
    loaded = run_eval_mod.load_samples(y)
    assert len(loaded) == 1
    assert loaded[0]["id"] == "sample_1"
    assert loaded[0]["scenario"] == "未分类"
