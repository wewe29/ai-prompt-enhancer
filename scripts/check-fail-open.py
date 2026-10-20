#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PromptCraft v0.4.0 —— 「把失败显示成成功」清单复核器。

用法：
    python scripts/check-fail-open.py
    python scripts/check-fail-open.py --json
    python scripts/check-fail-open.py --only A,C

它读 docs/v040-失败伪装清单.json，对每条 id 做一次结构探测，并输出 OPEN / FIXED / UNVERIFIED。

设计纪律（与本项目自身的审查结论一致）：
  * 探测不到 = 不等于通过。kind=manual 的条目一律输出 UNVERIFIED，绝不输出 PASS。
  * 只要还有 P0 未闭环，或有 P0/P1 处于 UNVERIFIED，退出码非 0。
  * 结论必须来自当前工作区实际文件内容，不能来自清单里写的行号（行号只是定位线索）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs" / "v040-失败伪装清单.json"

OPEN = "OPEN"            # 缺陷仍在代码里
FIXED = "FIXED"          # 探测目标已消失
UNVERIFIED = "UNVERIFIED"  # 工具无法判定，需人工/独立复核


def _read(rel: str) -> str | None:
    path = ROOT / rel
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.replace("\r\n", "\n")


def _line_of(text: str, needle: str) -> int | None:
    idx = text.find(needle)
    return text.count("\n", 0, idx) + 1 if idx >= 0 else None


def _block(text: str, pattern: str) -> tuple[str | None, int | None]:
    """截取正则指定的代码块，返回 (块文本, 块起始行)。找不到返回 (None, None)。"""
    import re

    try:
        rx = re.compile(pattern, re.S)
    except re.error as exc:  # 探测本身写错 = 未判定，不是通过
        raise ValueError(f"正则无法编译：{exc}") from exc
    m = rx.search(text)
    if not m:
        return None, None
    return m.group(0), text.count("\n", 0, m.start()) + 1


def probe_item(item: dict) -> tuple[str, str]:
    """返回 (状态, 证据文本)。"""
    probe = item.get("probe") or {}
    kind = probe.get("kind", "manual")
    rel = probe.get("file", "")
    pattern = probe.get("pattern", "")

    if kind == "manual":
        note = probe.get("note") or "需人工复核"
        return UNVERIFIED, note

    text = _read(rel)
    if text is None:
        return UNVERIFIED, f"文件不存在：{rel}（无法判定）"

    if kind in ("substring_present", "substring_absent"):
        hit = pattern in text
        if kind == "substring_present":
            if hit:
                return OPEN, f"{rel}:{_line_of(text, pattern)} 命中"
            return FIXED, f"{rel} 已无该模式"
        if hit:
            return OPEN, f"{rel}:{_line_of(text, pattern)} 仍然存在"
        return FIXED, f"{rel} 已无该模式"

    if kind in ("regex_present", "regex_absent"):
        import re

        try:
            rx = re.compile(pattern)
        except re.error as exc:  # 探测本身写错 = 未判定，不是通过
            return UNVERIFIED, f"正则无法编译：{exc}"
        m = rx.search(text)
        if kind == "regex_present":
            if m:
                return OPEN, f"{rel}:{text.count(chr(10), 0, m.start()) + 1} 命中"
            return FIXED, f"{rel} 已无该模式"
        if m:
            return OPEN, f"{rel}:{text.count(chr(10), 0, m.start()) + 1} 仍然存在"
        return FIXED, f"{rel} 已无该模式"

    # ---- 块级探测：把「修复有没有落到这一段代码里」变成可机器判定的问题 ----
    if kind in ("guard_in_block", "forbidden_in_block"):
        block_pattern = probe.get("block", "")
        needle = probe.get("guard") or probe.get("forbidden") or ""
        try:
            block, start = _block(text, block_pattern)
        except ValueError as exc:
            return UNVERIFIED, str(exc)
        if block is None:
            return UNVERIFIED, f"{rel} 未定位到目标代码块（无法判定）"
        if kind == "guard_in_block":
            if needle in block:
                return FIXED, f"{rel}:{start} 代码块内已出现守卫 {needle!r}"
            return OPEN, f"{rel}:{start} 代码块内缺少守卫 {needle!r}"
        if needle in block:
            return OPEN, f"{rel}:{start} 代码块内仍存在 {needle!r}"
        return FIXED, f"{rel}:{start} 代码块内已无 {needle!r}"

    if kind == "regex_forbidden_in_block":
        block_pattern = probe.get("block", "")
        needle = probe.get("forbidden", "")
        try:
            block, start = _block(text, block_pattern)
        except ValueError as exc:
            return UNVERIFIED, str(exc)
        if block is None:
            return UNVERIFIED, f"{rel} 未定位到目标代码块（无法判定）"
        import re

        try:
            rx = re.compile(needle)
        except re.error as exc:
            return UNVERIFIED, f"正则无法编译：{exc}"
        if rx.search(block):
            return OPEN, f"{rel}:{start} 代码块内仍命中禁止模式"
        return FIXED, f"{rel}:{start} 代码块内已无禁止模式"

    return UNVERIFIED, f"未知探测类型：{kind}"


