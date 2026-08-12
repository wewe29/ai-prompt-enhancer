"""Phase 5 测试：repeats 缓存键/多数聚合、控制组默认、probe 解析、元数据字段。

规格 §6.3/§6.4/§6.5 + 计划 Phase 5 Task 5.1-5.5。全部离线，不联网。
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_targets  # noqa: E402
import report as report_mod  # noqa: E402
import run_eval as run_eval_mod  # noqa: E402


# ---- Task 5.2：repeats 缓存键 ----
def test_rep_key_n1_has_no_suffix():
    assert run_eval_mod._rep_key(1, 0) == ""
    assert run_eval_mod._rep_key(1, 2) == ""


def test_rep_key_n3_has_rep_suffix():
    assert [run_eval_mod._rep_key(3, i) for i in range(3)] == ["_rep0", "_rep1", "_rep2"]


def test_repeats_flag_parses():
    assert run_eval_mod.parse_args(["--repeats", "3"]).repeats == 3
    assert run_eval_mod.parse_args([]).repeats is None


# ---- Task 5.2：多 rep 裁判聚合（均值 + 多数胜）----
def _j(winner, delta, reason="r"):
    return {
        "original": {"accuracy": 5, "completeness": 5},
        "enhanced": {"accuracy": 5 + delta, "completeness": 5 + delta},
        "deltas": {"accuracy": delta, "completeness": delta},
        "winner": winner,
        "reason": reason,
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }


def test_aggregate_judges_majority_winner():
    agg = run_eval_mod.aggregate_judges([_j("enhanced", 2), _j("enhanced", 1), _j("tie", 0)])
    assert agg["winner"] == "enhanced"
    assert agg["deltas"]["accuracy"] == pytest.approx(1.0)
    assert agg["enhanced"]["completeness"] == pytest.approx(6.0)
    assert agg["usage"]["input_tokens"] == 3
    assert agg["usage"]["output_tokens"] == 6


def test_aggregate_judges_no_majority_is_tie():
    agg = run_eval_mod.aggregate_judges([_j("enhanced", 2), _j("original", -1), _j("tie", 0)])
    assert agg["winner"] == "tie"


def test_aggregate_judges_two_vs_one_original():
    agg = run_eval_mod.aggregate_judges([_j("original", -2), _j("original", -1), _j("enhanced", 1)])
    assert agg["winner"] == "original"
    assert agg["deltas"]["accuracy"] == pytest.approx(-2.0 / 3.0)


def test_aggregate_judges_empty_returns_none():
    assert run_eval_mod.aggregate_judges([]) is None
    assert run_eval_mod.aggregate_judges(None) is None
    assert run_eval_mod.aggregate_judges([None, None]) is None


# ---- Task 5.1：控制组默认开启 --no-control-group 关闭 ----
def test_control_group_default_on():
    assert run_eval_mod.resolve_control_group({}, False) is True
    assert run_eval_mod.resolve_control_group({"run": {"control_group": True}}, False) is True


def test_control_group_config_off_and_flag_off():
    assert run_eval_mod.resolve_control_group({"run": {"control_group": False}}, False) is False
    assert run_eval_mod.resolve_control_group({"run": {"control_group": True}}, True) is False
    assert run_eval_mod.resolve_control_group({}, True) is False


def test_no_control_group_flag_parses():
    assert run_eval_mod.parse_args(["--no-control-group"]).no_control_group is True
    assert run_eval_mod.parse_args([]).no_control_group is False


def test_config_defaults_control_group_and_repeats(tmp_path):
    import config as config_mod

    y = tmp_path / "cfg.yaml"
    y.write_text("run: {}\n", encoding="utf-8")
    cfg = config_mod.load_config(y)
    assert cfg["run"]["control_group"] is True
    assert cfg["run"]["repeats"] == 1


# ---- Task 5.1：pad_to_length ±5% ----
def test_pad_to_length_within_5pct():
    out = run_eval_mod.pad_to_length("短", 1000)
    assert 950 <= len(out) <= 1000


def test_pad_to_length_overlong_truncated():
    out = run_eval_mod.pad_to_length("长" * 500, 100)
    assert len(out) == 100
    assert out == "长" * 100


def test_pad_to_length_near_target_unchanged():
    text = "长" * 1030
    assert run_eval_mod.pad_to_length(text, 1000) == text


# ---- Task 5.4：probe 解析（mock urlopen）----
class _Resp:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


def test_probe_models_returns_only_200():
    requested = []

    def fake_urlopen(req, timeout=None):
        model = json.loads(req.data)["model"]
        requested.append(model)
        header_vals = list(req.headers.values())
        assert "ark-key" in header_vals
        assert "2023-06-01" in header_vals
        if model == "ok-model":
            return _Resp(200)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with mock.patch("probe_targets.urllib.request.urlopen", side_effect=fake_urlopen):
        available, unavailable = probe_targets.probe_models("https://base", "ark-key", ["ok-model", "bad-model"])

    assert available == ["ok-model"]
    assert unavailable == [("bad-model", "HTTP 404")]


def test_probe_model_200_true():
    with mock.patch("probe_targets.urllib.request.urlopen", return_value=_Resp(200)):
        ok, status = probe_targets.probe_model("https://base", "k", "m")
    assert ok is True
    assert status == "200"


def test_probe_model_error_false():
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    with mock.patch("probe_targets.urllib.request.urlopen", side_effect=boom):
        ok, status = probe_targets.probe_model("https://base", "k", "m")
    assert ok is False
    assert status == "HTTP 429"


# ---- Task 5.3：元数据字段进入 raw 文件 ----
def _meta_payload():
    return {
        "meta": {"targets": ["t1"], "judge_model": "j", "enhance_model": "e"},
        "samples": [
            {
                "id": "s1", "scenario": "A",
                "original": "o", "enhanced_text": "e", "enhanced": {"primary_prompt": "e"},
                "results": {"t1": {
                    "original_output": "o1", "enhanced_output": "e1", "padded_output": "p1",
                    "original_latency_s": 1.2, "enhanced_latency_s": 2.5, "padded_latency_s": 3.0,
                    "original_est_tokens": 10, "enhanced_est_tokens": 20, "padded_est_tokens": 15,
                    "original_model": "t1", "enhanced_model": "t1", "padded_model": "t1",
                }},
            },
        ],
    }


def test_raw_files_include_metadata_fields(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    report_mod._write_raw_files(raw_dir, _meta_payload())

    for variant, latency, tokens in (("original", 1.2, 10), ("enhanced", 2.5, 20), ("padded", 3.0, 15)):
        d = json.loads((raw_dir / f"s1_t1_{variant}.json").read_text(encoding="utf-8"))
        assert d["latency_s"] == latency
        assert d["est_tokens"] == tokens
        assert d["model"] == "t1"


def test_raw_files_reps_include_metadata(tmp_path):
    payload = _meta_payload()
    payload["samples"][0]["results"]["t1"]["reps"] = [
        {"rep": 0, "original_output": "a", "original_latency_s": 0.5, "original_est_tokens": 7,
         "original_model": "t1"},
        {"rep": 1, "original_output": "b", "original_latency_s": 0.7, "original_est_tokens": 8,
         "original_model": "t1"},
    ]
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    report_mod._write_raw_files(raw_dir, payload)

    d0 = json.loads((raw_dir / "s1_t1_original_rep0.json").read_text(encoding="utf-8"))
    assert d0["latency_s"] == 0.5
    assert d0["est_tokens"] == 7
    assert d0["rep"] == 0
    d1 = json.loads((raw_dir / "s1_t1_original_rep1.json").read_text(encoding="utf-8"))
    assert d1["latency_s"] == 0.7
    assert d1["est_tokens"] == 8


# ---- Task 5.1：裁判标签中性（不含“原始/增强”字样）----
def test_judge_message_labels_neutral():
    import judge as judge_mod

    msg = judge_mod._build_judge_message(
        {"original": "写一段代码", "enhanced": "写一段代码"},
        "提示词A内容", "回答A内容", "提示词B内容", "回答B内容",
    )
    assert "提示词A：" in msg
    assert "提示词B：" in msg
    assert "原始" not in msg
    assert "增强" not in msg


def test_judge_system_prompt_labels_neutral():
    import judge as judge_mod

    sys_prompt = judge_mod._judge_system_prompt(True)
    assert "提示词A" in sys_prompt
    assert "提示词B" in sys_prompt
    assert "原始" not in sys_prompt
    assert "增强" not in sys_prompt


def test_assign_labels_uses_prompt_text_not_internal_dict():
    import judge as judge_mod

    sample = {
        "original": "任务原文",
        "enhanced": {"status": "ready", "primary_prompt": "增强后提示词", "delivery_status": "complete"},
        "enhanced_text": "增强后提示词",
    }
    prompt_a, ans_a, prompt_b, ans_b = judge_mod._assign_labels(sample, "回答B内容", "回答A内容", True)
    assert prompt_a == "增强后提示词"
    assert prompt_b == "任务原文"
    msg = judge_mod._build_judge_message(sample, prompt_a, ans_a, prompt_b, ans_b)
    assert "增强后提示词" in msg
    assert "任务原文" in msg
    assert "delivery_status" not in msg
    assert "primary_prompt" not in msg
