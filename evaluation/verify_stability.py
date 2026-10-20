#!/usr/bin/env python3
"""v0.4.0 档位稳定性实验：同一条提示词连点 N 次"增强"，看它稳不稳。

**这个脚本是重建的。** 原脚本随 F 盘一起丢失（见
`docs/v040-架构审查.md` P0-4），本文件按报告里给出的指标口径重新实现，
并把每条口径的**判据写进代码**，使结果可复算、可核对。

它测量的是一件很朴素的事：**用户对同一条提示词点两次"增强"，拿到的结果
可能不一样。** 对 clear 档尤其要命——那正是用户最常走的路径（写清楚了
目标/背景/约束/格式，然后点"增强"）。

**指标口径（本文件以此为准，不依赖任何散落在报告里的说法）**：

============================  ==========================================================
术语                          定义
============================  ==========================================================
档位组合                      一条样本在 N 次运行里出现的**去重后按 none<light<clarify
                              排序**的档位序列，用 ``+`` 连接。``none+light`` 表示
                              这条样本在几次运行里既给过 none 也给过 light。
一致                          档位组合长度为 1（几次运行给了同一个档位）。
飘                            档位组合长度 > 1。
none/light 边界样本           该样本 N 次运行中**至少有一次**的档位落在 {none, light} 内。
                              即：它至少一次被当作"不澄清"的候选。
                              换句话说：三次都判 clarify 的样本不进这个分母
                              （对 clear 档就是全部 20 条都进，对 severe 档
                              则只剩个别样本进）。
翻转                          边界样本里，档位组合长度 > 1（在候选档位之间摇摆过）。
翻转率                        翻转数 / 边界样本数。
                              ⚠ 这个口径会把"none↔clarify"这种跨越也算作翻转，
                              因为报告的原表（clear 11 = none+light×10 +
                              light+clarify×1、medium 6 = none+light×4 +
                              light+clarify×2）正是这么算的。
                              为免口径混用，本脚本**同时**给出严格口径
                              ``flip_rate_strict``：分母只算「全部运行都在
                              {none, light} 内」的样本。
长度比                        ``len(primary_prompt) / len(original)``，逐次计算。
长度比波动                    一条样本 N 次长度比的 ``max - min``；avg/max 是跨样本的
                              均值与最大值。
逐次调用分歧率                与该样本**众数档位**不同的调用次数 / 总调用次数。
                              众数平手时取该样本**首次出现**的档位为准。
交付状态漂移                  该样本 N 次的 ``delivery_status`` 不全相同则计 1 条。
============================  ==========================================================

**产物带身份**（P0-4 / D1 / D2）：文件名与目录都编码「提示词版本 + 模型 +
温度 + 样本集 + 重复次数 + 短哈希」；``--resume`` 前会做身份一致性闸，
身份不同直接拒绝续跑。

用法::

    # 全量 60 条 × 3 次
    py -3 evaluation/verify_stability.py --repeats 3

    # 只跑 clear 档
    py -3 evaluation/verify_stability.py --repeats 3 --levels clear

    # 不调 API，只用已有产物重算指标
    py -3 evaluation/verify_stability.py --judge-only --run-dir <产物目录>
"""
from __future__ import annotations

# 本机代理可能指向未运行的端口，会导致连接被拒。必须在网络库初始化前清掉。
import os

for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_var, None)

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enhancer  # noqa: E402
import run_identity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "evaluation" / "samples_v30.yaml"
RESULTS_DIR = ROOT / "evaluation" / "results"

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/chat/completions"
MAX_TOKENS = 8192
MAX_ATTEMPTS = 2

LEVEL_ORDER = {"none": 0, "light": 1, "clarify": 2}
# 缺失档位是一个**独立取值**，不是"没有档位"。
# 此前 Python 侧把缺失的 enhancement_level 补成 "light"；B2 修掉之后，
# 模型没给档位会真的留空。若这里把空值静默丢出组合，
# 一条「三次里两次没给档位」的样本会被算成"稳定" —— 正好是本脚本
# 要测的那类"看起来稳定其实没测到"的假象。
MISSING_LEVEL = "(空)"
DIFFICULTIES = ("clear", "medium", "severe")


# --------------------------------------------------------------- 指标计算
def level_combo(levels: list[str]) -> str:
    """档位组合：去重 → 按 none<light<clarify 排序 → 用 '+' 连接。

    缺失档位记作 ``(空)``，排最后，保持稳定。
    """
    unique = {lv if lv else MISSING_LEVEL for lv in levels}
    ordered = sorted(unique, key=lambda lv: (LEVEL_ORDER.get(lv, 99), lv))
    return "+".join(ordered)


