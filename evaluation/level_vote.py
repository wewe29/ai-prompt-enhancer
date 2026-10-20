"""档位多数投票 + 一致度暴露。

## 借鉴

`left0ver/prompt-engineer` 的 `code/self_consistency_prompting/experiment.py`。
那个实现里有三件事是本项目直接需要的：

1. **投票结果必须暴露一致度**，而不是只返回一个"胜者"。
   本项目此前的"翻转率"一直只是离线报告里的数字，产品侧完全看不到。
2. **平局要显式标记**，不能静默取一个。
   我们的 none/light 摇摆，本质就是"无法判定"被静默当成了一次判定。
3. **按首次出现位置破平局**，保证同样输入的结果可复现。

## 硬规则

* 只有 1 条有效结果时，**不投票**，`voted=False`、`is_uncertain=False`，
  且 `agreement` 为该唯一值占 1.0 —— 这种情况必须让调用方知道"没投过票"，
  否则会误以为一致。
* 无有效结果 → `decision=None`，调用方必须走降级，不能自己兜底。
* `agreement` 永远是"最高票数 / 有效数"，不是"是否一致"的布尔。

## 一个必须记住的数学事实

**n=2 时不存在多数。** 任意一次 A≠B 都是 1:1 平局，按首次出现破平局必然取 A，
于是 `majority_vote([A, B]) ≡ A`，与模型无关。实测（v0.4.0，clear 60 次 /
medium 57 次点击）`vote == 首次采样` 命中 **100%**。

self-consistency 的原始实现是 `paths=5`。要用「多数决定」就必须 n≥3。

注意与本项目既有 `verify_stability.py` 指标的**区别**（v0.4.0 代码审查 M5）：
那里的"翻转率"是**离线指标**，统计同一批样本多次运行之间是否出现档位差异；
这里的 `agreement` 是**单次调用内**多次采样的结果一致度。
两个数不是一回事，不要混用。
"""

from __future__ import annotations

import collections
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

LEVELS = ("none", "light", "clarify")
_UNKNOWN = "(空)"


@dataclass(frozen=True)
class LevelVote:
    """单次投票的完整结果。**一致度是产物的一部分，不是副产品。**"""

    decision: str | None
    agreement: float
    votes: dict[str, int]
    valid_paths: int
    total_paths: int
    voted: bool
    is_tie: bool
    is_uncertain: bool
    paths: list[str | None] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def majority_vote(paths: Sequence[str | None]) -> LevelVote:
    """对多条采样路径的档位做多数投票。

    参数 paths 顺序有意义：破平局时取**首次出现**的档位，保证可复现。
    """
    total = len(paths)
    valid = [p for p in paths if p]
    if not valid:
        return LevelVote(
            decision=None,
            agreement=0.0,
            votes={},
            valid_paths=0,
            total_paths=total,
            voted=False,
            is_tie=False,
            is_uncertain=True,
            paths=list(paths),
        )

    counts = collections.Counter(valid)
    first_position: dict[str, int] = {}
    for position, level in enumerate(valid):
        first_position.setdefault(level, position)

    highest = max(counts.values())
    winners = [lv for lv, c in counts.items() if c == highest]
    decision = min(winners, key=lambda lv: first_position[lv])
    is_tie = len(winners) > 1
    # 只有 1 条有效路径时"一致"是平凡的，不算真的一致
    is_uncertain = len(valid) > 1 and len(counts) > 1

    return LevelVote(
        decision=decision,
        agreement=round(highest / len(valid), 3),
        votes={str(k): v for k, v in sorted(counts.items())},
        valid_paths=len(valid),
        total_paths=total,
        voted=len(valid) > 1,
        is_tie=is_tie,
        is_uncertain=is_uncertain,
        paths=list(paths),
    )


def describe_for_user(vote: LevelVote) -> str:
    """给产品侧的非阻塞提示文案。不弹窗、不阻断任何操作。"""
    if not vote.valid_paths:
        return ""
    if not vote.voted:
        return "档位判断：仅 1 次有效采样，未做一致性投票。"
    if vote.is_tie:
        detail = "、".join(f"{k}×{v}" for k, v in vote.votes.items())
        return f"档位判断：{vote.total_paths} 次采样出现平局（{detail}），已按首次结果呈现，建议重新生成。"
    if vote.is_uncertain:
        detail = "、".join(f"{k}×{v}" for k, v in vote.votes.items())
        return f"档位判断：{vote.total_paths} 次采样结果不一致（{detail}），已按多数决定，建议核对原文与结果。"
    return ""


def main() -> int:
    cases = [
        ("全一致", ["light", "light"]),
        ("2:1 多数", ["none", "light", "light"]),
        ("平局", ["none", "light"]),
        ("全空（全失败）", [None, None]),
        ("1 条有效 1 条失败", [None, "light"]),
        ("1 条有效（未投票）", ["light"]),
        ("三档全不同", ["none", "light", "clarify"]),
    ]
    for name, paths in cases:
        v = majority_vote(paths)
        print(f"{name:<18} {json.dumps(v.to_dict(), ensure_ascii=False)}")
        tip = describe_for_user(v)
        if tip:
            print(f"{'':<18} 提示: {tip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
