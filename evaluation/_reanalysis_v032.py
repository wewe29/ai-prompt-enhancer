#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v0.3.2 盲评数据的独立再分析（只读本地已有数据，不发任何 API 请求）。

目的：
  1. 复算 docs/PromptCraft-v0.3.2-评测报告.md §5.2 的人工/自动裁判分层表
  2. 复算 docs/evaluation-data-v032/blind_review_agreement.json 里的每一个数字
  3. 量化"排除平局样本"对一致性指标的影响
  4. 检验 dims_mad_vs_judge / overall_mad 的尺度归一化方式是否可复现

用法:
  python evaluation/_reanalysis_v032.py
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "evaluation-data-v032")

DIMS = ("accuracy", "completeness", "relevance", "clarity")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def fmt_pct(x):
    return f"{100 * x:5.1f}%"


def cohen_kappa(pairs):
    """pairs: list of (label_a, label_b)，标签集合由数据决定。"""
    cats = sorted({x for p in pairs for x in p})
    n = len(pairs)
    if n == 0:
        return float("nan")
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    m = [[0] * k for _ in range(k)]
    for a, b in pairs:
        m[idx[a]][idx[b]] += 1
    po = sum(m[i][i] for i in range(k)) / n
    pe = 0.0
    for i in range(k):
        ri = sum(m[i]) / n
        ci = sum(m[j][i] for j in range(k)) / n
        pe += ri * ci
    if abs(1 - pe) < 1e-12:
        return float("nan")
    return (po - pe) / (1 - pe)


