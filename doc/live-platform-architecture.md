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
POST   /api/dashboard/login
POST   /api/dashboard/logout
GET    /api/dashboard/session
GET    /api/dashboard/collector
GET    /api/dashboard/tts/status
POST   /api/dashboard/tts/start
POST   /api/dashboard/tts/stop
```

### Dashboard 登录鉴权

- 令牌文件：`data/dashboard-token.txt`
- 可用 `DASHBOARD_TOKEN` 环境变量覆盖
- 登录后写入 HttpOnly cookie `dsh_dashboard`
- 除 `/api/dashboard/login` 外，所有 `/api/dashboard/*` API 都要求登录
- 静态页面 `/dashboard/` 可打开，但数据接口返回 401，页面会显示登录框

### 房间空闲自动回收

后台任务 `dashboard-room-reaper` 每 5 分钟检查：

```text
live / connecting: 不处理
closed / error:   5 分钟后删除
stopped:          30 分钟后删除
```

可用环境变量控制：

```text
DASHBOARD_REAPER_SEC=300
DASHBOARD_REAPER_ENABLED=0
```

### TTS 资源生命周期

TTS 是当前最大内存项，已提供 Dashboard 控制：

```text
POST /api/dashboard/tts/start
POST /api/dashboard/tts/stop
GET  /api/dashboard/tts/status
```

启动脚本：

```text
scripts/start_tts.ps1
```

本机实测：

```text
半精度 is_half=true 在 GTX 1060 上 RSS 从约 0.9GB 涨到约 4.1GB，
已回退 full precision；不要在生产用半精度。
full precision 首次合成后 RSS 约 2.7GB，空闲时停止 TTS 可释放。
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

## 4. Phase 2：Python 原生 Douyin collector（已落地）

已新增：

```text
src/open_llm_vtuber/live/collectors/douyin_native.py
scripts/native_douyin_collector.py
scripts/douyin_sign.mjs
tests/test_douyin_native.py
```

数据面全部在 Python：

```text
cookie 链
-> web_rid 解析 / 开播状态
-> X-Bogus 签名
-> WebSocket 握手 + 5s 心跳 + ack
-> PushFrame / gzip / Response / protobuf 解码
-> Chat / Gift / Member -> RoomEvent
-> OLV /live-ws
```

唯一的 Node 使用是 `scripts/douyin_sign.mjs`：

```text
Node 只做 signature oracle，不是数据面；
不启动 dyhub server，不连接 dyhub HTTP/WS，不使用 dyhub_bridge.py。
```

切换方式：

```text
DASHBOARD_COLLECTOR=native
```

Dashboard 的“连接房间”会启动：

```text
python scripts/native_douyin_collector.py --room-id <web_rid>
```

或直接运行：

```text
python scripts/native_douyin_collector.py \
  --room-id 615688226618 \
  --olv-url ws://127.0.0.1:12393/live-ws
```

离线验证：

```text
11 tests passed: protobuf 解码 / chat / gift / member /
room id / room status / 事件映射 / 不泄露 cookie
```

实测注意：当前测试房间在原生探测时页面返回“直播间已结束”，
未能完成真实直播事件验证；上线前应先用一个正在直播的房间灰度。

> 不建议直接删除 dyhub。抖音签名、protobuf、礼物字段和风控变化快，先保留 `DASHBOARD_COLLECTOR=dyhub` 可回退。

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

- Dashboard 控制面 + 登录鉴权
- dyhub 房间/Cookie/清理/事件/歌曲状态聚合
- Python 原生 Douyin collector（数据面 Python，Node 仅签名）
- 房间空闲自动回收
- TTS 启停生命周期
- OLV LiveSession registry
- 资源清理入口

仍待验证 / 后续：

- 原生 collector 真实直播灰度（当前测试房间页面返回已结束）
- 多采集器自动故障切换
- 进程级内存图表（当前为 OLV 当前进程 RSS）
- 原生 collector 的礼物事件需要登录 Cookie
