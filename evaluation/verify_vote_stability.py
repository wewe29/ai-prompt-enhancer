#!/usr/bin/env python3
"""投票是否真的压得住档位摇摆（v0.4.0 效果验证闭环 · 第 4 项）。

## 要回答的问题

`level_vote_enabled` 开启后，每次增强并行采样 2 次、多数决定档位。
**这到底能不能把 clear 档的摇摆压下去？**

## 实验设计

同一次运行里同时得到两条曲线（**唯一变量就是"用第一次还是用投票结果"**）：

对每条样本、每次"点击"：
  * 发 2 次调用：A（视作单次调用的基线）与 B（视作生产里的影子调用）
  * 记下 `level_a`、`level_b`、`level_vote = majority(level_a, level_b)`

跑 K 次点击后按样本统计：
  * **单次基线翻转**：K 次的 `level_a` 不全相同
  * **投票后翻转**  ：K 次的 `level_vote` 不全相同
  * **逐次分歧率**  ：所有点击里 A≠B 的比例（投票到底有没有被触发）

## 实测结论（2026-10-04，240 次调用）

| | clear | medium |
|---|---|---|
| 单次调用翻转 | 9/20 = 45% | 3/19 = 16% |
| 投票后翻转 | 9/20 = 45% | 3/19 = 16% |
| 逐次调用分歧率 | 18/60 = 30% | 8/57 = 14% |
| **投票改过结果的次数** | **0/60** | **0/57** |

**投票一次都没改变过结果。** 根因是数学的：n=2 时不存在多数，
任意 A≠B 都是 1:1 平局，按首次出现破平局必然取 A，
于是 `majority_vote([A, B]) ≡ A`（恒等式，与模型无关）。

**这个脚本保留下来是为了让下一个人别再重做这个实验。**

用法：
    py -3 evaluation\\verify_vote_stability.py --levels clear --clicks 3
    py -3 evaluation\\verify_vote_stability.py --levels clear,medium --clicks 3 --summarize-only
"""

from __future__ import annotations

import os

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_v, None)

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import yaml  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enhancer  # noqa: E402
from level_vote import majority_vote  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evaluation" / "results"

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/chat/completions"
MAX_TOKENS = 8192
TEMPERATURE = 0.1          # 与 src-tauri/src/provider.rs 的产品实际值一致
MAX_ATTEMPTS = 2


