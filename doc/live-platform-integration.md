# B站 / 抖音直播接入说明

## 统一架构

```text
B站官方 Open Platform collector ─┐
                                  ├─> RoomEvent
抖音官方/采集器归一化事件 ────────┘
                                         │
                                         ▼
                                  LiveSession / LiveDirector
                                         │
                                         ▼
                                  LiveStreamerAgent
                                         │
                                         ▼
                                  OLV TTS + Live2D
```

平台只负责“产生事件”，OLV 仍然是唯一的 Agent、TTS、音频和 Live2D 运行时。不要把 Amaidesu、dyhub 或另一个 TTS 进程作为第二套主播运行时。

## B站：官方直播开放平台

仓库已有：

```text
src/open_llm_vtuber/live/collectors/bilibili_official/
src/open_llm_vtuber/live/bilibili.py
```

### 1. 开通

1. 在 [B站直播开放平台](https://open-live.bilibili.com/) 注册开发者/主播身份。
2. 创建应用并申请直播开放权限。
3. 获取：

```text
BILI_ID_CODE              主播身份码
BILI_APP_ID               应用 ID
BILI_ACCESS_KEY           Access Key
BILI_ACCESS_KEY_SECRET    Access Key Secret
```

这些值只放环境变量，不写入 `conf.yaml`、日志或 Git。

### 2. 启动采集

通过 `/live-ws` 发送：

```json
{"type":"bilibili-start","config":{"from_env":true}}
```

collector 会连接开放平台 WebSocket，把官方消息转换为 `RoomEvent`：

```text
LIVE_OPEN_PLATFORM_DM          -> chat
LIVE_OPEN_PLATFORM_SEND_GIFT   -> gift
LIVE_OPEN_PLATFORM_SUPER_CHAT  -> gift
LIVE_OPEN_PLATFORM_GUARD       -> gift
```

进入房间、在线人数变化不会触发主播说话。

### 3. 验收条件

- 有真实开放平台应用和已授权直播间
- `BILI_ID_CODE`、`BILI_APP_ID`、Access Key/Secret 齐全
- 能看到真实弹幕、礼物、SC、上舰事件进入 `LiveDirector`

当前没有这些凭据，因此不能宣称 B站真实采集已验收。

## 抖音：先分清官方与非官方

### 路线 A：官方「直播小玩法」OpenAPI（合规优先）

抖音对普通直播间弹幕没有像 B站这样简单的公开 WebSocket 接口。官方路线是：

1. 注册[抖音开放平台](https://developer.open-douyin.com/)开发者。
2. 创建“直播小玩法”应用，提交玩法和演示材料。
3. 审核通过后申请测试白名单。
4. 使用官方 OpenAPI/测试工具接收：

```text
WebcastChatMessage      评论/弹幕
WebcastGiftMessage      礼物
WebcastLikeMessage      点赞
WebcastRoomUserSeqMessage 入场/在线
```

5. 通过 WebSocket/回调把事件送进本项目。

这条路合规、稳定，但门槛高：需要应用审核、测试和上线流程；不是“填一个房间号就能连”。

### 路线 B：外部采集器中转（原型/自用）

可使用社区采集器：

- [dyhub](https://github.com/ymstar/dyhub)：浏览器/CDP 或轻量直连，支持 WS/SSE/Webhook。
- [DouyinLiveJava](https://github.com/lulajax/DouyinLiveJava)：Java WebSocket + protobuf。
- `douyin-live-go`：Go 轻量采集器。

让采集器只做“取弹幕”，然后转成统一事件发给 OLV：

```json
{
  "type": "douyin-event",
  "payload": {
    "method": "WebcastChatMessage",
    "data": {
      "message_id": "m1",
      "room_id": "123",
      "create_time": 1700000000,
      "content": "主播你好",
      "user": {"id": "u1", "nickname": "小鱼"}
    }
  }
}
```

或者直接使用已归一化的 `room-event`：

```json
{
  "type": "room-event",
  "event": {
    "id": "douyin-1",
    "type": "chat",
    "platform": "douyin",
    "room_id": "123",
    "received_at_ms": 1700000000000,
    "user": {"id": "u1", "name": "小鱼"},
    "content": "主播你好"
  }
}
```

`douyin-event` 已支持：

```text
WebcastChatMessage / chat / danmaku / comment -> chat
WebcastGiftMessage / gift                    -> gift
WebcastLikeMessage / like                    -> 忽略（不触发说话）
WebcastRoomUserSeqMessage / enter            -> 忽略
```

## 明确不做的事

- 不把抖音 WebSocket 的 `signature`、`ttwid`、protobuf 逆向代码直接塞进 OLV 主进程。
- 不承诺非官方协议长期稳定；平台改协议会导致采集中断。
- 不把 B站/抖音 cookie、Access Key、ttwid 写进仓库。
- 不用弹幕直接触发评论、发布、私信等写操作；这些必须走官方授权和人工审核。
- 不把 dyhub 或采集器当成第二个 Agent；它只产生事件。

## 下一步需要你提供

B站：

```text
BILI_ID_CODE
BILI_APP_ID
BILI_ACCESS_KEY
BILI_ACCESS_KEY_SECRET
真实直播间
```

抖音：

```text
你是否有已审核的直播小玩法应用？
还是只做个人非商用原型、允许使用外部采集器？
```
