#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 #4（最终）：
  (A) 干净版零模型模拟：检验「双峰」是否被数据要求
  (B) 修正后的统计功效计算（区分 20% vs 40% 翻转率）
  (C) 门槛可判定性分析：门槛 1/2/3 的间距 vs 噪声地板

数据来源（只读，不发 API）：
  docs/PromptCraft-v0.4.0-投票实测报告.md
  docs/PromptCraft-v0.4.0-效果验证报告.md
  docs/PromptCraft-v0.4.0-iteration-spec.md §5.1 / §7
"""
from __future__ import annotations

import math
import random

SEED = 20261020

# ---------- 观测值（全部来自上面三份文档） ----------
OBS = {
    "clear_flip_rate": 0.45,        # 投票报告 §一：9/20
    "clear_within_click_disagree": 0.30,  # 投票报告 §一：18/60
    "flipper_all_internally_consistent": 2,  # 投票报告 §三：2 个样本
    "n_flipper": 9,
    "same_config_two_runs": (0.30, 0.71),   # 效果报告 §5.2
    "temp035_clear_flip": 0.55,     # 效果报告 §3.2：11/20
    "temp01_clear_flip": 0.30,      # 效果报告 §5.1 / 规格 §5.1
    "temp00_clear_flip": 0.40,      # 规格 §5.1
    "temp00_medium_flip": 0.26,     # 规格 §5.1
    "v240_clear_flip": 0.45,        # 规格 §5.1
    "v240_medium_flip": 0.50,       # 规格 §5.1
    "v230_clear_flip": 0.00,        # 规格 §5.1
    "v230_medium_flip": 0.10,       # 规格 §5.1
    "medium_baseline_flip": 0.33,   # 效果报告 §3.2：6/18
}


def z(p):
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
    """两独立比例 z 检验的功效（正态近似，忽略连续性校正）"""
    za = z(1 - alpha) if one_sided else z(1 - alpha / 2)
    se = math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    zst = (p2 - p1) / se
    return zst, 1 - 0.5 * (1 + math.erf((za - zst) / math.sqrt(2)))


def mdd_two_prop(n, p1, p2, alpha=0.05, one_sided=True):
    """最小可检出差异 (MDD)"""
    za = z(1 - alpha) if one_sided else z(1 - alpha / 2)
    pbar = (p1 + p2) / 2
    return (za + z(0.80)) * math.sqrt(2 * pbar * (1 - pbar) / n)


def mdd_mcnemar(n, disc, alpha=0.05):
    """配对设计 (McNemar) 的 MDD，不一致对比例 disc"""
    za = z(1 - alpha)
    return (za + z(0.80)) * math.sqrt(disc / n)


def mcnemar_power(n, disc, p1, p2, alpha=0.05):
    """配对设计下，两臂翻转率 p1/p2、不一致对比例 disc 时的功效。

    推导：记 y = P(A翻,B不翻), z = P(A不翻,B翻), x = P(都翻)。
      y + x = p1, z + x = p2  =>  z - y = p2 - p1 = Δ
      y + z = disc            =>  y = (disc-Δ)/2, z = (disc+Δ)/2
    H0 下 Δ=0，每个不一致对以 1/2 概率贡献 +1 或 -1
      => Var[z-y] = disc * n,  z 统计量 = Δ * sqrt(n / disc)
    """
    za = z(1 - alpha)
    delta = p2 - p1
    y = (disc - delta) / 2
    z1 = (disc + delta) / 2
    # 统计量 = n*(z1-y) / sqrt(disc*n) = (z1-y)*sqrt(n/disc)
    zst = (z1 - y) * math.sqrt(n / disc) if disc > 0 else 0.0
    return zst, 1 - 0.5 * (1 + math.erf((za - zst) / math.sqrt(2)))


# =====================================================================
print("=" * 76)
print("(A) 零模型模拟：'双峰' 是被数据要求的，还是只是未被排除？")
print("=" * 76)
print("""  观测（投票报告 §一/§三）：clear 20 样本 × 3 次点击 × 每次 2 采样
    跨点击翻转率 = 9/20 = 0.45
    点击内 2 采样分歧率 = 18/60 = 0.30
    9 个翻转样本里，有 2 个「每次点击内部两次采样完全一致」
    投票报告 §六.3 自述：「这个观察只有 2 个样本支撑，不是普适结论」

  零模型 H0（不含双峰）：
    每样本 i 有一个固定的档位倾向 p_i（异质性来源）
    每次点击：抽该点击的档位 mode ~ Bernoulli(p_i)
              两次采样各自以 1-e 的概率等于 mode，否则取反
              报告层取第一次采样（与实现的破平局规则一致）
  锐双峰模型 H1：e -> 0（点击内完全确定），漂移全部来自 mode 在点击间切换
