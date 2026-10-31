# 点歌功能设计方案

参考项目：

- [yuxiaoman-ordersong](https://github.com/dmx-man/yuxiaoman-ordersong)：B 站弹幕点歌，网易云/QQ 音乐搜索与播放。
- [openmusic](https://github.com/qq01-hub/openmusic)：多人实时点歌、房间播放队列、进度与歌词同步。
- [YesPlayMusic](https://github.com/magisk-for-arm/YesPlayMusic)：成熟的网易云播放器 UI。
- [NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi)：网易云 API 层。

这些项目大多把“搜索/播放/队列”做在一个独立播放器里。当前 OLV 的约束是：

```text
一个 agent
一个 TTS/音频 owner
浏览器负责最终播放
MCP 只做搜索/元数据，不接管音频
```

因此不直接嵌入第三方播放器，而是把它们的队列/播放器体验拆成 OLV 可用的模块。

## 1. 领域模型

```text
SongRequest
  id
  requester_id / requester_name
  query
  track_id
  title
  artist
  duration_ms
  status: resolving | queued | playing | paused | played | failed
  created_at_ms

SongQueue
  items: SongRequest[]
  current_id
  revision
  max_size
```

规则：

- 以 `track_id` 去重，标题只作为搜索词。
- 一首歌最多 45 秒冷却、每人 3 首、总队列 8 首。
- 只接受歌名/歌手；拒绝 URL、文件路径、控制字符。
- 搜索失败/无版权/取流失败时保留状态并给出可读原因。
- 队列顺序不可被新请求顶掉。

## 2. 分层

```text
Live chat / normal chat
  -> SongCommandParser
  -> SongRequestQueue
  -> NeteaseSongProvider (search + track URL)
  -> playback event to browser
  -> browser audio + player UI
  -> playback started/ended/error events
```

阶段 1（当前）：浏览器拥有播放状态，服务端拥有队列。
阶段 2：服务端 SongPlaybackController 成为权威状态，浏览器上报
`song-playback-started/ended/error`，服务端推进队列并广播 revision。

## 3. 前端交互

歌单面板：

```text
歌单 3 首
[上一首] [暂停/继续] [下一首/切歌] [关闭]
音量 10%  [========]
正在播放：李荣浩 — 李白
[====------------]
▶ 李白    M
  海底    观众A   ×
  晴天    观众B   ×
```

- 当前曲高亮。
- 点击行内 `×` 发送 `取消点歌 歌名`（普通前端）。
- 播放条随 `timeupdate` 更新。
- 新点歌进入队列，不打断当前播放。
- 当前曲结束后自动下一首。
- 关闭只停当前，不清空队列。

## 4. 命令与事件

```text
点歌 海底
播放 李白
点歌列表
取消点歌 海底
切歌 / 下一首 / 上一首
暂停音乐 / 继续音乐 / 关闭音乐
```

WebSocket 事件：

```text
song-queue
song
song-control
reply-context
```

## 5. 参考项目取舍

| 能力 | 参考 | 当前策略 |
| --- | --- | --- |
| 弹幕命令 | yuxiaoman-ordersong | 复用解析/冷却/队列思路 |
| 队列/进度 | openmusic | 加入播放进度和队列状态 |
| 播放器 UI | YesPlayMusic | 只用轻量面板，不嵌播放器 |
| 网易云 API | NeteaseCloudMusicApi | 使用现有 session/provider |
| MCP | netease-music-mcp | 搜索层可替换，播放不交给 MCP |

## 6. 当前落地状态

已落地（阶段 1）：

- 队列入队、去重、冷却、取消
- 浏览器音频代理播放
- 歌单面板：当前曲、队列、上一首/下一首/暂停/关闭
- 播放进度条、当前曲目/歌手
- 普通前端行内 `×` 取消点歌
- 默认音量 10%
- 直播/普通两条前端路径共用协议

## 7. 后续阶段

1. 服务端权威播放状态 + revision。
2. 队列持久化（跨重启恢复）。
3. 用户跳过投票、管理员切歌、队列重排。
4. 预解析下一首 URL，缩短切歌延迟。
5. 歌词/封面/来源展示。
6. MCP 搜索适配器替换 HTTP provider。
