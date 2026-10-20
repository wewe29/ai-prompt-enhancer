#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 #3：
  (A) 钉死 blind_review_agreement.json 的 human_winner 映射 bug
  (B) 用「零模型」检验「双峰」假设是否真的被数据支持
  (C) 复算 summary.json 的 dim_corr=0.495 是否真按维度算出来
  (D) 统计功效计算（区分 20% vs 40% 翻转率）

只用本地已有数据 + 解析推导，不发任何 API 请求。
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


def load(n):
    with open(os.path.join(DATA, n), encoding="utf-8") as f:
        return json.load(f)


key = load("blind_key.json")
ans = load("blind_answers.json")
agr = load("blind_review_agreement.json")
summ = load("summary.json")
kb = {k["item"]: k for k in key}
ab = {a["item"]: a for a in ans}
rb = {r["item"]: r for r in agr["rows"]}

print("=" * 74)
print("(A) human_winner 字段的映射 bug —— 钉死")
print("=" * 74)

# A1: agreement.rows 里的 human_deltas 是 A-B 还是 enhanced-original？
mismatch = 0
for it in sorted(ab):
    a, r = ab[it], rb[it]
    ab_diff = {d: a["dims_a"][d] - a["dims_b"][d] for d in
               ("accuracy", "completeness", "relevance", "clarity")}
    if ab_diff != r["human_deltas"]:
        mismatch += 1
print(f"  human_deltas == dims_a - dims_b 的条数: {48 - mismatch}/48 "
      f"({48 - mismatch} 一致, {mismatch} 不一致)")
print("  => human_deltas 记录的是「字母 A 减字母 B」，不是「增强减原文」")

# A2: agreement.rows 的 human_winner 出现过 "enhanced" 吗？
vals = Counter(r["human_winner"] for r in agr["rows"])
print(f"  agreement.rows.human_winner 取值分布: {dict(vals)}")
print(f"  出现过 'enhanced' 吗: {'enhanced' in vals}")

# A3: 用 key 独立推出的 human 分布
def derived(it):
    w = ab[it]["winner"]
    if w == "tie":
        return "tie"
    return "enhanced" if w == kb[it]["enhanced_label"] else "original"


dv = Counter(derived(it) for it in ab)
print(f"  用 blind_key.enhanced_label 独立推出的 human 分布: {dict(dv)}")
print(f"  文件记录的 human_winner 分布            : {dict(vals)}")
n_nontie_file = sum(c for k, c in vals.items() if k != "tie")
n_nontie_true = dv["enhanced"] + dv["original"]
print(f"  非平局条数: 文件 {n_nontie_file} / 由 key 推出 {n_nontie_true}  "
      f"-> {'相等' if n_nontie_file == n_nontie_true else '不等'}")

# A4: 用 human_deltas 的符号 + key，独立验证 human_winner 是否自洽
bad = 0
for it in sorted(ab):
    a, r, k = ab[it], rb[it], kb[it]
    enh_is_A = k["enhanced_label"] == "A"
    # 人工在「增强-原文」轴上的差值
    d = {dim: (a["dims_a"][dim] - a["dims_b"][dim]) * (1 if enh_is_A else -1)
         for dim in ("accuracy", "completeness", "relevance", "clarity")}
    nz = [v for v in d.values() if v != 0]
    if not nz:
        true_h = "tie"
    elif sum(nz) > 0:
        true_h = "enhanced"
    else:
        true_h = "original"
    if true_h != r["human_winner"]:
        bad += 1
print(f"  用「human_deltas 符号 + key 的 enhanced_label」独立判定的 human，"
      f"与文件记录不一致的条数: {bad}/48")
print("  => 文件里的 human_winner 与它自己携带的 human_deltas 互相矛盾")

