# OLV 直播平台架构与 dyhub 收编方案

## 1. 现状

```text
抖音
  ↓
dyhub（Node 采集器）
  ↓  /live-ws
dyhub_bridge.py（Python 适配）
  ↓  /live-ws
OLV LiveSession
  ├─ Director / Agent / TTS
  ├─ LiveStore（SQLite）
  └─ /live-view-ws
        ↓
    live-view.html
        ↓
    OBS / CABLE
```

当前问题：

- 采集、桥接、直播运行时分散在多个进程和脚本里
- 没有统一控制面，排障要同时看 dyhub、bridge、OLV、OBS
- 房间、Cookie、礼物能力散落在 dyhub 页面
- 每增加一个外部能力就多一个浏览器/脚本
- 资源占用缺少可见性，stopped/error 房间长期保留

## 2. 目标架构

```text
                    ┌─────────────────────────────┐
                    │        OLV Dashboard        │
                    │  控制面 / 观测 / 运维        │
                    └─────────────┬───────────────┘
                                  │ /api/dashboard/*
                    ┌─────────────▼───────────────┐
                    │      OLV Core (FastAPI)      │
                    │  LiveSession / TTS / Store   │
                    └─────────────┬───────────────┘
                                  │ 统一事件入口
                    ┌─────────────▼───────────────┐
                    │      LiveIngress Adapter     │
                    │  当前：dyhub HTTP/WS         │
                    │  后续：Python 原生采集器      │
                    └─────────────┬───────────────┘
                                  │
                    ┌─────────────▼───────────────┐
                    │      Douyin Collector        │
                    │  lightweight / browser       │
                    └─────────────────────────────┘
```

### 设计原则

1. **OLV 是唯一音频和直播会话 owner**
2. **Dashboard 只做控制面，不启动第二个 agent / 浏览器**
3. **采集器放在 LiveIngress 后面，可替换**
4. **所有外部服务通过 OLV API 管理，不再让用户记多个端口**
5. **默认 lightweight collector，只有必须时才开 browser**

## 3. 当前已实现：Phase 1 控制面

### Dashboard

```text
http://127.0.0.1:12393/dashboard/
```

页面包含：

- OLV / TTS / dyhub / bridge 健康状态
- 当前直播会话
- 直播间列表、状态、消息数、Cookie 状态
- 连接房间 / 断开 / 删除
- 清理 stopped / closed / error 房间
- 设置房间 Cookie
- 实时事件流
- 歌曲播放状态和控制
- 可选进程资源占用

### Dashboard API

```text
GET    /api/dashboard/status
GET    /api/dashboard/events
GET    /api/dashboard/rooms
POST   /api/dashboard/rooms/connect
POST   /api/dashboard/rooms/{roomId}/disconnect
DELETE /api/dashboard/rooms/{roomId}
PUT    /api/dashboard/rooms/{roomId}/cookie
POST   /api/dashboard/rooms/cleanup
POST   /api/dashboard/song/control
GET    /api/dashboard/resources
```

### 资源优化

已落地：

- Dashboard 是静态 HTML，不新增 Node/Python 服务，不新增浏览器
- 房间列表支持一键清理 stopped/closed/error 房间
- 查看器只轮询轻量 API，默认 4 秒一次
- 事件只读取 `live-review.jsonl` 尾部，不加载全量
- 歌曲使用已缓存的网易云喜欢歌单，不重复启动 CLI
- 采集继续使用 dyhub lightweight，不启用浏览器内核

后续需要：

- 进程级 RSS/CPU 采集（安装 `psutil` 后自动显示）
- 房间空闲自动 disconnect（按 `lastEventAt`）
- 采集器健康检查失败自动退避，而不是持续重连

## 4. Phase 2：把 dyhub 数据面收进 OLV

当前 Dashboard 已完成 **控制面收编**，但抖音协议采集仍在 dyhub。

下一阶段应新增：

```text
src/open_llm_vtuber/live/ingress/
  base.py          # 统一事件接口
  dyhub_adapter.py # 当前适配器
  douyin_native.py # Python 原生轻量采集器
  normalizer.py    # 抖音消息 -> RoomEvent
```

迁移顺序：

1. 把 dyhub 的 `RoomEvent` 规范化逻辑抽成 OLV `normalizer`
2. 用 Python 实现 lightweight WebSocket 握手、心跳、protobuf 解码
3. 用 `LiveIngress` 接口同时支持 dyhub 和 native
4. Dashboard 选择 `collector=native`
5. 灰度一个测试房间，确认 chat/gift/join 完整
6. 再移除 dyhub 进程和 bridge

> 不建议直接删除 dyhub。抖音签名、protobuf、礼物字段和风控变化快，先保留可回退适配器。

## 5. Phase 3：资源目标

```text
OLV Python        1 个主进程
Collector         1 个 lightweight 采集器
Bridge            1 个（native 后可为 0）
TTS               1 个
Dashboard         0 个额外进程（静态页）
OBS               1 个
```

目标不是把所有东西塞进一个进程，而是：

- 减少重复浏览器
- 减少重复 WebSocket
- 减少无效房间
- 让每个外部依赖都有健康检查和替换点

## 6. 运维入口

```text
Dashboard: http://127.0.0.1:12393/dashboard/
Live view:http://127.0.0.1:12393/live-view.html
Health:   scripts/live_health_check.py
Audio:    scripts/audio_health_check.py
```

## 7. 当前边界

已实现：

- Dashboard 控制面
- dyhub 房间/Cookie/清理/事件/歌曲状态聚合
- OLV LiveSession registry
- 资源清理入口

尚未实现：

- Python 原生抖音采集器
- 房间空闲自动回收
- psutil 进程图表
- 多采集器灰度切换
- Dashboard 登录鉴权（当前仅本机使用）
