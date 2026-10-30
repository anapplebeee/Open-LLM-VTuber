# 来源与修改说明

`proto.py`、`game_checkpoint.py`、`backoff.py` 和 `websocket_client.py` 改编自
Amaidesu 2.1.1（commit `4ed96745c0dc1db0e9ecf29a8f8054fdcc5128fd`）的
Bilibili 官方开放平台客户端，原项目采用 MIT License。

本目录中的改动：

- 将项目专用日志门面替换为本地 loguru 适配器；
- 将事件输出改为 `RoomEvent`，由 `collector.py` 接入 OLV 的 `LiveDirector`；
- 恢复 HTTPS 证书校验（原实现显式使用 `verify=False`）；
- 移除对 Amaidesu `BaseCollector`、`EventBus` 和配置系统的依赖；
- 新增 OLV 侧的凭据配置、生命周期和映射测试。

原始 MIT 许可全文见 `LICENSE-AMAIDESU`。该许可不覆盖 Bilibili 平台服务条款、
主播凭据或角色素材。
