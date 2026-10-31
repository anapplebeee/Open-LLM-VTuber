# OLV 直播集成代码审查与修复记录

**审查日期：** 2026-10-31
**审查对象：** 本仓库工作树中的直播集成（`src/open_llm_vtuber/live/`、`LiveStreamerAgent`、`/live-ws`、`/proxy-ws` 相关路径）
**上游基线：** Open-LLM-VTuber 1.2.1（`pyproject.toml`），当前工作树 commit `ccb0928` 之上的未提交改动
**验证命令：** `python -m unittest discover -s tests -p "test_*.py"`（66 项通过）

> 本文是源码级审查和本机测试记录，不是真实 B 站、TTS、Live2D、OBS 或长稳验收。

## 1. 结论

1. **架构方向成立。** 直播编排（`LiveDirector` + `LiveSession`）、决策（`LiveReActPlanner`）、表达（`LiveReplyer`）、播放（`LiveBrowserOutput`）和事实存储（`LiveStore`）职责分离；OLV 的对话/TTS/Live2D 仍是唯一表现层，没有第二套 TTS 或第二个决策循环。
2. **`/live-ws` 是单场直播入口，`/proxy-ws` 是共享单后端代理。** 两者不是同一个会话模型；`/proxy-ws` 的所有代理客户端共享 OLV 服务端的一个 `client_uid`，这是播放回执能够匹配导播回合的前提（见 §2.3）。
3. **本轮修复了 10 个 OLV 直播代码缺陷和 6 个跨仓 Node 联动缺陷**，覆盖：提前播放回执、重复 `backend-synth-complete`、B 站 collector 回调未 await、`live_active` 泄漏、停止时 TTS 任务泄漏、队列优先级/淘汰、场次对话只存 AI 侧、事件控制字符、自动开始 Rundown 的启动顺序等。
4. **仍不能宣称生产可用。** 单播放端没有服务端强制、OLV 没有回合 ID、播放回执没有超时、`/proxy-ws` 没有认证、真实 B 站/TTS/Live2D 未验收；这些在 §5 明确列出。

## 2. 架构确认

### 2.1 分层与所有权

```text
Bilibili official collector / 模拟 room-event
        │  RoomEvent（不可信观众数据）
        ▼
LiveDirector  有界队列、去重、过期、优先级、批处理
        │  LiveBatch（单个消费者）
        ▼
LiveSession   情绪/观众/记忆/ContextPacket/场次事实
        │  BatchInput(metadata.live_batch)
        ▼
LiveStreamerAgent
  ├─ LiveReActPlanner：说/不说、理由、表达指令、情绪
  ├─ LiveReplyer：只生成一小段主播台词
  └─ OLV sentence_divider / tts_filter / actions_extractor / TTSTaskManager
        │  audio / display_text / actions
        ▼
LiveBrowserOutput ── /live-ws WebSocket ── OLV 前端（唯一播放端）
        │
        └─ LiveStore(SQLite)：live_sessions / live_events / live_chat / session_recaps
```

| 层 | 唯一所有者 | 说明 |
|---|---|---|
| 直播事件、聚合、优先级、Rundown | `LiveDirector` / `LiveSession` | 不再启动第二个 Agent 循环 |
| 直播决策 | `LiveReActPlanner` | 有界 JSON 决策；最多一次 session recall |
| 台词表达 | `LiveReplyer` | 不做工具调用、不拼事件结构 |
| TTS、音频、字幕、Live2D | OLV 原生链路 | `LiveBrowserOutput` 复用 `process_agent_output` |
| 场次事实 | `LiveStore` | `live_chat` 未替代 OLV 的普通聊天历史 |
| 普通 `/client-ws` | OLV 自身 | 与 `/live-ws` 分离，不能同时把两者当同一会话 |

### 2.2 状态与并发边界

