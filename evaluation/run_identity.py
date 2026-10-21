"""运行身份（run identity）：让每个评测产物自带"自己是谁"的证明。

**为什么需要它**（v0.4.0 架构审查 P0-4 / D1 / D2 / D3）：

1. **产物名不编码身份。** 旧脚本把输出写死成 `v035_enhance_raw.json`，
   每次重跑**静默覆盖**上一次的结果；而 v0.4.0 的运行也会写进 `v035_*`。
   ⇒ 事后无法判断一份产物是哪一次运行产生的。
2. **`--resume` 不校验参数。** 旧 `--resume` 复用"任何没有 error 的既有记录"，
   **不检查 prompt / 模型 / 参数是否变过**。改完 SYSTEM_PROMPT 再 `--resume`，
   新旧 prompt 的结果会被混进同一个文件并一起汇总。
3. **判定逻辑的版本没有被记录。** 同一份 raw 用不同判定逻辑重跑，
   产出的文件无法区分。

**本模块提供三件东西**：

* :func:`build_identity` —— 由「提示词版本 + 提示词正文哈希 + 模型 + 温度 +
  样本集哈希 + 重复次数」算出这次运行的**身份**；
* :func:`stamped_name` —— 把身份压进文件名（短哈希 + 关键字段），并支持
  产物目录级的时间戳隔离；
* :func:`assert_same_identity` —— `--resume` 之前的**身份一致性闸**：
  身份不一致就拒绝续跑，而不是把新旧混进一份产物。

**刻意不做的事**：不改变任何实验的统计口径，只负责"标明来源"。
统计口径的变更必须走单独的、更显眼的流程。

用法::

    from run_identity import build_identity, stamped_name, assert_same_identity

    identity = build_identity(model="deepseek-flash", temperature=0.1, repeats=3)
    path = RESULTS_DIR / stamped_name("v040_stability_raw", identity)
    assert_same_identity(existing_payload, identity)   # --resume 时调用
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # pragma: no cover - 仅在缺少 enhancer 时降级
    import enhancer
except Exception:  # noqa: BLE001 - 身份计算不应因可选依赖失败
    enhancer = None  # type: ignore[assignment]


IDENTITY_KEY = "_identity"

# 身份里参与哈希的字段。顺序固定，值变化即身份变化。
_IDENTITY_FIELDS = (
    "prompt_version",
    "prompt_sha256",
    "model",
    "temperature",
    "samples_sha256",
    "repeats",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_identity(
    *,
    model: str,
    temperature: float,
    samples_path: Path | str | None = None,
    repeats: int = 1,
    prompt_version: str | None = None,
    prompt_text: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """算出这次运行的身份。

    参数
    ----
    model / temperature
        本次运行实际使用的模型与采样温度。
    samples_path
        样本集文件路径；参与哈希，换样本集即换身份。
    repeats
        重复次数；换重复次数即换身份（指标口径不同）。
    prompt_version / prompt_text
        提示词版本号与正文。**正文参与哈希**是刻意的：
        有人改了提示词却忘了改版本号时，身份仍会变，不会静默混跑。
    extra
        额外参与哈希的字段（例如 ``judge_version``）。

    返回
    ----
    dict
        含 ``fields``（参与哈希的字段）、``tag``（短哈希）、
        ``created_at``（UTC ISO8601）。
    """
    if prompt_version is None:
        prompt_version = getattr(enhancer, "SYSTEM_PROMPT_VERSION", "unknown")
    if prompt_text is None and enhancer is not None:
        prompt_text = getattr(enhancer, "SYSTEM_PROMPT", "")

    fields: dict[str, Any] = {
        "prompt_version": prompt_version,
        "prompt_sha256": _sha256_text(prompt_text or ""),
        "model": model,
        "temperature": temperature,
        "samples_sha256": _sha256_file(Path(samples_path)) if samples_path else "",
        "repeats": repeats,
    }
    if extra:
        fields.update(extra)

    canonical = json.dumps(
        {k: fields[k] for k in _IDENTITY_FIELDS},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    tag = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]

    return {
        "fields": fields,
        "tag": tag,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def identity_of(payload: Any) -> dict[str, Any] | None:
    """从产物 payload 里取出身份；没有身份则返回 ``None``。"""
    if isinstance(payload, dict):
        found = payload.get(IDENTITY_KEY)
        if isinstance(found, dict):
            return found
    return None


def stamped_name(base: str, identity: dict[str, Any], *, timestamp: str | None = None) -> str:
    """生成带身份标签的文件名。

    形如 ``v040_stability_raw__m-deepseek-flash__t0.1__r3__a1b2c3d4e5.json``。
    时间戳进**目录**而不是文件名——文件名里的时间戳会让"同一次运行"的
    多次追加写产生多个文件名，破坏增量落盘。
    """
    fields = identity.get("fields", {})
    model = str(fields.get("model", "unknown")).replace("/", "-")
    temperature = fields.get("temperature", "?")
    repeats = fields.get("repeats", 1)
    tag = identity.get("tag", "0000000000")
    return f"{base}__m-{model}__t{temperature}__r{repeats}__{tag}.json"


def run_dir(base_dir: Path | str, identity: dict[str, Any]) -> Path:
    """按运行身份分配一个独立的产物目录。

    目录名带 UTC 时间戳 + **随机后缀** + 身份标签 ⇒ 每次运行一个目录，
    **绝不互相覆盖**。

    **随机后缀不是可有可无的**：此前只用「微秒时间戳」做区分，实测在 GitHub 的
    windows-latest runner 上，两次连续调用会拿到**完全相同**的时间戳
    （Windows 的系统时钟分辨率比微秒粗，两次调用落在同一个 tick 内），
    于是两个运行目录指向同一路径 ⇒ 后一次把前一次的产物覆盖掉。
    这个缺陷在本机一直绿、只在 CI 上红 —— 正是「按行号直接改会改错地方」
    那条教训的翻版：**本机绿 ≠ 正确**。

    随机后缀保证唯一性**不依赖时钟精度**；时间戳只给人看（可排序、可读）。
    """
    fields = identity.get("fields", {})
    model = str(fields.get("model", "unknown")).replace("/", "-")
    temperature = fields.get("temperature", "?")
    repeats = fields.get("repeats", 1)
    tag = identity.get("tag", "0000000000")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    unique = uuid.uuid4().hex[:8]
    return Path(base_dir) / f"{stamp}-{unique}__m-{model}__t{temperature}__r{repeats}__{tag}"


def assert_same_identity(
    existing: Any,
    identity: dict[str, Any],
    *,
    context: str = "",
) -> None:
    """``--resume`` 前的身份一致性闸。身份不同 ⇒ 直接抛错。

    **不做"打个警告继续"** —— 那正是 D2 描述的失败模式：把新旧结果
    混进同一份产物，然后一起汇总出一个谁也复现不出的数字。
    """
    found = identity_of(existing)
    if found is None:
        raise ValueError(
            "既有产物没有运行身份，无法安全续跑。"
            "请改用新产物名（去掉 --resume），或删除旧产物后重跑。"
        )

    old_fields = found.get("fields", {})
    new_fields = identity.get("fields", {})
    differing = [
        key
        for key in sorted(set(old_fields) | set(new_fields))
        if old_fields.get(key) != new_fields.get(key)
    ]
    if differing:
        detail = "\n".join(
            f"  - {key}: 既有={old_fields.get(key)!r} vs 本次={new_fields.get(key)!r}"
            for key in differing
        )
        where = f"（{context}）" if context else ""
        raise ValueError(
            "既有产物与本次运行的身份不一致，拒绝续跑"
            f"{where}；差异字段：\n{detail}\n"
            "把新旧结果混进一份产物会产出一个谁也复现不出的数字。"
            "请使用新的产物路径，或手动确认后删除旧产物。"
        )
