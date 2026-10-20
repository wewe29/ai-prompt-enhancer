"""增强温度的单一来源断言（v0.4.0 架构审查 P0-5）。

增强温度是 v0.4.0 主线「档位是否稳定」的**隐藏参数**，它同时出现在 5 个地方：

| 位置 | 角色 |
|---|---|
| `src-tauri/src/provider.rs` 的 `ENHANCEMENT_TEMPERATURE` | 产品实际值（Rust 侧唯一来源） |
| `evaluation/enhancer.py` 的 `ENHANCEMENT_TEMPERATURE` | Python 侧唯一来源（请求兜底默认值） |
| `evaluation/config.yaml` 的 `enhancer.temperature` | 评测实际读取的配置 |
| `evaluation/config.py` 的 `DEFAULTS["enhancer"]["temperature"]` | 评测默认配置 |
| `evaluation/verify_enhance.py` 的 `TEMPERATURE` | 产出「档位分布」数字的脚本 |

这 5 处此前是**手工同步**的，0 处被自动校验：产品改了而评测侧没改，
run_eval / bench_* 的全部结论就建立在与线上不同的温度上，
而配置看起来完全可信（config.yaml 当时还写着"与后端一致"，实际是 0.35）。

本文件把这个手工约定变成**可执行的断言**：任何一处漂移，pytest 立刻失败。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 允许从 evaluation 目录直接运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import enhancer  # noqa: E402
import verify_enhance  # noqa: E402
import yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_RS = PROJECT_ROOT / "src-tauri" / "src" / "provider.rs"
CONFIG_YAML = Path(__file__).resolve().parent / "config.yaml"

# 产品当前采用的增强温度。改动它必须同时更新评测侧 4 处，
# 并在提交说明里写明依据（v0.4.0 的报告已声明 41 个百分点的噪声地板，
# 任何小于它的差异都不可解读）。
EXPECTED_TEMPERATURE = 0.1


def _rust_temperature() -> float:
    """从 provider.rs 源码提取 ENHANCEMENT_TEMPERATURE 的值。"""
    source = PROVIDER_RS.read_text(encoding="utf-8")
    match = re.search(
        r"pub const ENHANCEMENT_TEMPERATURE:\s*f64\s*=\s*([0-9.]+)\s*;",
        source,
    )
    assert match, "未在 provider.rs 中找到 ENHANCEMENT_TEMPERATURE 常量"
    return float(match.group(1))


def _yaml_temperature() -> float:
    loaded = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
    value = (loaded.get("enhancer") or {}).get("temperature")
    assert value is not None, "config.yaml 里找不到 enhancer.temperature"
    return float(value)


def _all_temperatures() -> dict[str, float]:
    return {
        "provider.rs (产品实际值)": _rust_temperature(),
        "enhancer.py (Python 侧来源)": float(enhancer.ENHANCEMENT_TEMPERATURE),
        "config.yaml (评测配置)": _yaml_temperature(),
        "config.py (评测默认)": float(config.DEFAULTS["enhancer"]["temperature"]),
        "verify_enhance.py (档位分布脚本)": float(verify_enhance.TEMPERATURE),
    }


def test_every_temperature_site_agrees():
    """5 处取值必须完全一致，否则评测结论与线上产品跑在不同温度上。"""
    values = _all_temperatures()
    distinct = set(values.values())
    assert len(distinct) == 1, (
        "增强温度在多处之间不一致：\n"
        + "\n".join(f"  {name} = {value}" for name, value in values.items())
    )


def test_temperature_matches_the_product_value():
    """当前产品值钉在 EXPECTED_TEMPERATURE，改动需要显式说明理由。"""
    values = _all_temperatures()
    for name, value in values.items():
        assert value == EXPECTED_TEMPERATURE, (
            f"{name} = {value}，与产品当前值 {EXPECTED_TEMPERATURE} 不符；"
            "改动温度必须同步更新全部 5 处并写明依据"
        )


def test_provider_rs_uses_the_constant_instead_of_a_literal():
    """provider.rs 的请求体必须用常量，不能在旁边又写一个字面量。"""
    source = PROVIDER_RS.read_text(encoding="utf-8")
    assert '"temperature": ENHANCEMENT_TEMPERATURE' in source, (
        "provider.rs 的请求体应引用 ENHANCEMENT_TEMPERATURE 常量，"
        "而不是再写一个字面量温度"
    )
    literals = re.findall(r'"temperature"\s*:\s*([0-9.]+)', source)
    assert not literals, f"provider.rs 里仍有硬编码温度：{literals}"


def test_enhancer_py_fallback_uses_the_constant():
    """enhancer.py 的兜底默认值必须引用常量（配置缺项时的实际生效值）。"""
    source = Path(enhancer.__file__).read_text(encoding="utf-8")
    assert "enh.get(\"temperature\", ENHANCEMENT_TEMPERATURE)" in source, (
        "enhancer.py 的 temperature 兜底默认值应引用 ENHANCEMENT_TEMPERATURE 常量"
    )
