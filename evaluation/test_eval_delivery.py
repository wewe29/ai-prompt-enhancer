"""v0.2.1 评测可靠性测试：增强失败单条隔离、交付状态统计、基准增强失败去重。

规格 §4.6 评测三条 + 交付状态聚合。全部 mock，不联网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_benchmark as bench_mod  # noqa: E402
import report as report_mod  # noqa: E402
import run_eval as run_eval_mod  # noqa: E402


# ---- 1. run_eval 增强失败单条隔离 ----
def test_enhance_failure_does_not_abort_batch(monkeypatch, tmp_path):
    samples = [
        {"id": "s1", "original": "bad prompt first", "scenario": "A"},
        {"id": "s2", "original": "good prompt second", "scenario": "A"},
    ]
    cfg = {
        "run": {"max_cost_usd": 5.0, "delay_between": 0},
        "targets": {},
        "enhancer": {"model": "m"},
        "judge": {"model": "j"},
        "_eval_root": str(tmp_path),
    }
    monkeypatch.setattr(run_eval_mod, "load_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(run_eval_mod, "load_samples", lambda *_a, **_k: samples)
    monkeypatch.setattr(run_eval_mod, "resolve_api_key", lambda *_a, **_k: "")
    monkeypatch.setattr(run_eval_mod, "resolve_judge_key", lambda *_a, **_k: "")
    monkeypatch.setattr(run_eval_mod, "load_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(run_eval_mod, "save_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(run_eval_mod, "export_manual_list", lambda *_a, **_k: tmp_path / "清单.html")

    def fake_enhance(original, _cfg, _key, **_kw):
        if "bad prompt" in original:
            raise run_eval_mod.EnhanceError("模拟增强服务失败")
        return {
            "status": "ready",
            "primary_prompt": f"enhanced: {original}",
            "suggestions": [],
            "questions": [],
            "changes": [],
        }

    monkeypatch.setattr(run_eval_mod, "api_enhance", fake_enhance)
    run_eval_mod.run_with_budget.spent = 0.0

    import argparse

    args = argparse.Namespace(
        config=None, samples="samples.yaml", regression=False, target=None,
        personas=None, login=False, skip_enhance=False, skip_infer=False,
        skip_judge=False, skip_prompt_judge=False, max_cost=None, offline=False,
        no_control_group=True, repeats=None,
        manual=True, manual_answers=None,
    )
    monkeypatch.setattr(run_eval_mod, "parse_args", lambda *_a, **_k: args)

    rc = run_eval_mod.main()

    assert rc == 0  # 整批未被终止
    assert "error" in samples[0]["enhanced"]  # 失败样本记录 {"error": ...}
    assert samples[1]["enhanced"].get("primary_prompt") == "enhanced: good prompt second"
    assert samples[1]["enhanced_text"] == "enhanced: good prompt second"


# ---- 2. report._aggregate 交付状态统计 ----
def _delivery_payload():
    return {
        "meta": {"targets": ["t1"]},
        "samples": [
            {"id": "s1", "original": "o1", "results": {}, "enhanced": {"primary_prompt": "e1", "delivery_status": "complete"}},
            {"id": "s2", "original": "o2", "results": {}, "enhanced": {"primary_prompt": "e2", "delivery_status": "partial"}},
            {"id": "s3", "original": "o3", "results": {}, "enhanced": {"primary_prompt": "o3", "delivery_status": "fallback"}},
            {"id": "s4", "original": "o4", "results": {}, "enhanced": {"error": "增强服务返回空结果"}},
        ],
    }


def test_aggregate_delivery_status_counts():
    agg = report_mod._aggregate(_delivery_payload())
    assert agg["summary"]["delivery"] == {
        "complete": 1, "partial": 1, "fallback": 1, "hard_failure": 1,
    }
    assert agg["summary"]["delivery_rate"] == pytest.approx(0.75)


def test_aggregate_delivery_legacy_defaults_complete():
    payload = {
        "meta": {},
        "samples": [
            {"id": "s1", "original": "o1", "enhanced": {"primary_prompt": "e1"}},
            {"id": "s2", "original": "o2", "enhanced": {"error": "x"}},
        ],
    }
    agg = report_mod._aggregate(payload)
    assert agg["summary"]["delivery"] == {
        "complete": 1, "partial": 0, "fallback": 0, "hard_failure": 1,
    }
    assert agg["summary"]["delivery_rate"] == pytest.approx(0.5)


def test_aggregate_delivery_all_fail_zero_rate():
    payload = {
        "meta": {},
        "samples": [
            {"id": "s1", "original": "o1", "enhanced": {"error": "x"}},
            {"id": "s2", "original": "o2", "enhanced": {"error": "y"}},
        ],
    }
    agg = report_mod._aggregate(payload)
    assert agg["summary"]["delivery"]["hard_failure"] == 2
    assert agg["summary"]["delivery_rate"] == 0.0


def test_aggregate_delivery_empty_no_samples():
    agg = report_mod._aggregate({"meta": {}, "samples": []})
    assert agg["summary"]["delivery"] == {
        "complete": 0, "partial": 0, "fallback": 0, "hard_failure": 0,
    }
    assert agg["summary"]["delivery_rate"] == 0.0


def test_markdown_delivery_section(tmp_path):
    report_mod.generate(tmp_path, _delivery_payload())

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["delivery"]["complete"] == 1
    assert summary["summary"]["delivery_rate"] == pytest.approx(0.75)

    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "交付状态" in md
    assert "完整交付（complete）" in md
    assert "部分交付（partial）" in md
    assert "原文回退（fallback）" in md
    assert "增强失败（hard_failure）" in md
    assert "有效率" in md


# ---- 3. model_benchmark 增强失败去重 ----
def test_benchmark_enhance_failure_deduped(monkeypatch, tmp_path):
    prompts = [
        {"id": "good", "text": "good prompt", "scenario": "A"},
        {"id": "bad", "text": "bad prompt", "scenario": "A"},
    ]
    cfg = {
        "models": [{"id": "m1", "label": "M1"}, {"id": "m2", "label": "M2"}],
        "prompts": prompts,
        "enhancer": {"model": "e", "api_key_env": "", "base_url": "", "protocol": "openai", "temperature": 0.3},
        "judge": {"model": "j"},
        "api": {"base_url": "", "protocol": "anthropic", "api_key_env": "", "api_key": "k"},
        "params": {
            "retries": 0, "retry_backoff_s": [0], "delay_between_s": 0,
            "max_tokens": 100, "timeout_s": 1, "temperature": 0.7,
        },
        "scenario_temperatures": {},
        "scenario_weights": {},
        "run": {"out_dir": str(tmp_path / "out")},
    }
    monkeypatch.setattr(bench_mod, "load_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(bench_mod, "load_prompts", lambda *_a, **_k: prompts)
    monkeypatch.setattr(bench_mod.bench_api, "resolve_api_key", lambda *_a, **_k: "k")
    monkeypatch.setattr(bench_mod, "load_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(bench_mod, "save_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(bench_mod, "_pace", lambda *_a, **_k: None)

    def fake_enhance(original, *_a, **_k):
        if "bad" in original:
            return {"error": "增强结果校验失败：解析失败"}
        return {"enhanced": f"enhanced {original}", "status": "ready", "task_type": "other"}

    monkeypatch.setattr(bench_mod, "_enhance_prompt", fake_enhance)
    monkeypatch.setattr(
        bench_mod, "_infer_one",
        lambda *_a, **_k: {"text": "answer", "latency_s": 0.1, "usage": {}},
    )
    monkeypatch.setattr(
        bench_mod, "_judge_one",
        lambda *_a, **_k: {
            "accuracy": 8, "completeness": 8, "relevance": 8,
            "clarity": 8, "novelty": 6, "reason": "ok",
        },
    )

    out_dir = tmp_path / "out"
    rc = bench_mod.main(["--out-dir", str(out_dir)])
    assert rc == 0

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))

    # 增强失败只计一次（prompt 级），不再逐模型复制
    assert len(summary["enhance_failures"]) == 1
    entry = summary["enhance_failures"][0]
    assert entry["prompt_id"] == "bad"
    assert "增强失败" in entry["error"]
    assert entry["model_count"] == 2

    # 失败 prompt 不产生逐模型 detail（每个模型被跳过）
    assert [d for d in summary["details"] if d["prompt_id"] == "bad"] == []
    assert [d for d in summary["details"] if d["error"]] == []

    # 质量均分聚合不受增强失败影响：2 模型 × 1 有效 prompt
    by_id = {m["id"]: m for m in summary["models"]}
    for mid in ("m1", "m2"):
        assert by_id[mid]["judged_prompts"] == 1
        assert by_id[mid]["quality_mean"] == pytest.approx(8.0)

    md = (out_dir / "report_benchmark.md").read_text(encoding="utf-8")
    assert "影响 2 个模型" in md
    assert "bad" in md