def mode_level(levels: list[str]) -> str:
    """众数档位；平手时取**首次出现**的那个。"""
    present = [lv if lv else MISSING_LEVEL for lv in levels]
    if not present:
        return ""
    counts = Counter(present)
    best = max(counts.values())
    for lv in present:
        if counts[lv] == best:
            return lv
    return present[0]


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """由原始记录算全部指标。**纯函数**——不碰网络、不读环境变量。

    ``records`` 的结构：每条记录是一次「样本 × 重复序号」的增强结果，
    至少包含 ``id`` / ``ambiguity_level`` / ``repeat`` / ``enhancement_level``
    / ``delivery_status`` / ``original_len`` / ``primary_len`` / ``error``。
    """
    by_sample: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_sample.setdefault(record["id"], []).append(record)
    for runs in by_sample.values():
        runs.sort(key=lambda r: r.get("repeat", 0))

    by_difficulty: dict[str, dict[str, Any]] = {}
    for difficulty in DIFFICULTIES:
        sample_ids = [
            sid for sid, runs in by_sample.items()
            if runs and runs[0].get("ambiguity_level") == difficulty
        ]
        sample_ids.sort()

        combos = Counter()
        consistent = 0
        drifted = 0
        boundary = 0
        flipped = 0
        strict_boundary = 0
        strict_flipped = 0
        delivery_drift = 0
        length_spreads: list[float] = []
        disagree_calls = 0
        total_calls = 0

        for sid in sample_ids:
            runs = by_sample[sid]
            levels = [str(r.get("enhancement_level") or "") for r in runs]
            combo = level_combo(levels)
            combos[combo] += 1
            combo_len = len(combo.split("+")) if combo else 0
            if combo_len == 1:
                consistent += 1
            else:
                drifted += 1

            # none/light 边界样本（报告口径）：至少一次的档位落在 {none, light} 内
            if any(lv in ("none", "light") for lv in levels):
                boundary += 1
                if combo_len > 1:
                    flipped += 1

            # 严格口径：全部运行的档位都在 {none, light} 内
            if levels and all(lv in ("none", "light") for lv in levels):
                strict_boundary += 1
                if combo_len > 1:
                    strict_flipped += 1

            # 交付状态漂移
            statuses = {str(r.get("delivery_status") or "") for r in runs}
            if len(statuses) > 1:
                delivery_drift += 1

            # 长度比波动
            ratios = [
                (r.get("primary_len") or 0) / r["original_len"]
                for r in runs
                if r.get("original_len")
            ]
            if len(ratios) > 1:
                length_spreads.append(max(ratios) - min(ratios))

            # 逐次调用分歧率
            mode = mode_level(levels)
            total_calls += len(levels)
            disagree_calls += sum(1 for lv in levels if lv != mode)

        by_difficulty[difficulty] = {
            "samples": len(sample_ids),
            "consistent": consistent,
            "drifted": drifted,
            "consistent_rate": round(consistent / len(sample_ids), 4) if sample_ids else None,
            "level_combos": dict(sorted(combos.items(), key=lambda kv: (-kv[1], kv[0]))),
            "none_light_boundary": {
                "samples": boundary,
                "flipped": flipped,
                "flip_rate": round(flipped / boundary, 4) if boundary else None,
                "strict_samples": strict_boundary,
                "strict_flipped": strict_flipped,
                "flip_rate_strict": round(strict_flipped / strict_boundary, 4) if strict_boundary else None,
                "length_spread_avg": round(statistics.fmean(length_spreads), 4) if length_spreads else None,
                "length_spread_max": round(max(length_spreads), 4) if length_spreads else None,
                "delivery_status_drift": delivery_drift,
            },
            "per_click_disagreement": {
                "disagree_calls": disagree_calls,
                "total_calls": total_calls,
                "rate": round(disagree_calls / total_calls, 4) if total_calls else None,
            },
        }

    errors = [r for r in records if r.get("error")]
    return {
        "by_difficulty": by_difficulty,
        "totals": {
            "records": len(records),
            "samples": len(by_sample),
            "errors": len(errors),
            "error_samples": len({r["id"] for r in errors}),
        },
    }


# --------------------------------------------------------------- API 调用
def call_deepseek(api_key: str, user_message: str, temperature: float) -> dict[str, Any]:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": enhancer.SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        BASE_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)
    content = payload["choices"][0]["message"]["content"] or ""
    result = enhancer.normalize_result(enhancer.parse_result(content))
    enhancer.validate_result(result)
    return result


