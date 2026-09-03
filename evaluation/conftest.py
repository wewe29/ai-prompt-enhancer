"""统一评测测试的 pytest 临时目录到 ``%TEMP%\\PromptCraft-pytest-<pid>``。

仅当命令行没有显式传入 ``--basetemp`` 时生效；会话结束后删除该目录。
``scripts/build-check.ps1`` 会显式传 ``--basetemp``，此时本插件完全旁路，
由其 ``finally`` 负责清理。两条路径都保证 %TEMP% 下不留 pytest 残留。
"""

import os
import shutil
import tempfile

import pytest

_OWNED_BASETEMP = None


@pytest.hookimpl(tryfirst=True)  # 必须先于 tmpdir 插件构造 TempPathFactory
def pytest_configure(config):
    global _OWNED_BASETEMP
    if config.option.basetemp is None:
        base = os.path.join(tempfile.gettempdir(), f"PromptCraft-pytest-{os.getpid()}")
        config.option.basetemp = base
        _OWNED_BASETEMP = base


def pytest_unconfigure(config):
    if _OWNED_BASETEMP and os.path.isdir(_OWNED_BASETEMP):
        shutil.rmtree(_OWNED_BASETEMP, ignore_errors=True)
