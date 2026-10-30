# B站直播开放平台凭据获取

本项目使用的是 **B站直播开放平台（OpenLive）**，不是普通直播间的 `SESSDATA` Cookie，也不是直播间房间号。

官方入口：

- 直播开放平台：<https://open-live.bilibili.com/>
- 开发文档：<https://open-live.bilibili.com/document/>
- 应用管理：<https://open-live.bilibili.com/open-manage>
- 主播身份码入口：<https://play-live.bilibili.com>（页面右下角“身份码”）

## 1. 准备账号

1. 使用**要开播的主播账号**登录 B站。
2. 完成 B站账号实名认证；开放平台创建应用通常还要求主体/开发者信息。
3. 确认账号有直播权限，并能进入自己的直播间。

## 2. 创建 OpenLive 应用

1. 打开 <https://open.bilibili.com/> 或 <https://open-live.bilibili.com/>，进入开发者/应用管理。
2. 创建应用，选择直播开放平台/OpenLive 相关能力。
3. 按页面要求填写应用名称、用途、服务器信息等；测试阶段一般也需要先创建应用。
4. 提交后等待审核。审核通过后，在应用详情页取得：
   - `AppId`（项目 ID）
   - `AccessKeyId`（本项目配置名 `access_key`）
   - `AccessKeySecret`（本项目配置名 `access_key_secret`）

`AccessKeySecret` 通常只在创建/重置时展示一次。丢失后按平台流程重置，不要截图发到聊天或提交到 Git。

## 3. 获取主播身份码 `id_code`

1. 用**实际开播的主播账号**打开 <https://play-live.bilibili.com>。
2. 在页面右下角找到“身份码”（有的页面叫“主播身份码”）。
3. 复制该身份码，填入本项目的 `BILI_ID_CODE`。

注意：

- `id_code` 不是直播间房间号；
- 一个身份码对应一个主播/房间上下文；
- 应用 `app_id` 与身份码必须属于同一开放平台应用和主播账号；
- 如果页面没有身份码入口，先确认该账号已开通直播和 OpenLive 能力。

## 4. 配置环境变量

不要把密钥写入 `conf.yaml`、Git 或聊天。PowerShell 当前会话示例：

```powershell
$env:BILI_ID_CODE = "这里填主播身份码"
$env:BILI_APP_ID = "这里填数字 AppId"
$env:BILI_ACCESS_KEY = "这里填 AccessKeyId"
$env:BILI_ACCESS_KEY_SECRET = "这里填 AccessKeySecret"
```

需要持久化时使用 Windows“用户环境变量”或受控的密钥管理器；不要提交 `.env`。

可选：

```powershell
$env:BILI_API_HOST = "https://live-open.biliapi.com"
$env:BILI_GAME_ID_CHECKPOINT = "data/bili_game_id.json"
```

## 5. 先做单房间采集验收

```powershell
uv run python scripts/check_bilibili_official.py --seconds 60
```

然后在自己的直播间发一条测试弹幕。预期日志出现：

```text
event #1 type=chat user=... detail=...
```

如果没有事件：

1. 确认 `id_code` 是主播身份码，不是房间号；
2. 确认应用已审核通过且 `app_id`/`access_key` 属于同一应用；
3. 确认主播账号正在开播，且应用有 OpenLive 权限；
4. 查看是否报 `app/start`、`7003`、`GameId` 或权限错误；
5. 上次异常退出可能留下服务端 `game_id`，本项目会尝试清理并等待冷却后重连。

## 6. 再接入 OLV

单房间采集通过后，在 `/live-ws` 发送：

```json
{
  "type": "bilibili-start",
  "config": { "from_env": true }
}
```

凭据只从环境变量读取，不进入 WebSocket 日志。`/live-ws` 当前没有独立鉴权，只应在本机或受信网络使用。

## 常见错误

| 现象 | 可能原因 |
|---|---|
| `missing Bilibili environment variables` | 四个环境变量没有设置，或当前 PowerShell 不是设置它们的那个会话 |
| `app/start` 失败 | `app_id`/`id_code` 不匹配、应用未审核、权限不足或主播未开播 |
| `7003` / `GameId` / 心跳过期 | 旧场次未结束或服务端会话失效；客户端会清理并重连，仍失败则人工在开放平台结束旧场次 |
| 没有任何事件 | 身份码错误、房间不是该主播、弹幕没有真正发到该房间，或应用没有事件权限 |
| 连接后立即断开 | 服务器时间不准、网络代理、WebSocket 被防火墙拦截，或 `access_key_secret` 错误 |
