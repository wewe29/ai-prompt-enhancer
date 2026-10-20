#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v0.4.0 效果测量方案 —— 引用数字的单一事实源。

本脚本只读本仓库已有的文件（docs/evaluation-data-v032/*.json 与
docs/PromptCraft-v0.4.0-*.md），不发起任何 API 请求。
每条打印都标注数字的来源文件，便于报告逐条核对。

    python evaluation/_v040_measurement_numbers.py
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "evaluation-data-v032")
SEED = 20261020


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

print("=" * 78)
print("【1】揭盲映射 bug —— 根因与影响")
print("=" * 78)
print("  来源文件: evaluation/v032_blind_review.py:153-157")
print("    human_winner  = str(a['winner']).strip().lower()   # -> 'a' / 'b'")
print("    enhanced_label= k['enhanced_label']                # -> 'A' / 'B'")
print("    human_mapped = 'enhanced' if human_winner == enhanced_label else 'original'")
print("  => 'a' == 'A' 恒为 False => 任何非平局人工判断都被记成 'original'")
print(f"  产物实证: docs/evaluation-data-v032/blind_review_agreement.json")
print(f"    rows[].human_winner 取值 = {dict(Counter(r['human_winner'] for r in agr['rows']))}")
print(f"    出现 'enhanced' 的条数 = "
      f"{sum(1 for r in agr['rows'] if r['human_winner']=='enhanced')} / 48")
print("  对照: 同函数 :180 的 delta 映射写法正确 => MAD 可复算, winner 不可复算")


def corrected(it):
    w = ab[it]["winner"]
    if w == "tie":
        return "tie"
    return "enhanced" if w.upper() == kb[it]["enhanced_label"] else "original"


print()
print("=" * 78)
print("【2】修正后的人工结论 vs docs/PromptCraft-v0.3.2-评测报告.md §5.2")
print("=" * 78)
rep = {"clear": (0, 8, 8), "medium": (0, 9, 7), "severe": (0, 10, 6)}
print(f"  {'难度':<8}{'n':>4}   修正后(增强/原文/平)      报告原文(增强/原文/平)")
for d in ("clear", "medium", "severe"):
    c = Counter(corrected(it) for it in ab if rb[it]["difficulty"] == d)
    j = Counter(rb[it]["judge_winner"] for it in ab
                if rb[it]["difficulty"] == d)
    n = sum(c.values())
    print(f"  {d:<8}{n:>4}   {c['enhanced']:>3}/{c['original']:>3}/{c['tie']:>3}"
          f"          {rep[d][0]:>3}/{rep[d][1]:>3}/{rep[d][2]:>3}"
          f"        裁判 {j['enhanced']}/{j['original']}/{j['tie']}")
tot = Counter(corrected(it) for it in ab)
print(f"  {'合计':<8}{len(ab):>4}   {tot['enhanced']:>3}/{tot['original']:>3}"
      f"/{tot['tie']:>3}          0/27/21")

print()
print("=" * 78)
print("【3】一致性指标：三种口径 + 修正前后")
print("=" * 78)


def kappa(pairs):
    cats = ("enhanced", "original", "tie")
    idx = {c: i for i, c in enumerate(cats)}
    m = [[0] * 3 for _ in range(3)]
    for a, b in pairs:
        m[idx[a]][idx[b]] += 1
    n = len(pairs)
    po = sum(m[i][i] for i in range(3)) / n
    pe = sum((sum(m[i]) / n) * (sum(m[j][i] for j in range(3)) / n)
             for i in range(3))
    return (po - pe) / (1 - pe) if abs(1 - pe) > 1e-12 else float("nan")


for label, mapper in (("文件原样 (agreement.rows)", lambda it: rb[it]["human_winner"]),
                       ("修正后 (key.enhanced_label)", corrected)):
    pairs = [(mapper(it), rb[it]["judge_winner"]) for it in sorted(ab)]
    nt = [(a, b) for a, b in pairs if a != "tie" and b != "tie"]
    print(f"  [{label}]")
    print(f"    全量一致   {sum(1 for a,b in pairs if a==b)}/{len(pairs)} = "
          f"{100*sum(1 for a,b in pairs if a==b)/len(pairs):.1f}%")
    print(f"    非平局一致 {sum(1 for a,b in nt if a==b)}/{len(nt)} = "
          f"{100*sum(1 for a,b in nt if a==b)/len(nt):.1f}%   "
          f"(文件记录 winner_agreement_non_tie = {agr['winner_agreement_non_tie']})")
    print(f"    direction_consistent = "
          f"{(sum(1 for a,b in nt if a==b)/len(nt)) >= 0.5}  "
          f"(文件记录 {agr['direction_consistent']})")
    print(f"    Cohen's kappa = {kappa(pairs):.4f}")
    print(f"    被排除出分母的平局相关样本 = "
          f"{len(pairs)-len(nt)}/{len(pairs)} = "
          f"{100*(len(pairs)-len(nt))/len(pairs):.1f}%")

print()
print("=" * 78)
print("【4】clear 档「增强是否有益」—— 修正后 + bootstrap 置信区间")
print("=" * 78)
clear = [it for it in ab if rb[it]["difficulty"] == "clear"]


def boot_rate(items, seed=SEED, nb=20000):
    rng = random.Random(seed)
    n = len(items)
    vals = []
    for _ in range(nb):
        s = [items[rng.randrange(n)] for _ in range(n)]
        vals.append(sum(1 for it in s if corrected(it) == "enhanced") / n)
    vals.sort()
    return vals[int(0.025 * nb)], vals[int(0.975 * nb)]


lo, hi = boot_rate(clear)
print(f"  clear 档人工判「增强更优」: {sum(1 for it in clear if corrected(it)=='enhanced')}"
      f"/{len(clear)} = {100*sum(1 for it in clear if corrected(it)=='enhanced')/len(clear):.1f}%")
print(f"  bootstrap 95% CI (20000 次, seed={SEED}): [{100*lo:.1f}%, {100*hi:.1f}%]")
print(f"  clear 档人工判「原文更优」: "
      f"{sum(1 for it in clear if corrected(it)=='original')}/{len(clear)}")
print(f"  => 4 胜 / 4 负 / 8 平, 与「人工零胜」是不同结论;")
print(f"     且 n=16 时 25% 的胜率与 0% 不可区分（CI 下界 {100*lo:.1f}%）")

print()
print("=" * 78)
print("【5】覆盖度 / 失败 —— 来自 docs/evaluation-data-v032/v032_facts.json")
print("=" * 78)
cov = facts["coverage_by_target"]
print(f"  delivery_rate = {facts['delivery_rate']}   "
      f"gen_timeout_ratio = {facts['gen_timeout_ratio']}   "
      f"failure_count = {facts['failure_count']}")
tot_s = sum(v["samples"] for v in cov.values())
tot_j = sum(v["judged"] for v in cov.values())
tot_r = sum(v["rep_total"] for v in cov.values())
tot_e = sum(v["rep_error"] for v in cov.values())
for t, v in cov.items():
    print(f"  {t:<20} samples={v['samples']} judged={v['judged']} "
          f"未判={v['samples']-v['judged']}  "
          f"rep_error={v['rep_error']}/{v['rep_total']} "
          f"({100*v['rep_error']/v['rep_total']:.1f}%)")
print(f"  合计: 样本 {tot_s}, 已判 {tot_j}, 未判 {tot_s-tot_j}; "
      f"重复调用报错 {tot_e}/{tot_r} = {100*tot_e/tot_r:.1f}%")
print(f"  summary.total = {summ['summary']['total']} (== sum(judged) = {tot_j})")
print(f"  summary.gate = {summ['summary']['gate']}")
print(f"  => 头条 delivery_rate=100% / timeout=0% 与 "
      f"{100*tot_e/tot_r:.1f}% 调用报错、{tot_s-tot_j} 条未判同时成立")

print()
print("=" * 78)
print("【6】summary.json 里的 dim_corr —— 独立复算")
print("=" * 78)


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy)


for dim in ("accuracy", "completeness", "relevance", "clarity"):
    hs, js = [], []
    for it in sorted(ab):
        a, k = ab[it], kb[it]
        enhA = k["enhanced_label"] == "A"
        hs.append((a["dims_a"][dim] - a["dims_b"][dim]) * (1 if enhA else -1))
        js.append(k["judge_deltas"][dim])
    print(f"  r({dim:<13}) = {pearson(hs, js):+.4f}")
print(f"  文件 dim_corr = {summ['summary']['judge_agreement']['dim_corr']}")
print(f"  => 五个维度取值全同 ({len(set(summ['summary']['judge_agreement']['dim_corr'].values()))} 个不同值), "
      f"且与任一独立复算值都不相等")
print(f"  根因: evaluation/judge.py:186-191 只算一个 Spearman rho 再复制到每个维度键")

print()
print("=" * 78)
print("【7】MAD 复算 —— 唯一完全对上的数字")
print("=" * 78)
rep_mad = agr["dims_mad_vs_judge"]
for scale, div in (("原值 0-10（不归一化）", 1.0), ("归一化 /10", 10.0)):
    per = {d: [] for d in ("accuracy", "completeness", "relevance", "clarity")}
    for it in sorted(ab):
        a, k = ab[it], kb[it]
        for d in per:
            enhA = k["enhanced_label"] == "A"
            h = ((a["dims_a"][d] if enhA else a["dims_b"][d])
                 - (a["dims_b"][d] if enhA else a["dims_a"][d])) / div
            per[d].append(abs(h - k["judge_deltas"][d]))
    vals = {d: sum(v) / len(v) for d, v in per.items()}
    vals["overall"] = sum(sum(v) for v in per.values()) / (4 * len(ab))
    match = all(abs(vals[d] - rep_mad[d]) < 0.002 for d in rep_mad)
    print(f"  [{scale}] " + " ".join(f"{d}={vals[d]:.3f}" for d in rep_mad)
          + f" overall={vals['overall']:.3f}   {'<== 与文件一致' if match else ''}")
print(f"  文件 overall_mad = {agr['overall_mad']}")
print(f"  => 复算一致；但 human 用 0-10 分、judge 用 [-1,1] delta，"
      f"这个 '1.117' 没有量纲含义")

print()
print("=" * 78)
print("【8】clear 翻转率的四个版本 —— 全部来自已留存文档")
print("=" * 78)
print("  55%  docs/PromptCraft-v0.4.0-效果验证报告.md §3.2  (11/20, 翻转率)")
print("  30%  docs/PromptCraft-v0.4.0-效果验证报告.md §5.1  (「降到 30%」)")
print("  30%  docs/PromptCraft-v0.4.0-效果验证报告.md §5.2  (同配置两次运行之一)")
print("  71%  docs/PromptCraft-v0.4.0-效果验证报告.md §5.2  (同配置两次运行之二)")
print("  45%  docs/PromptCraft-v0.4.0-投票实测报告.md §一      (9/20, 翻转率)")
print("  30%  docs/PromptCraft-v0.4.0-投票实测报告.md §一      (18/60, "
      "逐次调用分歧率 —— 与上面 30% 不是同一个量)")
print("  40%  docs/PromptCraft-v0.4.0-iteration-spec.md §5.1   (temp 0.0, 翻转率)")
print("  26%  docs/PromptCraft-v0.4.0-iteration-spec.md §5.1   (temp 0.0, medium 翻转率)")
print()
print("  => 同一个 clear 档翻转率, 在留存文档里出现过 {55, 30, 71, 45, 40}% 五个值;")
print("     其中 30% 同时是「翻转率」和「分歧率」两个不同量的取值。")

print()
print("=" * 78)
print("【9】『55% -> 30% 是真实改善(约 2.5 个标准误)』的复核")
print("=" * 78)
p1, p2, n = 0.55, 0.30, 20
se = math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
print(f"  若 55%(n=20) 与 30%(n=20) 是两个独立比例: "
      f"z = {abs(p1-p2)/se:.2f} 个标准误")
print(f"  规格声称 2.5 个标准误 -> 实际 {abs(p1-p2)/se:.2f}")
print(f"  两次运行取均值才是无偏估计: (30%+71%)/2 = 50.5%")
print(f"  30% 与 71% 相差 41 个百分点, 而 55%->30% 只差 25 个百分点")
print(f"  => 结论: 『温度改善』的效应量小于观测到的运行间噪声, 不可解读")
print(f"  来源: docs/PromptCraft-v0.4.0-iteration-spec.md §1/§5.1/§10.2")
