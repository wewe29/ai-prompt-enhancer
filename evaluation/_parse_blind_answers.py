#!/usr/bin/env python3
"""解析 severe 盲评的人工答案（原报告 §六 引用的脚本名）。

这是 `v040_severe_blind.py parse` 的**兼容入口**——两边的功能完全相同，
保留这个文件是因为《v0.4.0 效果验证报告》§六 的复现命令引用了它，
删掉会让那份报告的命令直接失效（这正是 P0-4 说的"结论与复现材料不同版本"）。

实际实现见 :mod:`v040_severe_blind`。

用法::

    py -3 evaluation/_parse_blind_answers.py \
        --answers <blind_answers.json> --key <blind_key.json> --out <scored.json>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v040_severe_blind as severe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="解析 severe 盲评答案（兼容入口）")
    parser.add_argument("--answers", required=True, help="人工填写的答案 json/csv")
    parser.add_argument("--key", required=True, help="blind_key.json")
    parser.add_argument("--out", default=None, help="输出路径")
    args = parser.parse_args()
    return severe.cmd_parse(args)


if __name__ == "__main__":
    sys.exit(main())
