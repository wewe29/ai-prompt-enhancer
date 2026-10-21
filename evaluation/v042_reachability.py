#!/usr/bin/env python3
"""v0.4.2 效果线的**可达性核算**：这次运行到底能支撑哪几条门槛。

为什么要有这个脚本
==================
v0.4.0 的门槛 G4/G5/G6 要求一定的样本量，而**要求本身是现场推导出来的**
（`gate_decidability.derive_requirements`）。光看门槛文件不知道「现在够不够」，
必须拿**真实运行结果**去对账：

    实测可评规模  vs  门槛要求的规模  ⇒  缺口

三类分母必须分开算，否则口径会错：

======================  ==========================  =======================
门槛                    n 的单位                     本脚本算的分子
======================  ==========================  =======================
G4 翻译场景命中率        问题条数 (questions)        翻译场景的**问题**数
G5 severe 整体命中率     问题条数 (questions)        severe 档的**问题**数
G6 问已知信息条数        样本条数 (samples)          有 must_not_question 标注的**样本**数
G7 交付状态漂移          运行次数 (runs)             本脚本不负责（另跑）
======================  ==========================  =======================

**G4/G5 的 n 是问题数不是样本数** —— 基线 44/60 与 1/9 里的分母都是「问题」。

用法
====
    python evaluation/v042_reachability.py --raw <raw.json>
    python evaluation/v042_reachability.py --raw <raw.json> --json

0 次 API 调用，只读产物与已提交的门禁规范。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 本机控制台默认 GBK，报告里若出现 GBK 编不了的符号（⇒、→、⋯），
# print() 会抛 UnicodeEncodeError 把整份报告打断 ——
# 报告工具被自己的数据搞崩，比显示一个 '?' 严重得多。
try:  # pragma: no cover - 取决于运行环境是否支持 reconfigure
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 老解释器没有 reconfigure 时忽略
    pass

from gate_decidability import derive_requirements  # noqa: E402

SPEC_PATH = HERE / "gate_spec_v040.json"

#: G6 的「0 事件 95% 率上界」近似式（= 3/n）。
#: 与 gate_spec_v040.json 的 note 一致：n=20 时约 15%，n≥60 时才 ≤5%。
ZERO_EVENT_UPPER_BOUND_NUMERATOR = 3.0


def load_spec() -> dict[str, Any]:
    with SPEC_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_records(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """读 raw 产物。兼容三种结构。

    * ``{"_identity": {...}, "records": [...]}``（verify_enhance.py 当前产物）
    * ``{"records": [...]}``（verify_stability.py）
    * 顶层就是 list（历史产物）
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = None
    if isinstance(payload, dict):
        identity = payload.get("_identity")
        records = payload.get("records", [])
    else:
        records = payload
    return list(records), identity


def _question_count(record: dict[str, Any]) -> int:
    return len(record.get("questions") or [])


def measure(records: list[dict[str, Any]]) -> dict[str, Any]:
    """把一次真实运行折算成各门槛关心的规模。"""
    ok = [r for r in records if not r.get("error")]
    severe = [r for r in ok if r.get("ambiguity_level") == "severe"]
    severe_units = [r for r in severe if _question_count(r) > 0]
    translate_units = [r for r in severe_units if r.get("scenario") == "翻译"]

    labeled = [r for r in severe if r.get("must_not_question")]
    # 第六维命中数：直接用判定结果，避免这里重算一遍匹配逻辑。
    hits = [r for r in labeled if _has_redundant_hit(r)]

    return {
        "total_records": len(records),
        "error_count": len(records) - len(ok),
        "judged_count": len(ok),
        "severe_total": len(severe),
        "severe_units_with_questions": len(severe_units),
        "severe_units_without_questions": len(severe) - len(severe_units),
        "severe_questions": sum(_question_count(r) for r in severe_units),
        "translate_units_with_questions": len(translate_units),
        "translate_questions": sum(_question_count(r) for r in translate_units),
        "labeled_samples": len(labeled),
        "redundant_hits": len(hits),
        "questions_per_unit": (
            round(sum(_question_count(r) for r in severe_units) / len(severe_units), 2)
            if severe_units else 0.0
        ),
    }


