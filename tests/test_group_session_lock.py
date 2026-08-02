"""
tests/test_group_session_lock.py
────────────────────────────────
测试群聊会话级锁（get_session_lock）:
  - 同 (群, 人格) 返回同一把锁
  - 不同群 / 不同人格返回不同锁
  - 同一把锁互斥，保证请求串行执行

按 AGENTS.md 约定：用 importlib 直接加载目标 .py 文件，
绕过 plugins/group/__init__.py（会导入 mcp 等重依赖）。
"""

import sys
import os
import types
import importlib.util
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mock nonebot
sys.modules.setdefault("nonebot", MagicMock())
sys.modules.setdefault("nonebot.log", MagicMock(logger=MagicMock()))
sys.modules.setdefault("nonebot.adapters", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot.v11", MagicMock())

# mock nonebot.get_driver 返回带 group_whitelist 的 config
_mock_config = MagicMock()
_mock_config.group_whitelist = []
_mock_driver = MagicMock()
_mock_driver.config = _mock_config
sys.modules["nonebot"].get_driver = MagicMock(return_value=_mock_driver)

# 构造 plugins / plugins.group 包（不触发 __init__.py 的 handler 导入）
_plugins_pkg = types.ModuleType("plugins")
_plugins_pkg.__path__ = [str(ROOT / "plugins")]
sys.modules.setdefault("plugins", _plugins_pkg)

_group_pkg = types.ModuleType("plugins.group")
_group_pkg.__path__ = [str(ROOT / "plugins" / "group")]
sys.modules["plugins.group"] = _group_pkg

# 加载 utils.py
_spec = importlib.util.spec_from_file_location(
    "plugins.group.utils",
    ROOT / "plugins" / "group" / "utils.py",
)
_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_utils)

get_session_lock = _utils.get_session_lock

import pytest


# ──────────────────── 锁标识 ────────────────────

class TestLockIdentity:

    def test_same_group_persona_same_lock(self):
        assert get_session_lock("g1", "default") is get_session_lock("g1", "default")

    def test_different_persona_different_lock(self):
        assert get_session_lock("g1", "default") is not get_session_lock("g1", "catgirl")

    def test_different_group_different_lock(self):
        assert get_session_lock("g1", "default") is not get_session_lock("g2", "default")


# ──────────────────── 互斥性 ────────────────────

class TestLockMutualExclusion:

    async def test_same_key_serializes(self):
        """同一把锁下，后到的协程必须等先到的释放"""
        lock = get_session_lock("g1", "default")
        order: list[str] = []

        async def holder():
            async with lock:
                order.append("holder-enter")
                await asyncio.sleep(0.05)
                order.append("holder-exit")

        async def waiter():
            async with lock:
                order.append("waiter-enter")

        t1 = asyncio.create_task(holder())
        await asyncio.sleep(0.01)  # 确保 holder 先拿到锁
        t2 = asyncio.create_task(waiter())
        await asyncio.gather(t1, t2)

        assert order == ["holder-enter", "holder-exit", "waiter-enter"]

    async def test_different_keys_run_concurrently(self):
        """不同 (群, 人格) 的请求互不阻塞"""
        lock_a = get_session_lock("g1", "default")
        lock_b = get_session_lock("g1", "catgirl")
        order: list[str] = []

        async def holder_a():
            async with lock_a:
                order.append("a")
                await asyncio.sleep(0.05)
                order.append("a-done")

        async def holder_b():
            async with lock_b:
                order.append("b")
                order.append("b-done")

        t1 = asyncio.create_task(holder_a())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(holder_b())
        await asyncio.gather(t1, t2)

        # b 不需要等 a 释放，在 a 完成前就执行完毕
        assert order.index("b-done") < order.index("a-done")
