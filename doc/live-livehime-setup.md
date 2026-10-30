# 直播姬（Livehime）接入静态舞台页

当前阶段用于验证 **画面捕获和声音链路**，还不是完整 OLV Live2D 页面。

## 1. 启动静态舞台页

在仓库根目录启动本地静态服务器：

```powershell
cd reference/Open-LLM-VTuber
python -m http.server 8765 --bind 127.0.0.1
```

浏览器打开：

```text
http://127.0.0.1:8765/web_tool/live-stage.html
```

页面是绿色背景 + 原创蓝鲸占位图。绿色背景用于直播姬的色度键；如果直播姬没有色度键，改用“窗口捕获”并裁切到角色区域。

## 2. 直播姬添加画面

在直播姬的“添加素材/来源”中选择以下之一：

1. **浏览器源**：直接填上面的 URL（如果当前版本支持）；
2. **窗口捕获**：选择打开该 URL 的浏览器窗口；
3. **显示器捕获**：最后手段，裁切到舞台区域。

如果窗口捕获把绿色背景带进画面：

- 优先使用直播姬的“色度键/绿幕”功能，选择绿色；
- 没有色度键时，用 OBS 做色度键后输出虚拟摄像头，再让直播姬捕获虚拟摄像头。

## 3. 接入声音

OLV 的 TTS 最终会在浏览器/播放端出声。直播姬需要捕获这个声音，不能把系统里所有声音都当成主播声音。

推荐：

```text
浏览器音频输出 -> VB-Audio Virtual Cable Input
直播姬麦克风/音频输入 -> CABLE Output
```

或者使用直播姬的“系统声音”捕获，但需要接受会把其他系统声音一起推流。

不要同时让 OLV 和直播姬各自播放一份 TTS，否则会产生双声/回声。

## 4. 弹幕仍然走开放平台

直播姬只负责推流，不提供供第三方程序读取的弹幕 API。弹幕/礼物/SC 必须使用：

- `BILI_ID_CODE`
- `BILI_APP_ID`
- `BILI_ACCESS_KEY`
- `BILI_ACCESS_KEY_SECRET`

先运行：

```powershell
uv run python scripts/check_bilibili_official.py --seconds 60
```

验证单房间事件后再接 `/live-ws`。

## 5. 当前限制

- `web_tool/live-stage.html` 是静态占位页，不会说话，也不会根据弹幕变化；
- OLV `frontend/` 子模块未初始化，完整 Live2D 页面还需要初始化前端；
- 直播姬没有稳定的官方远程控制 API；自动切换场景/显示隐藏建议改用 OBS + `obs-websocket`。
