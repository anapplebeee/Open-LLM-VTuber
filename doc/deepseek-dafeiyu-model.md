# DeepSeek 大肥鱼 Live2D 接入记录

## 调研结论

GitHub 上“大肥鱼”相关内容分三类：

| 仓库 | 内容 | 是否能直接接 OLV |
|---|---|---|
| [lrplrplrp/dsh-live2d](https://github.com/lrplrplrp/dsh-live2d) | 含 `deepseek.moc3`、纹理、物理、表情、动作，仓库声明 MIT | **可以，本次采用** |
| [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet) | 三视图精灵图桌宠，MIT 代码，不是 Live2D | 不能直接接 |
| [EussGaler/WhaleMaid-for-Codex](https://github.com/EussGaler/WhaleMaid-for-Codex) | C++/Qt Live2D，模型素材 CC BY-NC-SA 4.0 | 非商用可用，不适合直接进商业直播 |
| [Andersen216/dsh-whale-girl-live2d](https://github.com/Andersen216/dsh-whale-girl-live2d) | DSH 插件/桌宠，模型素材 CC BY-NC-SA 4.0 | 非商用参考 |

本次采用的源：

```text
repo:   https://github.com/lrplrplrp/dsh-live2d
commit: 1ba6522a18408716a5c71a81575503ddad9b96be
path:   assets/deepseek_l2d/
license: MIT (仓库声明)
```

## 已接入内容

```text
live2d-models/deepseek/
  deepseek.model3.json
  deepseek.moc3
  deepseek.physics3.json
  deepseek.cdi3.json
  deepseek.2048/texture_00.png
  happy.exp3.json
  happy2.exp3.json
  puzzled.exp3.json
  relax.exp3.json
  unhappy.exp3.json
  anger.exp3.json
  sad.exp3.json
  surprised.exp3.json
  idle.motion3.json
  speak.motion3.json
  thinkend.motion3.json
  thinking.motion3.json
  LICENSE
  NOTICE.md
```

接入改动：

1. `model_dict.json` 增加 `deepseek` 模型项。
2. `conf.yaml` 的 `live2d_model_name` 改为 `deepseek`。
3. `deepseek.model3.json` 的 `LipSync` 组加入 `ParamMouthOpenY`，用于 TTS 口型同步。
4. 新增 `Talk` 动作组，指向自带 `speak.motion3.json`；OLV 播放语音时会调用 `startRandomMotion("Talk")`，源模型原先只有 `Idle/Anima`，所以没有说话动作。
5. 模型 URL 加 `?v=20261031b`，避免浏览器继续使用旧表情/LipSync 缓存配置。
6. 新增 `anger.exp3.json`、`sad.exp3.json`、`surprised.exp3.json`，把表情从 5 个扩展到 8 个；不改 moc3。
7. 标准情绪标签映射到模型已有表情：

```text
neutral  -> relax
joy      -> happy
smirk    -> happy2
fear     -> puzzled
surprise -> surprised
sadness  -> sad
anger    -> anger
disgust  -> unhappy
default  -> relax
```

`emotionMap` 现在直接使用模型表情名，不再使用硬编码索引；OLV 的 `Actions.expressions` 支持字符串，前端会调用 `setExpression("happy")` 等真实表情名。

## 验证

```text
GET /live2d-models/info -> 200，count=1，当前仅 deepseek
GET /live2d-models/deepseek/deepseek.model3.json -> 200
GET /live2d-models/deepseek/deepseek.moc3        -> 200
GET /live2d-models/deepseek/deepseek.2048/texture_00.png -> 200
GET 8 个 exp3.json / idle.motion3.json -> 200
Live2DModel('deepseek') -> emo_str 正确加载
/client-ws 初始 set-model-and-conf -> model_info.name = deepseek
真实 WS 请求：
AUDIO_TEXT = [surprise]天哪。
ACTIONS = {"expressions": ["surprised"]}
```

当前 Windows 沙箱内 Edge headless 无法启动（`platform_channel` 权限拒绝），所以还没有自动化浏览器截图；资源加载、表情动作映射和 OLV 注册表解析已通过，最终画面需在浏览器里确认。

## HitAreas 限制

`deepseek.moc3` 中存在 `ArtMesh0..14`，但模型没有可读的 ArtMesh→头部/身体的语义映射；`PartFace` 等是 Part，不是 HitArea 规范要求的 ArtMesh ID。直接猜 ID 会造成点击无效或点错部位，因此本轮没有伪造 `HitAreas` 或 `tapMotions`。

如果拿到模型工程文件（`.can3`/PSD）或模型师提供的 ArtMesh ID，可以在 `deepseek.model3.json` 增加类似：

```json
"HitAreas": [
  {"Id": "<head-artmesh-id>", "Name": "Head"},
  {"Id": "<body-artmesh-id>", "Name": "Body"}
]
```

并在 `model_dict.json` 配置 `tapMotions`；当前点击动作仍不可用。

浏览器仍需清除旧的 `localStorage.modelInfo`，否则可能继续使用之前的 `mao_pro` 模型。

## 授权注意事项

上游仓库 README 声明 MIT，本次保留其 LICENSE 与 NOTICE。
但“DeepSeek/大肥鱼”角色形象可能涉及 DeepSeek 官方形象、角色作者或商标权利；商业直播、收费产品或带收益活动前应单独确认授权。`WhaleMaid` 和 `Andersen216` 的模型素材明确是 CC BY-NC-SA 4.0，不要直接用于商业用途。
