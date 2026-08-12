"""模型横向评测主入口（火山方舟 Agent Plan）。

流程：增强（复用 enhancer.py）→ 每(模型×提示词)推理 → LLM 裁判打分 →
按模型聚合（维度均分/延迟/输出长度/胜率/综合分）→ summary.json + report_benchmark.md。

用法示例：
  python model_benchmark.py                                    # 全量（配置内模型 × 全部提示词）
  python model_benchmark.py --max-prompts 3                    # 只跑前 3 条提示词
  python model_benchmark.py --models deepseek-v4-flash glm-5.2 # 只评测指定模型
  python model_benchmark.py --skip-enhance --skip-infer        # 断点续跑（复用 results/bench/ 缓存）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# 允许从 evaluation 目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)  # 重定向/管道下也能实时看到进度

import bench_api  # noqa: E402
import bench_judge  # noqa: E402
import bench_report  # noqa: E402
import enhancer  # noqa: E402

EVAL_ROOT = Path(__file__).resolve().parent
CACHE_DIR = EVAL_ROOT / "results" / "bench"

DEFAULTS: dict[str, Any] = {
    "api": {
        "base_url": "https://ark.cn-beijing.volces.com/api/plan",
        "protocol": "anthropic",
        "api_key_env": "ARK_PLAN_API_KEY",
        "api_key_file": "key.local",
        "api_key": None,
    },
    "enhancer": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "protocol": "openai",
        "model": "doubao-seed-2-1-pro-260628",
        "api_key_env": "ARK_API_KEY",
        "temperature": 0.35,
    },
    "scenario_temperatures": {
        "编程": 0.2,
        "翻译": 0.2,
        "数据分析": 0.2,
        "问答": 0.4,
        "写作": 0.5,
        "模糊请求": 0.5,
        "创意": 0.7,
    },
    "judge": {
        "model": "deepseek-v4-flash",
        "temperature": 0,
        "weights": {"quality": 0.6, "relevance": 0.2, "speed": 0.2},
    },
    "models": [],
    "prompts": [],
    "samples_file": "samples/samples.yaml",
    "params": {
        "max_tokens": 4096,
        "timeout_s": 240,
        "retries": 2,
        "retry_backoff_s": [2, 5],
        "delay_between_s": 1.0,
        "temperature": 0.7,  # 推理默认温度（样本 temperature / scenario_temperatures 未命中时使用）
    },
    "run": {"out_dir": "results/benchmark"},
}

INFER_TEMPERATURE = 0.7


# ---- 配置 ----
def load_config(path: str | Path) -> dict[str, Any]:
    import yaml

    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = EVAL_ROOT / cfg_path
    cfg = _deep_copy(DEFAULTS)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"配置文件 {cfg_path} 顶层必须是映射")
        cfg = _deep_merge(cfg, loaded)
    else:
        print(f"[配置] 未找到 {cfg_path}，使用内置默认配置")
    cfg["_config_path"] = str(cfg_path)
    return cfg


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    out = _deep_copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = _deep_copy(value)
    return out


# ---- 提示词 ----
def load_prompts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """配置内嵌 prompts 优先；为空时读取 samples_file 的 original 字段。"""
    inline = cfg.get("prompts") or []
    if inline:
        prompts = []
        for i, item in enumerate(inline):
            prompts.append({
                "id": str(item.get("id") or f"prompt_{i + 1}"),
                "text": str(item.get("text") or "").strip(),
                "scenario": str(item.get("scenario") or ""),
                "temperature": item.get("temperature"),
            })
        return [p for p in prompts if p["text"]]
    import yaml

    path = Path(cfg.get("samples_file") or "samples/samples.yaml")
    if not path.is_absolute():
        path = EVAL_ROOT / path
    if not path.exists():
        print(f"[错误] 样本文件不存在：{path}")
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    prompts = []
    for i, sample in enumerate(data.get("samples") or []):
        prompts.append({
            "id": str(sample.get("id") or f"sample_{i + 1}"),
            "text": str(sample.get("original") or "").strip(),
            "scenario": str(sample.get("scenario") or ""),
            "temperature": sample.get("temperature"),
        })
    return [p for p in prompts if p["text"]]


# ---- 缓存 ----
def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-.]", "_", text)


def load_cache(kind: str, pid: str, mid: str = "") -> Any:
    name = f"{kind}_{_safe_name(pid)}" + (f"_{_safe_name(mid)}" if mid else "")
    path = CACHE_DIR / f"{name}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def save_cache(kind: str, pid: str, mid: str, payload: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{kind}_{_safe_name(pid)}" + (f"_{_safe_name(mid)}" if mid else "")
    with open(CACHE_DIR / f"{name}.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ---- 主流程 ----
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        print(f"[错误] 配置无效：{exc}")
        return 1

    prompts = load_prompts(cfg)
    if args.prompts:
        prompts = [p for p in prompts if p["id"] in args.prompts]
    if args.max_prompts is not None:
        prompts = prompts[: args.max_prompts]
    if not prompts:
        print("[错误] 提示词列表为空（配置 prompts 为空且 samples_file 无效）")
        return 1

    models = list(cfg.get("models") or [])
    for m in models:
        m.setdefault("label", m.get("id", ""))
    if args.models:
        models = [m for m in models if m["id"] in args.models]
    if args.max_models is not None:
        models = models[: args.max_models]
    if not models:
        print("[错误] 模型列表为空（配置 models 为空或 --models 未匹配）")
        return 1

    api_cfg = cfg.get("api") or {}
    try:
        api_key = bench_api.resolve_api_key(api_cfg, str(EVAL_ROOT))
    except ValueError as exc:
        print(f"[错误] {exc}")
        return 1

    enh_cfg = cfg.get("enhancer") or {}
    enh_env = str(enh_cfg.get("api_key_env") or "").strip()
    enh_key = os.environ.get(enh_env, "").strip() if enh_env else ""
    if not enh_key:
        enh_key = api_key
        print(f"[配置] 增强器环境变量 {enh_env or '(未配置)'} 未设置，回退 api 密钥")

    params = cfg.get("params") or {}
    retries = int(params.get("retries", 2))
    backoff_s = [float(x) for x in (params.get("retry_backoff_s") or [2, 5])]
    delay_between_s = float(params.get("delay_between_s", 1.0))
    max_tokens = int(params.get("max_tokens", 4096))
    timeout_s = float(params.get("timeout_s", 120))

    out_dir = Path(args.out_dir) if args.out_dir else Path(cfg["run"]["out_dir"])
    if not out_dir.is_absolute():
        out_dir = EVAL_ROOT / out_dir

    print(f"[配置] 协议 {api_cfg.get('protocol')} | 模型 {len(models)} 个 | 提示词 {len(prompts)} 条")
    print(f"[配置] 增强 {cfg['enhancer'].get('model')} | 裁判 {cfg['judge'].get('model')} | 输出 {out_dir}")

    # 阶段一：增强
    for i, prompt in enumerate(prompts):
        pid = prompt["id"]
        cached = load_cache("enhance", pid)
        if cached is not None:
            prompt["enhanced"] = cached
            print(f"[增强] {pid} 使用缓存")
        elif args.skip_enhance:
            prompt["enhanced"] = {"error": "无增强缓存（--skip-enhance）"}
        else:
            prompt["enhanced"] = _enhance_prompt(prompt["text"], cfg, enh_cfg, enh_key,
                                                 params, retries, backoff_s, timeout_s)
            save_cache("enhance", pid, "", prompt["enhanced"])
            if prompt["enhanced"].get("error"):
                print(f"[增强] {pid} 失败：{prompt['enhanced']['error']}")
            else:
                print(f"[增强] {pid} 完成（{len(prompt['enhanced'].get('enhanced', ''))} 字符）")
            _pace(delay_between_s)

    # 阶段二/三：推理 + 裁判
    # 增强失败按 prompt 级计一次，不再逐模型复制 error（规格 §4.6.3）。
    enhanced_by_pid = {p["id"]: (p.get("enhanced") or {}) for p in prompts}
    enhance_failed_prompts: set[str] = {
        pid for pid, e in enhanced_by_pid.items() if e.get("error")
    }
    enhance_failures: list[dict[str, Any]] = [
        {
            "prompt_id": pid,
            "error": f"增强失败：{enhanced_by_pid[pid].get('error', '')}",
            "model_count": len(models),
        }
        for pid in sorted(enhance_failed_prompts)
    ]

    details: list[dict[str, Any]] = []
    for model in models:
        mid, label = model["id"], model.get("label", model["id"])
        for prompt in prompts:
            pid = prompt["id"]
            if pid in enhance_failed_prompts:
                continue  # 该 prompt 增强失败，跳过全部模型评测
            detail = {
                "prompt_id": pid,
                "model_id": mid,
                "model_label": label,
                "scenario": prompt.get("scenario") or "未分类",
                "scores": None,
                "reason": "",
                "latency_s": None,
                "output_len": None,
                "usage": {},
                "error": None,
            }
            temp = prompt.get("temperature")
            if temp is None:
                temp = cfg.get("scenario_temperatures", {}).get(prompt.get("scenario", ""))
            if temp is None:
                temp = float(params.get("temperature", INFER_TEMPERATURE))
            infer = _infer_one(mid, pid, enhanced_by_pid[pid].get("enhanced", ""), cfg, api_cfg, api_key,
                               params, retries, backoff_s, timeout_s, args.skip_infer, temperature=temp)
            detail["latency_s"] = infer.get("latency_s")
            detail["output_len"] = len(infer.get("text") or "")
            detail["usage"] = infer.get("usage", {})
            if infer.get("error"):
                detail["error"] = f"推理失败：{infer['error']}"
            elif not (infer.get("text") or "").strip():
                detail["error"] = "推理输出为空，跳过裁判"
            else:
                cached = load_cache("judge", pid, mid)
                if cached is not None:
                    verdict = cached
                elif args.skip_judge:
                    verdict = {"error": "无裁判缓存（--skip-judge）"}
                else:
                    verdict = _judge_one(prompt, enhanced_by_pid[pid].get("enhanced", ""), infer.get("text", ""),
                                         cfg, api_cfg, api_key, params, retries, backoff_s, timeout_s)
                    save_cache("judge", pid, mid, verdict)
                    _pace(delay_between_s)
            if verdict.get("error"):
                detail["error"] = f"裁判失败：{verdict['error']}"
            else:
                detail["scores"] = {d: verdict.get(d, verdict.get("relevance", 0)) for d in bench_judge.DIMENSIONS}
                detail["reason"] = verdict.get("reason", "")
            if detail["error"]:
                print(f"  [{mid} × {pid}] {detail['error']}")
            else:
                q_dims = ("accuracy", "completeness", "clarity")
                quality_mean = sum(detail["scores"][d] for d in q_dims) / len(q_dims)
                novelty = detail["scores"].get("novelty")
                novelty_suffix = f"，新颖度 {novelty:.0f}" if novelty is not None else ""
                print(f"  [{mid} × {pid}] 完成（{detail['output_len']} 字符，"
                      f"{detail['latency_s']:.1f}s，质量均分 {quality_mean:.1f}{novelty_suffix}）")
            details.append(detail)

    # 阶段四：聚合 + 报告
    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "evaluation/model_benchmark.py",
        "enhancer_model": cfg["enhancer"].get("model", ""),
        "judge_model": cfg["judge"].get("model", ""),
        "protocol": api_cfg.get("protocol", "anthropic"),
        "base_url": api_cfg.get("base_url", ""),
        "models": [{"id": m["id"], "label": m.get("label", m["id"])} for m in models],
        "prompts": [{"id": p["id"], "scenario": p.get("scenario") or "未分类", "len": len(p["text"])} for p in prompts],
        "scenario_weights": cfg.get("scenario_weights", {}),
        "prompt_count": len(prompts),
        "model_count": len(models),
        "params": params,
        "flags": {
            "skip_enhance": args.skip_enhance,
            "skip_infer": args.skip_infer,
            "skip_judge": args.skip_judge,
        },
    }
    payload = {"meta": meta, "details": details, "enhance_failures": enhance_failures}
    agg = bench_report.aggregate(payload, cfg.get("judge", {}).get("weights"))
    summary = {"meta": meta, **agg}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = bench_report.generate(out_dir, summary)

    print("\n================ 模型评测完成 ================")
    _print_table(agg["models"])
    print(f"\n报告：{report_path}")
    print(f"数据：{out_dir / 'summary.json'}")
    return 0


# ---- 各阶段调用 ----
def _enhance_prompt(original: str, cfg: dict, enh_cfg: dict, enh_key: str,
                    params: dict, retries: int, backoff_s: list[float], timeout_s: float) -> dict:
    """增强使用独立配置（enhancer 走 v3 openai 端点，与 targets 的 plan 端点解耦）。"""
    user_message = enhancer.build_user_message(
        original,
        target_model="评测目标",
        verbosity="standard",
    )
    messages = [
        {"role": "system", "content": enhancer.SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    result = bench_api.call_with_retry(
        lambda: bench_api.call_chat(
            base_url=enh_cfg.get("base_url", ""),
            api_key=enh_key,
            protocol=enh_cfg.get("protocol", "openai"),
            model=enh_cfg.get("model", ""),
            messages=messages,
            temperature=float(enh_cfg.get("temperature", 0.35)),
            max_tokens=int(params.get("max_tokens", 4096)),
            timeout_s=timeout_s,
        ),
        retries, backoff_s, "增强",
    )
    if result.error:
        return {"error": result.error}
    try:
        parsed = enhancer.parse_result(result.text)
        enhancer.validate_result(parsed)
        parsed = enhancer.normalize_result(parsed)
    except ValueError as exc:
        return {"error": f"增强结果校验失败：{exc}"}
    return {
        "enhanced": parsed.get("primary_prompt", ""),
        "task_type": parsed.get("task_type", "other"),
        "status": parsed.get("status", "ready"),
        "usage": result.usage,
    }


def _infer_one(mid: str, pid: str, enhanced: str, cfg: dict, api_cfg: dict, api_key: str,
               params: dict, retries: int, backoff_s: list[float], timeout_s: float,
               skip: bool, temperature: float = INFER_TEMPERATURE) -> dict:
    cached = load_cache("infer", pid, mid)
    if cached is not None:
        return cached
    if skip:
        return {"error": "无推理缓存（--skip-infer）"}
    messages = [{"role": "user", "content": enhanced}]
    result = bench_api.call_with_retry(
        lambda: bench_api.call_chat(
            base_url=api_cfg.get("base_url", ""),
            api_key=api_key,
            protocol=api_cfg.get("protocol", "anthropic"),
            model=mid,
            messages=messages,
            temperature=temperature,
            max_tokens=int(params.get("max_tokens", 4096)),
            timeout_s=timeout_s,
        ),
        retries, backoff_s, f"推理 {mid}×{pid}",
    )
    if result.error:
        return {"error": result.error}
    payload = {"text": result.text, "latency_s": result.latency_s, "usage": result.usage}
    save_cache("infer", pid, mid, payload)
    return payload


def _judge_one(prompt: dict, enhanced: str, answer: str, cfg: dict, api_cfg: dict,
               api_key: str, params: dict, retries: int, backoff_s: list[float],
               timeout_s: float) -> dict:
    out = bench_api.call_with_retry(
        lambda: bench_judge.judge_answer(
            task=prompt["text"],
            prompt=enhanced,
            answer=answer,
            api_cfg=api_cfg,
            judge_cfg=cfg.get("judge") or {},
            api_key=api_key,
            timeout_s=timeout_s,
        ),
        retries, backoff_s, f"裁判 {prompt['id']}",
    )
    if isinstance(out, bench_api.ChatResult):
        return {"error": out.error}
    return out


def _pace(delay_between_s: float) -> None:
    if delay_between_s > 0:
        time.sleep(delay_between_s)


def _print_table(models: list[dict[str, Any]]) -> None:
    print("| 排名 | 模型 | 质量均分 | 相关性 | 延迟(s) | 综合分 |")
    print("|---:|---|---:|---:|---:|---:|")
    for m in models:
        print(f"| {m['rank']} | {m.get('label', m['id'])} | {m['quality_mean']:.2f} | "
              f"{m['dims'].get('relevance', 0):.2f} | {m['latency_mean']:.2f} | {m['composite']:.3f} |")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模型横向评测（增强 → 推理 → 裁判打分 → 报告）")
    parser.add_argument("--config", default="benchmark_config.yaml", help="配置路径（默认 benchmark_config.yaml）")
    parser.add_argument("--models", nargs="+", help="只评测指定模型 id，可写多个：--models deepseek-v4-flash glm-5.2")
    parser.add_argument("--prompts", nargs="+", help="只评测指定提示词 id，可写多个")
    parser.add_argument("--skip-enhance", action="store_true", help="复用缓存的增强结果")
    parser.add_argument("--skip-infer", action="store_true", help="复用缓存的推理结果")
    parser.add_argument("--skip-judge", action="store_true", help="复用缓存的裁判结果")
    parser.add_argument("--max-prompts", type=int, default=None, help="最多评测的提示词数")
    parser.add_argument("--max-models", type=int, default=None, help="最多评测的模型数")
    parser.add_argument("--out-dir", default=None, help="报告输出目录（默认配置 run.out_dir）")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
