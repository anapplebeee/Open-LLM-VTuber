# AI 直播 Agent 市场与 GitHub 实现调研

## 0. 结论先行

市场大致分三类：

```text
AI 陪伴产品：Character.ai / Talkie / 星野 / 猫箱
AI 数字人直播：Fay / Luna AI / 各类数字人直播平台
实时语音 Agent 基础设施：Pipecat / LiveKit Agents / TEN
```

小鲸当前属于第四种：

```text
有 Live2D 形象、有弹幕、有 TTS、有故事和点歌的
事件驱动型 AI 直播 Agent
```

它最值得借鉴的不是“更多模型”，而是这四件事：

1. **什么时候该说话**（Attention）
2. **现在在播什么**（Content Director）
3. **记住观众什么**（Memory）
4. **怎么验证人格没有跑偏**（Eval）

---

## 1. GitHub 项目对照

| 项目 | 核心实现 | 可借鉴 | 不适合直接照搬 |
|---|---|---|---|
| [Pipecat](https://github.com/pipecat-ai/pipecat) | 实时语音/多模态 Agent 的 composable pipeline、多 Agent handoff、structured conversations、metrics | 把“采集→LLM→TTS→输出”拆成可组合 processor；多 Agent 专业分工；结构化对话状态 | 它是语音/WebRTC 优先，直播弹幕是文本事件，整套 transport 不需要搬 |
| [LiveKit Agents](https://github.com/livekit/agents) | AgentSession、语义 turn detection、任务调度、MCP、内置测试和 judge | `AgentSession` 统一会话；语义轮次检测；用 judge 做回归测试 | WebRTC/电话栈很重，当前没有实时语音输入需求 |
| [TEN Agent](https://github.com/TEN-framework/TEN-Agent) | 实时多模态扩展图、VAD/Turn Detection、Live2D 示例、WebRTC/WebSocket | 模块化实时管线；Live2D 表情/口型；VAD/轮次检测 | 引入整套 TEN runtime 成本高 |
| [Fay](https://github.com/xszyou/Fay) | 数字人框架、自动播报、日程式主动对话、意图接口、知识库、记忆、MCP、管理页 | **日程式主动内容**、意图路由、管理控制台、记忆、工具调用、自动播报 | 功能面很宽，直接合并会稀释当前直播链路 |
| [Luna AI](https://github.com/Ikaros-521/AI-Vtuber) | 多直播平台适配、WebUI、TTS/LLM provider、平台事件、冒烟测试 | **平台 adapter registry**、控制台、provider 分层、依赖分组、smoke test | 平台适配和配置面很大，当前只需要抖音 + 少量扩展 |
| [Neuro](https://github.com/kimjammer/Neuro) | realtime STT/TTS、prompter 决定何时回复、模块化 prompt injection、记忆/RAG、控制面板 | **“什么时候说”比“说什么”更重要**；模块按优先级注入 prompt；控制面板 | 依赖本地大模型、Twitch，不适合当前抖音链路 |
| [Mem0](https://github.com/mem0ai/mem0) | 多层级记忆、增量抽取、实体链接、多信号检索 | 观众/房间/角色分层记忆；事实抽取；按人物和话题检索 | 需要向量/LLM 抽取，当前可先做本地轻量版 |

---

## 2. 对当前项目的准确诊断

当前已经有：

```text
LiveDirector       事件批处理 + 优先级
RundownState       线性节目单
ProactiveScheduler 定时主动说话
LiveMemory         带来源的记忆
SpeechGate         重复措辞检测
LiveSession        单一音频 owner
Dashboard          控制面
```

真正缺的是“这些东西之间的统一决策层”：

```text
Director 决定谁先处理
Proactive 决定多久没说话
Rundown 决定现在第几段
SpeechGate 决定这句话重复不重复
Agent 决定怎么回复
```

但没有一个组件统一回答：

```text
现在该不该开口？
该由哪个角色开口？
这句话是回应、推进节目，还是打断？
```

---

## 3. 最值得借鉴的 6 个点

### 3.1 AttentionController：从“来事件就回”变成“先判断”

Neuro 的 `prompter.py` 会综合：

- 观众是否正在说话
- AI 是否正在思考/说话
- 最近一条弹幕距今多久
- 当前是否有新事件
- 当前场景和时间

再决定是否 prompt LLM。

建议在 OLV 新增：

```text
live/attention.py
AttentionSignals:
  last_viewer_event_ms
  pending_gift
  story_active
  current_segment
  silence_ms
  recent_reply_dup
  speech_gate_passed
  chat_rate

AttentionDecision:
  WAIT / REPLY / PROACTIVE / INTERRUPT / HANDOFF
```

与现有模块的关系：

```text
LiveDirector -> 提供候选事件
ProactiveScheduler -> 提供空闲时间
SpeechGate -> 提供重复判定
RundownState -> 提供当前节目段
AttentionController -> 输出最终动作
```

这是当前性价比最高的一步。

### 3.2 ContentDirector：把 Rundown 从“备忘录”变成“节目状态机”

Fay 的“日程式主动对话”和 Pipecat 的 structured conversations 都说明：

```text
节目不是一堆随机弹幕，
而是一段段有目标、有开始、有结束的 segment。
```

当前 `RundownState` 只有线性 segment 和时长。建议增加：

```text
segment.goal
segment.allowed_actions
segment.deadline_ms
segment.fallback_action
segment.transition_condition
```

例如：

```text
故事段：允许 continue / stop / gift interrupt
猜歌段：允许 answer / next / end
收尾段：只允许感谢 / 预告 / 下播
```

这样礼物、弹幕、故事就不会互相抢状态。

### 3.3 Memory：从“存事实”升级为“抽取 + 检索 + 使用”

Mem0 值得借鉴的是：

```text
增量抽取
实体链接
多层级（user / session / agent）
多信号检索
```

当前 OLV 已有：

```text
MemoryItem / MemoryScope / provenance / SQLite
```

下一步不是换数据库，而是补：

1. 每轮结束后抽取 0～2 条“可复用事实”
2. 只在高价值场景注入（老观众、连续话题、承诺）
3. 用 `viewer + topic + recency` 检索，不把所有记忆塞 prompt
4. 明确禁止“假装记得没存过的事”

### 3.4 Specialist Agents：只拆 2～3 个角色，不要一开始做 Agent 军团

Pipecat / LiveKit 的 handoff 值得借鉴，但不要过度。

建议先拆：

```text
HostAgent    当前主播，负责日常弹幕
StoryAgent   长故事、章节、悬念
GameAgent    猜歌、二选一、故事接龙
```

由 `ContentDirector` 决定当前由谁接管，`LiveSession` 仍然只有一个音频 owner。

不要一上来做：

```text
10 个角色 + 自由对话 + 多 Agent 投票
```

那会把延迟和状态复杂度一起放大。

### 3.5 Eval：人格也需要回归测试

LiveKit 的 judge 测试、Pipecat 的 metrics 都在解决同一个问题：

```text
LLM 输出不可重复，不能只靠肉眼觉得“有那味”。
```

建议增加离线回放：

```text
输入：live-review.jsonl 的历史事件
输出：候选回复
评分：人格一致性 / 长度 / 重复度 / 客服腔 / 安全
```

先把“人格跑偏”变成可测指标，再谈迭代 prompt。

### 3.6 平台适配与运维：借鉴 Luna / Fay，但不要复制大而全

Luna AI 的强项是：

```text
多平台 adapter
WebUI 配置
provider 注册表
依赖分组
smoke test
```

Fay 的强项是：

```text
管理控制台
自动播报
意图接口
知识库
工具管理
```

当前 OLV Dashboard 已经有控制面，下一步应补：

```text
节目段控制
记忆查看/删除
人格评测结果
采集器健康与资源
```

而不是再做一个独立后台。

---

## 4. 不建议照搬的东西

- 不要把 OLV 改成 WebRTC/电话 Agent 架构：直播输入是弹幕事件，不是实时语音通话。
- 不要一次性引入 TEN/Pipecat/LiveKit 全栈：会引入第二套 session/音频生命周期。
- 不要做多 Agent 自由辩论：直播需要稳定延迟和明确节目节奏。
- 不要把所有历史记忆塞进 prompt：会变慢、变贵，也容易编造。
- 不要先做 3D/UE5 数字人：当前瓶颈是内容和决策，不是模型精度。

---

## 5. 建议路线图

### P0：1～2 周

- [ ] `AttentionController`：统一回答“现在该不该说”
- [ ] `ContentDirector`：segment goal + allowed actions + fallback
- [ ] Memory 抽取/检索预算，只注入高价值记忆
- [ ] 离线人格评测集（从 live-review 采样）

### P1：3～4 周

- [ ] StoryAgent / GameAgent 两个 specialist
- [ ] 节目段控制进入 Dashboard
- [ ] 礼物/弹幕/故事的统一抢占策略
- [ ] TTS 流式化与更细粒度打断

### P2：后续

- [ ] Python 原生抖音采集器替换 dyhub
- [ ] 多角色音色与 Live2D 动作
- [ ] 直播切片/高光导出
- [ ] 多平台 adapter registry

---

## 6. 一句话总结

小鲸不需要变成“更聪明的聊天机器人”，而需要变成：

```text
知道自己现在在播什么、
知道什么时候该开口、
知道该由哪个角色开口、
并且能被评测和复盘的 AI 直播 Agent。
```
