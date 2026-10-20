#!/usr/bin/env python3
"""v0.4.0 severe 档澄清质量人工盲评：导出 / 解析 / 按场景汇总。

**这个脚本是重建的。** 原脚本随 F 盘一起丢失（见
`docs/v040-架构审查.md` P0-4）。本文件按《v0.4.0 效果验证报告》§4 描述的
实验设计重新实现。

**它到底在问什么**：severe 档（信息严重缺失）会被增强器转成"澄清"——它会
反过来问用户几个问题。**"该不该问"和"问得好不好"是两个完全不同的轴。**
v0.3.5 报告只验了前者（20/20 档位恰当），本实验验后者。

**判据（只问一件事）**：对每条样本的 3 个澄清问题，评审者回答

    「其中有几个是『不回答就没法完成任务』的？」→ 3 / 2 / 1 / 0

并被明确告知：**不要因为「解释写得通」或「问得比用户想得细」就算命中——
细不等于必要。** 这条提醒是判据的一部分，不是客套。

**评审者看不到的东西**（刻意）：

* 样本标注（``expected_behavior`` / ``must_preserve`` / ``must_not_add``）
* 档位（``enhancement_level``）/ 场景 / 模型名
* 原始样本 ID（只给中性编号 R01–R20）

理由：这些标注本身未经独立复核；拿一把没校准的尺子当评分标准是循环论证。

**流程**::

    # 1) 导出盲评包（需要一份含 questions 的增强原始结果）
    py -3 evaluation/v040_severe_blind.py export --raw <raw.json> --outdir <目录>

    # 2) 评审者在 blind_packet.md 上填写，答案存成 blind_answers.json
    #    格式：[{"unit": "R01", "hits": 3, "reason": "..."}, ...]
    #    也接受 {"R01": 3, ...} 或 CSV（unit,hits）

    # 3) 解析
    py -3 evaluation/v040_severe_blind.py parse \
        --answers <blind_answers.json> --key <blind_key.json> --out <scored.json>

    # 4) 按场景汇总
    py -3 evaluation/v040_severe_blind.py aggregate --scored <scored.json> --out <agg.json>

**盲键保护**：``blind_key.json`` 含 R 编号到真实样本的映射，**评审期间不得查看**。
export 时会把它与盲评包分文件存放，并在提示语里写明。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "evaluation" / "samples_v30.yaml"

# 报告 §4.1 记录的固定随机种子：保证 R01–R20 的分配可复现
SHUFFLE_SEED = 20260930

ANSWER_INSTRUCTION = """\
## 怎么填

对每个编号，只回答一个问题：

**这组澄清问题里，有几个是「不回答就没法完成任务」的？** → 填 3 / 2 / 1 / 0

判据只有一个：**不回答它，我是不是就做不了事？**

> 不要因为「解释写得通」或「问得比用户想得细」就算命中 ——
> **细不等于必要。**
>
> 举个例子：用户说「把这段会议录音翻译成英文」，然后增强器问
> 「这段录音的内容在哪里？」—— 用户说「这段录音」，东西显然在他手上，
> 这就是**问得多余**，不是命中。

如果整组问题都没必要，填 0，并写一句原因。
"""


def _load_records(raw_path: Path) -> list[dict[str, Any]]:
    """读入增强原始结果。兼容两种结构：

    * ``verify_enhance.py`` 的产物：顶层是一个 list
    * ``verify_stability.py`` 的产物：``{"records": [...]}``
    """
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records", [])
    if not isinstance(payload, list):
        raise ValueError(f"{raw_path} 结构无法识别：既不是 list 也没有 records 键")
    return payload


def _pick_severe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挑出 severe 档且**确实转了澄清**（有 questions）的记录。

    带 error 的记录直接排除——它没有可评的问题。
    """
    picked = []
    for record in records:
        if record.get("error"):
            continue
        if record.get("ambiguity_level") != "severe":
            continue
        questions = record.get("questions") or []
        if not questions:
            continue
        picked.append(record)
    return picked


