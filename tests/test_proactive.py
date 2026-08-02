"""
proactive 心跳 + 主动发言引擎单元测试
──────────────────────────────────────
测试 keyed 空闲计时器 + 私聊/群聊心跳执行 + 开关控制。
"""

import sys
import os
import types
import json
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

# ── 设置路径 & mock NoneBot ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sys.modules.setdefault("nonebot", MagicMock())
sys.modules.setdefault("nonebot.log", MagicMock(logger=MagicMock()))
sys.modules.setdefault("nonebot.exception", MagicMock())
sys.modules.setdefault("nonebot.adapters", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot.v11", MagicMock())

# mock nonebot.get_driver / get_bot
_mock_config = MagicMock()
_mock_config.admin_number = "373900859"
_mock_config.proactive_idle_seconds = 1  # 1 秒用于测试
_mock_driver = MagicMock()
_mock_driver.config = _mock_config
sys.modules["nonebot"].get_driver = MagicMock(return_value=_mock_driver)
sys.modules["nonebot"].get_bot = MagicMock(return_value=MagicMock())

# ── 构造 plugins 包 ──
_plugins_pkg = types.ModuleType("plugins")
_plugins_pkg.__path__ = [str(ROOT / "plugins")]
_plugins_pkg.__package__ = "plugins"
sys.modules["plugins"] = _plugins_pkg


def _make_pkg(name: str, path: str) -> types.ModuleType:
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    pkg.__package__ = name
    sys.modules[name] = pkg
    return pkg


# mock plugins.chunker
_mock_chunker = types.ModuleType("plugins.chunker")
_mock_chunker.chunk_text = lambda text: [text] if text else []
_mock_chunker.send_chunked_raw = AsyncMock()
sys.modules["plugins.chunker"] = _mock_chunker

# mock plugins.llm
_mock_llm = types.ModuleType("plugins.llm")
_mock_llm.API_KEY = "test-key"
_mock_llm.BASE_URL = "https://test.example.com"
_mock_llm.MODEL = "test-model"
_mock_llm.LLM_PROVIDER = "deepseek"
_mock_llm.SUPPORTS_VISION = False
_mock_llm.call_llm = AsyncMock(return_value={"choices": [{"message": {"content": ""}}], "usage": {}})
sys.modules["plugins.llm"] = _mock_llm

# mock plugins.local_tools.manager
_make_pkg("plugins.local_tools", str(ROOT / "plugins" / "local_tools"))
_mock_lt_manager = types.ModuleType("plugins.local_tools.manager")
_mock_lt_manager.get_openai_tools = MagicMock(return_value=[])
_mock_lt_manager.handle_tool_call = AsyncMock(return_value=None)
_mock_lt_manager.list_tools_summary = MagicMock(return_value=[])
sys.modules["plugins.local_tools.manager"] = _mock_lt_manager

# mock plugins.mcp.manager
_make_pkg("plugins.mcp", str(ROOT / "plugins" / "mcp"))
_mock_mcp_manager = types.ModuleType("plugins.mcp.manager")
_mock_mcp_manager.get_openai_tools = MagicMock(return_value=[])
_mock_mcp_manager.call_tool = AsyncMock(return_value="")
_mock_mcp_manager.MAX_TOOL_ROUNDS = 10
_mock_mcp_manager.list_tools_summary = MagicMock(return_value=[])
sys.modules["plugins.mcp.manager"] = _mock_mcp_manager

# mock plugins.skill.manager
_make_pkg("plugins.skill", str(ROOT / "plugins" / "skill"))
_mock_skill_manager = types.ModuleType("plugins.skill.manager")
_mock_skill_manager.get_openai_tools = MagicMock(return_value=[])
_mock_skill_manager.handle_tool_call = MagicMock(return_value=None)
_mock_skill_manager.list_skills_summary = MagicMock(return_value=[])
_mock_skill_manager.build_catalog_prompt = MagicMock(return_value="")
sys.modules["plugins.skill.manager"] = _mock_skill_manager

# mock plugins.runtime_context
_mock_runtime_context = types.ModuleType("plugins.runtime_context")
_mock_runtime_context.build_runtime_context = MagicMock(return_value="\n当前时间: 2026-03-26 12:00:00（星期四）")
sys.modules["plugins.runtime_context"] = _mock_runtime_context

# mock plugins.chat.handler（私聊心跳的会话上下文）
_make_pkg("plugins.chat", str(ROOT / "plugins" / "chat"))
_mock_handler = MagicMock()
_mock_handler.load_history = MagicMock(return_value=[])
_mock_handler.trim_history = MagicMock(side_effect=lambda msgs: msgs)
_mock_handler.append_message = MagicMock()
_mock_handler.get_config = MagicMock(return_value={"last_message_at": "2026-03-26 11:00:00"})
_mock_handler.load_admin_prompt = MagicMock(return_value="你是助手")
_mock_handler.get_proactive_enabled = MagicMock(return_value=True)
sys.modules["plugins.chat.handler"] = _mock_handler

# mock plugins.persona.manager + plugins.group.utils（群聊心跳的会话上下文）
_make_pkg("plugins.persona", str(ROOT / "plugins" / "persona"))
_mock_persona = MagicMock()
_mock_persona.get_active_persona = MagicMock(return_value="default")
_mock_persona.load_history = MagicMock(return_value=[{"role": "assistant", "content": "上一条回复"}])
_mock_persona.append_message = MagicMock()
_mock_persona.load_persona_prompt = MagicMock(return_value="群人格 prompt")
_mock_persona.get_group_config = MagicMock(return_value={"last_message_at": "2026-03-26 11:00:00"})
_mock_persona.get_group_proactive = MagicMock(return_value=True)
sys.modules["plugins.persona.manager"] = _mock_persona

_make_pkg("plugins.group", str(ROOT / "plugins" / "group"))
_mock_group_utils = types.ModuleType("plugins.group.utils")
_mock_group_utils.trim_history = MagicMock(side_effect=lambda msgs, sp: msgs)
sys.modules["plugins.group.utils"] = _mock_group_utils

# ── 用 importlib 加载 proactive.py（根级引擎）──
_spec = importlib.util.spec_from_file_location(
    "plugins.proactive",
    ROOT / "plugins" / "proactive.py",
)
proactive = importlib.util.module_from_spec(_spec)
sys.modules["plugins.proactive"] = proactive
_spec.loader.exec_module(proactive)


@pytest.fixture(autouse=True)
def _cleanup_timer():
    """每个测试前后确保所有计时器被取消，并重置 mocks。"""
    _mock_chunker.send_chunked_raw.reset_mock()
    _mock_handler.reset_mock()
    _mock_persona.reset_mock()
    _mock_llm.call_llm.reset_mock()
    yield
    for key in list(proactive._idle_tasks):
        proactive.cancel_idle_timer(key)


def _mock_call_llm(reply_content: str):
    """让 call_llm 返回指定回复"""
    async def _fake_call_llm(messages, *, tools=None, source="unknown", timeout=120):
        return {"choices": [{"message": {"content": reply_content}}], "usage": {}}
    _mock_llm.call_llm = AsyncMock(side_effect=_fake_call_llm)


# ══════════════════════════════════════════════════════
# 计时器（按 key）
# ══════════════════════════════════════════════════════

class TestIdleTimer:

    async def test_reset_creates_task(self):
        proactive.reset_idle_timer("private")
        assert "private" in proactive._idle_tasks
        assert not proactive._idle_tasks["private"].done()

    async def test_cancel_clears_task(self):
        proactive.reset_idle_timer("private")
        proactive.cancel_idle_timer("private")
        assert "private" not in proactive._idle_tasks

    async def test_keys_are_independent(self):
        proactive.reset_idle_timer("private")
        proactive.reset_idle_timer("group:123")
        assert "private" in proactive._idle_tasks
        assert "group:123" in proactive._idle_tasks

    async def test_reset_while_running_defers_to_min(self):
        """剩余时间 < MIN_DEFER 时延后到 MIN_DEFER_SECONDS"""
        proactive.reset_idle_timer("private")  # IDLE_SECONDS=1
        deadline1 = proactive._idle_deadlines["private"]
        proactive.reset_idle_timer("private")  # remaining ≈ 1s < 600s → 延后
        assert proactive._idle_deadlines["private"] >= deadline1
        assert proactive._idle_deadlines["private"] >= proactive._now() + 500

    async def test_cancel_nonexistent_no_error(self):
        proactive.cancel_idle_timer("group:999")  # 不应报错


# ══════════════════════════════════════════════════════
# 私聊心跳
# ══════════════════════════════════════════════════════

class TestHeartbeatPrivate:

    async def test_heartbeat_ok_is_silent(self):
        _mock_call_llm("HEARTBEAT_OK")
        await proactive.run_heartbeat("private", "373900859")
        _mock_chunker.send_chunked_raw.assert_not_awaited()
        _mock_handler.append_message.assert_not_called()

    async def test_heartbeat_sends_message(self):
        _mock_call_llm("记得喝水哦，保重身体呀")
        await proactive.run_heartbeat("private", "373900859")
        _mock_chunker.send_chunked_raw.assert_awaited_once()
        args = _mock_chunker.send_chunked_raw.await_args.args
        assert args[1] == "private"
        assert "记得喝水哦" in args[3]
        _mock_handler.append_message.assert_called_once()

    async def test_disabled_flag_no_send(self):
        _mock_handler.get_proactive_enabled.return_value = False
        _mock_call_llm("记得喝水哦")
        await proactive.run_heartbeat("private", "373900859")
        _mock_llm.call_llm.assert_not_awaited()
        _mock_chunker.send_chunked_raw.assert_not_awaited()

    async def test_history_not_ending_with_assistant_skips(self):
        """历史最后一条不是 assistant（对话中途）时跳过心跳"""
        _mock_handler.load_history.return_value = [{"role": "user", "content": "在吗"}]
        _mock_call_llm("记得喝水哦")
        await proactive.run_heartbeat("private", "373900859")
        _mock_llm.call_llm.assert_not_awaited()


# ══════════════════════════════════════════════════════
# 群聊心跳
# ══════════════════════════════════════════════════════

class TestHeartbeatGroup:

    async def test_group_heartbeat_sends(self):
        _mock_call_llm("大家今天怎么样？都还好吗")
        await proactive.run_heartbeat("group", "123")
        _mock_chunker.send_chunked_raw.assert_awaited_once()
        args = _mock_chunker.send_chunked_raw.await_args.args
        assert args[1] == "group"
        assert args[2] == 123
        assert "大家今天怎么样？" in args[3]
        # 写入该群当前人格的历史
        _mock_persona.append_message.assert_called_once()
        call = _mock_persona.append_message.call_args
        assert call.args[0] == "123"
        assert call.args[2] == "default"

    async def test_group_disabled_no_send(self):
        _mock_persona.get_group_proactive.return_value = False
        _mock_call_llm("大家今天怎么样？")
        await proactive.run_heartbeat("group", "123")
        _mock_llm.call_llm.assert_not_awaited()
        _mock_chunker.send_chunked_raw.assert_not_awaited()

    async def test_group_persona_missing_skips(self):
        _mock_persona.load_persona_prompt.return_value = None
        _mock_call_llm("大家今天怎么样？")
        await proactive.run_heartbeat("group", "123")
        _mock_llm.call_llm.assert_not_awaited()

    async def test_group_history_not_ending_with_assistant_skips(self):
        _mock_persona.load_history.return_value = [{"role": "user", "content": "有人吗"}]
        _mock_call_llm("大家今天怎么样？")
        await proactive.run_heartbeat("group", "123")
        _mock_llm.call_llm.assert_not_awaited()


# ══════════════════════════════════════════════════════
# key 解析
# ══════════════════════════════════════════════════════

class TestKeyParse:

    def test_private_key(self):
        assert proactive._parse_key("private") == ("private", "373900859")

    def test_group_key(self):
        assert proactive._parse_key("group:123") == ("group", "123")

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            proactive._parse_key("bogus")
