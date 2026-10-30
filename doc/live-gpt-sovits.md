# GPT-SoVITS 接入记录

## 当前决定

**暂不切换运行中的 TTS。** 当前直播使用 Edge TTS：

```yaml
tts_model: 'edge_tts'
edge_tts:
  voice: 'zh-CN-XiaoyiNeural'
```

原因：本机还没有 GPT-SoVITS 服务、参考音频和逐字参考文本。直接切换会让直播没有声音。

## 已完成的代码

- `src/open_llm_vtuber/tts/gpt_sovits_tts.py` 已升级到 GPT-SoVITS **api_v2**
- 请求使用 `GET /tts`
- 每次请求携带 `ref_audio_path`、`prompt_text`、`prompt_lang`、`text_lang` 等字段
- 参考音频或参考文本为空时不会发出无效请求
- OLV 的文件式 TTS 输出固定使用 `streaming_mode=0`，不接 token 级流式
- 配置模板和适配器测试已更新

## 切换前必须准备

### 1. GPT-SoVITS 服务

本机运行 api_v2：

```text
http://127.0.0.1:9880/tts
```

启动成功标志：

```text
Uvicorn running on http://127.0.0.1:9880
```

### 2. 参考音频

- 时长 3–10 秒
- 干净、无背景音乐、无混响
- 音色和情绪就是希望角色使用的声音风格
- 必须拥有使用权；不要未经授权克隆声优、主播或动漫角色

### 3. 参考文本

`prompt_text` 必须是参考音频的**逐字转写**，不能概括，不能改写。

示例：

```yaml
gpt_sovits_tts:
  api_url: 'http://127.0.0.1:9880/tts'
  ref_audio_path: 'D:\GPT-SoVITS\referenceAudio\角色参考.wav'
  prompt_text: '参考音频里逐字对应的文本'
  prompt_lang: 'zh'
  text_lang: 'zh'
  text_split_method: 'cut5'
  batch_size: 1
  media_type: 'wav'
  streaming_mode: 0
  timeout: 120
```

## 可选：微调声音

零样本推理不需要训练，只要有参考音频和参考文本即可。

如果希望长期使用更稳定的角色音色，再做 GPT-SoVITS 微调：

- 准备几分钟到几十分钟的干净语音
- 完成切分、转写、特征提取
- 在官方预训练底模上微调 GPT/SoVITS 权重
- 需要 GPU 和训练时间

第一版先用零样本推理验证，不要一开始就训练。

## 切换步骤

1. 启动 GPT-SoVITS api_v2，确认 `127.0.0.1:9880` 可访问
2. 先直接请求 `/tts` 生成一段测试 WAV
3. 在 `conf.yaml` 填入参考音频和 `prompt_text`
4. 将 `tts_model` 改为 `gpt_sovits_tts`
5. 重启 OLV
6. 在 `http://localhost:12393` 测试一句话
7. 确认舞台页有声、有 Live2D 动作后，再接入直播姬

## 回滚

如果 GPT-SoVITS 服务不可用，改回：

```yaml
tts_model: 'edge_tts'
edge_tts:
  voice: 'zh-CN-XiaoyiNeural'
```

然后重启 OLV。不要在 GPT-SoVITS 和 Edge TTS 同时播放。