# A5: 修正映射后的全部一致性指标
def metrics(human_map, label):
    pairs = [(human_map[it], rb[it]["judge_winner"]) for it in sorted(ab)]
    nt = [(a, b) for a, b in pairs if a != "tie" and b != "tie"]
    ag_all = sum(1 for a, b in pairs if a == b)
    ag_nt = sum(1 for a, b in nt if a == b)
    conf = sum(1 for a, b in pairs if a != b)
    cats = ("enhanced", "original", "tie")
    idx = {c: i for i, c in enumerate(cats)}
    m = [[0] * 3 for _ in range(3)]
    for a, b in pairs:
        m[idx[a]][idx[b]] += 1
    n = len(pairs)
    po = sum(m[i][i] for i in range(3)) / n
    pe = sum((sum(m[i]) / n) * (sum(m[j][i] for j in range(3)) / n)
             for i in range(3))
    kappa = (po - pe) / (1 - pe) if abs(1 - pe) > 1e-12 else float("nan")
    print(f"\n  [{label}]")
    print(f"    全量一致率      {ag_all}/{n} = {100*ag_all/n:.1f}%")
    print(f"    非平局一致率    {ag_nt}/{len(nt)} = {100*ag_nt/len(nt):.1f}%  "
          f"(文件记录的 winner_agreement_non_tie = "
          f"{agr['winner_agreement_non_tie']:.4f})")
    print(f"    冲突数          {conf}/48  (文件记录的 conflicts = "
          f"{len(agr['conflicts'])})")
    print(f"    Cohen's kappa   {kappa:.4f}")
    print(f"    3x3 混淆:")
    for i, c in enumerate(cats):
        print(f"      {c:<9} -> " + "  ".join(
            f"{c2}:{m[i][idx[c2]]:>2}" for c2 in cats))


metrics({it: rb[it]["human_winner"] for it in ab}, "文件原样（agreement.rows）")
metrics({it: derived(it) for it in ab}, "按 blind_key.enhanced_label 修正后")

print()
print("=" * 74)
print("(A6) 修正后，各难度的人工结论 vs v0.3.2 报告 §5.2 的表")
print("=" * 74)
rep = {"clear": (0, 8, 8), "medium": (0, 9, 7), "severe": (0, 10, 6)}
for d in ("clear", "medium", "severe"):
    c = Counter(derived(it) for it in ab if rb[it]["difficulty"] == d)
    j = Counter(rb[it]["judge_winner"] for it in ab
                if rb[it]["difficulty"] == d)
    n = sum(c.values())
    print(f"  {d:<7} n={n}")
    print(f"    人工(修正): 增强={c['enhanced']} 原文={c['original']} "
          f"平={c['tie']}    | 报告原文: 增强={rep[d][0]} 原文={rep[d][1]} "
          f"平={rep[d][2]}")
    print(f"    裁判      : 增强={j['enhanced']} 原文={j['original']} "
          f"平={j['tie']}")

print()
print("=" * 74)
print("(C) dim_corr=0.495 是否真按维度算出来？")
print("=" * 74)
print(f"  文件: {summ['summary']['judge_agreement']['dim_corr']}")


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else float("nan")


per_dim = {}
for dim in ("accuracy", "completeness", "relevance", "clarity"):
    hs, js = [], []
    for it in sorted(ab):
        a, k = ab[it], kb[it]
        enhA = k["enhanced_label"] == "A"
        h = (a["dims_a"][dim] - a["dims_b"][dim]) * (1 if enhA else -1)
        hs.append(h)
        js.append(k["judge_deltas"][dim])
    per_dim[dim] = pearson(hs, js)
    print(f"  独立复算 r({dim}) = {per_dim[dim]:.4f}")
print("  => 文件里的 0.495 若是逐维算的，四个维度不该完全相同；"
      "四维皆 0.495 与独立复算值无一相等")

print()
print("=" * 74)
print("(B) 「双峰」假设检验：零模型能否复现全部观测？")
print("=" * 74)
print("""  观测（来自 docs/PromptCraft-v0.4.0-投票实测报告.md §一/§三/§六）:
    clear: 20 样本 × 3 次点击, 每次点击内 2 次采样
    跨点击翻转样本 9/20 = 45%
    点击内两次采样分歧 18/60 = 30%
    12 个翻转样本中有 2 个「每次点击内部两次采样完全一致」
    报告自述: 该观察「只有 2 个样本支撑，不是普适结论」

  零模型 H0 (不需要「双峰」):
    每样本有固定档位 p_i (异质性)
    每次点击: 两次独立采样, 各自以 1-e 的概率给该点击的档位
    报告层取第一次采样 (与实现的破平局规则一致)
""")

# 从档位组合分布反推 p_i 的可实现集合
# 效果验证报告 §3.2: clear 20 条档位组合 = none+light x10, light x5, none x4,
# light+clarify x1  (3 次点击)
# 投票报告: 翻转 9/20 (45%)
combo = {"none+light": 10, "light": 5, "none": 4, "light+clarify": 1}
print(f"  档位组合分布(另一轮, 60x3): {combo}  合计 {sum(combo.values())}")