def cmd_export(args: argparse.Namespace) -> int:
    records = _pick_severe(_load_records(Path(args.raw)))
    if not records:
        print("ERROR: 原始结果里没有可评的 severe 澄清样本", file=sys.stderr)
        return 2
    if len(records) > args.limit:
        records = records[: args.limit]

    order = list(range(len(records)))
    random.Random(SHUFFLE_SEED).shuffle(order)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    packet_lines = [
        "# severe 档澄清质量盲评包",
        "",
        "> 本包只给中性编号。**评审期间不要查看 `blind_key.json`**（它把编号对回了真实样本）。",
        "",
        ANSWER_INSTRUCTION,
        "",
        "---",
        "",
    ]
    key_rows: list[dict[str, Any]] = []

    for position, source_index in enumerate(order, start=1):
        record = records[source_index]
        unit = f"R{position:02d}"
        questions = record.get("questions") or []
        question_texts = [
            str(q.get("text") or q.get("question") or "").strip() for q in questions
        ]
        key_rows.append(
            {
                "unit": unit,
                "sample_id": record.get("id", ""),
                "scenario": record.get("scenario", ""),
                "ambiguity_level": record.get("ambiguity_level", ""),
                "expected_behavior": record.get("expected_behavior", ""),
                "enhancement_level": record.get("enhancement_level", ""),
                "questions": question_texts,
            }
        )
        packet_lines.append(f"## {unit}")
        packet_lines.append("")
        packet_lines.append("**用户的原话：**")
        packet_lines.append("")
        packet_lines.append("> " + str(record.get("original", "")).replace("\n", "\n> "))
        packet_lines.append("")
        packet_lines.append("**工具反过来问了：**")
        packet_lines.append("")
        for number, text in enumerate(question_texts, start=1):
            packet_lines.append(f"{number}. {text}")
        packet_lines.append("")
        packet_lines.append("**其中几个是「不回答就没法完成任务」的？** → ___ / 3")
        packet_lines.append("**理由（若填 0 必填）：**")
        packet_lines.append("")
        packet_lines.append("---")
        packet_lines.append("")

    (outdir / "blind_packet.md").write_text("\n".join(packet_lines), encoding="utf-8")
    (outdir / "blind_key.json").write_text(
        json.dumps(key_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    template = [{"unit": row["unit"], "hits": "", "reason": ""} for row in key_rows]
    (outdir / "blind_answers.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[export] 盲评单元 {len(key_rows)} 个 -> {outdir / 'blind_packet.md'}")
    print(f"[export] 揭盲映射（评审期间勿查看）-> {outdir / 'blind_key.json'}")
    print(f"[export] 答案模板 -> {outdir / 'blind_answers.json'}")
    return 0


def _load_answers(path: Path) -> dict[str, int]:
    """读答案。三种格式都收：list[dict] / dict[unit->int] / CSV(unit,hits)。"""
    text = path.read_text(encoding="utf-8")
    answers: dict[str, int] = {}

    if path.suffix.lower() == ".csv":
        for row in csv.DictReader(text.splitlines()):
            unit = str(row.get("unit") or row.get("id") or "").strip()
            hits = str(row.get("hits") or row.get("hit_count") or "").strip()
            if unit and hits != "":
                answers[unit] = int(hits)
        return answers

    payload = json.loads(text)
    if isinstance(payload, list):
        for item in payload:
            unit = str(item.get("unit") or item.get("id") or "").strip()
            hits = item.get("hits", item.get("hit_count"))
            if unit and hits != "" and hits is not None:
                answers[unit] = int(hits)
    elif isinstance(payload, dict):
        for unit, hits in payload.items():
            if isinstance(hits, int):
                answers[str(unit)] = hits
            elif isinstance(hits, dict):
                value = hits.get("hits", hits.get("hit_count"))
                if value is not None:
                    answers[str(unit)] = int(value)
    return answers


def cmd_parse(args: argparse.Namespace) -> int:
    key_rows = json.loads(Path(args.key).read_text(encoding="utf-8"))
    answers = _load_answers(Path(args.answers))

    scored: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in key_rows:
        unit = row["unit"]
        if unit not in answers:
            missing.append(unit)
            continue
        hits = answers[unit]
        question_count = len(row.get("questions") or [])
        if hits < 0 or hits > question_count:
            print(
                f"[警告] {unit} 的命中数 {hits} 超出问题数 {question_count}，按 0 处理",
                file=sys.stderr,
            )
            hits = 0
        scored.append(
            {
                "unit": unit,
                "sample_id": row.get("sample_id", ""),
                "scenario": row.get("scenario", ""),
                "hits": hits,
                "question_count": question_count,
            }
        )

    if missing:
        print(f"[警告] {len(missing)} 个单元未填写：{missing}", file=sys.stderr)

    total_hits = sum(item["hits"] for item in scored)
    total_questions = sum(item["question_count"] for item in scored)
    summary = {
        "units": len(scored),
        "missing_units": missing,
        "hit_distribution": dict(sorted(Counter(item["hits"] for item in scored).items(), reverse=True)),
        "total_hits": total_hits,
        "total_questions": total_questions,
        "hit_rate": round(total_hits / total_questions, 4) if total_questions else None,
    }
    out = {"summary": summary, "scored": scored}
    out_path = Path(args.out) if args.out else Path(args.answers).with_name("scored.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nscored -> {out_path}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.scored).read_text(encoding="utf-8"))
    scored = payload.get("scored", payload if isinstance(payload, list) else [])

    by_scenario: dict[str, dict[str, int]] = {}
    for item in scored:
        scenario = item.get("scenario") or "未标注"
        bucket = by_scenario.setdefault(scenario, {"units": 0, "hits": 0, "questions": 0})
        bucket["units"] += 1
        bucket["hits"] += int(item.get("hits", 0))
        bucket["questions"] += int(item.get("question_count", 0))

    rows = []
    for scenario, bucket in by_scenario.items():
        rows.append(
            {
                "scenario": scenario,
                "units": bucket["units"],
                "hits": bucket["hits"],
                "questions": bucket["questions"],
                "hit_rate": round(bucket["hits"] / bucket["questions"], 4) if bucket["questions"] else None,
            }
        )
    rows.sort(key=lambda r: (r["hit_rate"] if r["hit_rate"] is not None else -1), reverse=True)

    total_hits = sum(r["hits"] for r in rows)
    total_questions = sum(r["questions"] for r in rows)
    rates = [r["hit_rate"] for r in rows if r["hit_rate"] is not None]
    spread = (max(rates) / min(rates)) if rates and min(rates) > 0 else None

    out = {
        "by_scenario": rows,
        "totals": {
            "units": sum(r["units"] for r in rows),
            "hits": total_hits,
            "questions": total_questions,
            "hit_rate": round(total_hits / total_questions, 4) if total_questions else None,
            "scenario_rate_spread": round(spread, 2) if spread else None,
        },
    }
    out_path = Path(args.out) if args.out else Path(args.scored).with_name("by_scenario.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'场景':<12}{'条数':>6}{'命中/问题':>12}{'命中率':>9}")
    for row in rows:
        rate = f"{row['hit_rate']:.0%}" if row["hit_rate"] is not None else "—"
        print(f"{row['scenario']:<12}{row['units']:>6}{row['hits']:>6}/{row['questions']:<5}{rate:>9}")
    if spread:
        print(f"\n场景间命中率最大/最小之比：{spread:.1f} 倍")
    print(f"-> {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="v0.4.0 severe 档澄清质量人工盲评")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="导出盲评包")
    p_exp.add_argument("--raw", required=True, help="含 questions 的增强原始结果 json")
    p_exp.add_argument("--outdir", required=True, help="输出目录")
    p_exp.add_argument("--limit", type=int, default=20, help="最多导出多少条（默认 20）")
    p_exp.set_defaults(fn=cmd_export)

    p_parse = sub.add_parser("parse", help="解析人工答案")
    p_parse.add_argument("--answers", required=True, help="人工填写的答案 json/csv")
    p_parse.add_argument("--key", required=True, help="blind_key.json")
    p_parse.add_argument("--out", default=None, help="输出路径")
    p_parse.set_defaults(fn=cmd_parse)

    p_agg = sub.add_parser("aggregate", help="按场景汇总")
    p_agg.add_argument("--scored", required=True, help="parse 阶段的产物")
    p_agg.add_argument("--out", default=None, help="输出路径")
    p_agg.set_defaults(fn=cmd_aggregate)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
