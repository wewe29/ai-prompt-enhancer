"""门禁可判定性：统计功效计算与 PASS/FAIL 判定规则。

为什么需要这个模块
==================
v0.4.0 的效果门槛（`docs/PromptCraft-v0.4.0-iteration-spec.md` §7）全部作用在
**有运行间噪声的比率型指标**上。已实测：同一配置的两次独立运行给出 30% 与 71%
（`docs/PromptCraft-v0.4.0-效果验证报告.md` §5.2），噪声地板约 41 个百分点。
而门槛 1/2/3 的间距分别是 25 / 3 / 10 个百分点 —— **全部落在噪声地板以内**。

这类门槛有两种失效方式，而且都伪装成 PASS：

1. **阈值定在"同配置多跑里较好的一次"** ⇒ 重跑时门槛约等于掷硬币。
2. **只比点估计** ⇒ 一个单点数字恰好落在阈值另一侧就翻转结论。

本模块把这两件事变成可计算的东西：

- `required_n_two_proportions()` —— 要多少提示词，才能把"现状水平"与"目标水平"区分开
  （只考虑二项噪声）。
- `required_runs()` —— 要多少轮独立运行，才能让运行间噪声不淹没有效间距。
- `decide_rate()` —— 判定规则。**输出只有 PASS / FAIL**；"区间跨越阈值"是 FAIL 的
  **原因标签**，不是第三种输出（fail-closed，见 `docs/v040-缺失语义约定.md` R3）。

噪声模型
========
观测量的方差分解为两项：

    Var(p̂) = p(1-p)/n  +  τ²/R
              ↑二项噪声      ↑运行间漂移，靠独立运行轮数 R 摊薄

- `n` = 每轮每臂参与比较的提示词条数
- `R` = 独立运行轮数
- `τ` = 运行间标准差（**每个门槛自己的**，必须实测，不能借用别的门槛的）

`τ` 不可跨门槛套用：41 个百分点是**模型档位决策**的运行间漂移，
不是人工盲评的评审者间/轮间漂移。后者至今未测（`docs/PromptCraft-v0.4.0-效果验证报告.md`
§五.3 自述"1 名评审者、20 条、单轮"），因此人工类门槛必须先测出自己的 τ。

用法::

    from gate_decidability import decide_rate, required_n_two_proportions, required_runs
"""
from __future__ import annotations

import math
from typing import Any

__all__ = [
    "z",
    "required_n_two_proportions",
    "required_runs",
    "required_n_for_runs",
    "half_width",
    "decide_rate",
    "derive_requirements",
    "ALPHA",
    "POWER",
]

#: 显著性水平（单侧）。门禁是方向性的：用单侧区间。
ALPHA = 0.05
#: 目标功效。
POWER = 0.80


def z(p: float) -> float:
    """标准正态分位数（Acklam 近似，精度约 1e-9）。"""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p 必须在 (0,1) 之间，收到 {p}")
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
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + d[4])
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + d[4])
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def required_n_two_proportions(p_bad: float, p_good: float,
                               alpha: float = ALPHA, power: float = POWER) -> int:
    """区分"坏水平"与"好水平"所需的样本量（单样本二项检验，只含二项噪声）。

    n = [z(1-α)·√(p_bad(1-p_bad)) + z(power)·√(p_good(1-p_good))]² / (p_bad-p_good)²

    这是"单点估计还不够"的第一层：条数不足时，连抽样噪声都分不开两个水平。
    """
    if p_bad == p_good:
        raise ValueError("p_bad 与 p_good 相同，无法计算样本量")
    p1, p2 = (p_bad, p_good) if p_bad > p_good else (p_good, p_bad)
    num = (z(1 - alpha) * math.sqrt(p1 * (1 - p1))
           + z(power) * math.sqrt(p2 * (1 - p2))) ** 2
    return max(1, math.ceil(num / ((p1 - p2) ** 2)))


def required_runs(gap: float, tau: float, p_bar: float, n: int,
                  alpha: float = ALPHA) -> int | None:
    """让门槛"可判定"所需的独立运行轮数 R。

    可判定条件：噪声半宽 h ≤ gap/2，即

        z(1-α/2)·√(p̄(1-p̄)/n + τ²/R) ≤ gap/2
        ⇒ p̄(1-p̄)/n + τ²/R ≤ (gap / (2·z(1-α/2)))²

    返回 None 表示：在给定 n 与 τ 下，**再多的独立轮次也不足以把间距从噪声里分出来**
    （通常是二项项本身就吃掉了全部预算）。
    """
    if gap <= 0 or tau <= 0:
        return 1
    zc = z(1 - alpha / 2)
    budget = (gap / (2 * zc)) ** 2
    binomial = p_bar * (1 - p_bar) / max(1, n)
    if binomial >= budget:
        return None
    return max(1, math.ceil(tau * tau / (budget - binomial)))