def enhance_one(sample: dict[str, Any], api_key: str, temperature: float, repeat: int) -> dict[str, Any]:
    user_message = enhancer.build_user_message(
        sample["original"],
        target_model=sample.get("scenario", "通用助手"),
        verbosity="standard",
        custom_instructions=None,
        clarification_round=0,
        profile_summary=[],
        context_text="",
        clarification_answers=[],
        attachments=[],
    )
    record: dict[str, Any] = {
        "id": sample["id"],
        "repeat": repeat,
        "ambiguity_level": sample["ambiguity_level"],
        "scenario": sample.get("scenario", ""),
        "original_len": len(sample["original"]),
        "primary_len": 0,
        "enhancement_level": "",
        "delivery_status": "",
        "error": "",
    }
    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = call_deepseek(api_key, user_message, temperature)
            record.update(
                {
                    "enhancement_level": result.get("enhancement_level", ""),
                    "delivery_status": result.get("delivery_status", ""),
                    "primary_len": len(result.get("primary_prompt", "") or ""),
                    "error": "",
                }
            )
            return record
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code < 500:
                break
        except enhancer.EnhanceError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(2.0 * (attempt + 1))
    record["error"] = last_error
    return record


# ------------------------------------------------------------------- 主
def main() -> int:
    parser = argparse.ArgumentParser(description="v0.4.0 档位稳定性实验")
    parser.add_argument("--repeats", type=int, default=3, help="每条样本重复运行次数（默认 3）")
    parser.add_argument("--levels", default="", help="只跑指定难度，逗号分隔，如 clear,medium")
    parser.add_argument("--temperature", type=float, default=0.35, help="采样温度（默认 0.35 = 报告原始条件）")
    parser.add_argument("--smoke", type=int, default=0, help="只跑前 N 条（冒烟）")
    parser.add_argument("--resume", action="store_true", help="复用已有产物（做身份一致性校验）")
    parser.add_argument("--judge-only", action="store_true", help="不调 API，只用已有产物重算指标")
    parser.add_argument("--run-dir", default="", help="指定产物目录（--judge-only / --resume 时用）")
    args = parser.parse_args()

    identity = run_identity.build_identity(
        model=MODEL,
        temperature=args.temperature,
        samples_path=SAMPLES_PATH,
        repeats=args.repeats,
    )

    if args.run_dir:
        run_path = Path(args.run_dir)
    else:
        run_path = run_identity.run_dir(RESULTS_DIR / "stability", identity)
    run_path.mkdir(parents=True, exist_ok=True)

    raw_path = run_path / run_identity.stamped_name("v040_stability_raw", identity)
    summary_path = run_path / run_identity.stamped_name("v040_stability_summary", identity)

    # ---- 只重算指标：完全不需要 API Key ----
    if args.judge_only:
        if not raw_path.exists():
            print(f"ERROR: {raw_path} 不存在", file=sys.stderr)
            return 2
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        records = payload["records"]
        metrics = compute_metrics(records)
        summary = {
            run_identity.IDENTITY_KEY: payload.get(run_identity.IDENTITY_KEY, identity),
            "metrics": metrics,
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(f"\nsummary -> {summary_path}")
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 2

    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))["samples"]
    if args.levels:
        wanted = {lv.strip() for lv in args.levels.split(",") if lv.strip()}
        samples = [s for s in samples if s["ambiguity_level"] in wanted]
    if args.smoke:
        samples = samples[: args.smoke]

    existing: list[dict[str, Any]] = []
    if args.resume and raw_path.exists():
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        run_identity.assert_same_identity(payload, identity, context=str(raw_path))
        existing = payload.get("records", [])
        print(f"[resume] reuse {len(existing)} records")

    records: list[dict[str, Any]] = list(existing)
    for index, sample in enumerate(samples, start=1):
        for repeat in range(1, args.repeats + 1):
            if any(r["id"] == sample["id"] and r.get("repeat") == repeat for r in records):
                continue
            started = time.time()
            record = enhance_one(sample, api_key, args.temperature, repeat)
            record["_elapsed_sec"] = round(time.time() - started, 1)
            records.append(record)
            state = "ERR" if record.get("error") else (record.get("enhancement_level") or "?")
            print(
                f"[{index:>2}/{len(samples)}] {sample['id']:<34} "
                f"{sample['ambiguity_level']:<7} r{repeat} level={state:<8} "
                f"{record['_elapsed_sec']:>5}s",
                flush=True,
            )
            # 每条即时落盘，防止长跑中断丢失全部进度
            raw_path.write_text(
                json.dumps(
                    {run_identity.IDENTITY_KEY: identity, "records": records},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    metrics = compute_metrics(records)
    summary = {run_identity.IDENTITY_KEY: identity, "metrics": metrics}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nraw    -> {raw_path}")
    print(f"summary-> {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
