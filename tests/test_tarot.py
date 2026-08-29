"""
tests/test_tarot.py
────────────────────
塔罗占卜模块测试：
  - 牌库数据（22 大阿卡那 + 56 小阿卡那，无重复）
  - draw_cards（不重复、正逆位随机、clamp）
  - format_cards / parse_tarot_args / build_tarot_user_message
  - interpret_cards（mock call_llm）
  - local__tarot 工具（注册配置 + 函数行为）

注意：test_proactive.py 会在模块级把 plugins.* 相关模块永久替换成 mock，
test_runtime_context.py 也会重载 local_tools.manager。为避免 sys.modules
污染，本文件用 spec 隔离加载 tarot.py（与 test_proactive/test_runtime_context
同模式），不触碰全局注册表。
"""

import sys
import os
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock nonebot
sys.modules.setdefault("nonebot", MagicMock())
sys.modules.setdefault("nonebot.log", MagicMock(logger=MagicMock()))
sys.modules.setdefault("nonebot.adapters", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot", MagicMock())
sys.modules.setdefault("nonebot.adapters.onebot.v11", MagicMock())

import pytest


# ──────────────────── 隔离加载 tarot.py ────────────────────

def _load_tarot_isolated():
    """在临时 manager 上下文中加载 tarot.py，捕获注册信息后恢复 sys.modules。

    其他测试文件（test_proactive / test_runtime_context）会替换
    sys.modules 里的 plugins.* 模块，直接 import 会拿到 mock 或空注册表。
    这里用 spec 加载真实 tarot.py，并把 @register_tool 捕获下来，
    加载完立即恢复 sys.modules，不影响其他测试。
    """
    ROOT = Path(__file__).resolve().parent.parent

    saved = {}
    for name in ("plugins.local_tools", "plugins.local_tools.manager",
                 "plugins.local_tools.tarot"):
        saved[name] = sys.modules.get(name)

    _lt_pkg = types.ModuleType("plugins.local_tools")
    _lt_pkg.__path__ = [str(ROOT / "plugins" / "local_tools")]
    sys.modules["plugins.local_tools"] = _lt_pkg

    _captured: dict = {}

    def _fake_register_tool(name, description, parameters=None, admin_only=False):
        _captured.update({
            "name": name,
            "description": description,
            "parameters": parameters or {},
            "admin_only": admin_only,
        })
        return lambda func: func  # 装饰器原样返回函数

    _mock_mgr = types.ModuleType("plugins.local_tools.manager")
    _mock_mgr.register_tool = _fake_register_tool
    sys.modules["plugins.local_tools.manager"] = _mock_mgr

    _spec = importlib.util.spec_from_file_location(
        "plugins.local_tools.tarot", ROOT / "plugins" / "local_tools" / "tarot.py")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["plugins.local_tools.tarot"] = _mod
    _spec.loader.exec_module(_mod)

    # 恢复 sys.modules（其他测试依赖原有条目）
    for name, mod in saved.items():
        if mod is not None:
            sys.modules[name] = mod
        else:
            sys.modules.pop(name, None)

    return _mod, _captured


_tarot_mod, _tool_reg = _load_tarot_isolated()

TAROT_MAJOR_ARCANA = _tarot_mod.TAROT_MAJOR_ARCANA
TAROT_MINOR_ARCANA = _tarot_mod.TAROT_MINOR_ARCANA
draw_cards = _tarot_mod.draw_cards
format_cards = _tarot_mod.format_cards
parse_tarot_args = _tarot_mod.parse_tarot_args
build_tarot_user_message = _tarot_mod.build_tarot_user_message
interpret_cards = _tarot_mod.interpret_cards
tarot_tool = _tarot_mod.tarot_tool


# ──────────────────── 牌库数据 ────────────────────

class TestDeckData:

    def test_major_arcana_count(self):
        assert len(TAROT_MAJOR_ARCANA) == 22

    def test_minor_arcana_count(self):
        assert len(TAROT_MINOR_ARCANA) == 56

    def test_no_duplicates(self):
        all_names = TAROT_MAJOR_ARCANA + TAROT_MINOR_ARCANA
        assert len(all_names) == len(set(all_names)) == 78

    def test_four_suits_each_14(self):
        for suit in ("权杖", "圣杯", "宝剑", "星币"):
            count = sum(1 for c in TAROT_MINOR_ARCANA if c.startswith(suit))
            assert count == 14, f"{suit} 应有 14 张，实际 {count}"


# ──────────────────── draw_cards ────────────────────

class TestDrawCards:

    def test_draw_count(self):
        assert len(draw_cards(3)) == 3
        assert len(draw_cards(1)) == 1

    def test_no_duplicate_names(self):
        cards = draw_cards(5)
        names = [c["name"] for c in cards]
        assert len(names) == len(set(names))

    def test_names_in_deck(self):
        all_names = set(TAROT_MAJOR_ARCANA + TAROT_MINOR_ARCANA)
        for c in draw_cards(5):
            assert c["name"] in all_names
            assert isinstance(c["upright"], bool)

    def test_upright_reversed_both_appear(self):
        """正逆位随机性：500 次 5 连抽，正位/逆位都应出现过"""
        seen_upright = seen_reversed = False
        for _ in range(500):
            for c in draw_cards(5):
                if c["upright"]:
                    seen_upright = True
                else:
                    seen_reversed = True
        assert seen_upright and seen_reversed

    def test_clamp_above_max(self):
        assert len(draw_cards(10)) == 5

    def test_clamp_zero_and_negative(self):
        assert len(draw_cards(0)) == 1
        assert len(draw_cards(-3)) == 1

    def test_float_and_str_fallback(self):
        assert len(draw_cards(3.9)) == 3  # int() 兜底
        assert len(draw_cards("4")) == 4

    def test_invalid_value_default(self):
        assert len(draw_cards("abc")) == 1


# ──────────────────── format_cards ────────────────────

class TestFormatCards:

    def test_format_two_cards(self):
        cards = [
            {"name": "太阳", "upright": True},
            {"name": "魔术师", "upright": False},
        ]
        text = format_cards(cards)
        assert "你抽到了 2 张牌" in text
        assert "1. 正位 太阳" in text
        assert "2. 逆位 魔术师" in text


# ──────────────────── parse_tarot_args ────────────────────

class TestParseArgs:

    def test_no_args(self):
        assert parse_tarot_args("/塔罗") == (1, "")

    def test_num_only(self):
        assert parse_tarot_args("/塔罗 3") == (3, "")

    def test_num_and_question(self):
        assert parse_tarot_args("/塔罗 3 帮我看看") == (3, "帮我看看")

    def test_question_without_num(self):
        assert parse_tarot_args("/塔罗 明天运势") == (1, "明天运势")

    def test_num_and_multiword_question(self):
        assert parse_tarot_args("/塔罗 1 2 3") == (1, "2 3")

    def test_num_too_large_rejected(self):
        assert parse_tarot_args("/塔罗 6") == (0, "")

    def test_num_zero_rejected(self):
        assert parse_tarot_args("/塔罗 0") == (0, "")

    def test_max_num_accepted(self):
        assert parse_tarot_args("/塔罗 5") == (5, "")

    def test_non_command_text(self):
        assert parse_tarot_args("随便聊聊") == (1, "随便聊聊")


# ──────────────────── build_tarot_user_message ────────────────────

class TestBuildUserMessage:

    def test_contains_instruction_and_cards(self):
        cards = [{"name": "太阳", "upright": True}]
        msg = build_tarot_user_message(cards, "明天运势", "小明")
        assert "塔罗占卜师" in msg
        assert "1. 正位 太阳" in msg
        assert "求问者：小明" in msg
        assert "占卜问题：明天运势" in msg

    def test_empty_question_omits_section(self):
        msg = build_tarot_user_message([{"name": "愚者", "upright": False}])
        assert "占卜问题：" not in msg  # 段落标记不出现（指令文本里不含冒号段）


# ──────────────────── interpret_cards（mock call_llm） ────────────────────

@pytest.fixture
def mock_llm(monkeypatch):
    """注入 plugins.llm 模块（惰性 import 路径），返回 mock call_llm"""
    module = types.ModuleType("plugins.llm")
    mock_call = AsyncMock(return_value={
        "choices": [{"message": {"content": "  解读结果文本  "}}],
        "usage": {},
    })
    module.call_llm = mock_call
    monkeypatch.setitem(sys.modules, "plugins.llm", module)
    return mock_call


@pytest.mark.asyncio
class TestInterpretCards:

    async def test_messages_structure(self, mock_llm):
        cards = [{"name": "太阳", "upright": True}]
        reply = await interpret_cards("SYSTEM_PROMPT", cards, "明天运势", "小明")
        assert reply == "解读结果文本"  # strip 后返回

        args, kwargs = mock_llm.await_args
        messages = args[0]
        assert messages[0] == {"role": "system", "content": "SYSTEM_PROMPT"}
        assert messages[1]["role"] == "user"
        assert "1. 正位 太阳" in messages[1]["content"]
        assert kwargs.get("source") == "tarot"

    async def test_empty_content_returns_empty(self, mock_llm):
        mock_llm.return_value = {"choices": [{"message": {"content": ""}}], "usage": {}}
        reply = await interpret_cards("SYS", draw_cards(1))
        assert reply == ""

    async def test_exception_propagates(self, mock_llm):
        mock_llm.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError):
            await interpret_cards("SYS", draw_cards(1))


