#!/usr/bin/env python3
"""PromptCraft 增强能力验证脚本（v0.3.5）。

流程：
  阶段 A  读 evaluation/samples_v30.yaml 的 60 条样本，调用 DeepSeek-V4.1-Flash
          （model id ``deepseek-flash``）执行增强，落盘原始结果。
  阶段 B  对原始结果执行**确定性判定**（五维中可机械判定的部分），
          落盘可复算的指标。

设计原则（对应 docs/PromptCraft-增强器评审规则-单模型版.md）：
  * 本脚本**只做可机械判定的事**，不打分、不输出胜率。
  * 定性判断（是否过度补全、整体观感）留给评审方，不在脚本里假装能做。
  * 凭据只从环境变量读，绝不落盘、绝不写入产物。

用法：
    py -3 evaluation/verify_enhance.py            # 全量 60 条
    py -3 evaluation/verify_enhance.py --smoke 3  # 冒烟，只跑 3 条
    py -3 evaluation/verify_enhance.py --resume   # 断点续跑，复用已有产物
"""

from __future__ import annotations

# 本机代理默认指向 127.0.0.1:7897，未运行时会导致 WinError 10061 连接被拒。
# 必须在任何网络库初始化之前清掉。
import os

for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_var, None)

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enhancer  # noqa: E402  （复用既有增强器逻辑，不修改它）

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "evaluation" / "samples_v30.yaml"
RESULTS_DIR = ROOT / "evaluation" / "results"
RAW_PATH = RESULTS_DIR / "v035_enhance_raw.json"
DETERMINISTIC_PATH = RESULTS_DIR / "v035_deterministic.json"

# DeepSeek-V4.1-Flash 的正式 model name（2026-09-10 起）
# 旧名 deepseek-v4-flash 虽仍可用，但底层已切换到此模型，用正式名更明确。
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/chat/completions"
MAX_TOKENS = 8192  # V4.1-Flash 思考 token 占比很高，必须给足余量
TEMPERATURE = 0.35
MAX_ATTEMPTS = 2

# ---------------------------------------------------------------- 越权动作词
# 增强结果若夹带这些词，说明引入了用户未要求的"执行类"动作 → 指令越权嫌疑。
#
# 只匹配真正越权的表述。注意不能匹配 "直接给出可执行 SQL"、"直接回答" 这类
# 正常的输出格式要求——那是对**回答方式**的要求，不是让助手代替用户执行动作。
# v035 首轮的正则过宽，把 3 条正常样本误判为越权。
OVERREACH_PATTERNS = [
    r"自动(?:发送|提交|发布|点击|下载|安装|执行|运行|购买|下单)",
    r"帮我(?:发送|提交|发布|点击|下单|付款|注册|登录)",
    r"(?:请)?(?:你|助手|模型)(?:代为|替我|直接)(?:发送|提交|发布|点击|执行|下单|付款)",
    r"无需(?:确认|征得)(?:同意|许可|授权)",
    r"请自动(?:执行|运行|打开|启动)",
    r"（?:或|）?\s*并?(?:直接)?(?:代为|自动)(?:执行|发送|提交)\s*(?:操作|动作|步骤)?[）)]?",
]
# 这些是**正常**的输出格式要求，出现在增强结果里不构成越权，用于排除误伤
BENIGN_OVERLAP = [
    r"直接(?:给出|输出|回答|提供|写|说明|列出|返回|执行 SQL|运行 SQL)",
    r"可执行(?:的)? ?(?:SQL|代码|命令|语句)",
    r"请先(?:只读)?分析",
    r"不要(?:直接)?(?:执行|运行|修改)",
]

# ---------------------------------------------------------------- 数字抽取
# 用于"新增事实"检测：原文里出现的数字，结果里必须有对应来源。
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")


