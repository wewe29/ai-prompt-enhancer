"""门禁行为级复核器：用 committed fixture 跑 release_gate，逐条核对判定结果。

为什么需要它
============
`scripts/check-fail-open.py` 的探测方式多为「某个字符串是否还在」。
它能抓住**原地不改**的缺陷，抓不住**换了个写法、bug 还在**的缺陷
（`docs/evaluation-data-v040/README.md` §四 已经写明这条限制）。

本复核器走**行为级**：把真实产出的 fixture 喂进门禁，看判定结果对不对。
任何一条 fixture 的判定与期望不符 ⇒ 退出码非 0。

用法:
    python evaluation/check_gate_behavior.py
    python evaluation/check_gate_behavior.py --json

0 次 API 调用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from release_gate import compute_gates  # noqa: E402

FIXTURES = ROOT / "docs" / "evaluation-data-v040" / "fixtures"

#: fixture 名 → (期望总判定, 期望必须 FAIL 的门槛关键字)
EXPECTATIONS: dict[str, tuple[bool, list[str]]] = {
    "summary_pass.json": (True, []),
    "summary_missing_cost.json": (False, ["整体平均膨胀比"]),
    "summary_denominator_lost.json": (False, ["比较样本完整性"]),
    "summary_single_run.json": (False, ["[G4]", "[G5]"]),
    "summary_v040_gates_missing.json": (False, ["[G4]", "[G5]", "[G6]", "[G7]"]),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="门禁行为级复核（committed fixture → 判定结果）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = []
    ok_all = True
    for name, (expect_pass, expect_fail_keywords) in EXPECTATIONS.items():
        path = FIXTURES / name
        if not path.exists():
            results.append({"fixture": name, "ok": False, "error": "fixture 不存在"})
            ok_all = False
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        gates = compute_gates(summary)
        failed = [g for g in gates if not g["pass"]]
        all_pass = not failed

        missing = [kw for kw in expect_fail_keywords
                   if not any(kw in g["name"] for g in failed)]
        ok = (all_pass is expect_pass) and not missing
        ok_all = ok_all and ok
        results.append({
            "fixture": name,
            "expect": "PASS" if expect_pass else "FAIL",
            "actual": "PASS" if all_pass else "FAIL",
            "ok": ok,
            "missing_expected_failures": missing,
            "failed_gates": [f"{g['name']} | {g['value']} | {g['threshold']}" for g in failed],
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=== 门禁行为级复核（committed fixture）===")
        for r in results:
            status = "OK  " if r.get("ok") else "FAIL"
            line = f"[{status}] {r['fixture']}: 期望 {r.get('expect')} 实际 {r.get('actual')}"
            if r.get("error"):
                line += f"  ({r['error']})"
            print(line)
            for kw in r.get("missing_expected_failures", []):
                print(f"        !! 预期 FAIL 的门槛没有 FAIL: {kw}")
            for g in r.get("failed_gates", []):
                print(f"        FAIL: {g}")
        print()
        if ok_all:
            print("[复核] 全部 fixture 的判定与期望一致。")
            return 0
        print("[复核] 有 fixture 的判定与期望不一致 —— 门禁行为已漂移。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
