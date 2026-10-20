"""v0.4.0 实验分析的**共用库**。

## 为什么要有这个文件

2026-10-01 的代码审查发现：已知的「全失败样本被算成稳定」bug 在 `_phase1_final.py`
里修了，但另外 4 个兄弟分析脚本（`_temp_compare.py` / `_version_compare.py` /
`_temp_curve.py` / `_phase1_attribution.py`）保留着**同一个未修复公式**：

    used += 1                      # 在 continue 之前自增
    if not lv: continue            # 三次全失败的样本被跳过
    ...
    "stable": used - len(unstable) # 于是它被算成"完美稳定"

用真实 E5 数据实测：medium 60 次调用全部 HTTP 402，未修复公式输出
「三次全一致 20/20、翻转率 0%」——与 bug 的原始现象逐字相同。

**公式必须只有一份。** 本模块是唯一的实现，其余脚本一律调用它。

## 数据从哪来

统一从 `evaluation/experiments/manifest.json` 按**实验 ID** 解析路径，不再按文件名后缀猜。
原因是猜文件名已经出过一次错：新运行覆盖旧文件，导致对照表里"上一版"那列其实是最新的数据。

## 三条硬规则

1. 三次全失败的样本（零条档位数据）**既不算稳定也不算不稳定**，单列 `dead`。
2. 无数据时比率返回 `None`（JSON null），**不返回 0.0**。0.0 会被读成"测了且没翻"。
3. 找不到任何数据 → 打印醒目错误并返回退出码 2，**绝不静默输出空表报成功**。
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPDIR = ROOT / "evaluation" / "experiments"
MANIFEST = EXPDIR / "manifest.json"

LEVEL_ORDER = ["none", "light", "clarify"]

# expected_behavior → 可接受的档位集合。clear/light 接受 none 是有意的：
# "不该改就别改" 对这两种预期都算恰当。**这正是恰当率会奖励"什么都不做"的原因**，
# 所以恰当率不能单独当验收指标。
ACCEPTABLE = {
    "none": {"none", "light"},
    "light": {"none", "light"},
    "clarify": {"light", "clarify"},
}


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        raise FileNotFoundError(
            f"找不到 {MANIFEST}。实验数据统一在 evaluation/experiments/，"
            f"用 evaluation/_consolidate_experiments.py --move 生成。"
        )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def resolve(exp_id: str, needle: str, ext: str = ".json") -> pathlib.Path:
    """按实验 ID + 文件名片段解析出数据文件的绝对路径。找不到就抛，不返回 None。"""
    man = load_manifest()
    if exp_id not in man["experiments"]:
        raise KeyError(f"manifest 里没有实验 {exp_id}；可用：{sorted(man['experiments'])}")
    for rel, info in man["experiments"][exp_id]["files"].items():
        if info["src"] == f"{needle}{ext}":
            return EXPDIR / rel
    avail = [i["src"] for i in man["experiments"][exp_id]["files"].values()]
    raise KeyError(f"实验 {exp_id} 里没有 {needle}{ext}；该实验现有：{avail}")


def _level_key(levels: list[str]) -> str:
    ordered = sorted({lv for lv in levels if lv}, key=lambda x: LEVEL_ORDER.index(x) if x in LEVEL_ORDER else 99)
    return "+".join(ordered) if ordered else "(空)"


def load_samples() -> dict[str, dict[str, Any]]:
    import yaml

    raw = yaml.safe_load((ROOT / "evaluation" / "samples_v30.yaml").read_text(encoding="utf-8"))["samples"]
    return {s["id"]: s for s in raw}


def stats(runs_by_sample: dict[str, list[dict[str, Any]]], samples: dict[str, dict[str, Any]],
          level: str) -> dict[str, Any]:
    """把一次实验的原始运行记录汇总成可比较的指标。

    **dead 样本的处置是本函数存在的理由**：它们被单列，绝不混进 stable / unstable。
    """
    combos: collections.Counter = collections.Counter()
    dist: collections.Counter = collections.Counter()
    bloat: dict[str, list[float]] = collections.defaultdict(list)
    unstable: list[str] = []
    dead: list[str] = []
    partial: list[str] = []

    used = valid = stable = 0
    runs_ok = runs_all = errors = 0
    ok_rate_num = 0

    for sid, meta in samples.items():
        if meta["ambiguity_level"] != level:
            continue
        used += 1
        rs = runs_by_sample.get(sid)
        if not rs:
            dead.append(sid)
            continue

        runs_all += len(rs)
        errors += sum(1 for r in rs if r.get("error"))
        lv = [r["level"] for r in rs if not r.get("error") and r.get("level")]
        runs_ok += len(lv)

        if not lv:
            dead.append(sid)          # 三次全失败：不是"稳定"，是"没数据"
            continue
        valid += 1
        if len(lv) < len(rs):
            partial.append(sid)

        combos[_level_key(lv)] += 1
        dist.update(lv)
        if len(set(lv)) == 1:
            stable += 1
        else:
            unstable.append(sid)

        exp = meta.get("expected_behavior", "")
        for r in rs:
            if r.get("error") or not r.get("level"):
                continue
            if r["level"] in ACCEPTABLE.get(exp, {r["level"]}):
                ok_rate_num += 1
            if r.get("bloat_ratio"):
                bloat[r["level"]].append(r["bloat_ratio"])

    touching = sum(c for k, c in combos.items() if set(k.split("+")) & {"none", "light"})
    flip = touching - combos.get("none", 0) - combos.get("light", 0)

    return {
        "level": level,
        "samples": used,
        "valid_samples": valid,
        "dead_samples": dead,
        "partial_samples": partial,
        "runs_total": runs_all,
        "runs_ok": runs_ok,
        "errors": errors,
        "run_coverage": round(runs_ok / runs_all, 3) if runs_all else None,
        "stable_samples": stable,
        "unstable_samples": unstable,
        "level_combos": dict(combos),
        "flip_rate": (round(flip / touching, 3) if touching else None),
        "touching_none_light": touching,
        "level_dist": {k: dist.get(k, 0) for k in LEVEL_ORDER},
        "light_rate": round(dist.get("light", 0) / runs_ok, 3) if runs_ok else None,
        "none_rate": round(dist.get("none", 0) / runs_ok, 3) if runs_ok else None,
        "clarify_rate": round(dist.get("clarify", 0) / runs_ok, 3) if runs_ok else None,
        "bloat": {k: {"avg": round(sum(v) / len(v), 2), "max": round(max(v), 2), "n": len(v)}
                  for k, v in bloat.items()},
        # **恰当率必须和覆盖率一起看**：分母是成功运行数，失败越多分母越小、比率越好看。
        "ok_rate": (round(ok_rate_num / runs_ok, 3) if runs_ok else None),
    }


def require_data(rows: list[dict[str, Any]], min_valid: int = 1) -> None:
    """没有数据就大声失败。绝不静默输出空表。"""
    bad = [r for r in rows if r["valid_samples"] < min_valid]
    if not bad:
        return
    print("=" * 72, file=sys.stderr)
    print("ERROR: 以下变体没有足够的数据，指标不可用，拒绝输出空表：", file=sys.stderr)
    for r in bad:
        print(
            f"  {r.get('label', '?')}  有效样本 {r['valid_samples']}/{r['samples']}"
            f"  失败运行 {r['errors']}/{r['runs_total']}  覆盖率 {r['run_coverage']}",
            file=sys.stderr,
        )
        if r["dead_samples"]:
            print(f"    全失败样本: {', '.join(r['dead_samples'][:5])}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    raise SystemExit(2)


def print_table(rows: list[dict[str, Any]], title: str) -> None:
    """统一的多变体对照表。表头不再硬编码 /20，分母跟着数据走。"""
    print("=" * 92)
    print(title)
    print("=" * 92)
    print(f"{'变体':<26}{'覆盖率':>9}{'有效样本':>10}{'全一致':>8}{'翻转率':>9}{'light':>8}{'none':>8}{'恰当率':>9}")
    print("-" * 92)
    for r in rows:
        cov = r["run_coverage"]
        fr = r["flip_rate"]
        ok = r["ok_rate"]
        stable_txt = f"{r['stable_samples']}/{r['valid_samples']}" if r["valid_samples"] else "—"
        light = f"{r['light_rate']:.0%}" if r["light_rate"] is not None else "—"
        none_ = f"{r['none_rate']:.0%}" if r["none_rate"] is not None else "—"
        print(
            f"{r['label']:<26}{(f'{cov:.0%}' if cov is not None else '—'):>9}"
            f"{r['valid_samples']:>10}{stable_txt:>8}"
            f"{(f'{fr:.0%}' if fr is not None else 'N/A'):>9}{light:>8}{none_:>8}"
            f"{(f'{ok:.0%}' if ok is not None else 'N/A'):>9}"
        )
    print()
    for r in rows:
        flag = f"  [全失败 {len(r['dead_samples'])} 条]" if r["dead_samples"] else ""
        print(f"[{r['label']}] {r['level_combos']}{flag}")
