"""
tests/test_dashboard_memory.py
──────────────────────────────
测试 Dashboard 记忆管理路由的**路径正确性**：

  - 群记忆路径必须落在 data/groups/<群号>/MEMORY.md
  - 必须与 Bot 侧 persona.manager.group_memory_path 完全一致
    （历史上这里写成了 data/sessions/groups/<群号>/MEMORY.md，
     导致 Dashboard 编辑的群记忆存不到 Bot 会读的位置）
  - /scopes 要能列出「有会话但还没产生记忆文件」的群

按 AGENTS.md 约定：importlib 直接加载目标文件，nonebot / auth 用 mock。
每个用例都在 tmp_path 里跑，不碰真实的 data/。
"""

import sys
import os
import types
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mock nonebot（persona.manager 只用 nonebot.log）
for _m in (
    "nonebot",
    "nonebot.log",
    "nonebot.adapters",
    "nonebot.adapters.onebot",
    "nonebot.adapters.onebot.v11",
):
    sys.modules.setdefault(_m, MagicMock())
sys.modules["nonebot.log"].logger = MagicMock()


def _mk_pkg(name: str, path: Path | None = None) -> types.ModuleType:
    pkg = types.ModuleType(name)
    if path is not None:
        pkg.__path__ = [str(path)]
    sys.modules[name] = pkg
    return pkg


_mk_pkg("plugins", ROOT / "plugins")
_mk_pkg("plugins.dashboard", ROOT / "plugins" / "dashboard")
_mk_pkg("plugins.dashboard.routes", ROOT / "plugins" / "dashboard" / "routes")
_mk_pkg("plugins.persona", ROOT / "plugins" / "persona")

# auth 依赖：路由函数签名里的 Depends(get_current_user)
_auth_stub = types.ModuleType("plugins.dashboard.auth")
_auth_stub.get_current_user = lambda: "admin"
sys.modules["plugins.dashboard.auth"] = _auth_stub


def _load(name: str, rel: Path):
    spec = importlib.util.spec_from_file_location(name, rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_with_real_fastapi(name: str, rel: Path):
    """加载依赖 fastapi 的模块时，临时摘掉别的测试文件塞进来的 fastapi mock。

    test_dashboard_auth.py 会把 sys.modules["fastapi"] 换成 MagicMock 且不复原。
    沿用该 mock 的话，@router.get(...) 返回的是 MagicMock 实例，
    被装饰的路由函数会被整个替换掉 —— 后面 asyncio.run(...) 拿到的不是协程，
    直接报 "a coroutine was expected"。
    """
    saved: dict[str, object] = {}
    for key in ("fastapi", "fastapi.responses", "fastapi.security"):
        if isinstance(sys.modules.get(key), MagicMock):
            saved[key] = sys.modules.pop(key)
    try:
        return _load(name, rel)
    finally:
        # 复原，避免影响 test_dashboard_auth 等依赖该 mock 的测试
        sys.modules.update(saved)


persona_manager = _load(
    "plugins.persona.manager", ROOT / "plugins" / "persona" / "manager.py"
)
memory_routes = _load_with_real_fastapi(
    "plugins.dashboard.routes.memory",
    ROOT / "plugins" / "dashboard" / "routes" / "memory.py",
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把 cwd 切到临时目录，并预建群会话目录"""
    for d in (
        "data/admin",
        "data/groups",
        "data/sessions/groups/111",
        "data/sessions/groups/222",
    ):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ══════════════════════════════════════════════════════
# 路径正确性（核心回归点）
# ══════════════════════════════════════════════════════

class TestMemoryPath:

    def test_admin_scope(self, data_dir):
        assert memory_routes._memory_path("admin") == Path("data/admin/MEMORY.md")

    def test_group_scope_uses_groups_dir(self, data_dir):
        """群记忆必须在 data/groups/ 下，不能是 data/sessions/groups/"""
        got = memory_routes._memory_path("111")
        assert got == Path("data") / "groups" / "111" / "MEMORY.md"
        assert "sessions" not in str(got)

    def test_matches_bot_side_path(self, data_dir):
        """与 Bot 读取用的 persona.manager.group_memory_path 完全一致"""
        assert (
            memory_routes._memory_path("111").resolve()
            == persona_manager.group_memory_path("111").resolve()
        )


# ══════════════════════════════════════════════════════
# 端到端：Dashboard 写 → Bot 读
# ══════════════════════════════════════════════════════

class TestWriteThenBotReads:

    def test_bot_can_read_what_dashboard_wrote(self, data_dir):
        class Req:
            content = "小明喜欢喝美式"

        asyncio.run(memory_routes.update_memory_content(Req(), scope="111"))

        bot_path = persona_manager.group_memory_path("111")
        assert bot_path.exists()
        assert "小明喜欢喝美式" in bot_path.read_text(encoding="utf-8")

    def test_creates_missing_group_dir(self, data_dir):
        """群里还没建过记忆目录时，写入应自动创建"""
        class Req:
            content = "内容"

        asyncio.run(memory_routes.update_memory_content(Req(), scope="333"))
        assert (data_dir / "data/groups/333/MEMORY.md").exists()

    def test_get_content_roundtrip(self, data_dir):
        class Req:
            content = "往返测试"

        asyncio.run(memory_routes.update_memory_content(Req(), scope="222"))
        result = asyncio.run(memory_routes.get_memory_content(scope="222"))
        assert result["content"] == "往返测试"


# ══════════════════════════════════════════════════════
# /scopes 枚举
# ══════════════════════════════════════════════════════

class TestListScopes:

    def test_lists_all_groups_with_session(self, data_dir):
        """有会话但还没记忆文件的群也要列出来（否则没法新建记忆）"""
        scopes = asyncio.run(memory_routes.list_memory_scopes())
        ids = [s["id"] for s in scopes]
        assert "admin" in ids
        assert "111" in ids
        assert "222" in ids

    def test_exists_flag_reflects_real_memory_file(self, data_dir):
        scopes = asyncio.run(memory_routes.list_memory_scopes())
        by_id = {s["id"]: s for s in scopes}
        assert by_id["111"]["exists"] is False  # 还没有记忆文件

        class Req:
            content = "内容"

        asyncio.run(memory_routes.update_memory_content(Req(), scope="111"))
        scopes = asyncio.run(memory_routes.list_memory_scopes())
        by_id = {s["id"]: s for s in scopes}
        assert by_id["111"]["exists"] is True
        assert by_id["222"]["exists"] is False

    def test_empty_session_dir(self, data_dir):
        """没有任何群会话时只返回 admin"""
        for child in (data_dir / "data/sessions/groups").iterdir():
            child.rmdir()
        (data_dir / "data/sessions/groups").rmdir()
        scopes = asyncio.run(memory_routes.list_memory_scopes())
        assert [s["id"] for s in scopes] == ["admin"]