def main() -> int:
    ap = argparse.ArgumentParser(description="复核「把失败显示成成功」清单")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--only", default="", help="只复核指定子族，逗号分隔，如 A,C")
    ap.add_argument("--registry", default="", help="指定清单文件路径（默认 docs/v040-失败伪装清单.json）")
    args = ap.parse_args()

    registry_path = Path(args.registry) if args.registry else REGISTRY
    if not registry_path.exists():
        print(f"找不到清单：{registry_path}", file=sys.stderr)
        return 2

    reg = json.loads(registry_path.read_text(encoding="utf-8"))
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    rows = []
    for item in reg["items"]:
        if only and item["family"].upper() not in only and item["id"].upper() not in only:
            continue
        status, evidence = probe_item(item)
        rows.append(
            {
                "id": item["id"],
                "family": item["family"],
                "severity": item["severity"],
                "file": item.get("file"),
                "line": item.get("line"),
                "claim": item.get("claim"),
                "acceptance": item.get("acceptance"),
                "owner": item.get("owner"),
                "status": status,
                "evidence": evidence,
            }
        )

    if args.json:
        print(json.dumps({"baseline": reg["baseline"], "items": rows}, ensure_ascii=False, indent=2))
    else:
        base = reg["baseline"]
        print(f"基线 commit = {base['commit']}  分支 = {base['branch']}")
        print(f"清单 = {registry_path}  条目 = {len(rows)}")
        print("-" * 110)
        for r in rows:
            print(
                f"[{r['severity']}] {r['id']:<3} {str(r['file']):<32}:{str(r['line']):<5} "
                f"{r['status']:<10} {r['evidence']}"
            )
        print("-" * 110)

    counts = {OPEN: 0, FIXED: 0, UNVERIFIED: 0}
    for r in rows:
        counts[r["status"]] += 1
    print(f"OPEN {counts[OPEN]} / FIXED {counts[FIXED]} / UNVERIFIED {counts[UNVERIFIED]} / TOTAL {len(rows)}")

    blocking = [
        r
        for r in rows
        if r["severity"] == "P0" and r["status"] in (OPEN, UNVERIFIED)
    ]
    p1_unverified = [r for r in rows if r["severity"] == "P1" and r["status"] == UNVERIFIED]
    if blocking:
        print(f"\n⛔ 仍有 {len(blocking)} 条 P0 未闭环：", ", ".join(r["id"] for r in blocking))
    if p1_unverified:
        print(f"⚠ 仍有 {len(p1_unverified)} 条 P1 工具无法判定，需人工复核：", ", ".join(r["id"] for r in p1_unverified))
    if not blocking and not p1_unverified:
        print("\n✅ 全部 P0 已闭环，且无未判定项。")
    return 1 if (blocking or p1_unverified) else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass
    raise SystemExit(main())