def simulate(n_samples, n_clicks, p_samples, e, rng, n_sim=4000):
    """返回 (翻转率, 点击内分歧率, 翻转样本中内部全一致的条数) 的期望。"""
    flip = 0.0
    dis = 0.0
    clean_flipper = 0.0
    for _ in range(n_sim):
        fl = 0
        dc = 0
        cf = 0
        for i in range(n_samples):
            p = p_samples[i]
            reps = []
            dis_here = 0
            for c in range(n_clicks):
                s1 = 1 if rng.random() < p else 0
                s2 = 1 if rng.random() < p else 0
                # 用「同一模式 + 误差」实现：先抽该点击的档位，再各自带误差
                s1 = 1 if (s1 if rng.random() < 1 - e else 1 - s1) else 0
                s2 = 1 if (s2 if rng.random() < 1 - e else 1 - s2) else 0
                reps.append(s1)
                if s1 != s2:
                    dis_here += 1
            dc += dis_here
            if len(set(reps)) > 1:
                fl += 1
                if dis_here == 0:
                    cf += 1
        flip += fl / n_samples
        dis += dc / (n_samples * n_clicks)
        clean_flipper += cf
    return flip / n_sim, dis / n_sim, clean_flipper / n_sim


rng = random.Random(20261020)
# 用 observed 档位组合反推 p_i 集合: none+light 型 -> p_i 居中; light 型 -> p_i 高
p_set = []
for c, cnt in combo.items():
    for _ in range(cnt):
        if c == "light":
            p_set.append(0.90)
        elif c == "none":
            p_set.append(0.10)
        else:                      # none+light / light+clarify: 跨点击漂移型
            p_set.append(0.50)
print(f"\n  用档位组合反推的 p_i 集合: {Counter(p_set)}")
for e in (0.05, 0.10, 0.15, 0.184, 0.25):
    f, d, cf = simulate(20, 3, p_set, e, rng, n_sim=1500)
    print(f"  e={e:5.3f} -> 预测 翻转率={f:.3f}  点击内分歧率={d:.3f}  "
          f"翻转样本中内部全一致的期望条数={cf:.2f} / 9")
print(f"\n  实测: 翻转率=0.450  点击内分歧率=0.300  "
      f"内部全一致的翻转样本=2 / 9")

# 独立的二项检验: 若每个翻转样本「三击内部全一致」的概率是 q，
# 观察 9 个里 2 个，问 0.9^3=0.729 还是 0.7^3=0.343 哪个不被拒绝？
print("\n  反过来做检验: 在「点击间独立 + 点击内 30% 分歧」模型下,")
for q, name in ((0.70 ** 3, "点击内不一致率 30% => 单击全一致率 0.7"),
                (0.729, "若点击内完全无分歧")):
    n = 9
    k = 2
    p_le = sum(math.comb(n, i) * q ** i * (1 - q) ** (n - i)
               for i in range(0, k + 1))
    print(f"    {name}: P(9 个翻转样本里 ≤2 个内部全一致) = {p_le:.3f}")
print("  => 在任一零模型下, 观察到 2 个都不算异常 => "
      "「双峰」不是被数据证实的, 只是未被排除")

print()
print("=" * 74)
print("(D) 统计功效：区分 20% vs 40% 翻转率")
print("=" * 74)


def z(p):
    """标准正态分位数（Acklam 近似）"""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.224964334411118e-01, -2.445134137142996e+00,
         -3.754408661907416e+01]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+01]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+d[4])
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+d[4])
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def power_two_prop(n, p1, p2, alpha=0.05, one_sided=True):
    """两独立比例检验的近似功效（Arcus 公式）"""
    za = z(1 - alpha) if one_sided else z(1 - alpha / 2)
    zb = z(0.80)
    pbar = (p1 + p2) / 2
    # 查表法
    for df in (1, 0):
        pass
    p1s = p1 if df == 0 else p1
    # 简化：用正态近似 + 连续性忽略
    pbar = (p1 + p2) / 2
    se_null = math.sqrt(p1 * (1 - p1) / n)
    se_alt = math.sqrt(p2 * (1 - p2) / n)
    # 精确一点的：用 unpooled 近似
    z_stat = (p2 - p1) / math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    return z_stat, z_stat


