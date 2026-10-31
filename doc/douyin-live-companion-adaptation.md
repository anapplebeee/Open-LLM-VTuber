# 抖音直播伴侣适配（小鲸 / OLV）

更新时间：2026-10-31。目标是让 OLV 的 Live2D 画面和 TTS 声音进入抖音直播伴侣，同时明确当前弹幕能力的边界。

## 1. 当前可用入口

OLV 已运行：

```text
http://127.0.0.1:12393/
```

- `/`：真正的 OLV Live2D 前端，用于正式画面
- `/web-tool/live-stage.html`：测试用舞台页，绿色背景，但当前中间是占位图，不是实际 Live2D
- `/live-ws`：直播事件入口，接受 `room-event`、`bilibili-event`、`douyin-event`
- `/live-view-ws`：只读观看端，接收 `/live-ws` 广播的音频和 Live2D 动作
- `/live-view.html`：只读 Live2D 观看页面，自动连接 `/live-view-ws`
- `/client-ws`：普通聊天入口

不要把 `live-stage.html` 的占位图当成最终直播画面。

## 2. 推荐链路

```text
OLV 浏览器页面
  -> OBS Browser Source（或窗口捕获）
  -> OBS 虚拟摄像机
  -> 抖音直播伴侣「添加素材 -> 摄像头 -> OBS Virtual Camera」
```

本机当前未检测到 OBS Studio 安装。可以先直接用直播伴侣的窗口/屏幕捕获测试；需要绿幕、透明背景和稳定虚拟摄像头时，再安装官方 OBS Studio。

抖音官方文档明确支持把 OBS 作为摄像头素材：

- https://streamingtool.douyin.com/docs/guide_yr6qkl09
- OBS 虚拟摄像机：OBS 右下角「启动虚拟摄像机」

这样不需要让直播伴侣直接理解 OLV 的 WebSocket。

## 3. OBS 场景设置

### 竖屏画布

抖音常见直播画布：

```text
1080 × 1920
```

也可以在直播伴侣里按账号要求使用 720×1280；OBS 画布和直播伴侣保持一致。

### 添加 OLV 画面

1. OBS 新建场景。
2. 添加「浏览器」来源。
3. URL：

```text
http://127.0.0.1:12393/
```

4. 宽高设置为 1080 × 1920。
5. 勾选「关闭源时关闭页面」按需设置；首次加载后点一次 OBS 的「交互」，让页面获得音频播放权限。
6. 如果页面 UI 干扰画面，在 OBS 浏览器源的「自定义 CSS」中加：

```css
body * { visibility: hidden !important; }
canvas { visibility: visible !important; }
```

这样只保留 Live2D canvas，隐藏聊天/设置 UI。若模型没有显示，先删掉这段 CSS 确认 canvas 正常加载。

也可以用 OBS 的裁剪/变换把聊天侧栏裁掉，或者使用窗口捕获后裁剪。

### 绿色背景和抠图

OLV 前端的 `body` 是透明背景。可在 OBS 中：

```text
底层：颜色源，纯绿 #00FF00
上层：OLV 浏览器源
```

然后：

```text
OBS 虚拟摄像机 -> 抖音直播伴侣摄像头
-> 摄像头设置 -> 绿幕抠图
-> 自动取色/吸管取绿色
```

抖音官方绿幕说明：

- https://streamingtool.douyin.com/docs/guide_ztb12xj7

如果使用 `live-stage.html`，它本身已有绿色背景，但它当前只是占位图，不是 Live2D 正式画面。

## 4. 声音链路

OLV 的 TTS 声音在浏览器里播放。最简单的测试：

```text
OLV 页面声音输出 -> 系统默认扬声器
抖音直播伴侣 -> 麦克风/扬声器采集 -> 选择对应输出
```

抖音官方说明直播伴侣可以采集：

- 麦克风输入
- 扬声器输出

参考：

- https://streamingtool.douyin.com/docs/guide_4beqpl4o

正式直播建议：

```text
浏览器/OLV 声音 -> VB-Audio Virtual Cable 或 VoiceMeeter
抖音直播伴侣麦克风 -> 对应虚拟声卡
```

这样可以只把 OLV 的 TTS 送给直播间，不把系统提示音、音乐或浏览器其他声音混进去。

首次测试要做：

```text
1. 在 OLV 页面点一次，解除浏览器自动播放限制
2. 发送一句测试文本
3. 在 OBS 混音器确认有声音
4. 在直播伴侣的麦克风/扬声器表确认有电平
5. 用手机小号进直播间听回放
```

## 5. 抖音弹幕接入