# ──────────────────── local__tarot 工具 ────────────────────

class TestTarotTool:

    def test_registration(self):
        """注册配置：非 admin_only（私聊群聊都可见）、参数齐全、提示解读"""
        assert _tool_reg["name"] == "tarot"
        assert _tool_reg["admin_only"] is False
        assert "num" in _tool_reg["parameters"]["properties"]
        assert "question" in _tool_reg["parameters"]["properties"]
        assert "解读" in _tool_reg["description"]

    @pytest.mark.asyncio
    async def test_call_default(self):
        result = await tarot_tool()
        assert "你抽到了 1 张牌" in result

    @pytest.mark.asyncio
    async def test_call_with_num(self):
        result = await tarot_tool(num=2)
        assert "你抽到了 2 张牌" in result

    @pytest.mark.asyncio
    async def test_call_clamps_num(self):
        result = await tarot_tool(num=10)
        assert "你抽到了 5 张牌" in result

    @pytest.mark.asyncio
    async def test_call_with_question(self):
        result = await tarot_tool(num=1, question="明天")
        assert "占卜问题：明天" in result


# ──────────────────── issue #3：解读时带上对话上下文 ────────────────────

class TestInterpretCardsWithHistory:

    async def test_history_is_injected(self, mock_llm):
        """传了 history 时，历史消息应排在牌面消息之前"""
        history = [
            {"role": "user", "content": "我最近在纠结要不要换工作"},
            {"role": "assistant", "content": "说说看，是什么让你想换？"},
        ]
        await interpret_cards("SYS", draw_cards(1), "我该换吗", "小明", history=history)

        messages = mock_llm.await_args.args[0]
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "我最近在纠结要不要换工作"
        # 最后一条才是牌面
        assert "求问者：小明" in messages[-1]["content"]
        assert "占卜问题：我该换吗" in messages[-1]["content"]

    async def test_no_history_keeps_two_messages(self, mock_llm):
        """不传 history 时行为与改动前一致"""
        await interpret_cards("SYS", draw_cards(1))
        messages = mock_llm.await_args.args[0]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    async def test_history_truncated_to_limit(self, mock_llm):
        """history 只取最后 history_limit 条"""
        history = [{"role": "user", "content": f"旧消息{i}"} for i in range(50)]
        await interpret_cards("SYS", draw_cards(1), history=history, history_limit=5)
        messages = mock_llm.await_args.args[0]
        # system + 5 条历史 + 牌面 = 7
        assert len(messages) == 7
        assert messages[1]["content"] == "旧消息45"

    async def test_skips_tool_and_multimodal_messages(self, mock_llm):
        """tool 消息和多模态 content（list）应被跳过，只留纯文本轮次"""
        history = [
            {"role": "user", "content": "你好"},
            {"role": "tool", "content": "工具结果"},
            {"role": "assistant", "content": [{"type": "text", "text": "多模态回复"}]},
            {"role": "user", "content": "再问一句"},
        ]
        await interpret_cards("SYS", draw_cards(1), history=history)
        messages = mock_llm.await_args.args[0]
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "user", "user"]
        assert messages[1]["content"] == "你好"
        assert messages[2]["content"] == "再问一句"

    async def test_empty_history_noop(self, mock_llm):
        await interpret_cards("SYS", draw_cards(1), history=[])
        messages = mock_llm.await_args.args[0]
        assert len(messages) == 2