def bootstrap_ci(stat_fn, data, n_boot=20000, seed=20261020, alpha=0.05):
    rng = random.Random(seed)
    n = len(data)
    vals = []
    for _ in range(n_boot):
        sample = [data[rng.randrange(n)] for _ in range(n)]
        v = stat_fn(sample)
        if v is not None and not math.isnan(v):
            vals.append(v)
    vals.sort()
    lo = vals[int(alpha / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return lo, hi


def main():
    key = load("blind_key.json")
    ans = load("blind_answers.json")
    agr = load("blind_review_agreement.json")
    facts = load("v032_facts.json")
    summ = load("summary.json")

    key_by_item = {k["item"]: k for k in key}
    print(f"[载入] blind_key={len(key)} blind_answers={len(ans)} "
          f"agreement.rows={len(agr['rows'])}")

    # ---------- 身份编码检查 ----------
    print("\n=== 1. 身份编码检查（产物能否回溯到样本） ===")
    ans_keys = set()
    for a in ans:
        ans_keys |= set(a.keys())
    key_keys = set()
    for k in key:
        key_keys |= set(k.keys())
    print(f"  blind_answers 字段: {sorted(ans_keys)}")
    print(f"  blind_key     字段: {sorted(key_keys)}")
    id_fields = {"sample_id", "target", "difficulty", "run_id", "timestamp",
                 "model", "seed", "config_hash", "sha256"}
    print(f"  answers 里含身份字段: {sorted(ans_keys & id_fields) or '无'}")
    print(f"  key 里含身份字段   : {sorted(key_keys & id_fields) or '无'}")
    item_sets = {a["item"] for a in ans} & {k["item"] for k in key}
    print(f"  item 交集: {len(item_sets)} / answers {len(ans)} / key {len(key)}")
    print(f"  item 是否连续 1..48: "
          f"{sorted(item_sets) == list(range(1, len(item_sets) + 1))}")

    # ---------- 合并 ----------
    merged = []
    for a in ans:
        k = key_by_item.get(a["item"])
        if k is None:
            continue
        enh_label = k["enhanced_label"]          # 'A' or 'B' -> enhanced side
        human_raw = a["winner"]                   # 'A' / 'B' / 'tie'
        if human_raw == "tie":
            human = "tie"
        else:
            human = "enhanced" if human_raw == enh_label else "original"
        merged.append({**a, **{("k_" + x): v for x, v in k.items()},
                       "human": human, "judge": k["judge_winner"]})

    # ---------- 2. 复算分层表 ----------
    print("\n=== 2. 复算「人工 vs 自动裁判」分层表（对照 v0.3.2 报告 §5.2） ===")
    tbl = defaultdict(lambda: {"human": Counter(), "judge": Counter()})
    for m in merged:
        d = m["k_difficulty"]
        tbl[d]["human"][m["human"]] += 1
        tbl[d]["judge"][m["judge"]] += 1
    print(f"  {'难度':<8} {'n':>3} | {'人工:增强':>9} {'原文':>5} {'平':>4} | "
          f"{'裁判:增强':>9} {'原文':>5} {'平':>4}")
    tot_h, tot_j = Counter(), Counter()
    for d in ("clear", "medium", "severe"):
        if d not in tbl:
            continue
        h, j = tbl[d]["human"], tbl[d]["judge"]
        n = sum(h.values())
        tot_h += h
        tot_j += j
        print(f"  {d:<8} {n:>3} | {h['enhanced']:>9} {h['original']:>5} "
              f"{h['tie']:>4} | {j['enhanced']:>9} {j['original']:>5} {j['tie']:>4}")
    n = sum(tot_h.values())
    print(f"  {'合计':<8} {n:>3} | {tot_h['enhanced']:>9} {tot_h['original']:>5} "
          f"{tot_h['tie']:>4} | {tot_j['enhanced']:>9} {tot_j['original']:>5} "
          f"{tot_j['tie']:>4}")

    # ---------- 3. 一致性指标的三个口径 ----------
    print("\n=== 3. 「一致性」的三种口径（分母差别有多大） ===")
    all_pairs = [(m["human"], m["judge"]) for m in merged]
    non_tie = [(m["human"], m["judge"]) for m in merged
               if m["human"] != "tie" and m["judge"] != "tie"]
    tie_involved = [m for m in merged
                    if m["human"] == "tie" or m["judge"] == "tie"]
    agree_all = sum(1 for a, b in all_pairs if a == b)
    agree_nt = sum(1 for a, b in non_tie if a == b)
    print(f"  口径A 全量一致率     : {agree_all}/{n} = {fmt_pct(agree_all / n)}"
          f"   （分母含平局）")
    print(f"  口径B 仅非平局对一致率: {agree_nt}/{len(non_tie)} = "
          f"{fmt_pct(agree_nt / len(non_tie))}")
    print(f"  口径B 在原始文件里被记作 winner_agreement_non_tie = "
          f"{agr['winner_agreement_non_tie']}")
    print(f"  被排除的样本数: {len(tie_involved)}/{n} = "
          f"{fmt_pct(len(tie_involved) / n)}  <-- 口径B 把这批样本从分母里删了")
    print(f"  这些样本里的人/裁判取值: "
          f"{dict(Counter((m['human'], m['judge']) for m in tie_involved))}")
    print(f"  Cohen's kappa (全量 3x3): {cohen_kappa(all_pairs):.4f}")
    print(f"  Cohen's kappa (仅非平局对): {cohen_kappa(non_tie):.4f}")

    # clear 档：人赢的比例
    clear = [m for m in merged if m["k_difficulty"] == "clear"]
    clear_h_enh = sum(1 for m in clear if m["human"] == "enhanced")
    print(f"\n  clear 档人工判「增强更优」: {clear_h_enh}/{len(clear)} = "
          f"{fmt_pct(clear_h_enh / len(clear))}")
    lo, hi = bootstrap_ci(lambda s: sum(1 for m in s if m["human"] == "enhanced")
                          / len(s), clear)
    print(f"    bootstrap 95% CI (20000 次, seed=20261020): "
          f"[{fmt_pct(lo)}, {fmt_pct(hi)}]  <-- 0 胜但上界不贴 0，"
          f"n={len(clear)} 不足以断言'增强在 clear 档零收益'")

    # ---------- 4. 复算 MAD ----------
    print("\n=== 4. 复算 dims_mad_vs_judge / overall_mad ===")
    variants = {}
    for scale, div in (("human 原值(0-10)", 1.0), ("human 归一化(/10)", 10.0)):
        per = {d: [] for d in DIMS}
        for m in merged:
            enh = m["k_enhanced_label"]
            for d in DIMS:
                h = (m["dims_a"][d] if enh == "A" else m["dims_b"][d]) / div
                o = (m["dims_b"][d] if enh == "A" else m["dims_a"][d]) / div
                hdelta = h - o
                per[d].append(abs(hdelta - m["k_judge_deltas"][d]))
        variants[scale] = {d: sum(v) / len(v) for d, v in per.items()}
        variants[scale]["overall"] = sum(sum(v) for v in per.values()) / (
            4 * len(merged))
    for scale, vals in variants.items():
        print(f"  [{scale}] " + "  ".join(
            f"{d}={vals[d]:.3f}" for d in DIMS) + f"  overall={vals['overall']:.3f}")
    rep = agr["dims_mad_vs_judge"]
    print(f"  [文件记录] " + "  ".join(f"{d}={rep[d]:.3f}" for d in DIMS) +
          f"  overall={agr['overall_mad']:.3f}")
    match = [s for s, v in variants.items()
             if all(abs(v[d] - rep[d]) < 0.002 for d in DIMS)]
    print(f"  => 可复现的归一化方式: {match if match else '未能复现（两者都不吻合）'}")

    # ---------- 5. 事实层：覆盖与失败 ----------
    print("\n=== 5. v032_facts.json 覆盖度与失败 ===")
    print(f"  gen_calls_recorded = {facts['gen_calls_recorded']}")
    print(f"  gen_timeout = {facts['gen_timeout']}  ratio = "
          f"{facts['gen_timeout_ratio']}")
    print(f"  delivery_rate = {facts['delivery_rate']}")
    print(f"  failure_count = {facts['failure_count']}")
    print(f"  coverage_by_target = {facts['coverage_by_target']}")
    cov = facts["coverage_by_target"]
    if isinstance(cov, dict):
        vals = [v for v in cov.values() if isinstance(v, (int, float))]
        if vals:
            print(f"    -> 覆盖比例 min={min(vals)} max={max(vals)}")
            if min(vals) < 1.0:
                print(f"    -> **存在覆盖不满的目标(min={min(vals)})**，"
                      f"但 delivery_rate 报的是 {facts['delivery_rate']}")

    # ---------- 6. summary 可复算性 ----------
    print("\n=== 6. summary.json 结构与可复算性 ===")
    print(f"  顶层键: {list(summ.keys())}")
    c = summ.get("control")
    if c:
        print(f"  control(控制组): {json.dumps(c, ensure_ascii=False)[:400]}")
    s = summ.get("summary")
    if s:
        print(f"  summary: {json.dumps(s, ensure_ascii=False)[:600]}")
    for k in ("by_target", "by_scenario", "by_difficulty", "by_control",
              "by_prompt_judge", "by_ambiguity"):
        if k in summ:
            v = summ[k]
            try:
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:300]}")
            except Exception:
                print(f"  {k}: {type(v)}")


if __name__ == "__main__":
    main()