def required_n_for_runs(gap: float, tau: float, p_bar: float, runs: int,
                        alpha: float = ALPHA) -> int | None:
    """给定运行轮数 R，噪声半宽 ≤ gap/2 所需的最小提示词条数。

    由 h = z(1-α/2)·√(p̄(1-p̄)/n + τ²/R) ≤ gap/2 解出：

        n ≥ p̄(1-p̄) / ((gap/(2·z))² - τ²/R)

    返回 None 表示在给定 R 下**再加条数也无用**（二项项的极限仍大于预算）——
    这时只有把 R 提上去才可能。
    """
    if gap <= 0:
        return 1
    zc = z(1 - alpha / 2)
    budget = (gap / (2 * zc)) ** 2
    run_term = (tau * tau / max(1, runs)) if tau > 0 else 0.0
    if budget - run_term <= 0:
        return None
    n = p_bar * (1 - p_bar) / (budget - run_term)
    return max(1, math.ceil(n))


def half_width(p_hat: float, n: int, tau: float, runs: int,
               alpha: float = ALPHA) -> float:
    """估计量的噪声半宽 h（单侧，z_{1-α}）。"""
    se = math.sqrt(max(0.0, p_hat * (1 - p_hat) / max(1, n))
                   + (tau * tau / max(1, runs)))
    return z(1 - alpha) * se


def decide_rate(estimate: float | None, n: int | None, threshold: float,
                direction: str, tau: float | None = None, runs: int | None = None,
                alpha: float = ALPHA) -> dict[str, Any]:
    """比率型门槛的判定。**输出只有 PASS / FAIL。**

    规则（方向性门槛，`direction` ∈ {"<=", ">="}）：

    - PASS  ⟺  p̂ + h ≤ T   （`<=`）  /   p̂ − h ≥ T   （`>=`）
      即"取最坏情况也达标"。
    - FAIL  ⟺  p̂ − h > T   （`<=`）  /   p̂ + h < T   （`>=`）
      即"取最好情况也不达标"。
    - 其余情况（区间跨越阈值）⇒ **FAIL**，原因是"不可判定"。

    "不可判定"是 FAIL 的原因标签，不是第三种输出 —— 缺数据与不可判定一律不放行。
    """
    if direction not in ("<=", ">="):
        raise ValueError(f"direction 必须是 '<=' 或 '>='，收到 {direction!r}")

    if estimate is None:
        return {"pass": False, "value": "缺失",
                "reason": "未提供测量值（fail-closed）"}
    if n is None or n <= 0:
        return {"pass": False, "value": f"{estimate:.1%}（n 缺失）",
                "reason": "分母缺失，无法判定（fail-closed）"}
    if tau is None or runs is None or runs <= 0:
        return {"pass": False, "value": f"{estimate:.1%}（运行轮次/τ 缺失）",
                "reason": "缺少运行轮次或运行间噪声 τ，单次运行的结果不可进结论（fail-closed）"}

    h = half_width(estimate, n, tau, runs, alpha)
    if direction == "<=":
        if estimate + h <= threshold:
            return {"pass": True, "value": f"{estimate:.1%} ± {h:.1%}",
                    "reason": f"上界 {estimate + h:.1%} ≤ 阈值 {threshold:.1%}"}
        if estimate - h > threshold:
            return {"pass": False, "value": f"{estimate:.1%} ± {h:.1%}",
                    "reason": f"下界 {estimate - h:.1%} > 阈值 {threshold:.1%}"}
        return {"pass": False, "value": f"{estimate:.1%} ± {h:.1%}",
                "reason": (f"区间 [{estimate - h:.1%}, {estimate + h:.1%}] 跨越阈值 "
                           f"{threshold:.1%} ⇒ 不可判定（fail-closed）")}
    # direction == ">="
    if estimate - h >= threshold:
        return {"pass": True, "value": f"{estimate:.1%} ± {h:.1%}",
                "reason": f"下界 {estimate - h:.1%} ≥ 阈值 {threshold:.1%}"}
    if estimate + h < threshold:
        return {"pass": False, "value": f"{estimate:.1%} ± {h:.1%}",
                "reason": f"上界 {estimate + h:.1%} < 阈值 {threshold:.1%}"}
    return {"pass": False, "value": f"{estimate:.1%} ± {h:.1%}",
            "reason": (f"区间 [{estimate - h:.1%}, {estimate + h:.1%}] 跨越阈值 "
                       f"{threshold:.1%} ⇒ 不可判定（fail-closed）")}


