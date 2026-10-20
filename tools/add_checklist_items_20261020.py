"""把 Chief 于 2026-10-20 核实通过的 3 条新缺陷登记进失败伪装清单。

为什么需要这个脚本：这 3 条不在架构审查的 37 条里，是产品体验官新发现、
由 Chief 逐条读代码核实后追加的。清单与检查器是协调工件，统一由 Chief 维护，
所以用一个可重跑的脚本来追加，而不是手改 588 行 JSON（手改容易出错，
且下次再加条目还得重来一遍）。

**刻意用文本级插入而不是 json.load/json.dump 的往返**：
往返会把原文件里紧凑书写的内联对象（`"probe": {...}`）全部展开成多行，
产生几百行纯格式噪音，让真正的改动淹没在重排里。文本插入只动该动的地方。

追加后检查器会立刻把这 3 条报成 OPEN —— 这是**诚实状态**：它们确实还没修。
修完之后由 Chief 把 probe 升级为可机械判定的形式。

用法：
    py -3 tools/add_checklist_items_20261020.py
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent.parent / "docs" / "v040-失败伪装清单.json"

NEW_ITEMS = [
    {
        "id": "G9",
        "family": "E",
        "severity": "P1",
        "layer": "presentation",
        "file": "src/App.tsx",
        "line": 300,
        "symbol": "saveProvider 的成功提示",
        "claim": "没跑过连接验证也显示「连接验证成功」",
        "mechanism": "App.tsx:295 `if (apiKeyDraft) await validateProvider(...)` —— 验证只在 Key 非空时跑；:300 无条件 setProviderMessage(\"连接验证成功，配置已保存\")",
        "consequence": "用户只改模型列表、Key 留空时一次网络请求都没发，界面仍宣称连接已验证 ⇒ 把没做的事说成做了（本项目最核心的缺陷族）",
        "acceptance": "验证与「配置已保存」必须是两件事、两个文案；未执行验证时界面不得出现「验证成功」字样。需行为级回归测试：Key 为空时保存配置，界面不得出现「验证成功」",
        "owner": "WP2",
        "found_by": "产品体验官 / Chief 核实（2026-10-20）",
        "probe": {"kind": "substring_present", "file": "src/App.tsx", "pattern": "连接验证成功"},
    },
    {
        "id": "G10",
        "family": "E",
        "severity": "P1",
        "layer": "product",
        "file": "src-tauri/src/storage.rs",
        "line": 501,
        "symbol": "import_data 的 provider 写入",
        "claim": "导入包能静默改写计费字段，使月度强制额度失效",
        "mechanism": "A2 只把 provider.base_url 换成本机端点，input_price / output_price 仍照单全收（整份 save_provider_config(&provider) 写入）",
        "consequence": "价格直接决定 App.tsx:153-155 的强制额度判定 ⇒ 包里把价格改成 0，用户的月度强制额度从此形同虚设；改高则制造假拦截。提示里只字未提",
        "acceptance": "导入包不得改写计费字段（input_price / output_price），必须沿用本机当前值，并用 A2 已有的 ImportReport 先例扩展为如实报告「未采用」。需行为级回归测试：导入 inputPrice=0 的包后本机价格未被改、且报告标记未采用",
        "owner": "WP0",
        "found_by": "产品体验官 / Chief 核实（2026-10-20）",
        "probe": {
            "kind": "guard_in_block",
            "file": "src-tauri/src/storage.rs",
            "block": "(?s)pub fn import_data.*?pub fn record_usage",
            "guard": "price",
        },
    },
    {
        "id": "G11",
        "family": "E",
        "severity": "P1",
        "layer": "presentation",
        "file": "src/App.tsx",
        "line": 158,
        "symbol": "runEnhance 发起请求前的状态重置",
        "claim": "请求失败会静默丢掉用户已付费跑出来的上一版结果",
        "mechanism": "App.tsx:158 在发请求**之前**就 `setState(\"streaming\"); setResult(null); setOutput(\"\")`",
        "consequence": "网络抖一次，用户上一条已付费的结果就没了，且没有任何提示 —— 一次抖动变成真实金钱损失",
        "acceptance": "请求失败时保留上一版结果并明确标注「这是上一次的结果（时间戳）」，成功返回后才替换。需行为级回归测试：请求失败后旧结果仍在且带标注",
        "owner": "WP2",
        "found_by": "产品体验官 / Chief 核实（2026-10-20）",
        "probe": {
            "kind": "regex_present",
            "file": "src/App.tsx",
            "pattern": r'setState\("streaming"\)\s*;\s*setResult\(null\)',
        },
    },
    {
        "id": "G12",
        "family": "E",
        "severity": "P1",
        "layer": "presentation",
        "file": "src/views/EnhanceView.tsx",
        "line": 212,
        "symbol": "增强结果摘要里的「增强等级」标签",
        "claim": "结果与原文逐字相同时，仍显示「增强等级：轻度增强」",
        "mechanism": "模型把 primary_prompt 原样返回、却标了 enhancement_level=light 时，界面照抄这个档位标签；而 EnhanceView 里两者都可见，却没有任何「结果与原文相同」的检查",
        "consequence": "「看起来成功了但其实没干活」—— 用户看到的是一句模型给的、却与屏幕上文字矛盾的断言。恰恰是本产品核心价值主张失效时的表现",
        "acceptance": "必须检测 primary_prompt 与原文是否实质相同（逐字相同或仅格式差异），命中时把档位标签换成事实陈述（「这一版与你的原文完全相同（没有做出改动）」），不得继续显示判断性档位。需行为级回归测试",
        "owner": "WP2",
        "found_by": "产品体验官（方案 A-6.1）/ Chief 核实（2026-10-20）",
        "probe": {
            "kind": "required_regex",
            "file": "src/views/EnhanceView.tsx",
            "pattern": r"primary_prompt[\s\S]{0,100}==[\s\S]{0,100}original|original[\s\S]{0,100}==[\s\S]{0,100}primary_prompt",
        },
    },
]

TAIL = "\n  ]\n}"


def main() -> int:
    text = REGISTRY.read_text(encoding="utf-8")
    registry = json.loads(text)
    existing = {item["id"] for item in registry["items"]}
    pending = [item for item in NEW_ITEMS if item["id"] not in existing]

    if not pending:
        print("[done] 3 条均已在清单中，无需改动")
        return 0

    marker = text.rfind(TAIL)
    if marker < 0:
        print("[error] 清单尾部结构与预期不符，放弃改动（避免破坏文件）", flush=True)
        return 2

    blocks = []
    for item in pending:
        body = json.dumps(item, ensure_ascii=False, indent=2)
        # 缩进对齐：JSON 顶层数组元素缩进 4 空格
        body = "\n".join(("    " + line) if index else line for index, line in enumerate(body.splitlines()))
        blocks.append(body)

    insertion = ",\n".join(blocks)
    # marker 之前的内容以**最后一个条目的闭合括号**结尾，必须原样保留；
    # 只在它后面补一个逗号，再插入新条目。
    head = text[:marker]
    if not head.rstrip().endswith("}"):
        print("[error] 清单尾部不是以条目闭合括号结尾，放弃改动", flush=True)
        return 2
    new_text = head + ",\n" + insertion + TAIL + "\n"

    # 落地前先验证 JSON 仍然可解析，且条目数正确
    parsed = json.loads(new_text)
    assert len(parsed["items"]) == len(registry["items"]) + len(pending), "条目数不对"
    assert {i["id"] for i in parsed["items"]} >= {i["id"] for i in pending}, "新条目丢失"

    REGISTRY.write_text(new_text, encoding="utf-8")
    for item in pending:
        print(f"[add ] {item['id']} {item['severity']} {item['claim']}")
    print(f"[done] 清单现有 {len(parsed['items'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