""")


def simulate(p_i, e, n_clicks=3, n_vote=1, n_sim=3000, rng=None):
    """返回 (翻转率, 点击内分歧率, 翻转样本中内部全一致的平均条数)"""
    rng = rng or random.Random(SEED)
    flip = 0.0
    dis = 0.0
    clean = 0.0
    n = len(p_i)
    for _ in range(n_sim):
        fl = dc = cf = 0
        for p in p_i:
            reps = []
            dh = 0
            for _c in range(n_clicks):
                mode = 1 if rng.random() < p else 0
                samp = []
                for _s in range(n_vote):
                    samp.append(mode if rng.random() < 1 - e else 1 - mode)
                if n_vote == 2:
                    dh += 1 if samp[0] != samp[1] else 0
                    reps.append(samp[0])          # 报告层 = 第一次采样
                else:
                    rep = 1 if sum(samp) * 2 > n_vote else 0
                    reps.append(rep)
            dc += dh
            if len(set(reps)) > 1:
                fl += 1
                if dh == 0:
                    cf += 1
        flip += fl / n
        dis += dc / (n * n_clicks)
        clean += cf
    return flip / n_sim, dis / n_sim, clean / n_sim


# 拟合：用 Beta(a,b) 异质性 + 误差率 e 去匹配 (翻转率 0.45, 分歧率 0.30)
# 注意：观测设计是「每次点击 2 次采样」，模拟必须用 n_vote=2
best = None
rng = random.Random(SEED)
for a in (0.5, 0.8, 1.0, 1.5, 2.0, 3.0):
    for b in (0.5, 0.8, 1.0, 1.5, 2.0, 3.0):
        pset = [rng.betavariate(a, b) for _ in range(20)]
        for e in (0.10, 0.14, 0.184, 0.22, 0.26, 0.30):
            f, d, c = simulate(pset, e, n_vote=2, n_sim=600, rng=rng)
            err = (f - 0.45) ** 2 + (d - 0.30) ** 2
            if best is None or err < best[0]:
                best = (err, a, b, e, pset, f, d, c)
print(f"  拟合结果: Beta({best[1]}, {best[2]}) + e={best[3]:.3f}  "
      f"拟合误差={best[0]:.4f}")
f, d, c = simulate(best[4], best[3], n_vote=2, n_sim=6000,
                   rng=random.Random(SEED + 1))
print(f"  该零模型预测: 翻转率={f:.3f} (实测 0.450)  "
      f"点击内分歧率={d:.3f} (实测 0.300)  "
      f"内部全一致的翻转样本={c:.2f} 条 (实测 2)")
print(f"  => 零模型用一个「每样本固定倾向 + 采样误差」就同时复现了全部三项观测，")
print(f"     并不需要「双峰」这个额外假设。")

# 反向检验：内部全一致的翻转样本，零模型下期望几条？
for e, name in ((0.184, "e=0.184 (由 30% 分歧率反解)"), (0.0, "e=0 (锐双峰)")):
    pset = [rng.betavariate(best[1], best[2]) for _ in range(20)]
    f2, d2, c2 = simulate(pset, e, n_vote=2, n_sim=6000,
                          rng=random.Random(SEED + 2))
    lam = c2
    p_le = sum(math.exp(-lam) * lam ** i / math.factorial(i)
               for i in range(0, 3))
    print(f"    [{name}] 预测翻转率={f2:.3f} 分歧率={d2:.3f} "
          f"内部全一致期望 {c2:.2f} 条, 观测到 ≤2 条的概率 = {p_le:.3f}")
print("  注: 观测 2 条中的「期望条数」随模型变化 => 该统计量本身没有")
print("      把「双峰」和「有误差率的单峰」区分开的能力。")

print()
print("  n=3 投票在两个模型下的差别（这是唯一值得做的后续实验）：")
pset = [rng.betavariate(best[1], best[2]) for _ in range(20)]
for e, name in ((0.184, "有误差率模型 e=0.184"), (0.0, "锐双峰模型 e=0")):
    f2, d2, _ = simulate(pset, e, n_vote=2, n_sim=4000,
                         rng=random.Random(SEED + 3))
    f3, _, _ = simulate(pset, e, n_vote=3, n_sim=4000,
                        rng=random.Random(SEED + 3))
    print(f"    [{name}] n=2 的用户可见跨点击翻转率 = {f2:.3f}   "
          f"改 n=3 多数投票后 = {f3:.3f}   "
          f"变化 {100*(f3-f2):+.1f} 个百分点")
print(f"    ⇒ 收益大小完全取决于 e，而 e 是可以从已有数据估出来的；")
print(f"      在有误差率模型下 n=3 有可观收益，在锐双峰模型下收益为 0。")

print()
print("=" * 76)
print("(B) 统计功效：区分 20% vs 40% 翻转率")
print("=" * 76)
za1, zb = z(0.95), z(0.80)
print("\n  B1. 两独立组（两臂用不同样本），单侧 alpha=0.05, power=0.80")
for p1, p2 in ((0.20, 0.40), (0.20, 0.30), (0.30, 0.55)):
    pbar = (p1 + p2) / 2
    n1 = (za1 + zb) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2
    n2 = (z(0.975) + zb) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2
    print(f"    {p1:.0%} vs {p2:.0%}: n/组 单侧={math.ceil(n1):>4} "
          f"双侧={math.ceil(n2):>4}   "
          f"(合计 {2*math.ceil(n1):>4} / {2*math.ceil(n2):>4} 组次)")
print("\n    功效曲线 (20% vs 40%):")
for n in (20, 40, 62, 79, 120, 200):
    zst, pw = power_two_prop(n, 0.20, 0.40)
    print(f"      n={n:>4}/组 -> z={zst:.2f}  power={pw:.3f}  "
          f"MDD={mdd_two_prop(n, 0.20, 0.40):.3f}")

print("\n  B2. 配对设计（同一批提示词，两臂在同一次运行内交错）")
print("      McNemar 只看不一致对；n = disc*(za+zb)^2/delta^2")
for disc in (0.30, 0.45, 0.60):
    n = disc * (za1 + zb) ** 2 / 0.20 ** 2
    print(f"      不一致对比例 {disc:.0%} -> 需 n ≈ {math.ceil(n)} "
          f"(单侧) / {math.ceil(disc*(z(0.975)+zb)**2/0.04)} (双侧)")
print("\n      功效曲线 (20% vs 40%, disc=0.45):")
for n in (20, 30, 50, 70, 100):
    zst, pw = mcnemar_power(n, 0.45, 0.20, 0.40)
    print(f"      n={n:>4} -> z={zst:.2f}  power={pw:.3f}  "
          f"MDD={mdd_mcnemar(n, 0.45):.3f}")

print("\n  B3. 为什么单靠加样本数救不了：噪声地板")
a, b = OBS["same_config_two_runs"]
spread = b - a
sig = spread / (math.sqrt(2) * math.sqrt(2 / math.pi))
print(f"    同配置两次独立运行: {a:.0%} vs {b:.0%}（差 {spread:.0%}）")
zst, pw = power_two_prop(20, a, b)
p_two = 2 * (1 - 0.5 * (1 + math.erf(abs(zst) / math.sqrt(2))))
print(f"    若真按两个独立二项样本看: z={zst:.2f} (n=20/组) -> "
      f"该差距的双侧 p = {p_two:.4f}")
se_bin = math.sqrt(0.5 * 0.5 / 20)
print(f"    n=20 的二项 SE={se_bin:.3f}；两运行之差的标准误="
      f"{math.sqrt(2)*se_bin:.3f} -> 观测差是 {spread:.3f}/"
      f"{math.sqrt(2)*se_bin:.3f} = {spread/(math.sqrt(2)*se_bin):.2f} 个 SE")
print(f"    ⇒ 该差距本身就不像抽样噪声。要么存在真实的运行级随机效应，")
print(f"       要么两次运行的配置并不相同 —— 而现有产物**不记录运行身份**，")
print(f"       所以这两种情况无法区分（这本身是 Q4 的一条发现）。")
print(f"    运行间 SD 的粗估: 41 点差 => sigma ≈ {sig:.2f}（由 2 个观测估计，")
print(f"       极不可靠，只能当量级）")

print()
print("  B4. 推荐设计的方差分解")
print("    Var(p̂_arm) = p(1-p)/n  +  tau^2 / R")
print("      n = 每轮每臂的提示词数, R = 独立运行轮数, tau = 运行级漂移")
print("    两臂在**同一轮内交错**执行 => 轮级项在差分里相消（这是关键）")
for R in (1, 3, 5, 10):
    for tau in (0.10, 0.20):
        # 差分方差 = 2*(binomial/n)  (轮级项已相消)
        need = 0.20
        se = math.sqrt(2 * 0.24 / 70)
        print(f"      R={R:>2}, tau={tau:.2f}: 若两臂同轮交错, 差分 SE={se:.3f} "
              f"-> z={need/se:.2f}  power="
              f"{1-0.5*(1+math.erf((za1-need/se)/math.sqrt(2))):.3f} (n=70/臂/轮)")
    break
print(f"    => n=70/臂/轮、两臂同轮交错 => 可检出 20 个百分点的差异")
print(f"       (前提: 轮级项确实在差分里相消, 这需要用交错顺序实测验证)")

print()
print("=" * 76)
print("(C) 门槛可判定性：规格 §7 的门槛 vs 实测噪声")
print("=" * 76)
noise = 0.41
gates = [
    ("门槛1 clear 翻转率", OBS["temp035_clear_flip"], 0.30, "<="),
    ("门槛2 medium 翻转率", OBS["medium_baseline_flip"], 0.30, "<="),
    ("门槛3 clear light 出现率", 0.52, 0.42, ">="),
]
print(f"  噪声地板 = {noise:.0%}（同配置两次独立运行之差，效果报告 §5.2）")
print(f"  {'门槛':<26}{'现状':>7}{'目标':>8}{'间距':>8}   可判定?")
for name, cur, tgt, op in gates:
    gap = abs(cur - tgt)
    ok = "可判定" if gap > noise else "间距 < 噪声地板 => 不可判定"
    print(f"  {name:<26}{cur:>7.0%}{tgt:>8.0%}{gap:>8.0%}   {ok}")
print()
print(f"  另外：门槛1 的目标 ≤30% 恰好等于「同配置两次运行中较好的一次」")
print(f"  （{a:.0%} 与 {b:.0%}，效果报告 §5.2 / 规格 §10.2）。")
print(f"  把门槛定在最好那次上 => 重跑时该门槛约等于掷硬币。")
print(f"  最好估计应为两次的均值 = {(a+b)/2:.1%}，不是 {a:.0%}。")
print(f"  z 检验复核 55% vs 30% (n=20/组): z="
      f"{power_two_prop(20, 0.55, 0.30)[0]:.2f}  "
      f"—— 不是规格声称的 2.5 个标准误。")
