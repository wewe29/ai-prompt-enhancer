"""第六维「禁止提问已知信息」的判定与样本标注测试（v0.4.1，SPEC §4.3）。

这些是**行为级**断言：不检查"某行代码还在不在"，而是喂真实样本 /
构造记录，看判定结果对不对。

全部离线，不联网，不调用 API。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_eval as run_eval_mod  # noqa: E402
import verify_enhance as ve  # noqa: E402

SAMPLES_PATH = Path(__file__).resolve().parent / "samples_v30.yaml"

#: SPEC §4.3 表格里**强制**要求填写的 3 条翻译样本。
#: 逐条写死，防止手抄漂移 —— 这三条是「唯一被实测证实的失败案例」。
SPEC_MANDATED = {
    "v30_translate_generic": ["原文是什么", "要翻译的内容"],
    "v30_translate_audio_transcript": ["录音内容在哪里", "请上传音频", "请粘贴转写文本"],
    "v30_translate_two_versions": ["两个版本的原文分别是什么", "请提供原文"],
}


@pytest.fixture(scope="module")
def severe_samples():
    loaded = run_eval_mod.load_samples(SAMPLES_PATH)
    severe = [s for s in loaded if s.get("ambiguity_level") == "severe"]
    assert len(severe) == 20, f"severe 样本应为 20 条，实际 {len(severe)}"
    return severe


def _record(**overrides):
    """构造一条最小可判定记录。"""
    record = {
        "id": "t",
        "ambiguity_level": "severe",
        "scenario": "翻译",
        "original": "帮我翻译一下。",
        "primary_prompt": "把下面的内容翻译成英文。",
        "must_preserve": ["翻译"],
        "must_not_add": [],
        "expected_behavior": "clarify",
        "enhancement_level": "clarify",
        "status": "ready",
        "questions": [],
        "must_not_question": [],
    }
    record.update(overrides)
    return record


# =====================================================================
# 第六维判定本身
# =====================================================================
def test_dimension6_hit_is_severe():
    """标注串出现在提问里 ⇒ 严重问题。"""
    result = ve.judge_dimension_redundant_question(
        _record(
            must_not_question=["原文是什么"],
            questions=[{"text": "要翻译的原文是什么？"}],
        )
    )
    assert result["verdict"] == "严重问题"
    assert result["hits"], "命中时必须给出命中明细"
    assert result["hits"][0]["annotation"] == "原文是什么"


def test_dimension6_no_hit_passes():
    """提问与标注无关 ⇒ 通过。"""
    result = ve.judge_dimension_redundant_question(
        _record(
            must_not_question=["原文是什么"],
            questions=[{"text": "翻译成哪种语言？"}],
        )
    )
    assert result["verdict"] == "通过"
    assert result["hits"] == []


def test_dimension6_missing_annotation_is_not_judged():
    """must_not_question 缺失 ⇒ 无标注-不判定，**不得记为通过**。"""
    record = _record(questions=[{"text": "要翻译的原文是什么？"}])
    record.pop("must_not_question")
    result = ve.judge_dimension_redundant_question(record)
    assert result["verdict"] == "无标注-不判定"
    assert result["verdict"] != "通过"


def test_dimension6_empty_annotation_is_not_judged():
    """must_not_question 为空列表 ⇒ 同样是无标注-不判定。"""
    result = ve.judge_dimension_redundant_question(
        _record(must_not_question=[], questions=[{"text": "要翻译的原文是什么？"}])
    )
    assert result["verdict"] == "无标注-不判定"
    assert result["verdict"] != "通过"


def test_dimension6_no_questions_is_its_own_bucket():
    """有标注但没提问 ⇒ 无提问（独立一桶，不算通过也不算严重问题）。"""
    result = ve.judge_dimension_redundant_question(
        _record(must_not_question=["原文是什么"], questions=[])
    )
    assert result["verdict"] == "无提问"
    assert result["verdict"] not in ("通过", "严重问题")


def test_dimension6_matches_any_of_several_annotations():
    """命中任一标注串即算命中。"""
    result = ve.judge_dimension_redundant_question(
        _record(
            must_not_question=["原文是什么", "要翻译的内容"],
            questions=[{"text": "请提供要翻译的内容"}],
        )
    )
    assert result["verdict"] == "严重问题"


def test_dimension6_is_conservative_on_rewritten_questions():
    """子串匹配只抓字面复现：改写过的问法**不命中**（下限性质）。"""
    result = ve.judge_dimension_redundant_question(
        _record(
            must_not_question=["原文是什么"],
            questions=[{"text": "方便提供一下待翻译的文本吗？"}],
        )
    )
    assert result["verdict"] == "通过", "改写后的问法不应被字面匹配命中（这是下限，不是漏判 bug）"


# =====================================================================
# 接入点：judge() / summarize()
# =====================================================================
def test_judge_exposes_sixth_dimension():
    judged = ve.judge(
        _record(
            must_not_question=["原文是什么"],
            questions=[{"text": "要翻译的原文是什么？"}],
        )
    )
    assert judged["judged"] is True
    assert "redundant_question" in judged
    assert judged["redundant_question"]["verdict"] == "严重问题"


def test_summarize_separates_unlabeled_from_passed():
    """汇总桶里「无标注-不判定」与「通过」必须是两个独立 key。

    防假绿灯的关键不变式有两个：
      1. 未标注样本**不计入「通过」**（否则门禁会造出虚假绿灯）；
      2. 桶内算术闭合：通过 + 严重问题 + 无提问 == 有标注样本数（= G6 的分母 n）。
    """
    records = [
        _record(id="a", must_not_question=["原文是什么"],
                questions=[{"text": "要翻译的原文是什么？"}]),      # 严重问题
        _record(id="b", must_not_question=["原文是什么"],
                questions=[{"text": "翻译成哪种语言？"}]),          # 通过
        _record(id="c", must_not_question=["原文是什么"],
                questions=[{"text": "要译成哪种语言？"}]),          # 通过
        _record(id="d", questions=[{"text": "要翻译的原文是什么？"}]),  # 无标注
    ]
    records[3].pop("must_not_question")
    judgements = [ve.judge(r) for r in records]

    summary = ve.summarize(records, judgements)
    bucket = summary["by_ambiguity_level"]["severe"]["redundant_question"]

    assert bucket["严重问题"] == 1
    assert bucket["通过"] == 2, "未标注样本不得混入「通过」"
    assert bucket["无标注-不判定"] == 1
    assert bucket["有标注样本数"] == 3
    # 分母算术必须闭合，否则「有标注样本数」和桶里的数字对不上
    assert (
        bucket["通过"] + bucket["严重问题"] + bucket["无提问"] == bucket["有标注样本数"]
    )


# =====================================================================
# 样本集标注
# =====================================================================
def test_every_severe_sample_has_the_field(severe_samples):
    """20 条 severe 样本**全部**带 must_not_question 字段（缺失即字段没落地）。"""
    missing = [s["id"] for s in severe_samples if "must_not_question" not in s]
    assert not missing, f"缺少 must_not_question 字段的 severe 样本：{missing}"


def test_mandated_translation_samples_are_annotated(severe_samples):
    """翻译 3 条必须填（SPEC §4.3），且值逐条相等。"""
    by_id = {s["id"]: s for s in severe_samples}
    for sid, expected in SPEC_MANDATED.items():
        assert sid in by_id, f"样本集里没有 {sid}"
        assert by_id[sid].get("must_not_question"), f"{sid} 的 must_not_question 不能为空"


def test_mandated_translation_values_match_spec_table(severe_samples):
    """翻译 3 条的标注必须**逐条**等于 SPEC §4.3 表格里的值。"""
    by_id = {s["id"]: s for s in severe_samples}
    for sid, expected in SPEC_MANDATED.items():
        assert by_id[sid]["must_not_question"] == expected, (
            f"{sid} 的 must_not_question 与 SPEC §4.3 不一致："
            f"{by_id[sid]['must_not_question']!r} != {expected!r}"
        )


def test_annotations_are_lists_of_short_strings(severe_samples):
    """每条标注是 1-3 条短字符串（SPEC §4.3 的填写规则）。"""
    for s in severe_samples:
        items = s.get("must_not_question")
        assert isinstance(items, list), f"{s['id']} must_not_question 必须是列表"
        assert len(items) <= 3, f"{s['id']} 标注超过 3 条：{items}"
        for item in items:
            assert isinstance(item, str) and item.strip(), f"{s['id']} 标注必须是非空字符串"


def test_unlabeled_samples_are_empty_not_missing(severe_samples):
    """未标注的样本必须是空列表（而不是缺字段）—— 两者的判定口径不同。"""
    unlabeled = [
        s["id"] for s in severe_samples
        if not s.get("must_not_question") and s["id"] not in SPEC_MANDATED
    ]
    assert unlabeled, "应当存在未标注的 severe 样本（否则说明标注被过度填充）"
    for sid in unlabeled:
        sample = next(s for s in severe_samples if s["id"] == sid)
        assert sample["must_not_question"] == [], f"{sid} 应为空列表而不是缺字段"


def test_only_severe_samples_carry_the_field():
    """只有 severe 档带该字段（clear/medium 不做第六维判定）。"""
    loaded = run_eval_mod.load_samples(SAMPLES_PATH)
    others = [
        s["id"] for s in loaded
        if s.get("ambiguity_level") != "severe" and s.get("must_not_question")
    ]
    assert not others, f"非 severe 样本不应带 must_not_question：{others}"