def derive_requirements(spec: dict) -> list[dict]:
    """从门禁规范（事实）推导每个门槛的样本量与轮次要求。

    规范文件只存事实（阈值 / 基线 / 噪声模型 / 状态）；`required_n` 与
    `required_runs` 一律在这里现场推导，**不手写** —— 手写会与文档漂移，
    而漂移出来的门槛比没有门槛更危险。

    返回的每一行含：
      id, name, status, kind("rate"/"count"), required_n, required_runs,
      required_runs_asymptotic, gap, tau, threshold, baseline, decidable,
      decidable_note
    """
    alpha = float(spec.get("alpha", ALPHA))
    power = float(spec.get("power", POWER))
    models = spec.get("noise_models", {})
    out: list[dict] = []

    for g in spec.get("gates", []):
        nm = models[g["noise_model"]]
        tau = float(nm["tau"])
        min_runs = int(g.get("min_runs", 1) or 1)

        # ---- 计数型门槛（不是比率）----
        if g.get("count_unit") == "samples":
            certify_max = float(g.get("certify_rate_max", 0.05))
            need_n = int(3.0 / certify_max) + 1 if certify_max > 0 else 0
            out.append({
                "id": g["id"], "name": g["name"], "status": g.get("status", "active"),
                "kind": "count", "count_unit": "samples",
                "required_n": need_n, "required_runs": min_runs,
                "n_by_runs": {str(min_runs): need_n},
                "first_feasible": (min_runs, need_n),
                "gap": None, "tau": tau, "threshold": g["threshold"],
                "baseline": g.get("baseline"),
                "decidable": True,
                "decidable_note": (f"计数型：0 命中即可；n≥{need_n} 才能把真实率证到 "
                                   f"≤{certify_max:.0%}（0 事件的 95% 上界 ≈ 3/n）"),
            })
            continue

        # ---- 比率型门槛 ----
        base = float(g["baseline"])
        thr = float(g["threshold"])
        req_n = required_n_two_proportions(base, thr, alpha, power)
        p_bar = (base + thr) / 2
        gap = abs(base - thr)
        req_r = required_runs(gap, tau, p_bar, req_n, alpha)
        if req_r is not None:
            req_r = max(req_r, min_runs)

        asymptotic: int | None = None
        if tau > 0 and gap > 0:
            zc = z(1 - alpha / 2)
            budget = (gap / (2 * zc)) ** 2
            if budget > 0:
                asymptotic = int(tau * tau / budget) + 1

        # (n, R) 联合可行性：噪声半宽 ≤ 间距/2
        n_by_runs: dict[str, int | None] = {}
        for r in (1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 3000):
            n_by_runs[str(r)] = required_n_for_runs(gap, tau, p_bar, r, alpha)
        feasible = next(((int(r), n) for r, n in n_by_runs.items() if n is not None), None)

        if req_r is None:
            out.append({
                "id": g["id"], "name": g["name"], "status": g.get("status", "active"),
                "kind": "rate",
                "required_n": req_n, "required_runs": None,
                "required_runs_asymptotic": asymptotic,
                "n_by_runs": n_by_runs,
                "first_feasible": feasible,
                "gap": gap, "tau": tau, "threshold": thr, "baseline": base,
                "decidable": False,
                "decidable_note": (
                    f"在 n={req_n} 时不可判定（二项项已超过间距预算）；"
                    f"即使 n→∞ 也需 R≥{asymptotic} 轮独立运行"
                    if asymptotic else "不可判定"),
            })
            continue

        out.append({
            "id": g["id"], "name": g["name"], "status": g.get("status", "active"),
            "kind": "rate",
            "required_n": req_n, "required_runs": req_r,
            "required_runs_asymptotic": asymptotic,
            "n_by_runs": n_by_runs,
            "first_feasible": feasible,
            "gap": gap, "tau": tau, "threshold": thr, "baseline": base,
            "decidable": True,
            "decidable_note": f"n={req_n} 且 R≥{req_r} 时可判定",
        })
    return out