def _has_redundant_hit(record: dict[str, Any]) -> bool:
    """该样本是否命中了 must_not_question。

    复用 verify_enhance 的判定函数，**不重写一遍匹配逻辑** ——
    两处各写一遍，早晚会长歪。
    """
    try:
        import verify_enhance as ve
    except Exception:  # noqa: BLE001 - 导入失败不该让整个核算崩掉
        return False
    try:
        return ve.judge_dimension_redundant_question(record).get("verdict") == "严重问题"
    except Exception:  # noqa: BLE001 - 单条判不出异常不该中断核算
        return False


def gate_rows(spec: dict[str, Any], measured: dict[str, Any]) -> list[dict[str, Any]]:
    """把门禁要求和实测规模对成一张表。"""
    reqs = {r["id"]: r for r in derive_requirements(spec)}
    by_metric = {g["metric"]: g for g in spec["gates"]}

    rows = []

    # ---- G4：翻译场景（n = 问题数）----
    g4 = by_metric["blind_hit_rate_translate"]
    r4 = reqs["G4"]
    rows.append({
        "gate": "G4",
        "name": g4["name"],
        "n_unit": g4.get("n_unit"),
        "required_n": r4["required_n"],
        "required_runs": r4["required_runs"],
        "measured_n": measured["translate_questions"],
        "gap": measured["translate_questions"] - r4["required_n"],
        "reachable": measured["translate_questions"] >= r4["required_n"],
    })

    # ---- G5：severe 整体（n = 问题数）----
    g5 = by_metric["blind_hit_rate_severe"]
    r5 = reqs["G5"]
    rows.append({
        "gate": "G5",
        "name": g5["name"],
        "n_unit": g5.get("n_unit"),
        "required_n": r5["required_n"],
        "required_runs": r5["required_runs"],
        "measured_n": measured["severe_questions"],
        "gap": measured["severe_questions"] - r5["required_n"],
        "reachable": measured["severe_questions"] >= r5["required_n"],
    })

    # ---- G6：问已知信息（n = 标注样本数）----
    g6 = by_metric["redundant_question_count"]
    r6 = reqs["G6"]
    n_labeled = measured["labeled_samples"]
    upper = (
        round(ZERO_EVENT_UPPER_BOUND_NUMERATOR / n_labeled, 4) if n_labeled else None
    )
    rows.append({
        "gate": "G6",
        "name": g6["name"],
        "n_unit": g6.get("n_unit"),
        "required_n": r6["required_n"],
        "required_runs": r6["required_runs"],
        "measured_n": n_labeled,
        "gap": n_labeled - r6["required_n"],
        "reachable": n_labeled >= r6["required_n"],
        "hits": measured["redundant_hits"],
        "zero_event_upper_bound": upper,
        "certify_rate_max": g6.get("certify_rate_max"),
    })

    return rows


def extra_severe_samples_needed(measured: dict[str, Any], required_n: int) -> int | None:
    """按实测的「每单元问题数」外推：还差多少条 severe 样本才够 G5。

    ⚠ 这是**外推**，不是保证：新增样本不一定都转澄清（自检规则会让部分
    severe 不再转澄清），所以实测后必须重跑本核算。
    """
    per_unit = measured["questions_per_unit"]
    if per_unit <= 0:
        return None
    units_needed = math.ceil(required_n / per_unit)
    return max(0, units_needed - measured["severe_units_with_questions"])


