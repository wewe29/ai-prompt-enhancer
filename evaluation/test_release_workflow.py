"""发布工作流的参数化自检：发布说明路径必须真能解析到已提交的文件。

背景
====
v0.4.1 第一次打 tag 时，`release.yml` 的 `body_path` 用了
`steps.target.outputs.version`（= `0.4.1`，**去掉了 v**），
而发布说明的文件名约定是 `RELEASE_NOTES-<tag>.md`
（= `RELEASE_NOTES-v0.4.1.md`，**带 v**）
⇒ 路径对不上 ⇒ 发布步在构建完十几分钟后失败。

而本机跑任何测试都发现不了：这条路径只在 GitHub runner 上解析，
本地 `npm test` / `pytest` 一律绿。

所以这里把它变成**本机可判定**的东西：
解析表达式 → 算出实际路径 → 断言文件真的存在。

离线，不联网。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PACKAGE_JSON = ROOT / "package.json"
RELEASE_ASSETS = ROOT / "release-assets"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _version() -> str:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]


def _tag() -> str:
    return f"v{_version()}"


def _body_path_expr() -> str:
    # 表达式里含空格（${{ steps.target.outputs.tag }}）⇒ 必须按整行取，不能用 \S+
    match = re.search(r"body_path:\s*(.+?)\s*$", _workflow_text(), re.MULTILINE)
    assert match, "release.yml 里找不到 body_path"
    return match.group(1)


def test_body_path_expression_uses_the_tag_output():
    """body_path 必须用 tag 而不是 version。

    `version` 输出去掉了前缀 v（`$tag -replace '^v', ''`），
    而发布说明的文件名约定带 v ⇒ 两者解析到**不同**的文件。
    """
    expr = _body_path_expr()
    assert "steps.target.outputs.tag" in expr, f"body_path 应当用 tag：{expr}"
    assert "steps.target.outputs.version" not in expr, (
        f"body_path 用了 version（去 v），与文件名约定对不上：{expr}"
    )


def test_body_path_resolves_to_a_file_that_exists():
    """把表达式按当前版本解析出来，那个文件必须真的在仓库里。"""
    expr = _body_path_expr()
    resolved = expr.replace("${{ steps.target.outputs.tag }}", _tag())
    assert "${{" not in resolved, f"body_path 里的表达式没解析干净：{expr}"
    assert (ROOT / resolved).is_file(), (
        f"发布说明文件不存在：{resolved}（发布步会在构建十几分钟后才失败）"
    )


def test_tag_and_version_resolve_to_different_files():
    """反向对照：两个口径必须解析到不同文件，否则上一条测试没有区分力。"""
    by_tag = RELEASE_ASSETS / f"RELEASE_NOTES-{_tag()}.md"
    by_version = RELEASE_ASSETS / f"RELEASE_NOTES-{_version()}.md"
    assert by_tag.is_file(), f"缺少发布说明：{by_tag.name}"
    assert not by_version.exists(), (
        f"{by_version.name} 也存在 ⇒ tag 与 version 口径重合，本测试失去区分力"
    )


def test_release_notes_carry_no_effect_claims():
    """发布说明不得含效果宣称（v0.4.1 是前置版，只装尺子不报数）。"""
    text = (RELEASE_ASSETS / f"RELEASE_NOTES-{_tag()}.md").read_text(encoding="utf-8")
    assert "不宣称任何效果数字" in text or "不宣称任何效果" in text, (
        "发布说明必须显式声明本版不含效果宣称"
    )
    for banned in ("命中率提升", "整体质量提升", "不再问已知信息"):
        assert banned not in text, f"发布说明出现了不该出现的效果宣称：{banned}"


def test_shipped_readme_does_not_carry_a_stale_version():
    """随包 README 不得写死版本号。

    它是**静态文件**、每版都原样上传 ⇒ 写死的版本必然过期
    （从 v0.3.x 到 v0.4.1，它一直随包写着「0.1.0」）。
    版本由 zip 文件名与 exe 的版本信息承载。
    """
    text = (RELEASE_ASSETS / "README.txt").read_text(encoding="utf-8")
    found = re.findall(r"\b\d+\.\d+\.\d+\b", text)
    assert not found, f"随包 README 写死了版本号 {found}（静态文件每版都会过期）"


def test_artifact_glob_matches_the_built_zip_name():
    """`files:` 里的产物名必须与 build-portable.ps1 实际生成的 zip 名一致。"""
    text = _workflow_text()
    match = re.search(r"files:\s*\|(.*?)(?=\n\s*\n|\Z)", text, re.DOTALL)
    assert match, "release.yml 里找不到 files 段"
    glob = match.group(1)
    assert "${{ steps.target.outputs.version }}" in glob, (
        "产物名必须用 version（zip 名不带 v）"
    )
    # build-portable.ps1 产出的文件名：PromptCraft-<version>-windows-x64-portable.zip
    expected = f"PromptCraft-{_version()}-windows-x64-portable.zip"
    assert expected in glob.replace("${{ steps.target.outputs.version }}", _version()), (
        f"files 段与实际产物名不一致，应含 {expected}"
    )