- `LiveSession` 用 `asyncio.Lock` 串行化批次处理；`LiveDirector` 是单消费者。
- `ProactiveScheduler` 只产生一个合成 `proactive` 批次，必须等 director 队列为空且批次锁空闲。
- `LiveBrowserOutput` 每个 session 独占一个 `TTSTaskManager`；`end_batch` 等待合成完成和 ordered sender 排空后才发 `backend-synth-complete`。
- `LiveSession` 现在支持 `on_close`，停止时取消 `LiveBrowserOutput` 的 TTS 任务/发送器，避免 `/live-ws` 断开后后台任务继续向已关闭 socket 写音频。

### 2.3 `/proxy-ws` 协议事实（逐条核对源码）

1. **一个代理上游连接。** `proxy_handler.py` 只创建一个 `self.server_ws` 连接 `/client-ws`；所有 `/proxy-ws` 客户端的消息经 `forward_to_server` 走这一条连接。`routes.py` 对每条 `/client-ws` 连接生成一个 `client_uid`，所以代理客户端在 OLV 侧共享同一个 `client_uid`。
2. **播放回执能够匹配。** 播放端发出的 `frontend-playback-complete` 经代理转发后，`websocket_handler.py` 以共享 `client_uid` 调用 `message_handler.handle_message`，正好满足 `conversation_utils.finalize_conversation_turn` 的 `wait_for_response(client_uid, "frontend-playback-complete")`。**但任何代理客户端都能满足该等待，且没有回合 ID。**
3. **普通回合顺序。** `conversation-chain-start` → 生成/TTS → `backend-synth-complete` → 等待 `frontend-playback-complete` → `force-new-message` → `conversation-chain-end`。`backend-synth-complete` 过去在 `single_conversation.py` 和 `conversation_utils.py` 各发一次，且没有等待 ordered sender；本轮已修正为只在 finalize 发一次，并先 `wait_idle()`。
4. **单客户端打断不广播控制消息。** `proxy_handler.py` 收到 `interrupt-signal` 只把 `conversation_active=False` 并转发；`conversation_handler.py` 的 individual interrupt 取消任务、写历史，不发送任何控制消息；group 路径才会广播 `interrupt-signal`。Node 代理因此必须在发送成功后立即结束本地回合，不能等 `chain-end`。
5. **`wait_for_response` 无超时。** 播放端未打开、音频播放失败或回执丢失时，OLV 会无限等待；导播侧必须在本地超时并进入“需要人工恢复”，不能伪造回执。`/live-ws` 的 `LiveBrowserOutput` 明确不等待回执（直播必须继续推进），普通 `/client-ws` 仍等待。
6. **`force-new-message` 只属于有 TTS 的路径。** 没有 TTS 任务时不会发送；客户端不能把它当成每回合固定消息。

### 2.4 与旧 Node 原型的关系

工作区根目录的 Node 项目是早期外部导播原型；本仓库的 `live/` 才是当前 OLV 主仓内的直播实现。两者的 `/proxy-ws`、播放回执和回合边界结论一致，但 Node `public/olv-player.js` 只适用于普通 `/proxy-ws` 对话；`/live-ws` 使用 `LiveBrowserOutput`，不经过 Node 播放页。

## 3. 修复的缺陷

### 3.1 OLV 直播代码