def call(api_key: str, user_message: str) -> str:
    """返回 enhancement_level；拿不到就返回空串（调用方据此跳过）。"""
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": enhancer.SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    last = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(
                BASE_URL,
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.load(resp)
            content = (payload["choices"][0]["message"]["content"] or "")
            # normalize_result 返回 dict（不是 tuple）——与 verify_enhance.py 的用法保持一致
            value = enhancer.normalize_result(enhancer.parse_result(content))
            return str(value.get("enhancement_level") or "")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            last = f"HTTP {exc.code}: {detail}"
            if exc.code < 500:
                break
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(2.0 * (attempt + 1))
    print(f"    [调用失败] {last}", file=sys.stderr)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="档位投票是否压得住摇摆")
    ap.add_argument("--levels", default="clear")
    ap.add_argument("--clicks", type=int, default=3, help="每条样本模拟几次用户点击")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    ap.add_argument("--summarize-only", action="store_true")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    slug = "_".join(args.levels.split(","))
    raw_path = RESULTS / f"v040_vote_raw_{slug}_k{args.clicks}.json"

    samples = yaml.safe_load((ROOT / "evaluation" / "samples_v30.yaml").read_text(encoding="utf-8"))["samples"]
    levels = tuple(x.strip() for x in args.levels.split(",") if x.strip())
    samples = [s for s in samples if s["ambiguity_level"] in levels]
    if args.limit:
        samples = samples[: args.limit]

    data: dict[str, Any] = {}
    if args.summarize_only:
        if not raw_path.exists():
            print(f"ERROR: {raw_path} 不存在", file=sys.stderr)
            return 2
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
            return 2
        total = len(samples) * args.clicks * 2
        done = 0
        for index, sample in enumerate(samples, start=1):
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
            clicks = []
            for click in range(args.clicks):
                a = call(key, user_message)
                b = call(key, user_message)
                done += 2
                vote = majority_vote([a or None, b or None])
                clicks.append({
                    "a": a, "b": b,
                    "vote": vote.decision or "",
                    "agreement": vote.agreement,
                    "voted": vote.voted,
                    "is_tie": vote.is_tie,
                })
                print(f"[{index:>2}/{len(samples)}] {sample['id']:<32} click{click + 1}  "
                      f"A={a or '-':<8} B={b or '-':<8} vote={vote.decision or '-':<8} "
                      f"agree={vote.agreement}", flush=True)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            data[sample["id"]] = {"ambiguity_level": sample["ambiguity_level"], "clicks": clicks}
        print(f"\n[{done}/{total}] API calls done")

    # ---- 汇总 ----
    by_level: dict[str, Any] = {}
    for amb in levels:
        recs = [(sid, r) for sid, r in data.items() if r["ambiguity_level"] == amb]
        # 分歧率必须按档位算：用全局 clicks 会让 clear 与 medium 报出同一个数。
        clicks_here = [c for _sid, r in recs for c in r["clicks"]]
        usable_clicks = [c for c in clicks_here if c["a"] and c["b"] and c["vote"]]
        disagree = sum(1 for c in usable_clicks if c["a"] != c["b"])
        vote_changed = sum(1 for c in usable_clicks if c["vote"] != c["a"])
        single_flip = vote_flip = partial = 0
        detail = []
        for sid, r in recs:
            clicks = r["clicks"]
            usable = [c for c in clicks if c["a"] and c["vote"]]
            if len(usable) < 2:
                partial += 1
                continue
            a_levels = [c["a"] for c in usable]
            v_levels = [c["vote"] for c in usable]
            a_flip = len(set(a_levels)) > 1
            v_flip = len(set(v_levels)) > 1
            single_flip += a_flip
            vote_flip += v_flip
            if a_flip or v_flip:
                detail.append({
                    "id": sid,
                    "single_levels": a_levels,
                    "voted_levels": v_levels,
                    "single_flip": a_flip,
                    "voted_flip": v_flip,
                })
        by_level[amb] = {
            "samples": len(recs),
            "samples_usable": len(recs) - partial,
            "single_call_flip": single_flip,
            "voted_flip": vote_flip,
            "per_click_disagree": disagree,
            "per_click_total": len(usable_clicks),
            "disagree_rate": round(disagree / len(usable_clicks), 3) if usable_clicks else None,
            # 投票是否真的改过结果。n=2 时必然为 0：2 个样本没有"多数"，
            # A≠B 是 1:1 平局，按首次出现破平局恒等于 A。
            "vote_changed_result": vote_changed,
            "detail": detail,
        }

    summary = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "clicks": args.clicks,
        "levels": list(levels),
        "by_ambiguity_level": by_level,
    }
    print("\n=== 投票 vs 单次基线 ===")
    for amb, b in by_level.items():
        print(f"\n{amb}: 样本 {b['samples_usable']}/{b['samples']}")
        if b["samples_usable"]:
            print(f"  单次调用翻转样本 : {b['single_call_flip']}/{b['samples_usable']}"
                  f" = {b['single_call_flip']/b['samples_usable']:.0%}")
            print(f"  投票后翻转样本   : {b['voted_flip']}/{b['samples_usable']}"
                  f" = {b['voted_flip']/b['samples_usable']:.0%}")
        if b["disagree_rate"] is not None:
            print(f"  逐次调用分歧率   : {b['per_click_disagree']}/{b['per_click_total']}"
                  f" = {b['disagree_rate']:.0%}")
        print(f"  投票改过结果的次数: {b['vote_changed_result']}/{b['per_click_total']}"
              f"   <- n=2 时必然为 0")
    (RESULTS / f"v040_vote_summary_{slug}_k{args.clicks}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nraw     -> {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
