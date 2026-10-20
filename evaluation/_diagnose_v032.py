#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 #2：v0.3.2 产物内部的一致性交叉检验。

要回答的问题：
  Q1  blind_key.enhanced_label 的 A/B 语义，与 blind_review_agreement.rows 的
      human_winner 是否一致？（不一致 => 揭盲映射有 bug 或者 key/answers 不是同一轮）
  Q2  agreement.json 自己记录的 winner_agreement_non_tie=0.2692 能否复算出来？
  Q3  summary.json 里 by_prompt_judge 的各场景数值是否重复？
  Q4  judge_agreement.dim_corr 五个维度是否真的各自算出来？
  Q5  win_rate_ci 的口径是什么？
  Q6  summary 的分母 222 vs 样本 60x4=240，差在哪？
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "evaluation-data-v032")


def load(n):
    with open(os.path.join(DATA, n), encoding="utf-8") as f:
        return json.load(f)


key = load("blind_key.json")
ans = load("blind_answers.json")
agr = load("blind_review_agreement.json")
facts = load("v032_facts.json")
summ = load("summary.json")

kb = {k["item"]: k for k in key}
ab = {a["item"]: a for a in ans}
rb = {r["item"]: r for r in agr["rows"]}

print("=" * 72)
print("Q1  A/B 语义一致性：agreement.rows.human_winner vs 由 key 推出的 human")
print("=" * 72)
same = inv = other = 0
mismatch_samples = []
for it in sorted(set(kb) & set(rb) & set(ab)):
    k, r, a = kb[it], rb[it], ab[it]
    raw = a["winner"]
    derived = "tie" if raw == "tie" else (
        "enhanced" if raw == k["enhanced_label"] else "original")
    rep = r["human_winner"]
    if derived == rep:
        same += 1
    elif derived == ("tie" if rep == "tie" else
                     ("original" if rep == "enhanced" else "enhanced")):
        inv += 1
        if len(mismatch_samples) < 5:
            mismatch_samples.append((it, k["sample_id"], k["enhanced_label"],
                                     raw, derived, rep))
    else:
        other += 1
print(f"  与 key 一致 : {same}")
print(f"  与 key 相反 : {inv}")
print(f"  其它        : {other}")
if mismatch_samples:
    print("  相反样例 (item, sample_id, enhanced_label, answers.winner, "
          "推出, 文件记录):")
    for m in mismatch_samples:
        print("    ", m)

# 两种映射各自推出的人工结果分布
for name, mapper in (
    ("按 key.enhanced_label 映射", lambda it: (
        "tie" if ab[it]["winner"] == "tie" else
        ("enhanced" if ab[it]["winner"] == kb[it]["enhanced_label"]
         else "original"))),
    ("按 agreement.rows 反推（取反）", lambda it: (
        "tie" if ab[it]["winner"] == "tie" else
        ("original" if rb[it]["human_winner"] == "enhanced" else "enhanced"))),
):
    c = Counter()
    by_diff = defaultdict(Counter)
    for it in set(kb) & set(ab):
        v = mapper(it)
        c[v] += 1
        by_diff[rb[it]["difficulty"]][v] += 1
    print(f"\n  [{name}] 人工总分布: {dict(c)}")
    for d in ("clear", "medium", "severe"):
        if d in by_diff:
            h = by_diff[d]
            n = sum(h.values())
            print(f"      {d:<7} n={n}  增强={h['enhanced']} 原文={h['original']} "
                  f"平={h['tie']}")

print()
print("=" * 72)
print("Q2  winner_agreement_non_tie = 0.2692 能复算吗？")
print("=" * 72)
print(f"  文件记录: non_tie_pairs={agr['non_tie_pairs']}  "
      f"winner_agreement_non_tie={agr['winner_agreement_non_tie']}  "
      f"tie_or_vs_tie_cases={agr['tie_or_vs_tie_cases']}")
print(f"  文件记录: reviewed={agr['reviewed']}  "
      f"direction_consistent={agr['direction_consistent']}  "
      f"conflicts={len(agr['conflicts'])}")
print(f"  算术自洽性: non_tie_pairs + tie_or_vs_tie = "
      f"{agr['non_tie_pairs'] + agr['tie_or_vs_tie_cases']} "
      f"(应等于 reviewed={agr['reviewed']})")
print(f"  0.2692 x {agr['non_tie_pairs']} = "
      f"{0.2692 * agr['non_tie_pairs']:.2f}  (应接近整数一致数)")

# 用 agreement.rows 自身的 (human,judge) 复算三种口径
pairs = [(r["human_winner"], r["judge_winner"]) for r in agr["rows"]]
nt = [(a, b) for a, b in pairs if a != "tie" and b != "tie"]
print(f"  [用 agreement.rows 复算] 全量一致 "
      f"{sum(1 for a,b in pairs if a==b)}/{len(pairs)}")
