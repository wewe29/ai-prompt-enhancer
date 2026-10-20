"""v0.4.0 证据链的离线单测：运行身份、档位稳定性指标、severe 盲评流程。

**全程 0 次 API 调用。** 这些测试存在的意义是：P0-4 指出「没有人能复核
任何一个数字」，那么补回来的脚本必须先能被测试钉住，否则只是把不可复现
换成了不可复现的新样子。

覆盖：
* :mod:`run_identity` —— 身份随参数变、标签稳定、resume 闸会拦下身份不一致
* :mod:`verify_stability` —— 一致/飘/组合/边界翻转/长度波动/交付漂移/逐次分歧
* :mod:`v040_severe_blind` —— 盲评包不泄露身份、答案三格式、场景汇总
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_identity  # noqa: E402
import v040_severe_blind as severe  # noqa: E402
import verify_stability as stability  # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples_v30.yaml"


# ============================================================ run_identity
def _identity(**kwargs):
    base = dict(model="deepseek-flash", temperature=0.1, samples_path=SAMPLES, repeats=3)
    base.update(kwargs)
    return run_identity.build_identity(**base)


def test_identity_is_stable_for_same_inputs():
    a = _identity()
    b = _identity()
    assert a["tag"] == b["tag"]


@pytest.mark.parametrize(
    "changed",
    [
        {"model": "deepseek-chat"},
        {"temperature": 0.35},
        {"repeats": 5},
        {"prompt_version": "promptcraft-v9.9.9"},
        {"prompt_text": "改了提示词但忘了改版本号"},
    ],
)
def test_identity_changes_when_any_input_changes(changed):
    assert _identity(**changed)["tag"] != _identity()["tag"]


def test_identity_changes_when_sample_set_changes(tmp_path):
    other = tmp_path / "other.yaml"
    other.write_text("samples: []\n", encoding="utf-8")
    assert _identity(samples_path=other)["tag"] != _identity()["tag"]


def test_stamped_name_carries_identity():
    name = run_identity.stamped_name("v040_stability_raw", _identity())
    assert name.startswith("v040_stability_raw__m-deepseek-flash__t0.1__r3__")
    assert name.endswith(".json")


def test_run_dir_is_not_shared_between_runs():
    a = run_identity.run_dir(Path("results"), _identity())
    b = run_identity.run_dir(Path("results"), _identity())
    assert a != b  # 时间戳隔离 ⇒ 两次运行不共用目录


def test_assert_same_identity_rejects_mismatch():
    identity = _identity()
    with pytest.raises(ValueError) as excinfo:
        run_identity.assert_same_identity(
            {run_identity.IDENTITY_KEY: _identity(temperature=0.35)}, identity
        )
    assert "temperature" in str(excinfo.value)


def test_assert_same_identity_rejects_payload_without_identity():
    with pytest.raises(ValueError):
        run_identity.assert_same_identity({"records": []}, _identity())


def test_assert_same_identity_accepts_match():
    identity = _identity()
    run_identity.assert_same_identity({run_identity.IDENTITY_KEY: identity}, identity)


# ====================================================== verify_stability
def _rec(sample_id, level, *, difficulty="clear", repeat=1, original_len=100,
         primary_len=None, delivery="complete", error=""):
    if primary_len is None:
        primary_len = 100
    return {
        "id": sample_id,
        "repeat": repeat,
        "ambiguity_level": difficulty,
        "enhancement_level": level,
        "delivery_status": delivery,
        "original_len": original_len,
        "primary_len": primary_len,
        "error": error,
    }


def test_level_combo_is_sorted_and_deduped():
    assert stability.level_combo(["light", "none", "light"]) == "none+light"
    assert stability.level_combo(["clarify", "none"]) == "none+clarify"
    assert stability.level_combo(["light", "light"]) == "light"


def test_mode_level_takes_first_on_tie():
    assert stability.mode_level(["none", "light"]) == "none"
    assert stability.mode_level(["light", "none", "light"]) == "light"


def test_consistent_and_drifted_counting():
    records = [
        _rec("s1", "none", repeat=1),
        _rec("s1", "none", repeat=2),
        _rec("s1", "none", repeat=3),
        _rec("s2", "none", repeat=1),
        _rec("s2", "light", repeat=2),
        _rec("s2", "light", repeat=3),
    ]
    metrics = stability.compute_metrics(records)
    clear = metrics["by_difficulty"]["clear"]
    assert clear["samples"] == 2
    assert clear["consistent"] == 1
    assert clear["drifted"] == 1
    assert clear["consistent_rate"] == 0.5
    assert clear["level_combos"] == {"none": 1, "none+light": 1}


def test_flip_rate_boundary_follows_report_definition():
    """边界样本 = **至少一次**的档位落在 {none, light} 内；翻转 = 档位不一致。

    三次都判 clarify 的样本不进分母（它不是 none/light 的候选）。
    """
    records = [
        # 边界样本：摇摆过 ⇒ 算翻转
        _rec("s1", "none", repeat=1),
        _rec("s1", "light", repeat=2),
        _rec("s1", "light", repeat=3),
        # 边界样本：三次一致 ⇒ 不算翻转
        _rec("s2", "none", repeat=1),
        _rec("s2", "none", repeat=2),
        _rec("s2", "none", repeat=3),
        # 三次都 clarify ⇒ 不进分母
        _rec("s3", "clarify", repeat=1),
        _rec("s3", "clarify", repeat=2),
        _rec("s3", "clarify", repeat=3),
        # 跨越 clarify 的摇摆 ⇒ 报告口径算翻转，严格口径不算
        _rec("s4", "light", repeat=1),
        _rec("s4", "clarify", repeat=2),
        _rec("s4", "light", repeat=3),
    ]
    metrics = stability.compute_metrics(records)
    boundary = metrics["by_difficulty"]["clear"]["none_light_boundary"]
    assert boundary["samples"] == 3          # s1 / s2 / s4
    assert boundary["flipped"] == 2          # s1 / s4
    assert boundary["flip_rate"] == pytest.approx(2 / 3, abs=1e-4)
    # 严格口径：全部运行都在 {none, light} 内 ⇒ s1 / s2
    assert boundary["strict_samples"] == 2
    assert boundary["strict_flipped"] == 1
    assert boundary["flip_rate_strict"] == pytest.approx(0.5, abs=1e-4)


def _table_sample(levels, difficulty):
    return [
        _rec(f"{difficulty}-{index}", level, difficulty=difficulty, repeat=repeat)
        for index, levels_combo in enumerate(levels)
        for repeat, level in enumerate(levels_combo, start=1)
    ]


def test_reproduces_report_table_v040():
    """用报告《效果验证报告》§3.2 的档位组合分布，反推出与报告一致的数。

    报告原表：clear 边界 20 / 翻转 11 / 55%；medium 边界 18 / 翻转 6 / 33%。
    这条用例的作用是证明**重建脚本的口径与原报告一致**——否则重建出来的
    数字与报告对不上，就说明口径理解错了。
    """
    clear = _table_sample(
        [["none", "light", "light"]] * 10          # none+light × 10
        + [["light", "light", "light"]] * 5        # light × 5
        + [["none", "none", "none"]] * 4           # none × 4
        + [["light", "clarify", "light"]],         # light+clarify × 1
        "clear",
    )
    medium = _table_sample(
        [["light", "light", "light"]] * 12         # light × 12
        + [["none", "light", "light"]] * 4         # none+light × 4
        + [["clarify", "clarify", "clarify"]] * 2  # clarify × 2
        + [["light", "clarify", "light"]] * 2,     # light+clarify × 2
        "medium",
    )
    metrics = stability.compute_metrics(clear + medium)

    clear_b = metrics["by_difficulty"]["clear"]["none_light_boundary"]
    assert (clear_b["samples"], clear_b["flipped"]) == (20, 11)
    assert clear_b["flip_rate"] == pytest.approx(0.55, abs=1e-4)
    assert metrics["by_difficulty"]["clear"]["level_combos"] == {
        "none+light": 10, "light": 5, "none": 4, "light+clarify": 1
    }

    medium_b = metrics["by_difficulty"]["medium"]["none_light_boundary"]
    assert (medium_b["samples"], medium_b["flipped"]) == (18, 6)
    assert medium_b["flip_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["by_difficulty"]["medium"]["level_combos"] == {
        "light": 12, "none+light": 4, "clarify": 2, "light+clarify": 2
    }


def test_length_spread_and_delivery_drift():
    records = [
        _rec("s1", "none", repeat=1, original_len=100, primary_len=100),
        _rec("s1", "none", repeat=2, original_len=100, primary_len=130),
        _rec("s1", "none", repeat=3, original_len=100, primary_len=110),
    ]
    metrics = stability.compute_metrics(records)
    boundary = metrics["by_difficulty"]["clear"]["none_light_boundary"]
    assert boundary["length_spread_avg"] == pytest.approx(0.3)
    assert boundary["length_spread_max"] == pytest.approx(0.3)
    assert boundary["delivery_status_drift"] == 0

    records.append(_rec("s1", "none", repeat=4, delivery="partial"))
    metrics = stability.compute_metrics(records)
    boundary = metrics["by_difficulty"]["clear"]["none_light_boundary"]
    assert boundary["delivery_status_drift"] == 1


def test_per_click_disagreement_rate():
    records = [
        _rec("s1", "none", repeat=1),
        _rec("s1", "none", repeat=2),
        _rec("s1", "light", repeat=3),
    ]
    metrics = stability.compute_metrics(records)
    per_click = metrics["by_difficulty"]["clear"]["per_click_disagreement"]
    assert per_click["total_calls"] == 3
    assert per_click["disagree_calls"] == 1
    assert per_click["rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_errors_are_counted_not_swallowed():
    records = [
        _rec("s1", "none", repeat=1),
        _rec("s1", "", repeat=2, error="HTTP 500: boom"),
    ]
    metrics = stability.compute_metrics(records)
    assert metrics["totals"]["errors"] == 1
    assert metrics["totals"]["error_samples"] == 1


def test_metrics_are_deterministic():
    records = [_rec(f"s{i}", "none" if i % 2 else "light", repeat=r) for i in range(5) for r in (1, 2, 3)]
    assert stability.compute_metrics(records) == stability.compute_metrics(records)


# ==================================================== v040_severe_blind
def _severe_raw() -> list[dict]:
    return [
        {
            "id": f"v30_severe_{i}",
            "ambiguity_level": "severe",
            "scenario": "翻译" if i < 3 else "编程",
            "original": f"原话 {i}",
            "expected_behavior": "clarify",
            "enhancement_level": "clarify",
            "questions": [
                {"id": "q1", "text": f"问题一 {i}"},
                {"id": "q2", "text": f"问题二 {i}"},
                {"id": "q3", "text": f"问题三 {i}"},
            ],
            "error": "",
        }
        for i in range(5)
    ]


def test_export_writes_packet_key_and_template(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_severe_raw(), ensure_ascii=False), encoding="utf-8")
    outdir = tmp_path / "blind"
    rc = severe.cmd_export(
        argparse.Namespace(raw=str(raw), outdir=str(outdir), limit=20)
    )
    assert rc == 0
    assert (outdir / "blind_packet.md").exists()
    assert (outdir / "blind_key.json").exists()
    assert (outdir / "blind_answers.json").exists()

    key = json.loads((outdir / "blind_key.json").read_text(encoding="utf-8"))
    assert [row["unit"] for row in key] == ["R01", "R02", "R03", "R04", "R05"]


def test_blind_packet_does_not_leak_identity(tmp_path):
    """盲评包不得出现真实样本 ID、场景标签或档位。

    注意口径：判据是**每个编号单元的内容块里**不含这些信息。
    整包文本可以出现"翻译"这类词——评审说明里就有一个翻译场景的例子，
    拿整包做子串匹配会误报。
    """
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_severe_raw(), ensure_ascii=False), encoding="utf-8")
    outdir = tmp_path / "blind"
    severe.cmd_export(argparse.Namespace(raw=str(raw), outdir=str(outdir), limit=20))
    packet = (outdir / "blind_packet.md").read_text(encoding="utf-8")
    key = json.loads((outdir / "blind_key.json").read_text(encoding="utf-8"))

    # 按编号切块：块与块之间不含别的单元
    blocks = {}
    for chunk in packet.split("\n## "):
        head = chunk.split("\n", 1)[0].strip()
        if head.startswith("R"):
            blocks[head] = chunk

    assert set(blocks) == {row["unit"] for row in key}
    for row in key:
        block = blocks[row["unit"]]
        assert row["sample_id"] not in block, f"{row['unit']} 泄露了样本 ID"
        assert row["scenario"] not in block, f"{row['unit']} 泄露了场景标签"
        assert row["enhancement_level"] not in block, f"{row['unit']} 泄露了档位"


def test_export_is_deterministic_for_fixed_seed(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_severe_raw(), ensure_ascii=False), encoding="utf-8")
    keys = []
    for name in ("a", "b"):
        outdir = tmp_path / name
        severe.cmd_export(argparse.Namespace(raw=str(raw), outdir=str(outdir), limit=20))
        keys.append(
            [row["sample_id"] for row in json.loads((outdir / "blind_key.json").read_text(encoding="utf-8"))]
        )
    assert keys[0] == keys[1]


def test_parse_accepts_three_answer_formats(tmp_path):
    key_rows = [
        {"unit": "R01", "sample_id": "s1", "scenario": "翻译", "questions": ["a", "b", "c"]},
        {"unit": "R02", "sample_id": "s2", "scenario": "编程", "questions": ["a", "b", "c"]},
    ]
    key_path = tmp_path / "blind_key.json"
    key_path.write_text(json.dumps(key_rows, ensure_ascii=False), encoding="utf-8")

    # 1) list[dict]
    answers = tmp_path / "a1.json"
    answers.write_text(
        json.dumps([{"unit": "R01", "hits": 3}, {"unit": "R02", "hits": 1}]), encoding="utf-8"
    )
    # 2) dict[unit -> int]
    answers2 = tmp_path / "a2.json"
    answers2.write_text(json.dumps({"R01": 3, "R02": 1}), encoding="utf-8")
    # 3) CSV
    answers3 = tmp_path / "a3.csv"
    answers3.write_text("unit,hits\nR01,3\nR02,1\n", encoding="utf-8")

    for path in (answers, answers2, answers3):
        out = tmp_path / f"out-{path.name}.json"
        rc = severe.cmd_parse(
            argparse.Namespace(answers=str(path), key=str(key_path), out=str(out))
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["summary"]["units"] == 2
        assert payload["summary"]["total_hits"] == 4
        assert payload["summary"]["total_questions"] == 6
        assert payload["summary"]["hit_rate"] == pytest.approx(4 / 6, abs=1e-4)


def test_parse_reports_missing_units(tmp_path):
    key_rows = [{"unit": "R01", "sample_id": "s1", "scenario": "翻译", "questions": ["a"]}]
    key_path = tmp_path / "blind_key.json"
    key_path.write_text(json.dumps(key_rows, ensure_ascii=False), encoding="utf-8")
    answers = tmp_path / "a.json"
    answers.write_text("[]", encoding="utf-8")
    out = tmp_path / "out.json"
    severe.cmd_parse(argparse.Namespace(answers=str(answers), key=str(key_path), out=str(out)))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["missing_units"] == ["R01"]


def test_aggregate_computes_per_scenario_rates_and_spread(tmp_path):
    scored = {
        "scored": [
            {"unit": "R01", "sample_id": "s1", "scenario": "翻译", "hits": 1, "question_count": 3},
            {"unit": "R02", "sample_id": "s2", "scenario": "翻译", "hits": 0, "question_count": 3},
            {"unit": "R03", "sample_id": "s3", "scenario": "办公写作", "hits": 3, "question_count": 3},
        ]
    }
    path = tmp_path / "scored.json"
    path.write_text(json.dumps(scored, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "agg.json"
    severe.cmd_aggregate(argparse.Namespace(scored=str(path), out=str(out)))
    payload = json.loads(out.read_text(encoding="utf-8"))

    rows = {row["scenario"]: row for row in payload["by_scenario"]}
    assert rows["翻译"]["hits"] == 1
    assert rows["翻译"]["questions"] == 6
    assert rows["翻译"]["hit_rate"] == pytest.approx(1 / 6, abs=1e-4)
    assert rows["办公写作"]["hit_rate"] == pytest.approx(1.0)
    assert payload["totals"]["scenario_rate_spread"] == pytest.approx(6.0, abs=1e-4)