当前代码已经有：

```text
src/open_llm_vtuber/live/douyin.py
```

支持把这类归一化事件映射成 `RoomEvent`：

```json
{
  "type": "douyin-event",
  "payload": {
    "method": "WebcastChatMessage",
    "data": {
      "message_id": "m1",
      "room_id": "room-1",
      "content": "主播你好",
      "user": {"id": "u1", "nickname": "小鱼"}
    }
  }
}
```

发送到：

```text
ws://127.0.0.1:12393/live-ws
```

但**抖音直播伴侣本身不提供稳定的本地第三方弹幕 API**。不要直接逆向它的私有协议或抓取客户端流量。要自动化弹幕，需要：

1. 抖音开放平台/直播开放平台的正式权限和凭据；或
2. 明确授权的采集器，把归一化 JSON 送进 `douyin-event`；或
3. 先做人工输入/测试，不宣称自动读弹幕。

当前阶段可以先完成：

```text
画面捕获
声音捕获
手动输入测试
```

再决定是否申请抖音开放平台权限。

## 5.1 dyhub -> OLV 桥接（已实现）

桥接脚本：

```text
scripts/dyhub_bridge.py
```

它只做事件 I/O：连接 dyhub 的 WebSocket，映射 `chat/gift` 为 OLV `RoomEvent`，转发到 `/live-ws`。它不启动第二个 agent、TTS 或 Live2D。

启动顺序：

```text
1. 启动 OLV 主服务
2. 启动 dyhub，连接自有/已授权直播间
3. 启动桥接脚本
4. 打开 /live-view.html 或 OBS 捕获该页面
```

命令示例：

```powershell
.venv\Scripts\python.exe scripts\dyhub_bridge.py `
  --dyhub-url "ws://127.0.0.1:8757/ws?roomId=123456789&types=chat,gift" `
  --olv-url "ws://127.0.0.1:12393/live-ws"
```

如果 URL 里没有 `roomId`，可以加：

```powershell
--room-id 123456789
```

数据流：

```text
dyhub
  -> scripts/dyhub_bridge.py
  -> ws://127.0.0.1:12393/live-ws
  -> LiveSession / OLV agent / TTS / Live2D
  -> LiveOutputHub
  -> ws://127.0.0.1:12393/live-view-ws
  -> live-view.html / OBS
```

观看端：

```text
http://127.0.0.1:12393/live-view.html
```

`live-view.html` 会把浏览器的 `wsUrl` 设置为 `/live-view-ws`。如果之后要回到普通聊天，在 OLV 设置里把 WebSocket URL 改回：

```text
ws://127.0.0.1:12393/client-ws
```

桥接要求 dyhub 正在运行，并且直播间是自有/已授权；dyhub 的浏览器采集依赖 Chrome，轻量内核依赖抖音签名链路，平台更新可能失效。

## 6. 已知限制

- `live-stage.html` 是绿色背景占位页，不是实际 Live2D 舞台
- `live-view.html` 会修改浏览器 `wsUrl`；恢复普通聊天要改回 `/client-ws`
- OLV 主前端包含聊天/设置 UI，正式画面需要裁剪或隐藏 UI
- 抖音直播伴侣的弹幕没有本地公开 API；当前桥接依赖 dyhub/授权采集器
- dyhub 的浏览器采集依赖 Chrome/页面结构，平台更新可能失效
- 直播伴侣使用虚拟摄像头时可能提示“检测到虚拟摄像头”，按平台提示处理
- 1060 6GB 跑 Kokoro 没问题；跑 GPT-SoVITS V2ProPlus 只适合短句试听
- 不要把未授权的真人/角色声音接入直播

## 7. 最小验收

```text
[ ] http://127.0.0.1:12393/ 能打开并显示 Live2D
[ ] OBS 浏览器源能显示模型
[ ] OBS 虚拟摄像机在直播伴侣中可选
[ ] 直播伴侣摄像头画面正常
[ ] 绿幕抠图后背景透明/干净
[ ] OLV 测试语音能在直播伴侣音频表出现
[ ] 手机端能听到 TTS
[ ] 弹幕接入方式明确（人工/官方授权）
```

本机验证记录：

```text
/live-view-ws 初始 set-model-and-conf -> deepseek
/live-ws douyin-event -> /live-view-ws 收到 audio
fake dyhub -> scripts/dyhub_bridge.py -> OLV -> live-view 收到 audio
79 tests passed
```

浏览器画面仍未做自动截图验证；`live-view.html` 的 Live2D canvas 需要在真实浏览器/OBS 中确认。
