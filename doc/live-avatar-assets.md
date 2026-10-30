# 直播虚拟形象素材调查：DeepSeek“大肥鱼”

调查日期：2026-10-30。结论只针对公开仓库的声明，不构成法律意见。

## 本地仓库

`Open-LLM-VTuber` 和 `Amaidesu` 当前 Git 树中没有“大肥鱼/鲸鱼娘”形象资源；现有 Live2D 模型只有 `mao_pro`、`shizuku`，均为示例模型。

## GitHub 候选与授权

| 仓库 | 内容 | 授权结论 |
|---|---|---|
| [xiaoweigen/deepseek-dafeiyu-assets](https://github.com/xiaoweigen/deepseek-dafeiyu-assets) | 大肥鱼/鲸鱼娘六视图、古风设定图 | **CC BY-NC-SA 4.0，禁止商业使用**；署名上善无形、ZipZipPipe、YunYueSama、xiaoweigen |
| [YunYueSama/codex-deepseek-pet](https://github.com/YunYueSama/codex-deepseek-pet) | 完整桌宠、角色设定、动画、六视图 | 自定义“大肥鱼项目署名许可 1.0”，允许商用但必须署名作者和仓库；`ASSET_LICENSE.md` 明确底层社区参考图的第三方权利未被该许可覆盖 |
| [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) | 100+ 段大肥鱼动画、提示词和素材链 | 代码 MIT；README 明确素材“允许开源使用，禁止商用”，形象授权标注 CC BY-NC-SA 4.0 |
| [QCYTSN/dsh-dafeiyu](https://github.com/QCYTSN/dsh-dafeiyu) | 大肥鱼桌宠 | 代码 MIT；当前素材来自 dsh-pet，`legacy/dafeiyu` 明确为受限素材；不能据此商用 |
| [BrickAZ/deepseek-chan-emotes](https://github.com/BrickAZ/deepseek-chan-emotes) | 161 个 GIF 表情 | 仓库未提供 LICENSE；README 明确不授予第三方角色/动画额外许可 |
| [GarfieldZhung/DeepSeek-Whale-Girl](https://github.com/GarfieldZhung/DeepSeek-Whale-Girl) | 小鲸鱼桌宠和静态角色图 | 代码 MIT；README 明确 PNG 图片不在 MIT 覆盖范围，原作者/再分发许可未核实 |
| [Andersen216/dsh-whale-girl-live2d](https://github.com/Andersen216/dsh-whale-girl-live2d) | 可用的 `.moc3` Live2D 模型、44 个表情、8 个动作 | 代码 MIT；模型作者氵六青的使用须知写“商用直播√、禁止盗用/出售”，但角色形象上游是 CC BY-NC-SA 4.0；PROVENANCE 明确不解除 NC/SA。**不能据此把角色用于商业化直播** |
| [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet) | 三视图桌宠和精灵图 | 仓库 LICENSE 为 MIT，但没有单独素材授权说明；图片是否由作者有权授权不明确，不能仅凭 MIT 推断可商用 |
| [diwer/ai-biaoqingbao](https://github.com/diwer/ai-biaoqingbao) | 大肥鱼表情包 | 公开检索显示为 CC BY-NC-SA 4.0 素材来源；不能商用 |

## 结论

1. **不能直接把社区“大肥鱼”图提交进 OLV 主仓用于 B站商业直播。** 主流来源是 CC BY-NC-SA 4.0，明确禁止商业化；部分仓库的 MIT 只覆盖代码，不覆盖图片。
2. **YunYueSama 的自定义许可看似允许商用，但其底层参考素材权利没有被该许可覆盖**，需要单独确认原始角色作者授权，不能当作无条件可商用。
3. **DeepSeek 名称、鲸鱼标识和官方品牌**还涉及商标/品牌规范；即使使用官方 logo，也不等于获得角色商用授权。
4. 可行路径：
   - 委托画师制作原创蓝鲸娘形象，并取得书面商用/改编/直播授权；
   - 向“大肥鱼”权利人或具体素材作者取得书面商业授权；
   - 先用**原创临时占位形象**开发，不把它宣称为社区“大肥鱼”。

## 当前占位资源

`avatars/deepseek_dafeiyu_placeholder.svg` 是本项目自绘的**原创蓝鲸占位图**，不是社区“大肥鱼”，不包含上述仓库的图片、角色设计或动画素材。它只用于界面/联调占位，正式 Live2D 模型和角色设定应使用已获授权的素材。

正式接入前，至少需要：

- 角色商用/直播/改编授权文件；
- Live2D 模型（`.moc3`、`.model3.json`、纹理、physics、motion）或可用的 VTS 模型；
- 模型作者署名和素材许可说明；
- 角色名、人设、声音、音乐、字体分别核对授权。