def build_report(records: list[dict[str, Any]], identity: dict[str, Any] | None) -> dict[str, Any]:
    spec = load_spec()
    measured = measure(records)
    rows = gate_rows(spec, measured)
    g5_required = next(r["required_n"] for r in rows if r["gate"] == "G5")
    return {
        "identity": identity,
        "spec_version": spec["spec_version"],
        "measured": measured,
        "gates": rows,
        "g5_extra_severe_samples_needed": extra_severe_samples_needed(
            measured, g5_required
        ),
    }


def print_report(report: dict[str, Any]) -> None:
    m = report["measured"]
    ident = report.get("identity") or {}
    fields = ident.get("fields", {}) if isinstance(ident, dict) else {}

    print("=" * 78)
    print("v0.4.2 可达性核算（0 次 API 调用）")
    print("=" * 78)
    print(f"规范 spec_version = {report['spec_version']}")
    if fields:
        print(
            "运行身份：prompt_version={pv} model={m} temperature={t} samples_sha256={s}".format(
                pv=fields.get("prompt_version", "?"),
                m=fields.get("model", "?"),
                t=fields.get("temperature", "?"),
                s=str(fields.get("samples_sha256", "?"))[:12],
            )
        )
    print()
    print("--- 实测规模 ---")
    print(f"  样本总数 {m['total_records']}（判定 {m['judged_count']}，错误 {m['error_count']}）")
    print(f"  severe 档 {m['severe_total']} 条")
    print(f"    ├─ 转澄清（有提问）{m['severe_units_with_questions']} 条")
    print(f"    └─ 未转澄清          {m['severe_units_without_questions']} 条  ← 自检规则生效的独立观察值")
    print(f"    └─ 可评问题总数     {m['severe_questions']} 个（每单元 {m['questions_per_unit']} 个）")
    print(f"  翻译场景可评：{m['translate_units_with_questions']} 条 / {m['translate_questions']} 个问题")
    print(f"  有 must_not_question 标注的样本：{m['labeled_samples']} 条（第六维命中 {m['redundant_hits']} 条）")
    print()

    print("--- 门槛对账 ---")
    hdr = f"{'门槛':<5}{'n 单位':<11}{'要求 n':>8}{'要求 R':>8}{'实测 n':>9}{'缺口':>8}  可达"
    print(hdr)
    print("-" * len(hdr))
    for r in report["gates"]:
        print(
            f"{r['gate']:<5}{r['n_unit']:<11}{r['required_n']:>8}{r['required_runs']:>8}"
            f"{r['measured_n']:>9}{r['gap']:>+8}  {'是' if r['reachable'] else '否'}"
        )
    print()

    g6 = next(r for r in report["gates"] if r["gate"] == "G6")
    if g6["measured_n"]:
        print(
            f"G6 说明：0 命中的 95% 率上界 ≈ 3/n = {g6['zero_event_upper_bound']:.1%}"
            f"（n={g6['measured_n']}），规范自设的认证上限是 "
            f"{g6['certify_rate_max']:.0%}。"
        )
        if not g6["reachable"]:
            print(
                "  ⇒ 即使 0 命中，也**证不到**规范要求的 "
                f"{g6['certify_rate_max']:.0%}；只能说「这 {g6['measured_n']} 条标注样本上没复现」。"
            )
    print()

    extra = report["g5_extra_severe_samples_needed"]
    if extra is not None:
        print(
            f"G5 外推：按每单元 {m['questions_per_unit']} 个问题算，"
            f"还需新增约 **{extra} 条 severe 样本**才够要求 n。"
        )
        print("  （外推，非保证：新增样本不一定都转澄清 ⇒ 扩完必须重跑本核算）")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v0.4.2 效果线可达性核算")
    ap.add_argument("--raw", required=True, help="verify_enhance.py 产出的 raw json")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    path = Path(args.raw)
    if not path.exists():
        print(f"ERROR: 产物不存在：{path}", file=sys.stderr)
        return 2

    records, identity = load_records(path)
    report = build_report(records, identity)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
