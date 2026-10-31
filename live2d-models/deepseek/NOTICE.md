# DeepSeek 大肥鱼 Live2D 资源说明

- 来源仓库: https://github.com/lrplrplrp/dsh-live2d
- 上游提交: `1ba6522a18408716a5c71a81575503ddad9b96be`
- 上游声明: MIT License
- 上游资源目录: `assets/deepseek_l2d/`

当前目录保留了模型运行所需的 `.moc3`、纹理、物理、表情和动作文件，并做了两项接入调整：

1. `deepseek.model3.json` 的 `LipSync` 组加入 `ParamMouthOpenY`，让 OLV 的 TTS 口型同步可用。
2. 在 OLV 的 `model_dict.json` 中新增 `deepseek` 条目，把标准情绪标签映射到模型已有表情。

## 使用边界

上游仓库 README 声明 MIT。角色形象可能还涉及 DeepSeek 官方形象、角色作者或商标权利；如果用于商业直播、收费产品或带收益的活动，请先确认上游权利和 DeepSeek 品牌使用规则。保留本说明和上游 MIT 版权声明。
