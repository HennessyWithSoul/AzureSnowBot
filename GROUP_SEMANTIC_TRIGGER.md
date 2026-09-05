# 群聊语义 / 点名主动触发（设计）

本文记录「不 @ 也接话」的下一层：点名、引用 Bot、以及和 Bot 最近发言语义相近时插话。
**尚未实现。** 现有 `/chatter` 只覆盖复读句数和 5 分钟消息频率。

聊天模型可以继续用 DeepSeek。Embedding **必须另开一条**，DeepSeek 没有 `/embeddings`。

---

## 现状

| 触发 | 条件 | 通道 |
|------|------|------|
| @ Bot | 白名单群 | 完整 Agentic Loop |
| 复读 | `/chatter on`，连续相同句指数概率 | 原句跟读，不调 LLM |
| 热闹插话 | `/chatter on`，近 5 分钟条数指数概率 | 短回复 LLM，可 SKIP |
| 记忆向量 | `indexer.py` | 跟本次触发无关；且绑在 `LLM_PROVIDER` 上，DeepSeek 时整条空转 |

防骚扰已经有：`/chatter` 总开关、发言后概率减半、5 分钟未发言重置、inflight 锁、@ 不叠插话。新触发应复用这套，不要另开必回通道。

---

## 建议落地顺序

### 1. 点名 + 引用 Bot（先做）

便宜、准，不需要向量。

- 匹配 Bot 群名片 / 昵称、人格名、别名：`bot`、`机器人` 等（大小写不敏感）。
- QQ **回复 Bot 那条气泡**（`event.reply` 的发送者是 `self_id`）视为点名，比关键词更准。
- 走现有插话 LLM（短、可 SKIP），**不要**进完整 Agentic Loop。有人说「这机器人真烦」不该被当成提问。
- 已 @ 则只走主对话。未 `/chatter on` 则忽略。
- 点名只提高「该插话」的权重，仍允许模型回复 SKIP。

### 2. 字面跟帖（可选，零 API）

把当前用户句和 Bot **最近 30 分钟**回复做关键词 / 字符重合（或现成 BM25）。
「你刚说的 xxx」经常已经够用，作为向量落地前的过渡。

### 3. 向量跟帖（有 embedding 再做）

「和 Bot 最近半小时说的话语义近 → 接话」。

| 项 | 建议 |
|----|------|
| 索引 | 只存 **Bot 自己近 30 分钟**回复向量（内存 deque），不要整群历史 |
| 查询 | 短句、像在接话（疑问、指代「这个 / 你刚说」）；过短或纯表情跳过 |
| 阈值 | 余弦大约 ≥ 0.75 再考虑 |
| 触发 | **加分**：提高插话概率，或视为点名走插话；不要 100% 必回 |
| 共用 | 继续用减半 / 重置 / inflight |
| 禁止 | 每条群消息都问大模型「要不要回」 |

不要复用 `memory_search` 的整套混合检索。记忆索引是跨会话事实；这里只是「刚说的几句像不像」。
`embed_texts()` 可以共用，触发逻辑单独写。

---

## Embedding 怎么选

聊天继续 DeepSeek。向量必须独立，否则和现在的记忆索引一样会静默失败。

代码现状：`plugins/memory/indexer.py` 的 embedding 跟 `LLM_PROVIDER` 绑死。要启用应加单独的 `EMBEDDING_PROVIDER` / `EMBEDDING_API_KEY`（或本地后端），聊天 provider 不变。

### 云端 API（少改代码）

项目里已有调用形状（OpenAI 兼容 `/embeddings` 或 Gemini batch embed）：

| Provider | 模型 | 说明 |
|----------|------|------|
| 通义 Qwen | `text-embedding-v3` | 中文稳，国内好用 |
| Gemini | `gemini-embedding-001` | 中英都行 |
| OpenAI | `text-embedding-3-small` | 贵一点，质量稳 |

适合：已经有对应 key、群流量不大。

### 本地库（每条群消息都算、不想按次计费）

| 库 | 典型模型 | 特点 |
|----|----------|------|
| sentence-transformers | `BAAI/bge-small-zh-v1.5`、`jinaai/jina-embeddings-v2-base-zh` | Python 最常用 |
| fastembed | 量化 bge / jina | 依赖少，CPU 可跑 |
| onnxruntime + 量化 bge | 同上 | 更轻，适合常驻 Bot |

本机大约 100–400MB 模型。Mac CPU 上一句中文通常几十毫秒。
「短句 × 半小时内几条 Bot 回复」用 numpy 余弦即可，不必上 FAISS / Milvus。

### 不建议

- 继续指望 DeepSeek 出 embedding
- 为这个场景上向量数据库
- 和聊天共用同一个 `LLM_PROVIDER` 开关

---

## 和现有模块的关系

```
群消息
  → /chatter 关闭？直接 return
  → @ 了？主对话，结束
  → 复读骰子 / 5 分钟频率骰子（已有）
  → （拟）引用 Bot 或点名 → 插话 LLM
  → （拟）与近 30 分钟 Bot 回复：字面重合或余弦 ≥ 阈值 → 插话 LLM（加分，可 SKIP）
```

插话成功后仍调用 `note_bot_reply()`，和 @ 回复一样衰减概率。

记忆 RAG（`MEMORY.md` + history 索引）是另一件事：给 @ 对话检索长期事实。可以共用 embedding 后端，不要共用触发条件。
