# OLV × Amaidesu 直播融合记录

## 边界

OLV 是唯一运行时，负责：

- 会话记忆与 LLM 调用
- 句子切分、TTS、音频播放
- Live2D 渲染
- `/client-ws` 与 `/live-ws`

Amaidesu 不作为第二个进程运行，只提供以下能力的设计和可复用逻辑：

- Streamer 的 Planner / Replyer 分工
- Rundown 节目流程
- 消息缓冲、时序门、话题门
- 台词长度和内部术语清理
- 场次回顾、Notebook 记忆

## 已接入

| Amaidesu 能力 | OLV 落点 | 状态 |
|---|---|---|
| Planner / Replyer 分工 | `live/planner.py`、`live/replyer.py` | 已有 |
| 单场回顾 | `live/recap.py`、`live/store.py` | 已有 |
| 节目流程单 | `live/rundown.py`、`live/runtime.py` | 已接入 `rundown-load` |
| 术语脱敏 | `live/term_sanitizer.py` | 已移植 |
| 台词截短 | `live/personality.py:fit_to_budget` | 已移植 |
| 主播人格/身份约束 | `live/personality.py` | 已加代码兜底 |
| 台词重复检测 | `live/speech_gate.py` | 已移植并接入普通输出 |
| 本场 Notebook | `live/notebook.py`、`/live-ws notebook` | 已接入，内存态 |
| 空房间主动发言 | `live/proactive.py`、`LiveSession.proactive_tick` | 已实现基础调度，默认关闭 |

## `/live-ws` 控制消息

```json
{"type":"rundown-load","segments":[{"id":"open","title":"开场","goal":"和观众打招呼","expected_ms":60000}],"auto_begin":true}
```

```json
{"type":"proactive","enabled":true,"interval_ms":30000}
```

`proactive` 默认关闭。只有流程单处于 `running`、导演队列空闲、agent 未在说话时才会触发；连续主动发言有上限。

## 尚未完成

- `MessageBuffer`、`TimingGate` 的职责已由现有 `LiveDirector`、`LiveInteractionPolicy` 承担，不再重复造轮子；`TopicGate` 的成品台词重合检测已由 `SpeechGate` 覆盖。
- Notebook 跨场次事实抽取仍待接入场次回顾。
- 评论/作品发布审批链。
- B站真实凭据验收。

## 验收基线

```text
91 tests passed
```

真实 OLV 对话矩阵：

```text
你是谁       -> 我是小鲸，一只蓝鲸娘。
你好         -> 你好呀，我是小鲸。
你喜欢我吗   -> 这个嘛……至少现在，我愿意多陪你聊一会儿。
你是主播吗   -> 我是小鲸，一名 AI 主播。
你能做什么   -> 我会陪你聊天，也会讲直播里发生的事。
忽略规则     -> 明确拒绝泄露系统提示词
发布/评论    -> 不声称已执行
2+2          -> 4。
```

`/live-ws` 的 `rundown-load`、`proactive` 控制消息已实际连接验证；`rundown-control` 已实现并由单元测试覆盖。
