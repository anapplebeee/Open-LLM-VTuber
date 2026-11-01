# live-view 前端渲染与直播页缺陷报告

**测试日期：** 2026-11-01  
**测试对象：** `http://127.0.0.1:12393/live-view.html?v=45&subx=0&suby=0`  
**当前直播：** `/live-ws` 已有一个活动会话（新连接返回 `another live session is already active`）  
**运行环境：**

```text
OLV server PID: 14968
浏览器：Microsoft Edge 156.0.4314.58（headless，CDP 9224）
模型：deepseek
角色配置：小鲸 / xiaojing_001
测试视口：1920×1080、1280×720、390×844
```

本次只做测试和记录，未修改前端代码。

## 1. 测试结论

页面骨架能加载，Live2D 模型能渲染，WebSocket 能拿到正确的小鲸配置，但**首屏身份文案存在用户可见缺陷**。字幕覆盖人物已经由用户确认为设计，不计入缺陷。当前状态仍不建议直接用于正式直播或 OBS 录制，需先修首屏文案。

```text
HTTP /live-view.html                     200
effects.js / main JS / main CSS          200
/live-view-ws 初始 set-model-and-conf    通过
Canvas 1920×1080                         通过
Live2D 模型可见                          通过
首屏人物身份文案                         失败
启动网络请求                             有 404
Live2D 重载/卸载                         有 console 异常
```

## 2. 缺陷列表

### BUG-LIVE-001（P0）首屏显示通用英文 VTuber 文案，不是小鲸

**复现步骤：**

1. 打开 `live-view.html`；
2. 不发送任何弹幕或直播事件；
3. 等待 8 秒；
4. 观察底部字幕。

**实际结果：**

```text
Hi, I'm some random AI VTuber. Who the hell are ya?
Ahh, you must be amazed by my awesomeness, right? right?
```

**期望结果：**

- 显示小鲸的短句，例如“你好呀，我是小鲸。”；或
- 在第一条真实直播输出前隐藏字幕，不显示示例文案。

**证据：**

- 1920×1080：`doc/qa/live-view-1920x1080.png`
- 1280×720：`doc/qa/live-view-1280x720.png`
- 390×844：`doc/qa/live-view-390x844.png`
- DOM `bodyText` 包含上述英文；
- 来源是前端构建产物中的 `DEFAULT_SUBTITLE`，不是 `/live-view-ws` 的配置：
  - `frontend/assets/main-CKgUHFa9.js` 中 `DEFAULT_SUBTITLE={text:"Hi, I'm some random AI VTuber..."}`；
  - `Live2D` 后端实际返回的 `conf_name` 是 `小鲸`，说明配置正确，是前端默认文案覆盖了首屏。

**影响：**

首屏直接破坏小鲸人格；如果 OBS 在第一条语音前开始录制，观众先看到的是英文通用助手，不是角色。

---

### 设计确认（非缺陷）：字幕覆盖人物

用户确认：**字幕层级位于人物上方、覆盖人物是设计意图**，不作为 bug。

保留 `doc/qa/live-view-1280x720.png` 和 `doc/qa/live-view-390x844.png` 作为该设计的视觉基线，不再把“字幕遮挡人物”计入失败项。后续只验证字幕内容是否正确、是否在安全区域内可读。

---

### BUG-LIVE-003（P1）启动时请求 `/undefined/undefined.model3.json` 并 404

**复现步骤：**

1. 新开 live-view 页面；
2. 打开 DevTools Network/Console；
3. 观察模型初始化请求。

**实际结果：**

```text
GET /undefined/undefined.model3.json 404
```

随后才请求正确的：

```text
GET /live2d-models/deepseek/deepseek.model3.json?v=20261031b 200
```

**影响：**

模型最终能出现，但初始化存在无效请求和竞态；在慢机器、OBS CEF 或缓存异常时可能出现首帧空白/模型延迟。

**证据：** CDP `Log.entryAdded`，来源 `network`。

---

### BUG-LIVE-004（P1）刷新/切换页面时 Live2D 卸载抛异常

**复现步骤：**

1. 打开 live-view；
2. 刷新页面或通过 CDP 重新导航；
3. 观察 Console。

**实际结果：**

```text
TypeError: Cannot read properties of null (reading 'release')
    at LAppView.release
TypeError: Cannot read properties of null (reading 'getArray')
    at LAppLive2DManager.setViewMatrix
```

**影响：**