# ----------------------------------------------------------------- API 调用
def call_deepseek(api_key: str, user_message: str) -> dict[str, Any]:
    """调用 DeepSeek-V4.1-Flash，返回解析后的增强结果 dict。"""
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

    usage = payload.get("usage", {}) or {}
    completion_details = usage.get("completion_tokens_details") or {}
    result["_usage"] = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": completion_details.get("reasoning_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    return result


def enhance_one(sample: dict[str, Any], api_key: str) -> dict[str, Any]:
    """对单条样本执行增强，失败重试；始终返回一条可落盘的记录。"""
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

    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = call_deepseek(api_key, user_message)
            return {
                "id": sample["id"],
                "ambiguity_level": sample["ambiguity_level"],
                "scenario": sample.get("scenario", ""),
                "original": sample["original"],
                "must_preserve": sample.get("must_preserve", []),
                "must_not_add": sample.get("must_not_add", []),
                "expected_behavior": sample.get("expected_behavior", ""),
                "error": "",
                **result,
            }
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - 读取错误体失败不应掩盖主错误
                pass
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code < 500:
                break  # 4xx 是请求本身有问题，重试无意义
        except enhancer.EnhanceError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常统一记录，不中断整体
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(2.0 * (attempt + 1))

    return {
        "id": sample["id"],
        "ambiguity_level": sample["ambiguity_level"],
        "scenario": sample.get("scenario", ""),
        "original": sample["original"],
        "must_preserve": sample.get("must_preserve", []),
        "must_not_add": sample.get("must_not_add", []),
        "expected_behavior": sample.get("expected_behavior", ""),
        "error": last_error,
        "primary_prompt": "",
    }


