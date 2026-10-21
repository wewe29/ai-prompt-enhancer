"""门禁规范推导报告：把 evaluation/gate_spec_v040.json 里的事实推成可执行的数字。

用法:
    python evaluation/gate_spec_report.py            # 人读表格
    python evaluation/gate_spec_report.py --json     # 机器可读

本脚本不发起任何 API 调用，只读仓库内已提交的规范文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 本机控制台默认 GBK，规范里若出现 GBK 编不了的符号（⇒、→、⋯），
# print() 会抛 UnicodeEncodeError 把整份报告打断 —— 报告工具被自己的数据搞崩。
# 改成 errors="replace"：编不了的字符退化成 '?'，其余照常输出，报告不中断。
try:  # pragma: no cover - 取决于运行环境是否支持 reconfigure
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 老解释器没有 reconfigure 时忽略
    pass

from gate_decidability import (  # noqa: E402
    ALPHA,
    POWER,
    derive_requirements,
)

SPEC_PATH = HERE / "gate_spec_v040.json"


def load_spec() -> dict:
    with SPEC_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def derive(spec: dict) -> list[dict]:
    """从规范事实推导每个门槛的 required_n / required_runs。

    实现放在 `gate_decidability.derive_requirements`，与 release_gate 共用同一份，
    避免「文档里的数字」与「门禁用的数字」漂移。
    """
    return derive_requirements(spec)


def print_table(spec: dict, rows: list[dict]) -> None:
    print(f"规范 = {SPEC_PATH.name}（spec_version={spec['spec_version']}）")
    print(f"α={spec.get('alpha')}（单侧）  功效={spec.get('power')}  "
          f"基线 commit={spec.get('baseline_commit')}")
    print()
    for nm, m in spec["noise_models"].items():
        print(f"噪声模型 {nm}: τ={m['tau']}")
        print(f"    来源: {m['tau_source']}")
        print(f"    限制: {m['tau_caveat']}")
    print()
    hdr = (f"{'id':<4}{'门槛':<34}{'现状':>7}{'目标':>7}{'间距':>7}"
           f"{'τ':>6}{'需 n':>8}{'需 R':>7}  {'状态':<6} 可判定")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        gap = f"{r['gap']*100:.0f}pt" if r["gap"] is not None else "-"
        if r["kind"] == "count":
            base = f"{r['baseline']:.0f}条" if r["baseline"] is not None else "-"
            thr = f"{r['threshold']:.0f}条"
        else:
            base = f"{r['baseline']*100:.0f}%" if r["baseline"] is not None else "-"
            thr = f"{r['threshold']*100:.0f}%"
        req_r = r["required_runs"]
        req_r_s = "不可行" if req_r is None else str(req_r)
        fe = r.get("first_feasible")
        fe_s = f"R={fe[0]} 时 n≥{fe[1]}" if fe else "任何 (n,R) 都不可行"
        print(f"{r['id']:<4}{r['name'][:32]:<34}{base:>7}{thr:>7}{gap:>7}"
              f"{r['tau']:>6.2f}{r['required_n']:>8}{req_r_s:>7}  "
              f"{r['status']:<6} {fe_s}")
    print()
    # 已知缺口必须出现在报告里 —— 只藏在 JSON 里等于没有。
    gaps = [g for g in spec.get("gates", []) if g.get("known_gap")]
    if gaps:
        print("--- 已知缺口（active 门槛的可达性）---")
        for g in gaps:
            print(f"  [{g['id']}] {g['name']}")
            print(f"         {g['known_gap']}")
        print()
    print("说明：")
    print("  · 『需 n』= 把『现状水平』与『目标水平』区分开所需的提示词条数（纯二项口径）")
    print("  · 『需 R』= 让噪声半宽 ≤ 间距/2 所需的独立运行轮数；『不可行』表示再加轮次也补不回来")
    print("  · 作废（void）的门槛不参与放行判定，但必须显式出现在报告里 —— 作废不等于通过")
    print("  · 每条门槛 n 的单位见规范里的 n_unit 字段"
          "（questions=问题条数 / samples=样本条数 / runs=运行次数）"
          " —— 装配 v040_gates 时口径对不上，门禁就判错了")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="推导 v0.4.0 门禁规范的样本量与轮次要求")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    spec = load_spec()
    rows = derive(spec)
    if args.json:
        print(json.dumps({"spec_version": spec["spec_version"], "gates": rows},
                         ensure_ascii=False, indent=2))
    else:
        print_table(spec, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
