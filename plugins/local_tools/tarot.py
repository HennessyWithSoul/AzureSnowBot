"""
塔罗占卜
────────
牌库、抽牌、格式化、参数解析、LLM 解读，以及 local__tarot 工具注册。

两种触发方式：
  - 工具：LLM 在 agentic loop 里自主调用（抽牌后必须继续解读）
  - 命令：/塔罗 [牌数1-5] [问题]，群聊/私聊 handler 共用本模块

牌面：78 张韦特塔罗（22 大阿卡那 + 4×14 小阿卡那），
抽牌不重复，每张随机正位/逆位。
"""

from .manager import register_tool

# ──────────────────── 牌库 ────────────────────

# 22 张大阿卡那
TAROT_MAJOR_ARCANA: list[str] = [
    "愚者", "魔术师", "女祭司", "皇后", "皇帝", "教皇", "恋人", "战车",
    "力量", "隐者", "命运之轮", "正义", "倒吊人", "死神", "节制", "恶魔",
    "高塔", "星星", "月亮", "太阳", "审判", "世界",
]

# 56 张小阿卡那（四花色 × 14：一~十 + 侍从/骑士/王后/国王）
_TAROT_SUITS = ["权杖", "圣杯", "宝剑", "星币"]
_TAROT_RANKS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_TAROT_COURT = ["侍从", "骑士", "王后", "国王"]
TAROT_MINOR_ARCANA: list[str] = [
    f"{s}{r}" for s in _TAROT_SUITS for r in _TAROT_RANKS + _TAROT_COURT
]

_DECK: list[str] = TAROT_MAJOR_ARCANA + TAROT_MINOR_ARCANA  # 78 张

# ──────────────────── 抽牌与格式化 ────────────────────

DEFAULT_CARDS = 1  # /塔罗 不带数字时的默认抽牌数
MAX_CARDS = 5      # 上限


def draw_cards(num: int = DEFAULT_CARDS) -> list[dict]:
    """随机抽 num 张不重复的牌，每张随机正位/逆位。

    防御性处理：int() 兜底 LLM 传来的字符串/浮点，clamp 到 [1, MAX_CARDS]。
    返回 [{"name": str, "upright": bool}, ...]
    """
    import random

    try:
        num = int(num)
    except (TypeError, ValueError):
        num = DEFAULT_CARDS
    num = max(1, min(MAX_CARDS, num))

    chosen = random.sample(_DECK, num)
    return [{"name": n, "upright": random.random() < 0.5} for n in chosen]


def format_cards(cards: list[dict]) -> str:
    """格式化牌面文本：
    你抽到了 2 张牌：
    1. 正位 太阳
    2. 逆位 魔术师
    """
    lines = [
        f"{i}. {'正位' if c['upright'] else '逆位'} {c['name']}"
        for i, c in enumerate(cards, 1)
    ]
    return f"你抽到了 {len(cards)} 张牌：\n" + "\n".join(lines)


# ──────────────────── 参数解析 ────────────────────

def parse_tarot_args(text: str) -> tuple[int, str]:
    """解析 /塔罗 后的参数，返回 (num, question)。num == 0 表示参数非法。

    规则：
      - 无参数             → (DEFAULT_CARDS, "")
      - 首 token 是数字    → 1~MAX_CARDS 则 (n, 剩余文本)；否则 (0, "") 拒绝
      - 首 token 非数字    → 整段当问题 → (DEFAULT_CARDS, 整段)
    """
    if not text.startswith("/塔罗"):
        return DEFAULT_CARDS, text.strip()

    rest = text[len("/塔罗"):].strip()
    if not rest:
        return DEFAULT_CARDS, ""

    tokens = rest.split(maxsplit=1)
    if tokens[0].isdigit():
        n = int(tokens[0])
        if 1 <= n <= MAX_CARDS:
            return n, (tokens[1].strip() if len(tokens) > 1 else "")
        return 0, ""  # 0、负数、超过上限一律拒绝

    return DEFAULT_CARDS, rest.strip()


# ──────────────────── LLM 解读 ────────────────────

_TAROT_INSTRUCTION = (
    "现在请以一位专业塔罗占卜师的身份，为用户解读下面的牌面。"
    "先概述整体牌面能量，再逐张解读每张牌在正位/逆位下的核心含义及其对占卜问题的指向，"
    "最后综合所有牌给出整体建议。用自然流畅的中文分段输出，不要使用 markdown 格式。"
)


def build_tarot_user_message(
    cards: list[dict], question: str = "", asker: str = "",
) -> str:
    """组装解读用的 user 消息：解读指令 + 牌面 + 求问者 + 占卜问题"""
    parts = [_TAROT_INSTRUCTION, format_cards(cards)]
    if asker:
        parts.append(f"求问者：{asker}")
    if question.strip():
        parts.append(f"占卜问题：{question.strip()}")
    parts.append("请开始解读。")
    return "\n\n".join(parts)


async def interpret_cards(
    system_prompt: str,
    cards: list[dict],
    question: str = "",
    asker: str = "",
) -> str:
    """调用 LLM 解读牌面，返回解读文本（失败抛异常，由 handler 捕获）。

    system_prompt 由调用方按渠道拼好（人格 + 运行时上下文）。
    """
    from ..llm import call_llm  # 惰性 import：避免 llm.py import-time 副作用

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_tarot_user_message(cards, question, asker)},
    ]
    data = await call_llm(messages, source="tarot")
    return (data["choices"][0]["message"].get("content") or "").strip()


# ──────────────────── 工具注册 ────────────────────

@register_tool(
    name="tarot",
    description=(
        "为用户进行塔罗占卜：抽取 1-5 张不重复的塔罗牌（每张随机正位或逆位），返回牌面。"
        "当用户想占卜、算命、抽牌、看运势、问吉凶时使用。"
        "注意：调用完这个 tool 记得根据牌面为用户解读占卜结果，不要只展示牌面就结束。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "num": {
                "type": "integer",
                "description": "抽牌数量，1-5，默认 1",
            },
            "question": {
                "type": "string",
                "description": "占卜问题（可选），例如: 明天的运势如何",
            },
        },
    },
)
async def tarot_tool(num: int = DEFAULT_CARDS, question: str = "", **kwargs) -> str:
    """工具实现：抽牌 + 格式化返回牌面文本（解读由 LLM 在 agentic loop 里继续）。"""
    cards = draw_cards(num)
    text = format_cards(cards)
    if question.strip():
        text += f"\n占卜问题：{question.strip()}"
    return text