# ------------------------------------------------------------- 阶段 B 判定
def normalize_for_match(text: str) -> str:
    """归一化后再比对：抹平空格、全半角、单位写法等纯格式差异。

    判定的目的是发现"用户的事实被丢掉"，而不是"字符逐字未变"。
    形如 "60 秒" / "60秒"、"120 万" / "120（单位：万元）" 属于同一事实的
    不同书写形式，不应计为事实丢失（v035 首轮据此产生了 9 条假阳性）。
    """
    # 单位词与其前面的数字绑定，避免"120 万"因"万"被单独搬走而误判
    text = re.sub(r"(\d[\d.,]*)\s*(万|亿|千|百|ms|毫秒|秒|分钟|小时|%|个百分点|倍)", r"\1\2", text)
    # 归一化空白与全角标点
    text = re.sub(r"[\s\u3000]+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    return text


def judge_dimension_fact_fidelity(record: dict[str, Any]) -> dict[str, Any]:
    """维度 1 事实保真：must_preserve 子串是否被保留 + 是否出现原文没有的数字。"""
    primary = record.get("primary_prompt", "")
    normalized_primary = normalize_for_match(primary)

    # 事实可能保留在 primary_prompt，也可能保留在 assumptions/questions 里
    # （clarify 模式下，增强器常把约束复述到假设中）。
    assumptions_text = " ".join(
        str(a.get("text", "")) for a in record.get("assumptions", []) or []
    )
    questions_text = " ".join(
        str(q.get("question", "")) for q in record.get("questions", []) or []
    )
    haystack = normalize_for_match(primary + assumptions_text + questions_text)

    missing = [
        item
        for item in record.get("must_preserve", [])
        if item and normalize_for_match(item) not in haystack
    ]

    original_numbers = set(NUMBER_RE.findall(record.get("original", "")))
    result_numbers = set(NUMBER_RE.findall(primary + assumptions_text))
    # 结果里多出来的数字 = 可能编造事实（少数是格式换算，需人工复核）
    added = sorted(result_numbers - original_numbers)

    if not primary:
        verdict = "无法判断"
    elif missing:
        verdict = "严重问题"
    elif added:
        verdict = "轻微偏差"  # 有新增数字，先标轻微，人工复核后再定性
    else:
        verdict = "通过"
    return {"verdict": verdict, "missing": missing, "added_numbers": added}


def judge_dimension_preservation(record: dict[str, Any]) -> dict[str, Any]:
    """维度 2 原文保全：原文是否几乎原封不动保留（提示词类任务应高度保真）。"""
    original = record.get("original", "").strip()
    primary = record.get("primary_prompt", "").strip()
    if not primary:
        return {"verdict": "无法判断", "identical": False, "kept_ratio": 0.0}

    identical = original == primary
    if identical:
        return {"verdict": "通过", "identical": True, "kept_ratio": 1.0}

    # clarify 档：输出是"待确认问题 + 基于假设的临时方案"，
    # 本就不是原文的延续，用"原文保全度"衡量它没有意义（v035 首轮 20/20 误判）。
    if (record.get("enhancement_level") or "") == "clarify":
        return {"verdict": "澄清模式-不作判定", "identical": False, "kept_ratio": None}

    # 粗略保留度：原文按行/句切分后，有多少能在结果中找到
    segments = [s.strip() for s in re.split(r"[。\n；;]", original) if len(s.strip()) >= 6]
    if not segments:
        segments = [original]
    kept = sum(1 for seg in segments if seg in primary)
    ratio = kept / len(segments)

    if ratio >= 0.8:
        verdict = "通过"
    elif ratio >= 0.5:
        verdict = "轻微偏差"
    else:
        verdict = "严重问题"
    return {"verdict": verdict, "identical": False, "kept_ratio": round(ratio, 3)}


def judge_dimension_overreach(record: dict[str, Any]) -> dict[str, Any]:
    """维度 3 指令越权：是否违反 must_not_add，是否夹带越权动作。"""
    primary = record.get("primary_prompt", "")
    violations = [item for item in record.get("must_not_add", []) if item and item in primary]
    hits = [p for p in OVERREACH_PATTERNS if re.search(p, primary)]
    # 排除良性表述（如"直接给出可执行 SQL"）
    for pattern in BENIGN_OVERLAP:
        primary = re.sub(pattern, "", primary)
    hits = [p for p in hits if re.search(p, primary)]

    if not primary:
        verdict = "无法判断"
    elif violations or hits:
        verdict = "严重问题"
    else:
        verdict = "通过"
    return {"verdict": verdict, "must_not_add_violations": violations, "overreach_hits": hits}


def judge_dimension_bloat(record: dict[str, Any]) -> dict[str, Any]:
    """维度 4 冗余膨胀：长度比。长度本身不是质量指标，这里只量"膨胀"。

    阈值按档位区分：
      * none/light（可交付模式）——膨胀是有害的，越多越糟。
      * clarify（澄清模式）——输出是"待确认清单 + 临时方案"，本就应当更长，
        用同阈值会把正常澄清误判为严重膨胀（v035 首轮 severe 档 20/20 全部误判）。
        故 clarify 档只记录比值、不做通过/膨胀分档，结论交人工判断。
    """
    original = record.get("original", "").strip()
    primary = record.get("primary_prompt", "").strip()
    if not primary or not original:
        return {"verdict": "无法判断", "ratio": 0.0, "orig_len": len(original), "new_len": len(primary)}

    ratio = len(primary) / len(original)
    level = record.get("enhancement_level") or ""

    if level == "clarify":
        # 澄清模式：比值只作为事实记录，不判定优劣
        return {
            "verdict": "澄清模式-不作判定",
            "ratio": round(ratio, 2),
            "orig_len": len(original),
            "new_len": len(primary),
        }

    if ratio <= 1.5:
        verdict = "通过"
    elif ratio <= 3.0:
        verdict = "轻微膨胀"
    else:
        verdict = "严重膨胀"
    return {
        "verdict": verdict,
        "ratio": round(ratio, 2),
        "orig_len": len(original),
        "new_len": len(primary),
    }


def judge_dimension_restraint(record: dict[str, Any]) -> dict[str, Any]:
    """维度 5 的**可判定部分**：行为档位是否与样本标注的预期一致。

    这是 v0.3.2「清晰任务变差率 41.1%」的直接观察点：
    clear 档理应克制（none/light），若大改特改即为画蛇添足。
    """
    level = record.get("enhancement_level") or ""
    status = record.get("status") or ""
    expected = record.get("expected_behavior", "")
    ambiguity = record.get("ambiguity_level", "")

    if not level:
        return {"verdict": "无法判断", "level": level, "expected": expected, "match": None}

    # 预期行为到可接受 level 的映射
    acceptable = {
        "none": {"none", "light"},
        "light": {"none", "light"},
        "clarify": {"light", "clarify"},
    }.get(expected, {level})

    match = level in acceptable
    return {
        "verdict": "恰当" if match else "应更克制" if ambiguity == "clear" else "过度补全",
        "level": level,
        "expected": expected,
        "match": match,
        "delivery_status": status,
    }


def judge(record: dict[str, Any]) -> dict[str, Any]:
    """对单条增强结果执行五维确定性判定。"""
    if record.get("error"):
        return {"id": record["id"], "error": record["error"], "judged": False}
    return {
        "id": record["id"],
        "ambiguity_level": record.get("ambiguity_level", ""),
        "scenario": record.get("scenario", ""),
        "judged": True,
        "fact_fidelity": judge_dimension_fact_fidelity(record),
        "preservation": judge_dimension_preservation(record),
        "overreach": judge_dimension_overreach(record),
        "bloat": judge_dimension_bloat(record),
        "restraint": judge_dimension_restraint(record),
        "needs_human_review": True,  # 五个维度都存在自动判不了的部分
    }


# ------------------------------------------------------------------- 汇总
def summarize(records: list[dict[str, Any]], judgements: list[dict[str, Any]]) -> dict[str, Any]:
    """按难度分层汇总机械指标。**不输出任何分数或胜率。"""
    by_level: dict[str, dict[str, Any]] = {}
    for level in ("clear", "medium", "severe"):
        items = [j for j in judgements if j.get("ambiguity_level") == level and j.get("judged")]
        raw_items = [r for r in records if r.get("ambiguity_level") == level and not r.get("error")]

        def count(dimension: str, verdict: str) -> int:
            return sum(1 for j in items if j.get(dimension, {}).get("verdict") == verdict)

        levels_dist: dict[str, int] = {}
        for item in raw_items:
            key = item.get("enhancement_level") or "(空)"
            levels_dist[key] = levels_dist.get(key, 0) + 1

        # clarify 档的膨胀比/保全度按设计不参与分档统计，单独列为观察值
        ratios = [
            j["bloat"]["ratio"]
            for j in items
            if j["bloat"]["ratio"] > 0 and j["bloat"]["verdict"] != "澄清模式-不作判定"
        ]
        clarify_ratios = [
            j["bloat"]["ratio"]
            for j in items
            if j["bloat"]["verdict"] == "澄清模式-不作判定"
        ]
        by_level[level] = {
            "total": len([r for r in records if r.get("ambiguity_level") == level]),
            "judged": len(items),
            "errors": len([r for r in records if r.get("ambiguity_level") == level and r.get("error")]),
            "enhancement_level_distribution": levels_dist,
            "fact_fidelity": {
                "通过": count("fact_fidelity", "通过"),
                "轻微偏差": count("fact_fidelity", "轻微偏差"),
                "严重问题": count("fact_fidelity", "严重问题"),
            },
            "preservation": {
                "通过": count("preservation", "通过"),
                "轻微偏差": count("preservation", "轻微偏差"),
                "严重问题": count("preservation", "严重问题"),
            },
            "overreach": {
                "通过": count("overreach", "通过"),
                "严重问题": count("overreach", "严重问题"),
            },
            "bloat": {
                "通过": count("bloat", "通过"),
                "轻微膨胀": count("bloat", "轻微膨胀"),
                "严重膨胀": count("bloat", "严重膨胀"),
                "澄清模式不判定": count("bloat", "澄清模式-不作判定"),
                "avg_ratio": round(sum(ratios) / len(ratios), 2) if ratios else 0.0,
                "max_ratio": max(ratios) if ratios else 0.0,
                "clarify_avg_ratio": (
                    round(sum(clarify_ratios) / len(clarify_ratios), 2) if clarify_ratios else 0.0
                ),
            },
            "restraint": {
                "恰当": count("restraint", "恰当"),
                "应更克制": count("restraint", "应更克制"),
                "过度补全": count("restraint", "过度补全"),
            },
        }

    errors = [{"id": r["id"], "error": r["error"]} for r in records if r.get("error")]
    tokens = [r.get("_usage", {}) for r in records if not r.get("error")]
    return {
        "model": MODEL,
        "total_samples": len(records),
        "error_count": len(errors),
        "errors": errors,
        "by_ambiguity_level": by_level,
        "token_totals": {
            "prompt_tokens": sum(t.get("prompt_tokens", 0) for t in tokens),
            "completion_tokens": sum(t.get("completion_tokens", 0) for t in tokens),
            "reasoning_tokens": sum(t.get("reasoning_tokens", 0) for t in tokens),
        },
    }


# --------------------------------------------------------------------- 主
def main() -> int:
    parser = argparse.ArgumentParser(description="PromptCraft 增强能力验证")
    parser.add_argument("--smoke", type=int, default=0, help="只跑前 N 条（冒烟测试）")
    parser.add_argument("--resume", action="store_true", help="复用已有产物，跳过已成功的样本")
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="不调用 API，直接用已有 raw 产物重跑阶段 B 判定（修正判定逻辑后用）",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if args.smoke else ""
    raw_path = RESULTS_DIR / f"v035_enhance_raw{suffix}.json"
    det_path = RESULTS_DIR / f"v035_deterministic{suffix}.json"

    # ---- 只重跑判定：完全不需要 API Key ----
    if args.judge_only:
        if not raw_path.exists():
            print(f"ERROR: {raw_path} 不存在，请先跑一次增强", file=sys.stderr)
            return 2
        records = json.loads(raw_path.read_text(encoding="utf-8"))
        judgements = [judge(record) for record in records]
        summary = summarize(records, judgements)
        det_path.write_text(
            json.dumps({"summary": summary, "judgements": judgements}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\ndeterministic -> {det_path}")
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 2

    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))["samples"]
    if args.smoke:
        # 冒烟：每档各取 1 条，覆盖三种难度
        picked: list[dict[str, Any]] = []
        for level in ("clear", "medium", "severe"):
            picked.extend([s for s in samples if s["ambiguity_level"] == level][:1])
        samples = picked[: args.smoke]

    existing: dict[str, dict[str, Any]] = {}
    if args.resume and raw_path.exists():
        for item in json.loads(raw_path.read_text(encoding="utf-8")):
            if not item.get("error"):
                existing[item["id"]] = item
        print(f"[resume] reuse {len(existing)} succeeded records")

    records: list[dict[str, Any]] = []
    for index, sample in enumerate(samples, start=1):
        if sample["id"] in existing:
            records.append(existing[sample["id"]])
            continue
        started = time.time()
        record = enhance_one(sample, api_key)
        record["_elapsed_sec"] = round(time.time() - started, 1)
        records.append(record)

        state = "ERR" if record.get("error") else (record.get("enhancement_level") or "?")
        print(
            f"[{index:>2}/{len(samples)}] {sample['id']:<34} "
            f"{sample['ambiguity_level']:<7} level={state:<8} {record['_elapsed_sec']:>5}s",
            flush=True,
        )
        # 每条即时落盘，防止长跑中断丢失全部进度
        raw_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    judgements = [judge(record) for record in records]
    summary = summarize(records, judgements)
    det_path.write_text(
        json.dumps({"summary": summary, "judgements": judgements}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nraw        -> {raw_path}")
    print(f"deterministic -> {det_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
