# 直播系统优化设计方案（2026-11-01）

## 1. 现状与目标

当前直播链路：

```text
dyhub（弹幕/礼物/进场）
  -> scripts/dyhub_bridge.py
  -> OLV /live-ws
  -> LiveSession（director / agent / TTS）
  -> /live-view-ws
  -> live-view.html（OBS 浏览器源）
  -> OBS 监视设备
  -> VB-CABLE
  -> 抖音直播伴侣
```

当前主要问题不是某个单点功能，而是长期热修造成的职责重叠：

| 问题 | 严重度 | 现状 |
|---|---|---|
| 歌单状态分散在后端队列和前端 songData/songQueue | P0 | 已加自动恢复，但未彻底统一 |
| OBS 自动播放、CABLE 路由不可观测 | P0 | 靠人工听/看电平 |
| effects.js 2568 行，覆盖 Audio/WebSocket、全 DOM 扫描 | P0 | 继续加补丁风险高 |
| live-view-ws 同时承担输出和播放控制 | P1 | 协议边界不清晰 |
| 日志无法区分“生成回复”和“实际听到” | P1 | review 与 live_chat 口径不一致 |
| 测试数据污染生产 live-review | P1 | 已加 1970 防护，历史数据待清理 |
| 礼物依赖 dyhub Cookie，游客态为 0 | P1 | 需要配置，不是代码逻辑 |
| 服务重启依赖人工，无健康检查 | P1 | 需要 supervisor |

目标：

```text
播放状态单一所有者
音频链路可检测
字幕/DOM 不再猴补丁
每个观众事件都有 decision/reason/status
测试语料独立、可回放、不碰生产
服务可健康检查、可恢复
```

## 2. 目标架构

### 2.1 播放域：后端权威状态机

新增概念：

```text
SongSession
  queue[]
  current_entry_id
  current_index
  status: idle | resolving | ready | playing | paused | failed
  failure_count
```

原则：

- 前端只渲染 `song-state`，不自己维护 `songQueue/currentQueueIndex`
- 后端负责预解析下一首、失败重试、自动续播
- 前端只上报 `song-ended` / `song-error`
- 切歌命令统一进入后端控制通道

### 2.2 音频域：AudioBus

统一音频通道：

```text
AudioBus
  tts
  music
  bgm
  alert
```

统一负责：

- 自动播放解锁
- 音量/ducking
- 暂停/恢复
- 音频状态上报
- OBS/CABLE 诊断

### 2.3 前端视图

把 `effects.js` 拆成：

```text
live-audio.js
live-subtitle.js
live-playlist.js
live-effects.js
live-view.jsx
```

字幕、回复上下文、歌单使用稳定组件和 `data-dsh-*` 标记，不再用全 DOM 扫描和 clone 节点镜像。

### 2.4 协议拆分

```text
/live-producer-ws    dyhub -> OLV 输入
/live-output-ws      OLV -> 页面输出
/live-control-ws     页面/运营 -> 播放控制
```

协议带 version、event_id、idempotency key。

### 2.5 可观测性

每个事件至少记录：

```json
{
  "event_id": "...",
  "type": "chat",
  "decision": "allow|suppress|error",
  "reason": "noise|duplicate|planner_silent|...",
  "agent_latency_ms": 0,
  "tts_ms": 0,
  "audio_sent": true,
  "playback_result": "ok|failed|unknown"
}
```

测试日志写临时目录，生产 `live-review.jsonl` 只保留真实直播。

## 3. 分阶段实施

### Phase 1：稳定性（本次实施）

- [x] 弹幕语料采集/回放工具
- [x] 独立测试语料，不写生产 review
- [x] join 60 秒去重
- [x] 歌曲解析失败 `song-unavailable` + 自动跳曲
- [x] 播放目标携带 `query`，避免解析错版本
- [x] 无前缀歌名最小艺术家长度，减少误判
- [x] 1970 测试时间戳拦截
- [x] 历史测试 review 清理工具
- [x] 直播健康检查脚本
- [x] CABLE 音频 RMS 检查脚本（本次实测 RMS 0.005802）
- [x] dyhub 独立房间 Cookie 设置脚本
- [x] Python 111 tests（含歌曲失败、进场去重回归测试）

### Phase 2：播放与前端结构

- [ ] SongSession 后端权威状态机
- [ ] AudioBus
- [ ] effects.js 拆分
- [ ] 字幕正式组件
- [ ] live-view-ws 输出/控制拆分
- [ ] 浏览器端 E2E 测试

### Phase 3：性能与运营

- [ ] 时间/切歌/礼物/简单问候确定性快路径
- [ ] 上下文预算压缩
- [ ] TTS 队列背压与去重
- [ ] supervisor/PID/健康检查
- [ ] 2 小时长稳测试
- [ ] 1080x1920 清晰度和帧率基准

## 4. 验收标准

Phase 1：

```text
新增测试语料不进入 live-review
加入/解析失败不会卡住歌单
重复进场不重复欢迎
健康检查能报告 OLV/TTS/dyhub/礼物能力
108 tests 通过
```

Phase 2：

```text
后端能独立回答“当前第几首/下一首是什么”
前端刷新后队列状态可恢复
OBS 刷新后仍能明确报告音频是否解锁
字幕只有一个稳定节点，无 clone 镜像
```

Phase 3：

```text
P95 回复延迟可观测
连续直播 2 小时无队列卡死
礼物进入后 3 秒内产生感谢
OBS/CABLE 断链能被健康检查发现
```
