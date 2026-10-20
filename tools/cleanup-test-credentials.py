"""清理 Windows 凭据管理器里本项目遗留的测试条目。

只删两类**明确的测试残留**：
  - ``database-key.PromptCraftTest-*``  /  ``deepseek-api-key.PromptCraftTest-*``
  - ``database-key.PromptCraftProbe-*`` /  ``deepseek-api-key.PromptCraftProbe-*``

**绝不动**：
  - ``database-key.PromptCraft``（应用的数据库加密密钥）
  - ``deepseek-api-key.PromptCraft``（用户的真实 API Key）
  - 任何与 PromptCraft 无关的条目

为什么需要它：探针与测试的沙箱用 ``Drop`` 清理凭据，但**进程被中断时 Drop 不执行**。
2026-10-20 一次会话崩溃就在用户真实的凭据管理器里留下 105 条残留
（92 条 Probe + 13 条 Test）—— 与 v0.3.4 评审修过的那次（162 条）同类复发。
"""
from __future__ import annotations

import re
import subprocess
import sys

# 明确的测试残留模式（带连字符后缀的沙箱名）
TEST_RESIDUE = re.compile(r"^(database-key|deepseek-api-key)\.PromptCraft(Test|Probe)-[0-9a-fA-F-]+$")
# 生产条目：绝不能碰
PROTECTED = {"database-key.PromptCraft", "deepseek-api-key.PromptCraft"}


def list_targets() -> list[str]:
    out = subprocess.run(["cmdkey", "/list"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = out.stdout or ""
    return re.findall(r"Target:\s*LegacyGeneric:target=(\S+)", text)


def main() -> int:
    targets = list_targets()
    residue = [t for t in targets if TEST_RESIDUE.match(t)]
    protected_present = [t for t in targets if t in PROTECTED]
    unrelated = [t for t in targets if t not in residue and t not in protected_present]

    print(f"凭据条目总数：{len(targets)}")
    print(f"  测试残留（将删除）：{len(residue)}")
    print(f"  生产条目（将保留）：{len(protected_present)} -> {', '.join(protected_present)}")
    print(f"  无关条目（不触碰）：{len(unrelated)}")

    if not residue:
        print("没有残留需要清理。")
        return 0

    failed: list[str] = []
    for target in residue:
        # 注意：**不要加 /f**。本机 Windows 的 cmdkey /delete 语法是
        # `cmdkey /delete:targetname`，带 /f 会报「命令行参数不正确」并逐条失败。
        result = subprocess.run(
            ["cmdkey", f"/delete:{target}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        ok = result.returncode == 0
        if not ok:
            failed.append(target)

    remaining = [t for t in list_targets() if TEST_RESIDUE.match(t)]
    print(f"\n删除完成：{len(residue) - len(failed)} 条成功，{len(failed)} 条失败")
    print(f"复查：仍有 {len(remaining)} 条残留")
    for target in failed[:10]:
        print(f"  删除失败：{target}")
    if remaining:
        print("仍有残留（可能被占用或权限不足），需手工在「控制面板 -> 凭据管理器」清理。")
    # 复查生产条目仍在
    after = list_targets()
    for target in PROTECTED:
        if target in after:
            print(f"  [OK] 生产条目仍在：{target}")
        else:
            print(f"  [!!] 生产条目不见了：{target}")
            return 1
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
