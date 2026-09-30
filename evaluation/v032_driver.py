"""v0.3.2 可信评测执行辅助：prefetch（预取增强缓存）与 merge（按目标并行结果合并出报告）。

背景：v0.3.2 正式评测为 60 样本 × 4 目标 × repeats=3 × A/B/C 三组对照，单进程顺序运行
预计 15 小时以上；本工具将流程拆为：

  1. prefetch —— 一次性生成全部样本的增强结果与提示词级裁判并写入 _cache
     （run_eval.py --skip-enhance 可直接复用，避免各目标进程重复调用）；
  2. 每个目标一个进程并行运行：
       python run_eval.py --samples samples_v30.yaml --skip-enhance \
           --skip-prompt-judge --target plan_doubao --repeats 3 --max-cost 8
  3. merge    —— 按样本合并各目标结果目录的 samples.json，重新生成统一报告。

用法：
  python v032_driver.py prefetch [--samples samples_v30.yaml] [--max-cost 4]
  python v032_driver.py merge --runs results/<ts1> results/<ts2> ... [--human-review-count N]

仅服务于 v0.3.2 评测执行，不参与应用运行时。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT))


def cmd_prefetch(args: argparse.Namespace) -> int:
    import run_eval as R

    cfg = R.load_config(args.config)
    if args.max_cost is not None:
        cfg["run"]["max_cost_usd"] = args.max_cost
    api_key = R.resolve_api_key(cfg)
    judge_key = R.resolve_judge_key(cfg, api_key)
    samples = R.load_samples(args.samples or cfg.get("samples"))
    if not samples:
        print("[错误] 样本集为空")
        return 1
    delay = float(cfg["run"].get("delay_between", 2))
    print(f"[prefetch] 样本数：{len(samples)}｜增强模型：{cfg.get('enhancer', {}).get('model')}")
    ok = 0
    for i, sample in enumerate(samples, 1):
        sample.setdefault("enhanced", {})
        try:
            sample["enhanced"] = R.run_with_budget(
                lambda s=sample: R.api_enhance(s["original"], cfg, api_key, target_model="评测目标"),
                cfg, "增强",
            )
        except Exception as exc:  # 与 run_eval.main 一致：单条隔离
            sample["enhanced"] = {"error": str(exc)}
            print(f"[prefetch {i}/{len(samples)}] 增强 {sample['id']} 失败：{exc}")
        R.save_cache("enhance", sample["id"], payload=sample["enhanced"])
        sample["enhanced_text"] = sample["enhanced"].get("primary_prompt", "")
        if not sample["enhanced"].get("error"):
            ok += 1
            try:
                pj = R.run_with_budget(
                    lambda s=sample: R.judge_prompt_level(s["original"], s["enhanced_text"], cfg, judge_key),
                    cfg, "提示词裁判",
                )
                R.save_cache("prompt_judge", sample["id"], payload=pj)
            except Exception as exc:
                print(f"[prefetch {i}/{len(samples)}] 提示词裁判 {sample['id']} 失败：{exc}")
            time.sleep(delay)
        status = sample["enhanced"].get("delivery_status", "error" if sample["enhanced"].get("error") else "?")
        print(f"[prefetch {i}/{len(samples)}] {sample['id']} 完成（{status}）")
    print(f"[prefetch] 完成：{ok}/{len(samples)} 增强成功｜预估成本 ${R.run_with_budget.spent:.4f}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    import run_eval as R
    import report as report_mod

    runs = [Path(p) for p in args.runs]
    if len(runs) < 2:
        print("[错误] --runs 至少提供 2 个结果目录")
        return 1
    payloads: list[dict[str, Any]] = []
    for d in runs:
        p = d / "samples.json" if not d.name == "samples.json" else d
        if not p.exists():
            print(f"[错误] 找不到 {p}")
            return 1
        payloads.append(json.loads(p.read_text(encoding="utf-8")))

    # 以 (id, persona) 为键做并集合并：既支持同一批样本按目标拆分，也支持样本切片并行
    base = payloads[0]
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    repeats_seen: set[Any] = set()
    for p_i, payload in enumerate(payloads):
        repeats_seen.add((payload.get("meta") or {}).get("repeats"))
        for s in payload.get("samples", []):
            key = (s.get("id"), s.get("persona") or "")
            if key not in merged:
                merged[key] = json.loads(json.dumps(s, ensure_ascii=False))
                merged[key]["results"] = {}
                order.append(key)
            for tid, result in (s.get("results") or {}).items():
                if tid in merged[key]["results"]:
                    print(f"[错误] 目标 {tid} 在 {key[0]} 上重复出现，请检查 --runs 是否重叠")
                    return 1
                merged[key]["results"][tid] = result

    meta = dict(base.get("meta") or {})
    meta["targets"] = [t for p in payloads for t in (p.get("meta") or {}).get("targets", [])]
    if len(repeats_seen) > 1:
        print(f"[错误] 各运行目录 repeats 不一致：{repeats_seen}")
        return 1
    meta["repeats"] = repeats_seen.pop()
    samples_out = [merged[k] for k in order]
    meta["estimated_cost"] = round(R._total_cost(samples_out), 4)
    if args.human_review_count is not None:
        meta["human_review_count"] = int(args.human_review_count)
    payload = {"meta": meta, "samples": samples_out}

    out_dir = report_mod.timestamp_dir(R.RESULTS_ROOT)
    report_mod.generate(out_dir, payload)
    agg = report_mod._aggregate(payload)
    s = agg["summary"]
    print(f"[merge] 合并 {len(runs)} 个运行目录，目标：{meta['targets']}")
    print(f"[merge] 有效对比：{s['total']} 组｜胜/平/负：{s['wins']}/{s['ties']}/{s['losses']}")
    print(f"[merge] 报告目录：{out_dir}")
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    """把样本集按 round-robin 切成 N 份（用于按目标×切片并行运行），确定性可复现。"""
    import yaml

    src = Path(args.samples)
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    samples = data.get("samples") or []
    if args.n < 2:
        print("[错误] --n 至少为 2")
        return 1
    stem = src.stem
    for i in range(args.n):
        part = samples[i::args.n]
        out = src.with_name(f"{stem}_s{i}.yaml")
        out.write_text(yaml.safe_dump({"samples": part}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"[slice] {out}（{len(part)} 条）")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """从合并结果中提取评测报告必需事实：失败清单、超时比例、交付状态、按目标覆盖。"""
    samples_path = Path(args.samples)
    data = json.loads(samples_path.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    gen_total = gen_timeout = 0
    rep_with_error = 0
    coverage: dict[str, dict[str, int]] = {}
    for sample in data.get("samples", []):
        enhanced = sample.get("enhanced") or {}
        if enhanced.get("error"):
            failures.append({"sample_id": sample["id"], "stage": "enhance", "detail": enhanced["error"]})
        for tid, result in (sample.get("results") or {}).items():
            cov = coverage.setdefault(tid, {"samples": 0, "judged": 0, "rep_error": 0, "rep_total": 0})
            cov["samples"] += 1
            if result.get("judge"):
                cov["judged"] += 1
            reps = result.get("reps") or [result]
            for rep in reps:
                cov["rep_total"] += 1
                has_err = False
                for variant in ("original", "enhanced", "padded"):
                    err = rep.get(f"{variant}_error")
                    if err:
                        has_err = True
                        gen_total += 1
                        if "timed out" in str(err) or "timeout" in str(err).lower():
                            gen_timeout += 1
                        failures.append({"sample_id": sample["id"], "target": tid,
                                         "stage": f"gen:{variant}", "detail": str(err)[:160]})
                    elif rep.get(f"{variant}_output") is not None and rep.get(f"{variant}_output") != "":
                        gen_total += 1
                if has_err:
                    cov["rep_error"] += 1
                    rep_with_error += 1
                for jk in ("judge", "judge2", "judge_control", "judge_padded_vs_orig"):
                    if rep.get(jk) is None and not rep.get("original_error") and not rep.get("enhanced_error"):
                        failures.append({"sample_id": sample["id"], "target": tid,
                                         "stage": f"judge:{jk}", "detail": "judge 缺失（生成缺失或裁判失败）"})
    delivery = {"complete": 0, "partial": 0, "fallback": 0, "hard_failure": 0}
    for sample in data.get("samples", []):
        enhanced = sample.get("enhanced") or {}
        if not enhanced:
            continue
        status = "hard_failure" if enhanced.get("error") else enhanced.get("delivery_status", "complete")
        delivery[status if status in delivery else "complete"] += 1
    out = {
        "gen_calls_recorded": gen_total,
        "gen_timeout": gen_timeout,
        "gen_timeout_ratio": round(gen_timeout / gen_total, 4) if gen_total else None,
        "delivery": delivery,
        "delivery_rate": round(1 - delivery["hard_failure"] / max(1, sum(delivery.values())), 4),
        "coverage_by_target": coverage,
        "failure_count": len(failures),
        "failures": failures,
    }
    dest = Path(args.out) if args.out else samples_path.with_name("v032_facts.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[extract] 交付状态：{delivery}｜有效率 {out['delivery_rate']:.1%}")
    print(f"[extract] 生成调用 {gen_total}｜超时 {gen_timeout}（{out['gen_timeout_ratio']:.1%}）" if gen_total else "[extract] 无生成记录")
    print(f"[extract] 失败/缺失条目 {len(failures)} 个 → {dest}")
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """以 _cache 为唯一事实源重建合并 samples.json 并生成统一报告。

    正式评测按 目标×切片 拆成多个并行进程，各进程都写同一份缓存；本命令把缓存重组成
    完整 payload（样本元数据 + 每目标 3 个 rep 的生成与全部裁判），再走 report.generate。
    """
    import run_eval as R
    import report as report_mod
    import yaml

    import judge as judge_mod

    cfg = R.load_config(args.config)
    samples_def = yaml.safe_load((EVAL_ROOT / (args.samples or cfg.get("samples"))).read_text(encoding="utf-8"))
    targets = [t for t, tc in (cfg.get("targets") or {}).items() if tc.get("enabled")]
    repeats = int(args.repeats)

    # 缓存索引（文件名规则见 run_eval.save_cache）：
    #   infer_<sample>_<target>_<variant>_rep<k>.json（variant ∈ original/enhanced/padded）
    #   judge_<sample>_<target>__rep<k>.json（variant 位为空 → 双下划线）
    # 注意样本 id 可能含 "_rep" 子串，必须用已知目标列表从尾部精确切分
    index: dict[str, dict[tuple[str, str, str], Any]] = {"infer": {}, "judge": {}}
    target_names = sorted((t for t in (cfg.get("targets") or {})), key=len, reverse=True)
    for path in R.CACHE_DIR.glob("*.json"):
        stem = path.stem
        kind = "infer" if stem.startswith("infer_") else ("judge" if stem.startswith("judge_") else None)
        if kind is None:
            continue
        body = stem[len(kind) + 1:]
        m = re.search(r"_rep(\d+)$", body)
        if not m:
            continue
        rep = int(m.group(1))
        rest = body[:m.start()]
        if kind == "infer":
            variant_m = re.search(r"_(original|enhanced|padded)$", rest)
            if not variant_m:
                continue
            variant = variant_m.group(1)
            head = rest[:variant_m.start()]
            for t in target_names:
                if head.endswith("_" + t):
                    index["infer"][(head[: -len(t) - 1], t, f"{variant}|{rep}")] = json.loads(
                        path.read_text(encoding="utf-8"))
                    break
        else:
            for t in target_names:
                # save_cache(kind, sid, tid, "_rep<k>") 生成双下划线：<sid>_<tid>__rep<k>
                if rest.endswith("_" + t + "_"):
                    index["judge"][(rest[: -len(t) - 2], t, str(rep))] = json.loads(
                        path.read_text(encoding="utf-8"))
                    break

    out_samples: list[dict[str, Any]] = []
    for sd in samples_def.get("samples", []):
        sid = sd["id"]
        sample = dict(sd)
        enh = R.load_cache("enhance", sid)
        sample["enhanced"] = enh or {"error": "enhance 缓存缺失"}
        sample["enhanced_text"] = (enh or {}).get("primary_prompt", "")
        pj = R.load_cache("prompt_judge", sid)
        sample["prompt_judge"] = pj
        results: dict[str, Any] = {}
        for tid in targets:
            variants = ["original", "enhanced", "padded"]
            rep_store: list[dict[str, Any]] = []
            for rep in range(repeats):
                rep_out: dict[str, Any] = {"rep": rep}
                ok = True
                for variant in variants:
                    cached = index["infer"].get((sid, tid, f"{variant}|{rep}"))
                    if cached is None or not (cached.get("output") or "").strip():
                        rep_out[f"{variant}_error"] = "生成缺失（多次重试后仍失败/超时）"
                        rep_out[f"{variant}_output"] = ""
                        ok = False
                    else:
                        rep_out[f"{variant}_output"] = cached.get("output", "")
                        rep_out[f"{variant}_error"] = None
                        rep_out[f"{variant}_latency_s"] = cached.get("latency_s")
                        rep_out[f"{variant}_est_tokens"] = cached.get("est_tokens")
                        rep_out[f"{variant}_model"] = cached.get("model") or tid
                jc = index["judge"].get((sid, tid, str(rep)))
                for key in ("judge", "judge2", "judge_control", "judge_padded_vs_orig"):
                    rep_out[key] = (jc or {}).get(key)
                rep_store.append(rep_out)
            result: dict[str, Any] = {}
            result["reps"] = rep_store
            result.update(R._aggregate_rep_outputs(rep_store, variants, tid))
            result["judge"] = R.aggregate_judges([r.get("judge") for r in rep_store])
            result["judge_control"] = R.aggregate_judges([r.get("judge_control") for r in rep_store])
            result["judge_padded_vs_orig"] = R.aggregate_judges([r.get("judge_padded_vs_orig") for r in rep_store])
            agg2 = R.aggregate_judges([r.get("judge2") for r in rep_store])
            if agg2 is not None and result["judge"] is not None:
                result["agreement"] = R.judge_agreement(result["judge"], agg2)
            results[tid] = result
        sample["results"] = results
        out_samples.append(sample)

    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "targets": targets,
        "enhance_model": cfg.get("enhancer", {}).get("model", ""),
        "judge_model": cfg.get("judge", {}).get("model", ""),
        "estimated_cost": round(R._total_cost(out_samples), 4),
        "offline": False,
        "control_group": True,
        "repeats": repeats,
        "regression_mode": False,
        "human_review_count": int(args.human_review_count or 0),
        "judge_version": judge_mod.JUDGE_PROMPT_VERSION,
        "rebuild_from_cache": True,
    }
    payload = {"meta": meta, "samples": out_samples}
    out_dir = report_mod.timestamp_dir(R.RESULTS_ROOT) if not args.out_dir else Path(args.out_dir)
    report_mod.generate(out_dir, payload)
    (out_dir / "samples.json")
    agg = report_mod._aggregate(payload)
    s = agg["summary"]
    print(f"[rebuild] 样本 {len(out_samples)}｜目标 {targets}｜repeats={repeats}")
    print(f"[rebuild] 有效对比：{s['total']} 组｜胜/平/负：{s['wins']}/{s['ties']}/{s['losses']}")
    print(f"[rebuild] 报告目录：{out_dir}")
    return 0


def cmd_judge_fill(args: argparse.Namespace) -> int:
    """只对「三变体已生成齐且缺裁判缓存」的单元补裁判，不触发任何生成调用。"""
    import run_eval as R
    import yaml

    cfg = R.load_config(args.config)
    api_key = R.resolve_api_key(cfg)
    judge_key = R.resolve_judge_key(cfg, api_key)
    if args.max_cost is not None:
        cfg["run"]["max_cost_usd"] = args.max_cost
    jcfg = cfg.get("judge", {})
    cross_check = bool(jcfg.get("cross_check")) and bool(jcfg.get("second_model"))
    second_model = jcfg.get("second_model", "")

    sample_ids = set()
    sample_defs: dict[str, dict[str, Any]] = {}
    for sd in yaml.safe_load((EVAL_ROOT / args.samples).read_text(encoding="utf-8"))["samples"]:
        sample_ids.add(sd["id"])
        sample_defs[sd["id"]] = sd
    targets = [t for t, tc in (cfg.get("targets") or {}).items() if tc.get("enabled")]

    todo: list[tuple[str, str, int]] = []
    for path in R.CACHE_DIR.glob("infer_*.json"):
        stem = path.stem[len("infer_"):]
        m = re.search(r"_rep(\d+)$", stem)
        if not m:
            continue
        rep = int(m.group(1))
        variant_m = re.search(r"_(original|enhanced|padded)$", stem[:m.start()])
        if not variant_m or variant_m.group(1) != "original":
            continue
        head = stem[:variant_m.start()]
        for t in targets:
            if head.endswith("_" + t):
                sid = head[: -len(t) - 1]
                if sid not in sample_ids:
                    break
                variants = {}
                for v in ("original", "enhanced", "padded"):
                    c = R.load_cache("infer", sid, t, f"{v}_rep{rep}")
                    variants[v] = (c or {}).get("output") or ""
                if all(variants.values()) and R.load_cache("judge", sid, t, f"_rep{rep}") is None:
                    todo.append((sid, t, rep))
                break
    print(f"[judge-fill] 待补裁判单元：{len(todo)}")
    done = failed = 0
    for sid, tid, rep in todo:
        result: dict[str, Any] = {}
        for v in ("original", "enhanced", "padded"):
            result[f"{v}_output"] = (R.load_cache("infer", sid, tid, f"{v}_rep{rep}") or {}).get("output", "")
        sample = dict(sample_defs.get(sid, {"id": sid, "original": ""}))
        try:
            R._judge_variants(sample, tid, result, cfg, judge_key, False, cross_check, second_model, True)
            R.save_cache("judge", sid, tid, f"_rep{rep}",
                         {k: result.get(k) for k in ("judge", "judge2", "judge_control", "judge_padded_vs_orig")})
            if result.get("judge") is not None:
                done += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"[judge-fill] {sid} × {tid} rep{rep} 失败：{str(exc)[:120]}")
    print(f"[judge-fill] 完成 {done}｜失败 {failed}")
    return 0


def cmd_rejudge(args: argparse.Namespace) -> int:
    """对本机缓存中的已生成数据用当前裁判规则逐条重判（不触发任何生成调用）。

    与 judge-fill 的区别：rejudge 面向"规则换版"——旧裁判结果整体迁出缓存后，
    用当前 judge.py 规则对全部已生成 rep 重新打分；同时补齐 sample 的
    enhanced_text（judge-fill 曾遗漏，导致裁判消息中提示词 B 为空）。
    """
    import run_eval as R
    import yaml

    cfg = R.load_config(args.config)
    api_key = R.resolve_api_key(cfg)
    judge_key = R.resolve_judge_key(cfg, api_key)
    if args.max_cost is not None:
        cfg["run"]["max_cost_usd"] = args.max_cost
    jcfg = cfg.get("judge", {})
    cross_check = bool(jcfg.get("cross_check")) and bool(jcfg.get("second_model"))
    second_model = jcfg.get("second_model", "")

    sample_ids = set()
    sample_defs: dict[str, dict[str, Any]] = {}
    for sd in yaml.safe_load((EVAL_ROOT / args.samples).read_text(encoding="utf-8"))["samples"]:
        sample_ids.add(sd["id"])
        sample_defs[sd["id"]] = sd
    targets = [
        t for t, tc in (cfg.get("targets") or {}).items()
        if tc.get("enabled") and (not args.target or t in args.target)
    ]

    import judge as judge_mod

    print(f"[rejudge] 规则版本 {judge_mod.JUDGE_PROMPT_VERSION}｜目标 {targets}")
    todo: list[tuple[str, str, int]] = []
    for path in sorted(R.CACHE_DIR.glob("infer_*.json")):
        stem = path.stem[len("infer_"):]
        m = re.search(r"_rep(\d+)$", stem)
        if not m:
            continue
        rep = int(m.group(1))
        variant_m = re.search(r"_(original|enhanced|padded)$", stem[:m.start()])
        if not variant_m or variant_m.group(1) != "original":
            continue
        head = stem[:variant_m.start()]
        for t in targets:
            if head.endswith("_" + t):
                sid = head[: -len(t) - 1]
                if sid not in sample_ids:
                    break
                if R.load_cache("judge", sid, t, f"_rep{rep}") is not None:
                    break
                variants = {}
                for v in ("original", "enhanced", "padded"):
                    c = R.load_cache("infer", sid, t, f"{v}_rep{rep}")
                    variants[v] = (c or {}).get("output") or ""
                if variants["original"] and variants["enhanced"]:
                    todo.append((sid, t, rep))
                break
    print(f"[rejudge] 待重判单元：{len(todo)}")
    done = failed = 0
    for sid, tid, rep in todo:
        result: dict[str, Any] = {}
        for v in ("original", "enhanced", "padded"):
            result[f"{v}_output"] = (R.load_cache("infer", sid, tid, f"{v}_rep{rep}") or {}).get("output", "")
        sample = dict(sample_defs.get(sid, {"id": sid, "original": ""}))
        sample["enhanced_text"] = (R.load_cache("enhance", sid) or {}).get("primary_prompt", "")
        try:
            R._judge_variants(sample, tid, result, cfg, judge_key, False, cross_check, second_model, True)
            R.save_cache("judge", sid, tid, f"_rep{rep}",
                         {k: result.get(k) for k in ("judge", "judge2", "judge_control", "judge_padded_vs_orig")})
            if result.get("judge") is not None:
                done += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"[rejudge] {sid} × {tid} rep{rep} 失败：{str(exc)[:120]}")
    print(f"[rejudge] 完成 {done}｜失败 {failed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="v0.3.2 可信评测执行辅助")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("prefetch", help="预生成增强与提示词裁判缓存")
    p_pre.add_argument("--samples", default=None, help="样本 YAML 路径（默认取 config）")
    p_pre.add_argument("--config", default=None, help="config.yaml 路径")
    p_pre.add_argument("--max-cost", type=float, default=None, help="覆盖预算（美元）")
    p_pre.set_defaults(fn=cmd_prefetch)

    p_merge = sub.add_parser("merge", help="合并多个按目标运行的结果目录并出报告")
    p_merge.add_argument("--runs", nargs="+", required=True, help="各目标运行的结果目录")
    p_merge.add_argument("--human-review-count", type=int, default=None,
                         help="人工盲评比较数（写入 meta，用于报告结论门槛）")
    p_merge.set_defaults(fn=cmd_merge)

    p_slice = sub.add_parser("slice", help="把样本 YAML round-robin 切成 N 份（并行运行用）")
    p_slice.add_argument("--samples", default="samples_v30.yaml", help="源样本 YAML")
    p_slice.add_argument("--n", type=int, default=3, help="切片数")
    p_slice.set_defaults(fn=cmd_slice)

    p_ext = sub.add_parser("extract", help="提取评测报告必需事实（失败清单/超时比例/交付状态）")
    p_ext.add_argument("--samples", required=True, help="合并后的 samples.json")
    p_ext.add_argument("--out", default=None, help="输出 JSON 路径（默认同目录 v032_facts.json）")
    p_ext.set_defaults(fn=cmd_extract)

    p_rb = sub.add_parser("rebuild", help="以 _cache 为事实源重建合并数据并生成统一报告")
    p_rb.add_argument("--samples", default=None, help="样本 YAML（默认取 config）")
    p_rb.add_argument("--config", default=None, help="config.yaml 路径")
    p_rb.add_argument("--repeats", type=int, default=3, help="重复次数（默认 3）")
    p_rb.add_argument("--out-dir", default=None, help="报告输出目录（默认 results/<时间戳>）")
    p_rb.add_argument("--human-review-count", type=int, default=None, help="人工盲评比较数（写入 meta）")
    p_rb.set_defaults(fn=cmd_rebuild)

    p_jf = sub.add_parser("judge-fill", help="只对已生成齐但缺裁判的单元补裁判（不触发生成）")
    p_jf.add_argument("--samples", default="samples_v30.yaml", help="样本 YAML")
    p_jf.add_argument("--config", default=None, help="config.yaml 路径")
    p_jf.add_argument("--max-cost", type=float, default=None, help="覆盖预算（美元）")
    p_jf.set_defaults(fn=cmd_judge_fill)

    p_rj = sub.add_parser("rejudge", help="用当前裁判规则对缓存中已生成数据逐条重判（不触发生成）")
    p_rj.add_argument("--samples", default="samples_v30.yaml", help="样本 YAML（限定样本范围）")
    p_rj.add_argument("--config", default=None, help="config.yaml 路径")
    p_rj.add_argument("--target", action="extend", nargs="+", help="只处理指定目标")
    p_rj.add_argument("--max-cost", type=float, default=None, help="覆盖预算（美元）")
    p_rj.set_defaults(fn=cmd_rejudge)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