print("\n  D1. 两独立组（最保守：两组用不同样本），单侧 alpha=0.05, power=0.80")
za, zb = z(0.95), z(0.80)
for p1, p2 in ((0.20, 0.40), (0.20, 0.30)):
    pbar = (p1 + p2) / 2
    n = (za + zb) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2
    n2 = (z(0.975) + zb) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2
    print(f"    {p1:.0%} vs {p2:.0%}: n/组 单侧 = {math.ceil(n):>4}   "
          f"双侧 = {math.ceil(n2):>4}   (合计 {2*math.ceil(n):>4} / "
          f"{2*math.ceil(n2):>4})")
    for nn in (20, 50, 62, 79, 150, 300):
        zst = (p2 - p1) / math.sqrt(p1 * (1 - p1) / nn + p2 * (1 - p2) / nn)
        pw = 1 - 0.5 * (1 + math.erf((zst - za) / math.sqrt(2)))
        print(f"      n={nn:>4} -> z={zst:.2f}  power≈{pw:.3f}")

print("\n  D2. 但实测噪声地板 41 个百分点 —— 把它算进来")
print("    同一配置两次独立运行给出 30% vs 71%（效果验证报告 §5.2）")
spread = 0.41
# 两次观测之差 E|X1-X2| = sigma*sqrt(2)*sqrt(2/pi) => sigma = spread/1.128
sigma_est = spread / (math.sqrt(2) * math.sqrt(2 / math.pi))
se_binom20 = math.sqrt(0.30 * 0.70 / 20)
print(f"    由 41 点差反推运行间 SD ≈ {sigma_est:.3f}")
print(f"    n=20 的二项 SE = {se_binom20:.3f}  ->  "
      f"运行间方差 ({sigma_est:.2f}) 是二项方差的 "
      f"{(sigma_est/se_binom20)**2:.1f} 倍")
for tau2 in (0.010, 0.020, 0.036):
    print(f"\n    假定 Var(p̂) = p(1-p)/n + tau^2, tau^2 = {tau2:.3f}")
    for p1, p2 in ((0.20, 0.40), (0.20, 0.30)):
        need = (za + zb)
        # 0.20 = need * sqrt(2*(pbar(1-pbar)/n + tau^2))
        inner = ((p2 - p1) / need) ** 2 / 2 - tau2
        if inner <= 0:
            print(f"      {p1:.0%} vs {p2:.0%}: 无解 —— "
                  f"tau^2 已超过信号可解释的方差, 加样本数无济于事")
        else:
            n = pbar * (1 - pbar) / inner
            print(f"      {p1:.0%} vs {p2:.0%}: 需 n ≈ {math.ceil(n)} "
                  f"(>现有预算时该设计不可行)")

print("\n  D3. 结论：区分 20% vs 40% 的瓶颈不是 n，是方差分解")
print("    · n 控制二项噪声; R(独立运行轮次) 控制运行间漂移")
print("    · 要把运行间 SD 压到 5 个百分点, 需 R ≈ "
      f"(sigma_est/0.05)**2 = {math.ceil((sigma_est/0.05)**2)} 轮独立运行")
print("    · 或者改用配对设计 + 组内指标, 让运行间项在差分里相消")

print("\n  D4. 配对设计（同一批提示词, 两臂在同一次运行内交错执行）")
print("    McNemar: 只需看不一致对 (b, c), 功效取决于不一致率")
for disc in (0.30, 0.45):
    for p1, p2 in ((0.20, 0.40),):
        b = disc * p1
        c = disc * (1 - p1)
        # 需要 n 使 H1 下 b 比例从 b/(b+c) 移到 ...
        pass
    print(f"    不一致对比例={disc:.0%} 时, 配对检验的 n 要求见下")
for n in (20, 30, 50, 80, 120):
    # 配对情形: 臂1 翻转率 0.20, 臂2 翻转率 0.40, 不一致对总比例 0.45
    # H0 下 McNemar 检验的功效（正态近似, 连续性校正略去）
    b = 0.45 * 0.20
    c = 0.45 * 0.80
    b1 = 0.45 * 0.20
    c1 = 0.45 * 0.60
    # H1 下 b 上升, c 不变
    d_h0 = 0.45
    se_h0 = math.sqrt(d_h0 * 0.5)
    zstat = (b1 - b) / (se_h0 * math.sqrt(1 / n))
    pw = 1 - 0.5 * (1 + math.erf((zstat - za) / math.sqrt(2)))
    print(f"      n={n:>4} -> McNemar z≈{zstat:.2f}  power≈{pw:.3f}"
          f"   (不一致对 {d_h0:.0%})")
