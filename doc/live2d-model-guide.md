# 自制 Live2D 角色模型并接入 OLV

## 先理解：贴图和 Live2D 的区别

静态 PNG/SVG 只是“一张图”，不能眨眼、说话、呼吸或做表情。Live2D 模型是一套带参数和变形的运行时资源，至少包含：

- `.moc3`：模型本体
- `.model3.json`：模型入口和资源索引
- 纹理 PNG
- 表情 `.exp3.json`
- 动作 `.motion3.json`
- 物理 `.physics3.json`、姿势 `.pose3.json`（可选但推荐）

OLV 当前加载的是 `live2d-models/mao_pro/runtime/mao_pro.model3.json`。自制模型最终也要产出同样的运行时目录。

## 一、准备工具

- **Live2D Cubism Editor 5.x**：建模、参数、表情、动作、导出 `.moc3`
- **Photoshop / Clip Studio Paint / Illustrator**：画分层原画
- **Live2D Cubism Viewer**：导出前检查动作和表情
- 商用前先核对 Live2D 当前的 Editor、SDK 和素材授权条款

## 二、画分层原画

建议做正面或轻微侧面的半身像，先不要做复杂手部和大面积衣服褶皱。

PSD 需要拆成独立图层，至少包括：

```text
后发
身体/衣服
脖子
脸部底色
左眼白/左眼珠/左眼睑
右眼白/右眼珠/右眼睑
左眉/右眉
嘴部（闭口、半开、张口）
前发
刘海阴影
配饰
```

要点：

- 图层边缘要留出被变形时需要的余量
- 眼睛、嘴巴要做“闭合/半开/张开”的差分
- 头发和身体要留出重叠区域，避免转动时露出背景
- 纹理建议 2048 或 4096，先确认目标模型的性能预算
- 所有素材必须是自有或明确可商用授权的

## 三、在 Cubism Editor 里绑定

核心流程：

1. 导入 PSD
2. 给每个部件建立网格（Mesh）
3. 建立变形器（Deformer）
4. 设置参数（Parameter）
5. 给参数打关键帧，制作不同状态
6. 添加物理演算（头发、身体晃动）
7. 制作表情和动作
8. 在 Cubism Viewer 里检查

VTuber 常用标准参数：

```text
ParamAngleX / ParamAngleY / ParamAngleZ
ParamEyeLOpen / ParamEyeROpen
ParamEyeBallX / ParamEyeBallY
ParamMouthOpenY / ParamMouthForm
ParamBodyAngleX / ParamBodyAngleY / ParamBodyAngleZ
ParamBreath
```

OLV 会从文本中的表情标签提取情绪，再通过 `model_dict.json` 的 `emotionMap` 映射到表情索引。因此模型至少要有可用的表情文件，例如：

```text
expressions/happy.exp3.json
expressions/angry.exp3.json
expressions/sad.exp3.json
expressions/surprised.exp3.json
```

对应关系在 `model_dict.json` 中配置。

## 四、导出运行时文件

在 Cubism Editor 中导出：

- `.moc3`
- `.model3.json`
- 纹理
- 物理、姿势
- 表情和动作 JSON

目录建议：

```text
live2d-models/
└── xiaojing/
    └── runtime/
        ├── xiaojing.moc3
        ├── xiaojing.model3.json
        ├── xiaojing.physics3.json
        ├── xiaojing.pose3.json
        ├── textures/
        │   └── texture_00.png
        ├── expressions/
        │   ├── happy.exp3.json
        │   └── ...
        └── motions/
            ├── idle.motion3.json
            └── ...
```

## 五、接入 OLV

在根目录 `model_dict.json` 增加一项：

```json
{
  "name": "xiaojing",
  "description": "蓝鲸娘小鲸",
  "url": "/live2d-models/xiaojing/runtime/xiaojing.model3.json",
  "kScale": 0.5,
  "initialXshift": 0,
  "initialYshift": 0,
  "kXOffset": 1150,
  "idleMotionGroupName": "Idle",
  "emotionMap": {
    "neutral": 0,
    "joy": 1,
    "anger": 2,
    "sadness": 3,
    "surprise": 4
  },
  "tapMotions": {}
}
```

然后在 `conf.yaml` 改：

```yaml
character_config:
  live2d_model_name: 'xiaojing'
```

重启 OLV，打开：

```text
http://localhost:12393
```

如果模型能显示、眨眼、说话时嘴动，说明接入成功。

## 六、最省事的路径

如果只是先把直播跑起来：

1. 继续用 OLV 自带 `mao_pro` 验证全链路
2. 同时找画师/建模师制作自己的蓝鲸娘模型
3. 拿到运行时文件后按上面的目录接入

如果要商业直播，模型和 Live2D 软件都要确认商用授权；不要直接使用来源不明的动漫角色模型。
