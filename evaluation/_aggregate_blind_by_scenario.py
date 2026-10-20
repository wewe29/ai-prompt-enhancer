#!/usr/bin/env python3
"""按场景汇总 severe 盲评结果（原报告 §六 引用的脚本名）。

这是 `v040_severe_blind.py aggregate` 的**兼容入口**——两边的功能完全相同，
保留这个文件是因为《v0.4.0 效果验证报告》§四.3 / §六 的复现命令引用了它。

实际实现见 :mod:`v040_severe_blind`。

用法::

    py -3 evaluation/_aggregate_blind_by_scenario.py \
        --scored <scored.json> --out <by_scenario.json>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v040_severe_blind as severe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按场景汇总 severe 盲评（兼容入口）")
    parser.add_argument("--scored", required=True, help="parse 阶段的产物")
    parser.add_argument("--out", default=None, help="输出路径")
    args = parser.parse_args()
    return severe.cmd_aggregate(args)


if __name__ == "__main__":
    sys.exit(main())
