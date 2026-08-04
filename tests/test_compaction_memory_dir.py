"""群聊首次 compaction 创建长期记忆目录的回归测试。"""

from tests.test_compaction import merge_memories_into_file


def test_nonexistent_parent_directory(tmp_path):
    memory_file = tmp_path / "groups" / "1091556208" / "MEMORY.md"

    merge_memories_into_file(memory_file, {"对话备忘": ["首次群记忆"]})

    assert memory_file.exists()
    assert "首次群记忆" in memory_file.read_text(encoding="utf-8")