| ID | 位置 | 现象 | 修复 |
|---|---|---|---|
| L1 | `conversations/single_conversation.py`、`group_conversation.py`、`conversation_utils.py` | `backend-synth-complete` 先后发送两次；发送时只 `gather(task_list)`，没有等 `TTSTaskManager` 的 ordered sender，播放端可能在音频真正发出前就回执 | 删除两处提前发送；`finalize_conversation_turn` 先 `wait_idle(timeout=15)` 再发唯一一次；新增 `tests/test_conversation_turn.py` |
| L2 | `live/runtime.py` `start_bilibili` | 把同步 `self.ingest` 直接传给 collector；collector `await on_event(event)` 时得到 bool，B 站采集永远不进入队列 | 增加 async `forward()` 包装；新增 collector 回调回归测试 |
| L3 | `routes.py` `/live-ws` | `live_active=True` 后 `await session.start()` 失败不会复位，后续连接永远被拒；`session.stop()` 异常也会留下标志 | `session.start` 纳入 `try/finally`，`finally` 中 `stop` 后再复位；启动失败向客户端发 `error` |
| L4 | `live/runtime.py` `LiveSession.stop` | 只取消 proactive/collector/director，不取消 `LiveBrowserOutput` 的 TTS 任务；断线后 sender 任务和音频任务泄漏；部分 start 失败时 store 场次不结束 | 新增 `on_close` 回调和 `LiveBrowserOutput.close()`；用 `_store_started` 保证 store 只结束一次；`stop` 在未 running 但 store 已开始时仍清理 |
| L5 | `live/director.py` | 队列只在头部取同优先级事件，礼物会排在大量聊天之后；队列满时直接丢新事件，与设计“淘汰最低优先级较旧项”不一致 | 每次从全队列选最高优先级 band，band 内 FIFO；满时淘汰最低优先级 band 的第一项（较旧）；新增优先级/淘汰测试 |
| L6 | `live/store.py`、`live/runtime.py` | `append_events` 每个事件开一次 SQLite 连接/提交；`live_chat` 只有 AI 行，recap 无法还原观众侧对话 | 一个批次一个事务；`_handle_batch` 为有事件的批次写 user 行；新增持久化测试 |
| L7 | `live/events.py` | C0/C1 控制字符、bidi 覆盖、零宽字符可进入内容/名称/ID，可能伪造提示词标签 | 统一 `_clean_text` 清洗 id/platform/content/name/gift name |
| L8 | `live/runtime.py` `start` | `load_rundown(auto_begin=True)` 后 status 已是 running，`start()` 再次 `begin()` 会抛错 | 仅当 rundown 非 running 时 `begin()`；新增自动开始回归测试 |
| L9 | `routes.py` `bilibili-event` | 非法 B 站 payload 的 `RoomEvent` 校验异常未捕获，会打断 `/live-ws` 循环 | 与 `room-event` 一致，捕获并返回 `error` |
| L10 | `live/output.py` | session 停止时没有取消 TTS 任务 | `close()` 取消 `task_list` 并 `clear()` sender/queue |

### 3.2 跨仓 Node 联动的修复

这些在同工作区 `ailive` Node 原型中修复，影响 `/proxy-ws` 对接：

| ID | 位置 | 现象 | 修复 |
|---|---|---|---|
| N1 | `src/olv-proxy.js` | `chain-end` 没有 `turn.started` 校验；reset/超时后，旧的 `chain-end` 会完成新回合 | 只有 `turn.started === true` 才完成；迟到 `chain-end` 忽略；新增回归 |
| N2 | `src/olv-proxy.js` | OLV `error` 被当成终止；但 OLV agent-stream error 后仍会 finalize/`chain-end` | `error` 改为建议性 `lastError`，不立即失败回合 |
| N3 | `src/olv-proxy.js` | 90 秒计时从 `send()` 固定起算，长 TTS/播放会被误判超时；`resetBlocked()` 只清本地，OLV `conversation_active` 仍为 true | 每个回合 activity 刷新计时；`interrupt()` 成功后清 `blocked/lastError`；`runtime.resetOlv()` 先尝试带 heard text 的 `interrupt-signal` 再本地 reset |
| N4 | `public/olv-player.js` | 播放完成回执没有 `turn started` 门槛，`backend-synth-complete` 可能提前 ack；重连/旧 socket/旧 audio 回调会污染新回合 | 增加 `turnStarted` + 120ms quiet debounce；socket/audio generation guard；断开发送 `interrupt-signal` |
| N5 | `src/olv-proxy.js`、`src/core/events.js` | JSON 转义不等于提示词边界；控制/双向字符可伪造标签 | 增加随机 boundary；清洗 C0/C1/bidi/零宽字符 |
| N6 | `src/olv-proxy.js` | `JSON.parse('null')` 后访问 `message.type` 抛未捕获异常 | 校验非 null 对象；新增测试 |

### 3.3 明确未改的安全/部署项

- `/proxy-ws` 没有认证/Origin 校验，CORS 为 `*`；本机恶意网页可注入文本、打断并接收全部音频。部署必须绑定 localhost 或加 token/反向代理。
- 播放端“唯一”仍只依赖前端 BroadcastChannel 和操作规范；OLV 没有服务端播放端注册/锁。
- `frontend-playback-complete` 没有 `request_id`/turn id；任何代理客户端的裸回执都能满足当前 waiter。
- 输出内容审核（TTS 前）没有单独的敏感词/安全模型层。

