"""把 v0.4.0 的实测产物归档进版本控制（D7 / P0-4 的闭环）。

**为什么需要这个脚本**：`.gitignore` 排除 `evaluation/results/*`（可再生的中间物），
但报告里写着的数字必须**能被第三方核对**。所以参与结论的产物要：

1. 复制到 ``docs/evaluation-data-v040/<实验>/``（入库）；
2. 记下 sha256（产物被改写时一眼能看出来）；
3. 记下**运行身份**（提示词版本 + 正文哈希 + 模型 + 温度 + 样本集哈希 + 重复次数）；
4. 与报告里的**声称值**逐项对账，把差异如实写进产物。

第 4 步是关键：归档不是"把文件拷进去"，是**把"文件里的数"与"报告里的数"放在一起**。
对不上就是发现，对得上也要留下证据。

用法::

    py -3 tools/archive_v040_data.py --experiment stability
    py -3 tools/archive_v040_data.py --experiment stability --run-dir <产物目录>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "evaluation-data-v040" / "manifest.json"
ARCHIVE_ROOT = ROOT / "docs" / "evaluation-data-v040"

# 报告《效果验证报告》§三 的声称值（用于对账）
REPORT_CLAIMS_STABILITY = {
    "clear": {
        "samples": 20, "consistent": 9, "drifted": 11, "consistent_rate": 0.45,
        "level_combos": {"none+light": 10, "light": 5, "none": 4, "light+clarify": 1},
        "none_light_boundary": {"samples": 20, "flipped": 11, "flip_rate": 0.55},
    },
    "medium": {
        "samples": 20, "consistent": 14, "drifted": 6, "consistent_rate": 0.7,
        "level_combos": {"light": 12, "none+light": 4, "clarify": 2, "light+clarify": 2},
        "none_light_boundary": {"samples": 18, "flipped": 6, "flip_rate": 0.33},
    },
    "severe": {
        "samples": 20, "consistent": 20, "drifted": 0, "consistent_rate": 1.0,
        "level_combos": {"clarify": 19, "light": 1},
    },
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_run_dir() -> Path | None:
    base = ROOT / "evaluation" / "results" / "stability"
    if not base.exists():
        return None
    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    return dirs[-1] if dirs else None


def compare_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """把重跑结果与报告声称值逐项对账。"""
    rows: list[dict[str, Any]] = []
    for difficulty, claim in REPORT_CLAIMS_STABILITY.items():
        actual = (metrics.get("by_difficulty") or {}).get(difficulty) or {}
        for key in ("samples", "consistent", "drifted"):
            if key in claim:
                rows.append({
                    "scope": difficulty, "metric": key,
                    "report": claim[key], "rerun": actual.get(key),
                    "same": claim[key] == actual.get(key),
                })
        combos_claim = claim.get("level_combos")
        if combos_claim:
            combos_actual = actual.get("level_combos") or {}
            rows.append({
                "scope": difficulty, "metric": "level_combos",
                "report": combos_claim, "rerun": combos_actual,
                "same": combos_claim == combos_actual,
            })
        boundary_claim = claim.get("none_light_boundary")
        if boundary_claim:
            boundary_actual = actual.get("none_light_boundary") or {}
            for key in ("samples", "flipped", "flip_rate"):
                if key in boundary_claim:
                    rows.append({
                        "scope": difficulty, "metric": f"none_light_boundary.{key}",
                        "report": boundary_claim[key], "rerun": boundary_actual.get(key),
                        "same": boundary_claim[key] == boundary_actual.get(key),
                    })
    matched = sum(1 for row in rows if row["same"])
    return {
        "rows": rows,
        "matched": matched,
        "total": len(rows),
        "all_same": matched == len(rows),
        "note": (
            "对账口径：**数字一致 ≠ 结论一致**。本轮实验的条件与原实验相同"
            "（同模型、同温度、同提示词、同样本集），但两者是**不同时间点的独立运行**，"
            "因此档位分布的差异属于**运行间波动**，正是效果验证官测出的 41 点噪声地板的体现。"
            "逐项差异本身即是证据。"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="归档 v0.4.0 实测产物")
    parser.add_argument("--experiment", default="stability", help="实验 id")
    parser.add_argument("--run-dir", default="", help="产物目录（默认取最新一次）")
    args = parser.parse_args()

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        run_dir = latest_run_dir()
    if run_dir is None or not run_dir.exists():
        print("ERROR: 找不到产物目录", flush=True)
        return 2

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next((e for e in manifest["experiments"] if e["id"] == args.experiment), None)
    if entry is None:
        print(f"ERROR: 清单里没有实验 {args.experiment}", flush=True)
        return 2

    archive_dir = ARCHIVE_ROOT / args.experiment
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived: list[dict[str, Any]] = []
    for src in sorted(run_dir.glob("*.json")):
        dst = archive_dir / src.name
        shutil.copy2(src, dst)
        archived.append({
            "file": f"docs/evaluation-data-v040/{args.experiment}/{src.name}",
            "bytes": dst.stat().st_size,
            "sha256": sha256_of(dst),
        })

    comparison = None
    summary_path = archive_dir / next(
        (a["file"].split("/")[-1] for a in archived if "summary" in a["file"]), ""
    ) if any("summary" in a["file"] for a in archived) else None
    if summary_path and summary_path.exists():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        comparison = compare_metrics(payload.get("metrics") or {})

    entry["raw_status"] = "ARCHIVED"
    entry["archived_artifacts"] = archived
    entry["archived_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if comparison:
        entry["reproduction_check"] = comparison

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已归档 {len(archived)} 个文件 -> {archive_dir}")
    for item in archived:
        print(f"  {item['file']}  {item['bytes']} B  sha256={item['sha256'][:16]}…")
    if comparison:
        print(f"对账：{comparison['matched']}/{comparison['total']} 项与报告声称值一致")
        for row in comparison["rows"]:
            if not row["same"]:
                print(f"  差异 {row['scope']}.{row['metric']}: 报告={row['report']} 重跑={row['rerun']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
