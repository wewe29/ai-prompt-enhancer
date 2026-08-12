"""PromptCraft 提示词增强前后效果评测主入口。

用法示例：
  python run_eval.py                    # 全量评测（增强 + 网页推理 + 裁判 + 报告）
  python run_eval.py --login            # 打开浏览器手动登录各网页版站点
  python run_eval.py --target doubao    # 只评测指定目标
  python run_eval.py --skip-enhance     # 复用已缓存的增强结果
  python run_eval.py --offline          # 完全离线自测（mock 目标 + 确定性裁判）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

# 允许从 evaluation 目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigError, load_config, resolve_api_key, resolve_judge_key  # noqa: E402
from enhancer import EnhanceError, enhance as api_enhance  # noqa: E402
from judge import JudgeError, judge_agreement, judge_pair, judge_prompt_level  # noqa: E402
import report as report_mod  # noqa: E402
import targets  # noqa: E402

RESULTS_ROOT = Path(__file__).resolve().parent / "results"
CACHE_DIR = RESULTS_ROOT / "_cache"

_FILLER = "请确保回答完整、准确、结构清晰，先给结论再展开，并检查是否有遗漏。"


def pad_to_length(text: str, target_len: int) -> str:
    """在原文后追加中性说明句，使长度落在 target_len 的 ±5% 区间内。

    仅用于 A/B/C 对照组的填充变体 B：B 与 C（增强版）的字符数差需 ≤5%（规格 §6.3）。
    - 原文已接近目标长度（±5%）→ 原样返回；
    - 原文过短 → 追加固定中性句直到 ≥95%，再截到 target_len；
    - 原文过长（>105%）→ 截断到 target_len（保持长度一致，不注入领域知识）。
    """
    if len(text) <= target_len * 1.05:
        if len(text) >= target_len:
            return text
        parts = [text]
        while len("".join(parts)) < target_len * 0.95:
            parts.append(_FILLER)
        joined = "".join(parts)
        return joined[:target_len] if len(joined) > target_len else joined
    return text[:target_len]


def _rep_key(repeats: int, rep: int) -> str:
    """重复实验缓存键后缀：N=1 无后缀（与旧缓存向后兼容），N>1 为 _rep<i>。"""
    return "" if int(repeats) <= 1 else f"_rep{rep}"


def resolve_control_group(cfg: dict[str, Any], no_control_group: bool) -> bool:
    """控制组开关：默认开启（config run.control_group，缺省 true），--no-control-group 覆盖为关闭。"""
    if no_control_group:
        return False
    return bool(cfg.get("run", {}).get("control_group", True))


def aggregate_judges(rep_judges: list[dict[str, Any] | None] | None) -> dict[str, Any] | None:
    """把多个重复的 judge 结果聚合为一个：deltas/得分取均值，winner 取多数（未过半则 tie）。

    judge 结构沿用 judge_pair 返回：{"original","enhanced","deltas","winner","reason",...}。
    """
    valid = [j for j in (rep_judges or []) if isinstance(j, dict) and isinstance(j.get("deltas"), dict)]
    if not valid:
        return None
    dims = sorted({d for j in valid for d in j["deltas"]})
    mean = lambda vals: sum(vals) / len(vals) if vals else 0.0
    scores = {
        side: {
            dim: mean([float(j[side].get(dim, 0.0)) for j in valid if isinstance(j.get(side), dict)])
            for dim in dims
        }
        for side in ("original", "enhanced")
    }
    deltas = {dim: mean([float(j["deltas"].get(dim, 0.0)) for j in valid]) for dim in dims}
    winners: dict[str, int] = {}
    for j in valid:
        winners[j.get("winner", "tie")] = winners.get(j.get("winner", "tie"), 0) + 1
    best, count = max(winners.items(), key=lambda kv: kv[1])
    winner = best if count * 2 > len(valid) else "tie"
    usage = {
        key: sum(int(j.get("usage", {}).get(key, 0) or 0) for j in valid)
        for key in ("input_tokens", "output_tokens")
    }
    reasons = [str(j.get("reason", "")).strip() for j in valid if j.get("reason")]
    return {
        "original": scores["original"],
        "enhanced": scores["enhanced"],
        "deltas": deltas,
        "winner": winner,
        "reason": "；".join(reasons)[:500],
        "swapped": False,
        "heuristics": valid[0].get("heuristics", {}),
        "usage": usage,
    }


def _aggregate_rep_outputs(rep_store: list[dict[str, Any]], variants: list[str], tid: str) -> dict[str, Any]:
    """把多 rep 的变体输出聚合为顶层字段：output 取首个成功；error 仅全失败时保留；延迟/估 token 取均值。"""
    out: dict[str, Any] = {}
    for variant in variants:
        successes = [r[f"{variant}_output"] for r in rep_store if r.get(f"{variant}_output")]
        errors = [r.get(f"{variant}_error") for r in rep_store if r.get(f"{variant}_error")]
        out[f"{variant}_output"] = successes[0] if successes else ""
        out[f"{variant}_error"] = None if successes else (errors[0] if errors else None)
        lat = [r[f"{variant}_latency_s"] for r in rep_store if r.get(f"{variant}_latency_s") is not None]
        out[f"{variant}_latency_s"] = round(sum(lat) / len(lat), 3) if lat else None
        toks = [r[f"{variant}_est_tokens"] for r in rep_store if r.get(f"{variant}_est_tokens") is not None]
        out[f"{variant}_est_tokens"] = sum(toks) if toks else None
        out[f"{variant}_model"] = tid
    return out


def _judge_variants(
    sample: dict[str, Any],
    tid: str,
    result: dict[str, Any],
    cfg: dict[str, Any],
    judge_key: str,
    offline: bool,
    cross_check: bool,
    second_model: str,
    control_group: bool,
) -> int:
    """对单个 result（单次运行或单个 rep）执行全部裁判，返回主对比是否完成（0/1）。

    主对比 = 原始 A vs 增强 C；控制组 = C-B（增强 vs 填充）与 B-A（填充 vs 原始）。
    """
    if result.get("judge") is not None:
        return 1
    if result.get("original_error") or result.get("enhanced_error") \
       or not result.get("original_output") or not result.get("enhanced_output"):
        return 0
    try:
        if offline:
            result["judge"] = judge_offline(sample, result["original_output"], result["enhanced_output"], cfg, "")
        else:
            result["judge"] = run_with_budget(
                lambda s=sample, r=result: judge_pair(s, r["original_output"], r["enhanced_output"], cfg, judge_key),
                cfg, "裁判",
            )
        if cross_check:
            try:
                if offline:
                    result["judge2"] = judge_offline(sample, result["original_output"], result["enhanced_output"], cfg, "")
                else:
                    result["judge2"] = run_with_budget(
                        lambda s=sample, r=result: judge_pair(
                            s, r["original_output"], r["enhanced_output"], cfg, judge_key, judge_model=second_model
                        ),
                        cfg, "第二裁判",
                    )
            except JudgeError as exc:
                result["judge2"] = None
                print(f"  [第二裁判] {sample['id']} × {tid} 失败：{exc}")
            if result.get("judge2") is not None:
                result["agreement"] = judge_agreement(result["judge"], result["judge2"])
    except JudgeError as exc:
        result["judge"] = None
        print(f"  [裁判] {sample['id']} × {tid} 失败：{exc}")
    if control_group:
        for ckey, a_out, b_out in (
            ("judge_control", result.get("padded_output"), result.get("enhanced_output")),
            ("judge_padded_vs_orig", result.get("padded_output"), result.get("original_output")),
        ):
            if not a_out or not b_out:
                continue
            try:
                if offline:
                    result[ckey] = judge_offline(sample, a_out, b_out, cfg, "")
                else:
                    result[ckey] = run_with_budget(
                        lambda s=sample, a=a_out, b=b_out: judge_pair(s, a, b, cfg, judge_key),
                        cfg, "控制组裁判",
                    )
            except JudgeError as exc:
                result[ckey] = None
                print(f"  [控制组裁判] {sample['id']} × {tid} 失败：{exc}")
    return 1 if result.get("judge") is not None else 0


# ---- 离线确定性实现（--offline，用于无网络自测）----
def enhance_offline(original_text: str, _cfg: dict, _key: str, **_kw) -> dict[str, Any]:
    return {
        "status": "ready",
        "primary_prompt": (
            f"请围绕以下任务给出可执行、结构清晰的结果：\n\n{original_text}\n\n"
            "要求：保留我的原始意图；只使用我提供的事实；"
            "如果存在关键信息缺失，先提出不超过 3 个澄清问题；不要添加与任务无关的模板化内容。"
        ),
        "assumptions": [], "questions": [], "changes": [],
        "suggestions": [
            {"id": f"s{i}", "kind": "goal", "title": f"建议{i}", "purpose": "离线模拟",
             "content": "补充完成标准", "operation": "insert", "anchor": "", "applied": False}
            for i in range(1, 6)
        ],
        "risk_flags": [],
        "_usage": {"input_tokens": len(original_text), "output_tokens": len(original_text) * 2},
    }


def judge_offline(_sample, original_output: str, enhanced_output: str, _cfg, _key) -> dict[str, Any]:
    def score(text: str) -> dict[str, int]:
        length = len(text)
        return {
            "accuracy": 7, "completeness": 7, "relevance": 7, "clarity": 7,
        } if length > 30 else {"accuracy": 5, "completeness": 5, "relevance": 5, "clarity": 5}

    original_scores = score(original_output)
    enhanced_scores = score(enhanced_output)
    winner = "enhanced" if sum(enhanced_scores.values()) > sum(original_scores.values()) else "tie"
    return {
        "original": original_scores,
        "enhanced": enhanced_scores,
        "deltas": {d: enhanced_scores[d] - original_scores[d] for d in ("accuracy", "completeness", "relevance", "clarity")},
        "winner": winner,
        "reason": "离线模拟：更长的回答视为更完整",
        "swapped": False,
        "heuristics": {},
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def prompt_judge_offline() -> dict[str, Any]:
    return {
        "structure": 8, "constraint_retention": 9, "info_gain": 7,
        "over_enhancement": 2, "reason": "离线模拟",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


# ---- 主流程 ----
def main() -> int:
    args = parse_args()
    try:
        cfg = load_config(args.config)
        if args.max_cost is not None:
            cfg["run"]["max_cost_usd"] = args.max_cost
    except ConfigError as exc:
        print(f"[错误] 配置无效：{exc}")
        return 1

    offline = args.offline
    if offline:
        print("[模式] 离线自测模式：全部目标使用 mock，增强与裁判使用确定性实现")
        for tcfg in cfg.setdefault("targets", {}).values():
            tcfg["mode"] = "mock"
    control_group = resolve_control_group(cfg, args.no_control_group)
    if control_group:
        print("[模式] A/B/C 三组对照：启用（原始 A / 填充 B / 增强 C，主比较 C-B，辅助 B-A）")
    else:
        print("[模式] 长度控制组：已禁用（--no-control-group）")
    repeats = args.repeats if args.repeats is not None else int(cfg.get("run", {}).get("repeats", 1))
    if repeats < 1:
        print("[错误] --repeats 必须 ≥ 1")
        return 1
    if repeats > 1:
        print(f"[模式] 重复实验：每个(样本×目标×变体)重复 {repeats} 次")

    regression_mode = args.regression
    samples_path = args.samples or cfg.get("samples")
    if regression_mode:
        samples_path = "samples_regression.yaml"
    elif samples_path and "regression" in Path(str(samples_path)).name:
        regression_mode = True
    if regression_mode:
        print("[模式] 回归测试：样本为已优化提示词，重点统计增强是否引入回退")

    samples = load_samples(samples_path)
    if not samples:
        print("[错误] 样本集为空")
        return 1

    if args.personas:
        personas = load_personas(Path(cfg["_eval_root"]) / "personas.yaml")
        if not personas:
            print("[错误] 用户画像文件缺失或为空")
            return 1
        expanded: list[dict[str, Any]] = []
        for sample in samples:
            expanded.append({**sample, "persona": None})
            for pid in args.personas:
                variant = (sample.get("personas") or {}).get(pid)
                if variant is None:
                    continue
                expanded.append({
                    **sample,
                    "persona": pid,
                    "persona_label": personas.get(pid, {}).get("label", pid),
                    "original": variant,
                })
        samples = expanded

    api_key = ""
    judge_key = ""
    if not offline:
        try:
            api_key = resolve_api_key(cfg)
            judge_key = resolve_judge_key(cfg, api_key)
        except ConfigError as exc:
            print(f"[错误] {exc}")
            return 1

    # 阶段一：增强
    if args.login:
        return run_login(cfg, args.target)

    for sample in samples:
        sample.setdefault("enhanced", {})
        cached = load_cache("enhance", sample["id"], persona=sample.get("persona") or "") if args.skip_enhance else None
        if cached:
            sample["enhanced"] = cached
        else:
            if offline:
                sample["enhanced"] = enhance_offline(sample["original"], cfg, "")
            else:
                try:
                    sample["enhanced"] = run_with_budget(
                        lambda: api_enhance(sample["original"], cfg, api_key, target_model="评测目标"),
                        cfg, "增强",
                    )
                except Exception as exc:  # EnhanceError/JudgeError/网络/解析等任何异常：单条隔离，不终止整批
                    sample["enhanced"] = {"error": str(exc)}
                    print(f"[增强] {sample['id']} 失败：{exc}")
            save_cache("enhance", sample["id"], payload=sample["enhanced"], persona=sample.get("persona") or "")
        sample["enhanced_text"] = sample["enhanced"].get("primary_prompt", "")
        if sample["enhanced"].get("error"):
            print(f"[增强] {sample['id']} 失败：{sample['enhanced']['error']}")

        # 阶段一.5：增强质量（prompt 级）裁判
        cached_pj = load_cache("prompt_judge", sample["id"], persona=sample.get("persona") or "")
        if cached_pj is not None:
            sample["prompt_judge"] = cached_pj
        elif args.manual or args.skip_prompt_judge or sample["enhanced"].get("error"):
            sample["prompt_judge"] = None
        else:
            try:
                if offline:
                    sample["prompt_judge"] = prompt_judge_offline()
                else:
                    sample["prompt_judge"] = run_with_budget(
                        lambda s=sample: judge_prompt_level(s["original"], s["enhanced_text"], cfg, judge_key),
                        cfg, "提示词裁判",
                    )
                save_cache("prompt_judge", sample["id"], payload=sample["prompt_judge"],
                           persona=sample.get("persona") or "")
                time.sleep(float(cfg["run"].get("delay_between", 2)))
            except JudgeError as exc:
                sample["prompt_judge"] = {"error": str(exc)}
                print(f"  [提示词裁判] {sample['id']} 失败：{exc}")

    # 人工答案模式：用户粘贴的回答 → 直接裁判 + 报告（跳过增强与网页抓取）
    if args.manual_answers:
        return run_manual_answers(args, cfg, samples)

    # 人工模式：输出复制粘贴清单后退出（不联网抓取）
    if args.manual:
        path = export_manual_list(samples, cfg)
        print(f"\n[manual] 人工评测清单已生成：{path}")
        print("[manual] 打开文件，把每条『原始提示词』和『增强提示词』分别粘贴到网页版测试，保存回答。")
        return 0

    # 阶段二：网页/目标推理
    enabled = {
        tid: tcfg for tid, tcfg in (cfg.get("targets") or {}).items()
        if tcfg.get("enabled") and (not args.target or tid in args.target)
    }
    if not enabled:
        print("[错误] 没有启用的目标模型，请在 config.yaml 中配置 targets 或使用 --target")
        return 1
    print(f"[目标] 启用：{', '.join(enabled)}")

    browser_manager = None
    if any(t["mode"] == "web" for t in enabled.values()) and not offline:
        from targets.web_adapter import BrowserManager
        browser_manager = BrowserManager(cfg.get("browser", {}), Path(cfg["_eval_root"]) / ".browser_profile")
        browser_manager.start()

    try:
        for tid, tcfg in enabled.items():
            adapter = targets.create_adapter(tid, tcfg, cfg, browser_manager=browser_manager, api_key=api_key)
            print(f"\n========== 目标模型：{tid} ==========")
            login_failed = False
            for sample in samples:
                sample.setdefault("results", {})[tid] = {"judge": None}
                result = sample["results"][tid]
                variants = ["original", "enhanced"]
                if control_group and sample["enhanced_text"]:
                    variants.append("padded")
                rep_store: list[dict[str, Any]] = []
                for rep in range(repeats):
                    suffix = _rep_key(repeats, rep)
                    rep_out: dict[str, Any] = result if repeats <= 1 else {"rep": rep}
                    for variant in variants:
                        cache_variant = variant + suffix
                        cached = load_cache("infer", sample["id"], tid, cache_variant,
                                            persona=sample.get("persona") or "") if args.skip_infer else None
                        if cached is not None:
                            rep_out[f"{variant}_output"] = cached.get("output", "")
                            rep_out[f"{variant}_error"] = cached.get("error")
                            rep_out[f"{variant}_latency_s"] = cached.get("latency_s")
                            rep_out[f"{variant}_est_tokens"] = cached.get("est_tokens")
                            rep_out[f"{variant}_model"] = cached.get("model") or tid
                            continue
                        if variant == "original":
                            prompt = sample["original"]
                        elif variant == "enhanced":
                            prompt = sample["enhanced_text"]
                        else:
                            prompt = pad_to_length(sample["original"], len(sample["enhanced_text"]))
                        if not prompt:
                            rep_out[f"{variant}_error"] = "无提示词（增强失败）"
                            continue
                        if login_failed:
                            rep_out[f"{variant}_error"] = "目标站点登录失败，已跳过"
                            continue
                        temp = sample.get("temperature")
                        if temp is None:
                            temp = cfg.get("scenario_temperatures", {}).get(sample.get("scenario", ""))
                        try:
                            start = time.perf_counter()
                            output = adapter.infer(prompt, temperature=temp)
                            latency_s = time.perf_counter() - start
                            est_tokens = math.ceil(len(output) / 1.8)
                            rep_out[f"{variant}_output"] = output
                            rep_out[f"{variant}_latency_s"] = latency_s
                            rep_out[f"{variant}_est_tokens"] = est_tokens
                            rep_out[f"{variant}_model"] = tid
                            save_cache("infer", sample["id"], tid, cache_variant,
                                       {"output": output, "latency_s": latency_s,
                                        "est_tokens": est_tokens, "model": tid, "error": None},
                                       persona=sample.get("persona") or "")
                            print(f"  [{sample['id']}][{variant}{suffix}] 完成（{len(output)} 字符）")
                        except targets.LoginRequired as exc:
                            login_failed = True
                            rep_out[f"{variant}_error"] = f"登录失败：{exc}"
                            print(f"  [{sample['id']}][{variant}] {exc}")
                        except targets.RateLimited as exc:
                            wait = float(cfg["run"].get("retry_seconds", 60))
                            print(f"  [{sample['id']}][{variant}] 限流，等待 {wait:.0f}s 后重试一次")
                            time.sleep(wait)
                            try:
                                start = time.perf_counter()
                                output = adapter.infer(prompt, temperature=temp)
                                latency_s = time.perf_counter() - start
                                est_tokens = math.ceil(len(output) / 1.8)
                                rep_out[f"{variant}_output"] = output
                                rep_out[f"{variant}_latency_s"] = latency_s
                                rep_out[f"{variant}_est_tokens"] = est_tokens
                                rep_out[f"{variant}_model"] = tid
                                save_cache("infer", sample["id"], tid, cache_variant,
                                           {"output": output, "latency_s": latency_s,
                                            "est_tokens": est_tokens, "model": tid, "error": None},
                                           persona=sample.get("persona") or "")
                            except targets.TargetError as retry_exc:
                                rep_out[f"{variant}_error"] = f"重试仍失败：{retry_exc}"
                        except targets.TargetError as exc:
                            rep_out[f"{variant}_error"] = str(exc)
                            print(f"  [{sample['id']}][{variant}] 失败：{exc}")
                        time.sleep(float(cfg["run"].get("delay_between", 2)))
                    if repeats > 1:
                        rep_store.append(rep_out)
                if repeats > 1:
                    result["reps"] = rep_store
                    result.update(_aggregate_rep_outputs(rep_store, variants, tid))
                if result.get("original_error") and result.get("enhanced_error"):
                    result["judge"] = None
            adapter.close()
    finally:
        if browser_manager:
            browser_manager.stop()

    # 阶段三：裁判
    judged = 0
    jcfg = cfg.get("judge", {})
    cross_check = bool(jcfg.get("cross_check")) and bool(jcfg.get("second_model"))
    second_model = jcfg.get("second_model", "")
    for sample in samples:
        for tid, result in (sample.get("results") or {}).items():
            reps = result.get("reps")
            if reps is not None:
                # 多 rep：逐 rep 裁判（可缓存）→ 均值/多数聚合到 result 顶层
                for rep in reps:
                    suffix = _rep_key(repeats, rep.get("rep", 0))
                    cached = load_cache("judge", sample["id"], tid, suffix,
                                        persona=sample.get("persona") or "") if args.skip_judge else None
                    if cached is not None:
                        for key in ("judge", "judge2", "judge_control", "judge_padded_vs_orig"):
                            rep[key] = cached.get(key)
                        continue
                    _judge_variants(sample, tid, rep, cfg, judge_key, offline,
                                    cross_check, second_model, control_group)
                    save_cache("judge", sample["id"], tid, suffix,
                               {key: rep.get(key) for key in
                                ("judge", "judge2", "judge_control", "judge_padded_vs_orig")},
                               persona=sample.get("persona") or "")
                result["judge"] = aggregate_judges([r.get("judge") for r in reps])
                result["judge_control"] = aggregate_judges([r.get("judge_control") for r in reps])
                result["judge_padded_vs_orig"] = aggregate_judges([r.get("judge_padded_vs_orig") for r in reps])
                if result["judge"] is not None:
                    judged += 1
                agg2 = aggregate_judges([r.get("judge2") for r in reps])
                if agg2 is not None and result.get("judge") is not None:
                    result["agreement"] = judge_agreement(result["judge"], agg2)
            else:
                judged += _judge_variants(sample, tid, result, cfg, judge_key, offline,
                                          cross_check, second_model, control_group)

    # 阶段四：报告
    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "targets": list(enabled.keys()),
        "enhance_model": cfg.get("enhancer", {}).get("model", ""),
        "judge_model": cfg.get("judge", {}).get("model", ""),
        "estimated_cost": round(_total_cost(samples), 4),
        "offline": offline,
        "control_group": control_group,
        "repeats": repeats,
        "regression_mode": regression_mode,
    }
    payload = {"meta": meta, "samples": samples}
    out_dir = report_mod.timestamp_dir(RESULTS_ROOT)
    report_mod.generate(out_dir, payload)

    agg = report_mod._aggregate(payload)
    s = agg["summary"]
    print("\n================ 评测完成 ================")
    print(f"有效对比：{s['total']} 组 | 增强更优：{s['improved']}（{s['improved_pct']}%）| 胜/平/负：{s['wins']}/{s['ties']}/{s['losses']}")
    if regression_mode:
        print(f"回归失败率：{100.0 * s.get('regression_failure_rate', 0.0):.1f}% | 变差率：{100.0 * s.get('regression_worse_rate', 0.0):.1f}%")
    print("各维度平均 Delta：", {d: agg['dimensions'][d]['mean'] for d in ("accuracy", "completeness", "relevance", "clarity")})
    print(f"预估成本：${meta['estimated_cost']:.4f}")
    print(f"报告目录：{out_dir}")
    print(f"  - report.md / report.html / summary.json / raw/")
    return 0


# ---- 辅助 ----
def run_manual_answers(args: argparse.Namespace, cfg: dict[str, Any], samples: list[dict[str, Any]]) -> int:
    """人工收集的回答（JSON）→ 裁判打分 → 生成报告。

    JSON 格式：
    {"answers": {"<sample_id>": {"<target_id>": {"original": "回答", "enhanced": "回答"}}}}
    每条样本的增强提示词从缓存读取（与清单一致）。
    """
    import json

    path = Path(args.manual_answers)
    if not path.exists():
        print(f"[错误] 找不到回答文件：{path}")
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    answers = data.get("answers", data)

    api_key = ""
    judge_key = ""
    if not args.offline:
        try:
            api_key = resolve_api_key(cfg)
            judge_key = resolve_judge_key(cfg, api_key)
        except ConfigError as exc:
            print(f"[错误] {exc}")
            return 1

    used, filled = 0, 0
    for sample in samples:
        enhanced = load_cache("enhance", sample["id"], persona=sample.get("persona") or "") or {}
        sample["enhanced"] = enhanced
        sample["enhanced_text"] = enhanced.get("primary_prompt", "")
        sample.setdefault("results", {})
        for tid, pair in (answers.get(sample["id"]) or {}).items():
            original_out = str(pair.get("original", "")).strip()
            enhanced_out = str(pair.get("enhanced", "")).strip()
            sample["results"][tid] = {
                "original_output": original_out,
                "enhanced_output": enhanced_out,
                "judge": None,
            }
            if original_out and enhanced_out:
                filled += 1

    judged = 0
    jcfg = cfg.get("judge", {})
    cross_check = bool(jcfg.get("cross_check")) and bool(jcfg.get("second_model"))
    second_model = jcfg.get("second_model", "")
    for sample in samples:
        for tid, result in (sample.get("results") or {}).items():
            if not result.get("original_output") or not result.get("enhanced_output"):
                print(f"  [跳过] {sample['id']} × {tid}：原始或增强回答缺失")
                continue
            try:
                if args.offline:
                    result["judge"] = judge_offline(sample, result["original_output"], result["enhanced_output"], cfg, "")
                else:
                    result["judge"] = run_with_budget(
                        lambda s=sample, r=result: judge_pair(s, r["original_output"], r["enhanced_output"], cfg, judge_key),
                        cfg, "裁判",
                    )
                judged += 1
                if cross_check:
                    try:
                        if args.offline:
                            result["judge2"] = judge_offline(sample, result["original_output"], result["enhanced_output"], cfg, "")
                        else:
                            result["judge2"] = run_with_budget(
                                lambda s=sample, r=result: judge_pair(
                                    s, r["original_output"], r["enhanced_output"], cfg, judge_key, judge_model=second_model
                                ),
                                cfg, "第二裁判",
                            )
                    except JudgeError as exc:
                        result["judge2"] = None
                        print(f"  [第二裁判] {sample['id']} × {tid} 失败：{exc}")
                    if result.get("judge2") is not None:
                        result["agreement"] = judge_agreement(result["judge"], result["judge2"])
            except JudgeError as exc:
                result["judge"] = None
                print(f"  [裁判] {sample['id']} × {tid} 失败：{exc}")

    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "targets": sorted({tid for s in samples for tid in s.get("results", {})}),
        "enhance_model": cfg.get("enhancer", {}).get("model", ""),
        "judge_model": cfg.get("judge", {}).get("model", ""),
        "estimated_cost": round(_total_cost(samples), 4),
        "manual_answers": True,
    }
    payload = {"meta": meta, "samples": samples}
    out_dir = report_mod.timestamp_dir(RESULTS_ROOT)
    report_mod.generate(out_dir, payload)
    agg = report_mod._aggregate(payload)
    s = agg["summary"]
    print("\n================ 人工答案评测完成 ================")
    print(f"填写组合：{filled} 组 | 裁判完成：{judged} 组 | 增强更优：{s['improved']}（{s['improved_pct']}%）| 胜/平/负：{s['wins']}/{s['ties']}/{s['losses']}")
    print(f"报告目录：{out_dir}")
    print(f"  - report.md / report.html / summary.json / raw/")
    return 0


def export_manual_list(samples: list[dict[str, Any]], cfg: dict[str, Any]) -> Path:
    """生成人工评测清单：md（记录用）+ html（带复制按钮，浏览器打开）。"""
    out_dir = Path(cfg["_eval_root"]) / "results" / "manual"
    out_dir.mkdir(parents=True, exist_ok=True)
    path_md = out_dir / "人工评测清单.md"
    lines = [
        "# 人工评测清单（提示词增强前后对比）",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本数：{len(samples)}",
        "",
        "## 使用方法",
        "1. 打开 豆包 / 千问 网页版（建议开新对话）；",
        "2. 把每条的【原始提示词】粘贴发送，复制保存回答；",
        "3. 新对话后粘贴【增强提示词】发送，复制保存回答；",
        "4. 把两组回答发给我，或贴进下方表格，我来做四维度打分对比。",
        "",
    ]
    for idx, sample in enumerate(samples, 1):
        lines.append(f"---")
        lines.append(f"## {idx}. {sample['id']}（{sample.get('scenario', '未分类')}）")
        lines.append("")
        lines.append("### 原始提示词")
        lines.append("```text")
        lines.append(sample["original"])
        lines.append("```")
        lines.append("")
        lines.append("### 增强提示词")
        enhanced = sample.get("enhanced") or {}
        if enhanced.get("error"):
            lines.append(f"> 增强失败：{enhanced['error']}")
        else:
            lines.append("```text")
            lines.append(sample.get("enhanced_text", ""))
            lines.append("```")
            if enhanced.get("status") == "needs_clarification":
                questions = enhanced.get("questions", [])
                if questions:
                    lines.append("")
                    lines.append("> 增强器提示需澄清：" + "；".join(q.get("text", "") for q in questions))
        lines.append("")
        lines.append("### 回答记录（人工粘贴后填写）")
        lines.append("| 版本 | 豆包回答 | 千问回答 |")
        lines.append("|---|---|---|")
        lines.append("| 原始版 | | |")
        lines.append("| 增强版 | | |")
        lines.append("")
    path_md.write_text("\n".join(lines), encoding="utf-8")
    path_html = out_dir / "人工评测清单.html"
    path_html.write_text(
        _build_manual_html(samples, time.strftime("%Y-%m-%d %H:%M:%S")), encoding="utf-8"
    )
    return path_html


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# 人工评测清单 HTML 版：提示词复制 + 回答粘贴（自动保存）+ 本地总结 + 复制答案/导出 answers.json
def _build_manual_html(samples: list[dict[str, Any]], generated_at: str) -> str:
    fields = [
        ("doubao_original", "豆包 · 原始版回答"),
        ("doubao_enhanced", "豆包 · 增强版回答"),
        ("qwen_original", "千问 · 原始版回答"),
        ("qwen_enhanced", "千问 · 增强版回答"),
        ("deepseek_original", "DeepSeek · 原始版回答"),
        ("deepseek_enhanced", "DeepSeek · 增强版回答"),
    ]
    css = (
        "body{font:14px/1.7 'Microsoft YaHei',system-ui,sans-serif;background:#11141b;color:#e5e7eb;"
        "max-width:920px;margin:0 auto;padding:24px 20px 60px}"
        "h1{font-size:20px}h2{font-size:16px;border-bottom:1px solid #2c3347;padding-bottom:6px;margin-top:30px}"
        ".card{background:#1a1f2b;border:1px solid #2c3347;border-radius:10px;padding:14px 16px;margin:12px 0}"
        ".lbl{color:#9aa3b5;font-size:12.5px;margin:10px 0 6px}"
        "pre{background:#0d1117;border:1px solid #2c3347;border-radius:8px;padding:12px;white-space:pre-wrap;"
        "word-break:break-word;font-size:13px;margin:0}"
        "button{background:#185FA5;color:#fff;border:0;border-radius:6px;padding:5px 14px;cursor:pointer;"
        "font-size:12.5px;margin:8px 8px 0 0}"
        "button:hover{background:#378ADD}button.alt{background:#0F6E56}"
        "textarea{width:100%;min-height:110px;background:#0d1117;border:1px solid #2c3347;border-radius:8px;"
        "color:#e5e7eb;padding:10px;font-size:13px;font-family:inherit;resize:vertical;box-sizing:border-box}"
        "textarea::placeholder{color:#5f6b7d}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}"
        "@media(max-width:720px){.grid{grid-template-columns:1fr}}"
        ".mini{color:#9aa3b5;font-size:12px;margin-bottom:4px}"
        ".sum{background:#222838;border:1px solid #2c3347;border-radius:8px;padding:10px 14px;margin-top:10px;"
        "font-size:13px;white-space:pre-wrap}.sum b{color:#6ea8fe}"
        ".steps{background:#222838;border-radius:8px;padding:10px 16px;color:#9aa3b5;font-size:13px}"
        ".tips{color:#9aa3b5;font-size:12.5px;margin-top:6px}"
    )
    head = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>人工评测清单</title><style>" + css + "</style></head><body>"
    )
    parts = [
        head,
        "<h1>人工评测清单（提示词增强前后对比）</h1>",
        f"<p style='color:#9aa3b5'>生成时间：{generated_at} · 样本数：{len(samples)}</p>",
        "<div class='steps'><b>使用步骤：</b>① 复制【原始版/增强版】提示词到 豆包/千问 网页版发送"
        "② 把回答粘贴进下方对应文本框（自动保存，刷新不丢）"
        "③ 点【本地总结】看要点归纳，或点【复制答案发给我】让我做 AI 打分对比，"
        "或点【导出 answers.json】后运行 <code>run_eval.py --manual-answers answers.json</code> 直接出报告</div>",
        "<div style='margin-top:10px'><button class='alt' onclick='exportJson()'>导出 answers.json（全部已填答案）</button>"
        "<span class='tips'> 导出后可运行：.\\\\.venv\\\\Scripts\\\\python.exe run_eval.py --manual-answers answers.json</span></div>",
    ]
    for idx, sample in enumerate(samples, 1):
        sid = sample["id"]
        parts.append(f"<h2>{idx}. {sid}（{sample.get('scenario', '未分类')}）</h2>")
        parts.append("<div class='card'>")
        parts.append("<div class='lbl'>原始提示词</div>")
        parts.append("<pre>" + _html_escape(sample["original"]) + "</pre>")
        parts.append("<button onclick='copyText(this)'>复制原始版</button>")
        enhanced = sample.get("enhanced") or {}
        parts.append("<div class='lbl'>增强提示词</div>")
        if enhanced.get("error"):
            parts.append("<pre>增强失败：" + _html_escape(enhanced["error"]) + "</pre>")
        else:
            parts.append("<pre>" + _html_escape(sample.get("enhanced_text", "")) + "</pre>")
            parts.append("<button onclick='copyText(this)'>复制增强版</button>")
            if enhanced.get("status") == "needs_clarification" and enhanced.get("questions"):
                parts.append("<div class='lbl' style='margin-top:8px'>增强器提示需澄清：" +
                             "；".join(_html_escape(q.get("text", "")) for q in enhanced["questions"]) + "</div>")
        parts.append("<div class='lbl'>回答记录（把网页版回答粘贴到对应文本框）</div>")
        parts.append("<div class='grid'>")
        for key, label in fields:
            parts.append(
                f"<div><div class='mini'>{label}</div>"
                f"<textarea id='ta_{sid}_{key}' placeholder='粘贴回答…' "
                f"oninput='saveVal(\"{sid}\",\"{key}\",this.value)'></textarea></div>"
            )
        parts.append("</div>")
        parts.append(f"<button class='alt' onclick='localSummarize(\"{sid}\")'>本地总结</button>")
        parts.append(f"<button onclick='copyAnswers(\"{sid}\", {idx})'>复制答案发给我（AI 总结打分）</button>")
        parts.append(f"<div class='sum' id='sum_{sid}' style='display:none'></div>")
        parts.append("</div>")
    js = (
        "<script>"
        "const FIELDS=['doubao_original','doubao_enhanced','qwen_original','qwen_enhanced',"
        "'deepseek_original','deepseek_enhanced'];"
        "const LABELS={doubao_original:'豆包·原始版',doubao_enhanced:'豆包·增强版',"
        "qwen_original:'千问·原始版',qwen_enhanced:'千问·增强版',"
        "deepseek_original:'DeepSeek·原始版',deepseek_enhanced:'DeepSeek·增强版'};"
        "const ORIG_KEYS=['doubao_original','qwen_original','deepseek_original'];"
        "const ENH_KEYS=['doubao_enhanced','qwen_enhanced','deepseek_enhanced'];"
        "function saveVal(sid,key,val){localStorage.setItem('pc_'+sid+'_'+key,val);}"
        "function loadVals(){document.querySelectorAll('textarea').forEach(ta=>{"
        "const m=/^ta_(.+)_(\\w+)$/.exec(ta.id);if(m){const v=localStorage.getItem('pc_'+m[1]+'_'+m[2]);"
        "if(v!==null)ta.value=v;}});}"
        "function getVals(sid){const o={};FIELDS.forEach(k=>{const ta=document.getElementById('ta_'+sid+'_'+k);"
        "o[k]=ta?ta.value.trim():'';});return o;}"
        "function copyText(btn){const pre=btn.parentElement.querySelector('pre');if(!pre)return;"
        "navigator.clipboard.writeText(pre.innerText).then(()=>{const old=btn.textContent;"
        "btn.textContent='已复制';setTimeout(()=>btn.textContent=old,1500);});}"
        "function summarize(t){const chars=t.replace(/\\s/g,'').length;"
        "const paras=t.split(/\\n\\s*\\n/).filter(x=>x.trim()).length;"
        "const sents=t.split(/[。！？!?；;\\n]/).map(x=>x.trim()).filter(x=>x.length>8);"
        "const kw=['建议','方案','原因','注意','步骤','结论','因为','所以','关键','问题','解决','应该','需要','推荐','避免','优势','不足','风险'];"
        "const pts=sents.filter(s=>kw.some(k=>s.includes(k))).slice(0,3).map(s=>s.slice(0,42));"
        "return {chars,paras,pts};}"
        "function localSummarize(sid){const v=getVals(sid);const out=[];"
        "FIELDS.forEach(k=>{const t=v[k];if(!t){out.push(LABELS[k]+'：未填写');return;}"
        "const s=summarize(t);out.push(LABELS[k]+'：'+s.chars+'字/'+s.paras+'段'+(s.pts.length?'，要点：'+s.pts.join('；'):''));});"
        "const ori=ORIG_KEYS.reduce((n,k)=>n+v[k].replace(/\\s/g,'').length,0);"
        "const enh=ENH_KEYS.reduce((n,k)=>n+v[k].replace(/\\s/g,'').length,0);"
        "if(ori+enh>0){const cmp=enh>ori?'增强版回答更长、信息更充分':(enh<ori?'增强版回答更精炼':'长度相当');"
        "out.push('对比：原始版共'+ori+'字 vs 增强版共'+enh+'字（'+cmp+'）');}"
        "const el=document.getElementById('sum_'+sid);el.style.display='block';"
        "el.innerHTML=out.join('\\n');}"
        "function copyAnswers(sid,idx){const v=getVals(sid);"
        "const t='第'+idx+'条（'+sid+'）：\\n豆包原始版：'+v.doubao_original+'\\n豆包增强版：'+v.doubao_enhanced+"
        "'\\n千问原始版：'+v.qwen_original+'\\n千问增强版：'+v.qwen_enhanced+"
        "'\\nDeepSeek原始版：'+v.deepseek_original+'\\nDeepSeek增强版：'+v.deepseek_enhanced;"
        "navigator.clipboard.writeText(t).then(()=>alert('已复制！把这段内容直接发给我，我做 AI 总结与打分。'));}"
        "function exportJson(){const answers={};document.querySelectorAll('.card').forEach(card=>{"
        "const ta=card.querySelector('textarea');if(!ta)return;"
        "const m=/^ta_(.+)_\\w+$/.exec(ta.id);if(!m)return;const sid=m[1];const v=getVals(sid);"
        "if(Object.values(v).some(x=>x)){answers[sid]={"
        "doubao:{original:v.doubao_original,enhanced:v.doubao_enhanced},"
        "qwen:{original:v.qwen_original,enhanced:v.qwen_enhanced},"
        "deepseek:{original:v.deepseek_original,enhanced:v.deepseek_enhanced}};}});"
        "const blob=new Blob([JSON.stringify({answers},null,2)],{type:'application/json'});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='answers.json';a.click();}"
        "loadVals();"
        "</script></body></html>"
    )
    parts.append(js)
    return "\n".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PromptCraft 增强效果评测")
    parser.add_argument("--config", default=None, help="config.yaml 路径（默认 evaluation/config.yaml）")
    parser.add_argument("--samples", default=None, help="样本 YAML 路径")
    parser.add_argument("--regression", action="store_true",
                        help="回归测试模式：使用 samples_regression.yaml，统计回归失败率/变差率")
    parser.add_argument("--target", action="extend", nargs="+", help="只评测指定 target，可写多个：--target doubao qwen")
    parser.add_argument("--personas", action="extend", nargs="+", help="按用户画像展开样本：--personas novice student office")
    parser.add_argument("--login", action="store_true", help="打开浏览器手动登录各网页站点后退出")
    parser.add_argument("--skip-enhance", action="store_true", help="复用缓存的增强结果")
    parser.add_argument("--skip-infer", action="store_true", help="复用缓存的目标回答")
    parser.add_argument("--skip-judge", action="store_true", help="复用缓存的裁判结果")
    parser.add_argument("--skip-prompt-judge", action="store_true", help="跳过增强质量(prompt 级)裁判")
    parser.add_argument("--max-cost", type=float, default=None, help="覆盖 max_cost_usd 预算")
    parser.add_argument("--offline", action="store_true", help="离线自测模式（mock 目标 + 确定性裁判）")
    parser.add_argument("--no-control-group", dest="no_control_group", action="store_true",
                        help="禁用长度控制组（默认开启 A/B/C 三组对照）")
    parser.add_argument("--repeats", type=int, default=None,
                        help="每个(样本×目标×变体)重复 N 次（默认取 config run.repeats，缺省 1）")
    parser.add_argument("--manual", action="store_true",
                        help="只生成人工评测清单（原始+增强提示词），供手动复制粘贴，不联网抓取")
    parser.add_argument("--manual-answers", default=None,
                        help="使用人工收集的回答直接出报告（JSON 文件，跳过增强与抓取）")
    return parser.parse_args(argv)


def load_samples(path: str | Path) -> list[dict[str, Any]]:
    import yaml
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    if not p.exists():
        print(f"[错误] 样本文件不存在：{p}")
        return []
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    samples = data.get("samples") or []
    for i, sample in enumerate(samples):
        sample.setdefault("id", f"sample_{i + 1}")
        sample.setdefault("scenario", "未分类")
        sample["original"] = (sample.get("original") or "").strip()
    return samples


def load_personas(path: str | Path) -> dict[str, dict[str, str]]:
    """加载用户画像定义（personas.yaml）→ {id: {label, note}}。"""
    import yaml
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    if not p.exists():
        print(f"[错误] 用户画像文件不存在：{p}")
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    personas = data.get("personas") or {}
    out: dict[str, dict[str, str]] = {}
    for pid, info in personas.items():
        if isinstance(info, dict):
            out[pid] = {"label": info.get("label", pid), "note": info.get("note", "")}
    return out


def load_cache(kind: str, sample_id: str, target: str = "", variant: str = "", persona: str = "") -> Any:
    name = f"{kind}_{sample_id}" + (f"_{persona}" if persona else "") + (f"_{target}" if target else "") + (f"_{variant}" if variant else "")
    path = CACHE_DIR / f"{name}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def save_cache(kind: str, sample_id: str, target: str = "", variant: str = "", payload: Any = None, persona: str = "") -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{kind}_{sample_id}" + (f"_{persona}" if persona else "") + (f"_{target}" if target else "") + (f"_{variant}" if variant else "")
    path = CACHE_DIR / f"{name}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for attempt in range(2):
        try:
            with open(path, "w", encoding="utf-8", errors="replace", newline="\n") as fh:
                fh.write(text)
            return
        except OSError:
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise


def _total_cost(samples: list[dict[str, Any]]) -> float:
    total = 0.0
    for sample in samples:
        usage = (sample.get("enhanced") or {}).get("_usage") or {}
        total += usage.get("input_tokens", 0) * 0.001 / 1000 + usage.get("output_tokens", 0) * 0.002 / 1000
        for result in (sample.get("results") or {}).values():
            judge = result.get("judge") or {}
            usage = judge.get("usage") or {}
            total += usage.get("input_tokens", 0) * 0.001 / 1000 + usage.get("output_tokens", 0) * 0.002 / 1000
    return total


def run_with_budget(fn, cfg: dict[str, Any], label: str):
    """预算保护：调用 fn 前后累计成本并检查上限。"""
    budget = float(cfg["run"].get("max_cost_usd", 2.0))
    if getattr(run_with_budget, "spent", 0.0) >= budget:
        raise JudgeError(f"已达到成本预算 ${budget}，停止继续调用（{label}）")
    result = fn()
    usage = result.get("_usage") if isinstance(result, dict) else None
    if isinstance(result, dict) and usage:
        cost = usage.get("input_tokens", 0) * 0.001 / 1000 + usage.get("output_tokens", 0) * 0.002 / 1000
        run_with_budget.spent = getattr(run_with_budget, "spent", 0.0) + cost
    return result


def run_login(cfg: dict[str, Any], only_targets: list[str] | None) -> int:
    """--login：打开浏览器逐站登录，按回车继续下一站，Ctrl+C 保留登录态。"""
    from targets.web_adapter import BrowserManager
    enabled = {
        tid: tcfg for tid, tcfg in (cfg.get("targets") or {}).items()
        if tcfg.get("enabled") and tcfg.get("mode") == "web" and (not only_targets or tid in only_targets)
    }
    if not enabled:
        print("[错误] 没有启用的网页版 target")
        return 1
    bm = BrowserManager(cfg.get("browser", {}), Path(cfg["_eval_root"]) / ".browser_profile")
    bm.start()
    try:
        for tid, tcfg in enabled.items():
            url = tcfg.get("url", "")
            page = bm.page_for(tid, url)
            print(f"\n=== 正在打开：{tid}（{url}）===")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                print(f"=== 页面已打开，请在浏览器中登录：{tid} ===")
            except Exception as exc:
                print(f"=== 页面打开失败（{exc}），可手动在地址栏输入 {url} 后登录：{tid} ===")
            print("完成登录后按回车进入下一站点（Ctrl+C 退出并保留登录态）")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                break
    finally:
        bm.stop()
    print("登录完成，登录态已保存在 .browser_profile/，可直接运行评测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