## 4. 验证

### 4.1 OLV 全量测试

```text
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
Ran 66 tests
OK
```

覆盖：直播事件契约、Director 批处理/优先级/淘汰、Rundown、Proactive、Context/Emotion/Memory、Store/Recap、Planner/Replyer、Personality sanitizer、`LiveStreamerAgent` 包装、B 站 official mapping/config、`finalize_conversation_turn` 等。

### 4.2 Node 原型回归

```text
npm test
# tests 54
# pass 54
# fail 0
```

覆盖：统一事件契约、配置、生命周期、JSONL 场次记录、Director 优先级/过期/关闭、模型失败、OLV 编码/回合/超时/重连/打断/阻塞恢复、B 站映射、运行时控制。

### 4.3 本机冒烟

- `npm start`（Node 原型，local 模式）能读取 `config/framework.json`，`/api/status` 返回 `rundown.status=armed`；`start` 后进入 `running`、当前环节 `opening`；`pause/resume/stop` 正常。
- OLV 不在线时 Node 服务仍监听，`upstream.connected=false`，`/api/olv/reset` 可清除本地错误；不会在监听前崩溃。
- 以上都不是真实 OLV 音频/TTS/Live2D/B 站验收。

## 5. 尚未验收 / 仍需处理

1. **真实 OLV/TTS/Live2D/B 站/OBS 联调**：本轮只做源码核对、单元测试和本机进程冒烟。
2. **单播放端强制**：必须加服务端播放端注册/锁或专用播放 token；否则多端会重复播放或错误回执。
3. **回合 ID/回执超时**：建议在 `text-input`、`chain-start/end`、`backend-synth-complete`、`frontend-playback-complete` 中加 `request_id`；`wait_for_response` 加 timeout；`proxy_message_queue` 加队列上限/取消语义。
4. **播放端失败恢复**：音频解码失败、浏览器自动播放拒绝、socket 断开仍可能让 OLV 永久等待；需要显式中断/重试/服务端 ack 超时。
5. **`/proxy-ws` 认证和 Origin 白名单**：上线前必须做。
6. **提示词注入与输出审核**：当前清洗控制字符和 JSON boundary，不等于内容安全验收；上线前需要独立审核层/敏感词库。
7. **`LiveSession` 未 running 但 `store` 已开始、`on_close` 异常等边界**：已做清理和日志，但仍建议为 `platform/collector` 增加有界停止超时。
8. **`LiveStore`/`memory` 增长**：`live_events`、`memory_items`、`ViewerRegistry` 尚未设置保留期/容量上限；生产前要定保留策略。
9. **`LiveBrowserOutput.end_batch` 不等待实际播放完成**：这是直播继续推进的设计选择，但会导致“已 end 的文本仍在播放”；OBS 录制/字幕时间轴需要按实际播放端校验。
10. **两份对话历史并存**：`LiveStreamerAgent` 仍使用 `BasicMemoryAgent._memory` 作为模型上下文，`LiveStore.live_chat` 又保存 user/assistant 行供 recap/审计。这不是两份台词源，但若严格按最终设计“LiveStore 单一事实源”，需要明确哪一份可重建、哪一份仅作缓存，并设置一致性/清理策略。

## 6. 变更文件（本工作树）

```text
src/open_llm_vtuber/conversations/conversation_utils.py
src/open_llm_vtuber/conversations/single_conversation.py
src/open_llm_vtuber/conversations/group_conversation.py
src/open_llm_vtuber/live/director.py
src/open_llm_vtuber/live/events.py
src/open_llm_vtuber/live/output.py
src/open_llm_vtuber/live/runtime.py
src/open_llm_vtuber/live/store.py
src/open_llm_vtuber/routes.py
tests/test_conversation_turn.py（新增）
tests/test_live_core.py
```

工作区 `ailive` 侧另有 Node 原型修复，见 `docs/代码审查与修复_OLV导播原型.md`。