print(f"  [用 agreement.rows 复算] 非平局一致 "
      f"{sum(1 for a,b in nt if a==b)}/{len(nt)} = "
      f"{sum(1 for a,b in nt if a==b)/len(nt):.4f}")
print(f"  [用 agreement.rows 复算] 全量 3x3 混淆:")
conf = Counter(pairs)
for (a, b), c in sorted(conf.items()):
    print(f"      human={a:<9} judge={b:<9} {c}")

print()
print("=" * 72)
print("Q3  by_prompt_judge 各场景数值是否重复？")
print("=" * 72)
bpj = summ["by_prompt_judge"]
seen = defaultdict(list)
for scen, dims in bpj.items():
    sig = json.dumps(dims, sort_keys=True, ensure_ascii=False)
    seen[sig].append(scen)
for sig, scens in seen.items():
    mark = "  <== 完全相同!" if len(scens) > 1 else ""
    print(f"  {scens} -> {sig[:120]}{mark}")

print()
print("=" * 72)
print("Q4  judge_agreement.dim_corr 五个维度是否真各自算出来？")
print("=" * 72)
dc = summ["summary"]["judge_agreement"]["dim_corr"]
print(f"  dim_corr = {dc}")
print(f"  不同取值个数 = {len(set(dc.values()))} / {len(dc)}  "
      f"(全部相同 => 极可能是单一聚合值复制到每个维度)")
wa = summ["summary"]["judge_agreement"]["winner_agreement"]
print(f"  winner_agreement = {wa}  n={summ['summary']['judge_agreement']['n']}")

print()
print("=" * 72)
print("Q5  win_rate_ci 口径")
print("=" * 72)
s = summ["summary"]
p = s["wins"] / s["total"]
se = math.sqrt(p * (1 - p) / s["total"])
print(f"  wins/total = {s['wins']}/{s['total']} = {p:.4f}")
print(f"  文件 CI = {s['win_rate_ci']}")
print(f"  正态近似 CI = [{p - 1.96*se:.4f}, {p + 1.96*se:.4f}]  "
      f"(SE={se:.4f})")
print(f"  文件 CI 半宽/SE = "
      f"{(s['win_rate_ci'][1]-s['win_rate_ci'][0])/2/se:.3f}  (1.96=正态, "
      f"1.96; Wilson/Clopper 会不同)")
print(f"  binomial_p = {s['binomial_p']}  "
      f"(恰好 0.0 => 数值下溢被四舍五入, 不是真 p 值)")

print()
print("=" * 72)
print("Q6  分母：summary 222 vs 60x4=240")
print("=" * 72)
cov = facts["coverage_by_target"]
tot_samples = sum(v["samples"] for v in cov.values())
tot_judged = sum(v["judged"] for v in cov.values())
tot_rep = sum(v["rep_total"] for v in cov.values())
tot_err = sum(v["rep_error"] for v in cov.values())
print(f"  样本总数 {tot_samples}, 已判 {tot_judged}, 未判 "
      f"{tot_samples - tot_judged}")
print(f"  重复调用 {tot_rep}, 报错 {tot_err} = "
      f"{100*tot_err/tot_rep:.1f}%")
for t, v in cov.items():
    print(f"    {t:<20} samples={v['samples']} judged={v['judged']} "
          f"未判={v['samples']-v['judged']} "
          f"rep_error={v['rep_error']}/{v['rep_total']}="
          f"{100*v['rep_error']/v['rep_total']:.1f}%")
print(f"  summary.total = {s['total']}   (= sum(judged) = {tot_judged}? "
      f"{s['total'] == tot_judged})")
print(f"  delivery_rate = {facts['delivery_rate']}  "
      f"gen_timeout_ratio = {facts['gen_timeout_ratio']}  "
      f"failure_count = {facts['failure_count']}")
print(f"  summary.delivery = {s['delivery']}")
print(f"  summary.gate = {s['gate']}")

print()
print("=" * 72)
print("Q7  by_ambiguity / by_scenario 的 n 是否对得上")
print("=" * 72)
for k in ("by_ambiguity", "by_scenario", "by_target", "by_persona",
          "by_magnitude"):
    v = summ.get(k)
    if not v:
        continue
    ns = {}
    for grp, d in v.items():
        if isinstance(d, dict) and "n" in d:
            ns[grp] = d["n"]
        elif isinstance(d, dict):
            for sub, sd in d.items():
                if isinstance(sd, dict) and "n" in sd:
                    ns[f"{grp}/{sub}"] = sd["n"]
    print(f"  {k}: n 合计 = {sum(ns.values())}  明细 = {ns}")

print()
print("=" * 72)
print("Q8  summary.dimensions 结构")
print("=" * 72)
d = summ["dimensions"]
print(f"  type={type(d).__name__}")
print(json.dumps(d, ensure_ascii=False)[:1500])