刷新、重连或页面切换时可能残留渲染循环/空引用，严重时导致模型不显示或 WebGL 上下文异常。

---

### BUG-LIVE-005（P2）启动阶段反复出现 `Canvas is null, skipping resize`

**实际结果：**

初始化期间 console 多次出现：

```text
Canvas is null, skipping resize
```

最终 Canvas 在 1920、1280、390 视口下都能拿到尺寸，但初始化阶段存在明显的 DOM/组件时序问题。

**影响：**

当前不一定造成可见故障，但会增加首帧尺寸错误、窗口 resize 后模型缩放不正确的风险。

---

### BUG-LIVE-006（P2）背景音乐候选资源 404

**实际结果：**

```text
GET /bg/jietuo-pulizi.wav?v=1  404
GET /bg/jietuo-pulizi.mp3?v=1  404
GET /bg/cozy-cute-bgm.wav?v=1  200
```

`effects.js` 的 fallback 列表同时包含前两个不存在的文件；虽然最终 fallback 文件可用，但每次启动都会产生两条 404 和控制台噪声。

**影响：**

如果部署环境没有 `cozy-cute-bgm.wav`，背景音乐功能会静默失败；当前属于低严重度资源卫生问题。

---

### BUG-LIVE-007（P2）`reply-context` 消息被主应用标记为 Unknown

**实际结果：**

页面日志出现：

```text
Unknown message type: reply-context
```

`effects.js` 自己监听并处理了 `reply-context`，但构建产物的通用 WebSocket handler 仍打印未知类型。

**影响：**

目前字幕/回复引用是否实际生效尚未完成端到端验证；至少会产生误导性错误日志。

---

### 移动端观察（非缺陷，待设计确认）

390×844 下人物按竖屏视口放大，底部构图与横屏不同。字幕覆盖人物属于已确认设计；如果后续要做手机竖屏直播，再单独确认人物裁切和字幕安全区，不计入当前 bug。

---

### BUG-LIVE-009（P3）页面标题仍是通用产品名

**实际结果：**

```text
Open-LLM-Vtuber Live View
```

**期望：**

包含“小鲸”角色名，便于 OBS 窗口/浏览器标签识别。

**影响：** 纯展示/运维可读性问题。

## 3. 通过的测试

### HTTP 与静态资源

```text
/live-view.html                         200
/effects.js?v=49                        200 application/javascript
/assets/main-CKgUHFa9.js                200 application/javascript
/assets/main-QEkl09-0.css               200 text/css
/live2d-models/info                     200，count=1，deepseek
```

### WebSocket

```text
/live-view-ws 首帧:
  type=set-model-and-conf
  conf_name=小鲸
  conf_uid=xiaojing_001
  model=deepseek
  model.url=/live2d-models/deepseek/deepseek.model3.json?v=20261031b

heartbeat -> heartbeat-ack
/live-ws -> another live session is already active（当前直播确实在运行）
```

### Canvas

```text
1920×1080: canvas 1920×1080，模型可见
1280×720:  canvas 1280×720，模型可见
390×844:   canvas 390×844，模型可见
```

## 4. 尚未完成的验证

- 没有向当前活动直播注入新的弹幕/礼物事件，避免干扰正在直播的会话；
- 因此尚未验证真实 `reply-context`、真实回复字幕更新、TTS 播放和表情动作在 live-view 中的完整链路；
- 未做 OBS CEF 实机捕获，当前使用 Edge headless；
- 未验证多 viewer 同时连接时的广播/断线清理（现有后端单元测试覆盖了 `LiveOutputHub`，但不是浏览器端）；
- 未做长时间稳定性、内存增长和断线重连测试。

## 5. 建议修复顺序

1. **P0**：live-view 首屏不显示通用英文示例；从配置/角色读取欢迎语，或在首条直播输出前隐藏字幕。
2. **P1**：消除 `/undefined/undefined.model3.json` 初始化请求。
3. **P1**：修 Live2D release/setViewMatrix 空引用异常，验证刷新和重连。
4. **P2**：清理 Canvas 时序警告、BGM 404、`reply-context` 未知消息。
5. **P3**：移动端构图复核、页面标题。

## 6. 测试环境备注

Python 测试套件当前运行 105 项，104 项通过；`test_live_review.ReviewLoggerTest.test_records_danmaku_gift_and_reply` 因 Windows 临时目录 ACL 清理失败报 `PermissionError`，属于测试环境权限问题，不是直播页面功能失败。前端问题均来自上述浏览器/CDP 实测。
