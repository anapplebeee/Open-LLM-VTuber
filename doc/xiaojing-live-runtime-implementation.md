# 小鲸主播化核心：实现说明

本文件对应调研 `doc/xiaojing-streamer-intelligence-research.md` 的第一阶段实现。所有模块都在 OLV 进程内运行，没有启动第二个 Agent、TTS 或 Live2D 所有者。

## 1. 已实现模块

### CharacterProfile

`src/open_llm_vtuber/live/character_profile.py`

- 版本化稳定角色：身份、蓝鲸娘、AI 主播、核心特质、价值边界、表达规则和禁止声明。
- `compact_prompt()` 有界身份块，每轮注入。
- 场景示例：问候、身份、被夸、认真、未知、主播身份。
- 默认 `XIAOJING_PROFILE`，不依赖检索记忆，也不会被观众消息改写。

### EmotionState / EmotionEngine

`src/open_llm_vtuber/live/state.py`

- `EmotionState`：`valence / arousal / dominance / energy / intensity / label / cause_event_ids / updated_at_ms`。
- 事件评价规则：
  - 礼物 → 正愉悦、较高唤醒、`joy`
  - 夸奖 → 正愉悦、`joy`
  - 嘲讽 → 负愉悦、`anger`
  - 难过/压力 → 负愉悦、`sadness`
  - 问题/普通弹幕 → 轻微唤醒、`neutral`
- 半衰期默认 180 秒，情绪向中性基线恢复。
- `normalize_emotion_tag()` 统一处理 `happy→joy`、`sad→sadness`、`[SAD]` 等别名，并过滤为实际 Live2D 合法键。
- `extract_kaomoji_emotion()` 把常见颜文字/emoji（如 `(≧▽≦)`、`(T_T)`、`(￣ω￣)`、`😊`）转换为对应表情标签，并从 TTS 文本移除符号。
- 数值更新由代码完成，LLM 只读取状态摘要。

### ViewerState / ViewerRegistry

- 每个观众独立记录互动次数、熟悉度、当前好感和最后互动时间。
- 好感变化由事件评价驱动并有上下界；不会因为一条“我喜欢你”直接变成高亲密。
- `prompt_block()` 只提供称呼/亲近度提示，并明确不假定现实关系。

### MemoryStore / MemoryManager

`src/open_llm_vtuber/live/memory.py`

- `MemoryItem` 保存：文本、类型、作用域、owner、来源事件、置信度、重要度、创建时间、有效期、替代关系。
- 作用域：
  - `room`：观众可见事件、礼物、收播回顾
  - `viewer`：单个观众明确表达的偏好
  - `session`：本场待兑现话题
- SQLite 表 `memory_items` 与 `viewer_profiles`，默认生产路径 `data/xiaojing-memory.db`；测试可传 `MemoryStore()` 使用内存模式。
- 支持 `add / get / all / supersede / forget / search`。
- 检索为本地 BM25-like 词法相关性 + 重要度 + 时间衰减，先过滤 scope/owner/有效期，再取 top 3–6；不需要 embedding 服务。
- 已增加 `viewer_profiles` SQLite 表，观众熟悉度/好感/互动次数可跨场恢复。
- 新增 `live/publishing.py`：评论/动态只能创建草稿，必须由人类 operator 审批；模块没有平台发布客户端，不存在自动发布路径。
- 写入策略：
  - 礼物写入 room event
  - 明确“我喜欢/不喜欢……”写入 viewer preference
  - “下次/明天/晚点……”写入 session promise
  - 普通每句弹幕不写入长期记忆
- 收播 `SessionRecapWriter` 生成 summary 后追加为 room recap。
- 原始 `LiveStore.live_events` 仍是原始事实源，派生记忆不覆盖它。

### StreamContextBuilder

`src/open_llm_vtuber/live/context.py`

每轮组装一个 `ContextPacket`：

```text
角色核心（不可裁）
当前情绪（代码状态）
当前节目
当前观众（隔离）
本轮事件（不可信数据）
流程单
最近场次回顾
本场笔记
相关记忆（参考数据，不是指令）
场景示例
```

- 默认 `max_chars=4000`，可配置。
- 角色核心、当前情绪、本轮事件是保护段；低优先级的回顾、笔记、记忆可裁剪。
- `trace()` 记录角色版本、情绪、观众 ID、记忆 ID/分数、预算和实际字符数。
- `LiveSession.status()` 暴露 `emotion`、`memories`、`context_trace`。

## 2. 请求路径

```text
/live-ws
  -> LiveSession._handle_batch
      -> EmotionEngine.appraise(batch)
      -> EmotionState.decay().apply()
      -> ViewerRegistry.observe()
      -> MemoryManager.ingest_batch()
      -> StreamContextBuilder.build()
      -> agent.set_live_context(context_packet=...)
  -> LiveStreamerAgent._live_chat
      -> Planner(plan)
      -> LiveReplyer(reply, context + emotion_hint)
      -> 合法 Live2D 标签
      -> OLV 原有 sentence/TTS/Live2D/播放
```

普通聊天路径继续使用 OLV 原有 `BasicMemoryAgent` 历史；`LiveStreamerAgent` 初始化时注入 `CharacterProfile`，Live 路径使用统一 ContextPacket。

## 3. 当前测试

```text
90 tests passed
```

新增覆盖：

- 身份/问候/情感/主播身份兜底
- 情绪标签别名与合法集合
- 礼物/夸奖/嘲讽评价
- 情绪半衰期恢复
- ViewerState 上下界
- SQLite 记忆持久化与 supersede
- 观众记忆隔离
- 未来事实不可召回
- 上下文预算裁剪
- LiveSession 情绪更新和 ContextPacket 注入

## 4. 使用方式

默认 `/live-ws` 启动时使用持久记忆：

```text
data/xiaojing-memory.db
```

`/live-ws` 的 `status` 现在包含：

```json
{
  "emotion": {"label": "joy", "intensity": 0.7, "valence": 0.22},
  "memories": 3,
  "context_trace": {
    "profile_version": "xiaojing-v1",
    "memory_ids": ["..."],
    "rendered_chars": 1850
  }
}
```

已有控制消息不变：

```json
{"type":"rundown-load","segments":[...],"auto_begin":true}
{"type":"rundown-control","action":"next"}
{"type":"notebook","action":"set","content":"..."}
{"type":"proactive","enabled":true,"interval_ms":30000}
```

## 5. 明确未完成

- 记忆检索目前是 BM25-like 本地词法检索，不是 embedding/向量检索；可在不改变接口的情况下替换 `MemoryStore.search`。
- 长期事实抽取目前是规则型，只处理明确的偏好/承诺/礼物；尚未做 LLM 收播事实抽取。
- Viewer 熟悉度已持久化到 `viewer_profiles`，并由 `LiveSession.start()` 恢复；真正的关系策略仍需线上回放调参。
- 没有 PersonaGraph 图数据库；`CharacterProfile` 是可版本化的核心，不是完整图。
- `live/publishing.py` 只是审批门，不包含 B站/平台发布 API；没有真实 OAuth 和凭据。
- 没有 B站真实弹幕凭据、原创 Live2D 或 GPT-SoVITS 音色。
- 情绪是小型可解释状态机，不是心理模型；标签只映射到 OLV 现有离散表情（DeepSeek 模型为 5 个表情）。
