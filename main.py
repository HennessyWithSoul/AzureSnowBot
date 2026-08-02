import sys
import os

from dotenv import load_dotenv

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

load_dotenv()

# ── Provider 选择（命令行参数）──
# 默认 deepseek；也可: python main.py qwen（需在 .env 配置对应 key）
_provider = sys.argv[1].lower() if len(sys.argv) > 1 else "deepseek"
if _provider == "qwen":
    os.environ["LLM_PROVIDER"] = "qwen"
    # glm 模式实际走 DashScope 的 qwen-plus 兼容接口
    if os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]
    os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    os.environ["OPENAI_MODEL"] = "qwen-plus"

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
