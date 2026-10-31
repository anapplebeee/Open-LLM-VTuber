# 小鲸：角色扮演、人格、情绪与记忆调研（2023–2026）

> 范围：优先 ACL Anthology、arXiv、论文作者官方 GitHub。目标是在 **OLV 单一运行时**内增强小鲸；Amaidesu 只移植模块，不启动第二套 Agent/TTS/Live2D 循环。
>
> 重要区分：**提示词、单轮情绪字段和 `[joy]` 标签都不是持久情绪系统**。持久情绪至少需要跨轮状态、事件评价、时间衰减/恢复、人格约束和可测试的状态迁移。

## 1. 结论与优先级

1. **最先借鉴 PersonaForge**：静态人格、语言风格、动态状态三层分离；动态状态包含 mood / energy / relationships。其 selective dual-process 也适合直播延迟控制，但只能作为 OLV 内的可选模块。
2. **用 FEMA 补跨轮情绪**：官方实现明确维护 emotional states，并将会话历史和虚拟时间用于情绪响应。可移植“事件评价 → 状态更新 → 时间衰减 → 表达约束”，不移植它的服务器或 Agent 循环。
3. **用 ThinkPersona 组织身份与记忆**：Persona Graph 将人生轨迹、价值观、关系、事件连成有来源的知识图。小鲸需要“身份事实/关系/经历图”，而不是继续堆自然语言提示词。
4. **用 CharacterEval、InCharacter、Ditto 建回归测试**：身份一致性、人格保真、角色知识、语言风格、未知问题拒绝是不同指标；不能只测“你是谁”。
5. **微调不是第一步**：RoleLLM、Ditto、Character-LLM 可复用数据构造法，但不会自动提供长期记忆或持续情绪。先完成状态层与评测，再考虑 LoRA。
6. **许可证红线**：CharacterGLM-6B 官方 README 明确开源模型不可商用及传播用途；Character-LLM 数据/资源也有非商业限制。二者不能直接作为小鲸直播模型或训练数据来源。

## 2. 可直接借鉴架构的官方实现

### 2.1 PersonaForge（ACL 2026 Findings）

- 论文：https://aclanthology.org/2026.findings-acl.386/
- 官方代码：https://github.com/fQwQf/PersonaForge
- 核心机制：
  - 三层人格：Core Traits（Big Five + defense mechanisms）、Speaking Style、Dynamic State（mood / energy / relationships）。
  - selective dual-process：仅关键轮次启用 Inner Monologue；README 报告约 40% 轮次触发，以 13.4% token 开销达到完整系统 96% 的表现。
  - 论文报告 50 轮人格漂移低于基线，并在 RoleBench 做外部验证。
- 对小鲸的可复用点：
  - `CharacterProfile` 与 `DynamicState` 分离，身份不被每轮心情覆盖。
  - 移植 `dynamic_state_manager` 的概念到 OLV `live/`；以 `LiveBatch` 为最小更新单位。
  - 只在身份冲突、强情绪、重大关系变化时增加一次有界反思，普通弹幕仍单轮生成。
- 局限/成熟度/许可证：
  - 新研究代码，不是直播生产框架；多一次模型调用会直接影响语音延迟。
  - 论文包含 LLM-as-judge，结果仍需小鲸自己的真人抽检。
  - 仓库 Apache-2.0；发布代码与 schema，不附带受版权保护的角色数据。基于 BookWorld，保留 NOTICE。

### 2.2 FEMA（CHI 2025）

- 论文：https://ieeexplore.ieee.org/document/11229401
- 官方代码：https://github.com/FEM-Agent/CHI2025-FEMA
- 核心机制：情绪感知与表达、人格定制、conversation history、virtual time；配置中可维护 agent emotional states。
- 对小鲸的可复用点：
  - 将情绪作为跨轮 `EmotionState`，而不是 Planner 的一次性字符串。
  - 事件、历史和时间共同更新状态；礼物、夸赞、嘲讽、冷场产生不同评价与恢复速度。
  - 人格决定同一事件的不同反应，例如“被夸仍嘴硬”是表达策略，不是删除情绪。
- 局限/成熟度/许可证：
  - 研究原型，依赖 OpenAI API 与 sentence-transformers；不能当作现成直播组件。
  - 需在本项目重新验证状态迁移、时间衰减、线程/异步安全和测试。
  - 官方仓库 MIT；第三方模型/API 许可另算。

### 2.3 ThinkPersona（ACL 2026）

- 论文：https://aclanthology.org/2026.acl-long.449/
- 官方代码：https://github.com/Hualeez/ThinkPersona
- 核心机制：Persona Graph 表示人生轨迹、价值观、关系和事件；从真实访谈构建 1,201 个图，派生 23,401 条 Question–Reasoning–Answer 数据，经微调使回答以身份证据为依据。
- 对小鲸的可复用点：
  - 用 `identity_fact / value / relationship / event` 节点和带 provenance 的边组织小鲸设定与长期记忆。
  - 每轮只检索相关身份证据，避免把完整角色圣经塞入每次生成。
  - 身份回答、关系回答和经历回答可追溯到节点，减少“我是 AI 助手/我不是主播”等无依据回答。
- 局限/成熟度/许可证：
  - 新研究实现，含 Neo4j、训练与实验依赖；直播不应展示长链思考。
  - 图只能改善证据组织，不保证模型永远服从，也不等于情绪系统。
  - GitHub 元数据为 MIT；README 同时声明 research purposes，数据、底座模型和第三方组件许可需分别复核。

## 3. 可训练角色扮演：方法可复用，成品模型不宜直接上播

### 3.1 Character-LLM（EMNLP 2023）

- 论文：https://aclanthology.org/2023.emnlp-main.814/
- 官方代码：https://github.com/choosewhatulike/trainable-agents
- 核心机制：Experience Reconstruction，从角色档案生成场景、经历对话和 protective scenes，训练特定人物代理；角色“情绪状态”主要存在于训练经历/场景。
- 可复用点：为小鲸生成“身份事实、典型经历、越界问题、身份诱导”训练样本；用 protective scenes 专门减少角色幻觉。
- 局限：九个人物、旧 Llama/FastChat 路线、训练成本高；没有提供直播持久情绪或长期记忆。
- 许可证：代码 Apache-2.0；README 标明数据 CC BY-NC 4.0，且资源限学术研究；Llama 1 权重与角色 IP 另行受限。**不可直接把其数据/模型用于商业直播。**

### 3.2 RoleLLM / RoleBench（ACL 2024 Findings）

- 论文：https://aclanthology.org/2024.findings-acl.878/
- 官方仓库：https://github.com/InteractiveNLP-Team/RoleLLM-public
- 核心机制：Role Profile Construction；Context-Instruct 提取角色知识/经历；RoleGPT 模仿语言风格；RoCIT 微调 RoleLLaMA / RoleGLM。RoleBench 含 168,093 个样本。
- 可复用点：
  - 将“角色知识”和“语言风格”分建数据集与评测，避免只模仿口癖。
  - 小鲸原创角色卡可生成 role-specific QA、一般指令角色化回答和未知问题样本。
- 局限：合成数据仍有事实/风格噪声；微调不能注入直播发生后的新事件，更不是情绪状态机。
- 许可证/成熟度：研究仓库 GitHub 元数据未声明许可证；在许可澄清前不要复制其代码。数据集、底座模型和剧本 IP 另行核查。

### 3.3 Ditto（ACL 2024）

- 论文：https://aclanthology.org/2024.acl-long.423/
- 官方代码：https://github.com/OFA-Sys/Ditto
- 核心机制：将角色对话生成视为阅读理解，自生成训练数据后 SFT；分别评测身份一致性、角色知识和未知问题拒绝。
- 关键结论：语言风格较容易通过监督获得；角色知识受底座模型能力约束，模仿强模型不等于获得其事实能力。
- 可复用点：用小鲸自有设定生成“知道/不知道/身份混淆/跨轮矛盾”数据与测试；保留知识证据以审查幻觉。
- 局限：不提供持续情绪或长期记忆；自动评测存在 LLM judge 偏差，官方评测还涉及 GPT-4 成本。
- 许可证：代码仓库 MIT；Wiki 数据、API 输出、底座模型与生成内容许可需分别确认。README 的训练复现配方仍有待完善，不宜视为开箱即用训练框架。

### 3.4 CharacterGLM（EMNLP 2024 Industry）

- 论文：https://aclanthology.org/2024.emnlp-industry.107/
- 官方仓库：https://github.com/thu-coai/CharacterGLM-6B
- 核心机制：角色属性包括身份、兴趣、观点、经历、成就、社交关系；行为包括语言特征、性格、情感表达和互动模式；以人工构建中文角色语料微调。
- 可复用点：角色 schema，以及 OOC、矛盾、重复、低信息量、主动性等错误分类。
- 局限：6B 开源模型较旧，仍有随机性和被用户误导风险；不代表直播人格/记忆基础设施已完成。
- **许可证红线**：代码仓库显示 Apache-2.0，但官方 README 明确“开源模型不具备商用能力，仅供学术研究使用，不可用于任何商业和传播用途”。不要将该开源模型直接用于 B 站直播，代码许可不能替代模型许可。

### 3.5 ChatHaruhi（2023 技术报告）

- 论文：https://arxiv.org/abs/2308.09597
- 官方仓库：https://github.com/LC1332/Chat-Haruhi-Suzumiya
- 核心机制：改进提示词 + 从剧本抽取角色记忆；覆盖 32 个角色、约 54k 模拟对话。
- 可复用点：将原创小鲸剧情节选为“角色事实记忆”和“风格示例”，按当前事件检索；知识证据与风格示例分开。
- 局限：主要解决角色知识与风格，不是持续情绪系统；老研究项目，剧本与角色 IP 风险独立。
- 许可证：代码仓库 Apache-2.0；剧本、动漫角色与训练数据使用权不能由代码许可证推定。

## 4. 情绪、人格与记忆的边界

### 4.1 Generative Agents（2023）

- 论文：https://arxiv.org/abs/2304.03442
- 核心机制：observation → memory retrieval → reflection → planning；高层反思影响后续行为。
- 可复用点：Amaidesu 的 SessionRecap/Notebook 可作为 OLV 内模块；在批次结束或高显著事件后反思，而非每条弹幕都反思。
- 局限：记忆与反思不是情绪状态；完整小镇模拟不能直接搬到低延迟直播。

### 4.2 MemGPT（2023）

- 论文：https://arxiv.org/abs/2310.08560
- 核心机制：分层记忆与虚拟上下文管理，在有限上下文窗口中调度快慢记忆。
- 可复用点：区分当前场次工作记忆、最近场次摘要、长期事实；检索需带时间、来源和置信度。
- 局限：记忆管理不自动产生人格或情绪；不要为引入 MemGPT 再启动第二套 Agent。

### 4.3 AI with Emotions（NLP4DH 2025）

- 论文：https://aclanthology.org/2025.nlp4dh-1.51/
- 核心机制：用 Russell Circumplex 的 valence / arousal 指定情绪，独立 GoEmotions 分类器评估生成文本是否符合目标。
- 可复用点：采用连续 V/A 状态，并加入独立输出评估，避免只信模型自报情绪。
- 局限：验证的是“按指定情绪表达”的能力，**不是跨轮情绪演化，也不是持久情绪系统**； workshop 研究。

### 4.4 Feeling First, Speaking Second（CAS @ LREC 2026）

- 论文：https://aclanthology.org/2026.cas-1.7/
- 核心机制：区分 Appraisal（本能情绪反应）与 Formulation（策略性表达），结合长期记忆、动态情绪、人格和目标。
- 可复用点：与 OLV Planner/Replyer 分工吻合：先更新真实状态，再决定主播如何嘴硬/克制地表达。
- 局限：模拟叙事场景、LLM-as-judge；作者也观察到高压下策略规划压过情绪真实性。未核实到官方代码，不建议当作可直接移植实现。

### 4.5 iPET（ACL 2025 Demo）

- 论文：https://aclanthology.org/2025.acl-demo.40/
- 核心机制：对话、记忆存储与综合、世界模拟/高层反思驱动行为；有长期真实产品部署描述。
- 可复用点：将用户偏好、角色经历和高层反思用于后续行为，而非只生成下一句。
- 局限：论文中的部署规模不等于开源实现成熟度；本报告未核实到官方开源代码，不作为可直接移植仓库推荐。

## 5. 评测实现

### CharacterEval（2024）

- 论文：https://arxiv.org/abs/2401.01275
- 官方代码：https://github.com/morecry/CharacterEval
- 机制：1,785 段多轮对话、23,020 个样本、77 个中文角色；四个维度十三项指标，提供 CharacterRM。
- 小鲸复用：建立身份、角色知识、语言风格、人格/行为、多轮矛盾的混合评测，而非只做关键词过滤。
- 局限：角色来自小说/剧本，直播弹幕分布不同；Reward Model 与 LLM judge 均需人工抽检。
- 许可：GitHub 元数据 MIT；arXiv 页面论文为 CC BY-NC-SA 4.0；角色文本、百科资料与模型权重许可分别核查。

### InCharacter（ACL 2024）

- 论文：https://aclanthology.org/2024.acl-long.102/
- 官方代码：https://github.com/Neph0s/InCharacter
- 机制：通过心理访谈量表评估角色人格保真，覆盖 32 个角色与 14 种量表；针对传统 self-report 的缺陷设计访谈法。
- 小鲸复用：定期做 Big Five/嘴硬心软等行为访谈，观察版本更新后人格漂移。
- 局限：是评测方法，不是人格生成器、记忆或情绪系统；心理量表不能替代真实观众偏好与直播安全评测。
- 许可：本报告未复核仓库及数据全部许可，商用复制前先检查仓库 LICENSE。

## 6. 小鲸的落地架构（仍是 OLV 单运行时）

```text
RoomEvent / LiveBatch
  -> Appraisal：事件对当前关系/目标/人格的影响
  -> EmotionState：valence/arousal、mood、energy、relationship；带时间衰减
  -> PersonaGraph 检索：身份事实、价值观、关系、事件、来源
  -> Live Planner：是否开口、节目目标、表达策略
  -> Live Replyer：按人格与当前状态生成短口语
  -> 确定性表达层：状态/Planner 强度 -> OLV 合法 emotionMap 标签
  -> OLV 唯一 TTS / Live2D / playback
```

### 静态与动态必须分开

- 静态：`name=小鲸`、`role=AI主播`、价值观、知识边界、语言风格、例句。
- 动态：本场心情、精力、对当前观众的关系、最近显著事件。
- 记忆：事实必须有来源、时间、置信度；跨场保存需隐私与人工清理策略。

### 表达层不要依赖模型自觉

- `happy` 等别名先归一化为实际 `emotionMap` 的 `joy`。
- 标签只是最终控制信号，不是情绪本身。
- 模型没输出标签时，可由持久 `EmotionState + Planner intensity` 确定性选择一个合法标签；TTS 文本与表情控制分离。

### 节奏与成本

- 每个 `LiveBatch` 最多一次有界状态更新。
- 普通弹幕不增加额外 LLM 调用；只有身份冲突、高显著事件、场次结束才触发反思。
- 所有新逻辑在 OLV 进程内，以纯 Python 模块和可注入时钟实现，便于 fake-clock 单测。

## 7. 建议实施顺序

1. **P0：状态与身份证据**：CharacterProfile、EmotionState、PersonaGraph、标签归一化；不训练。
2. **P1：回归测试**：50 轮身份/人格漂移、角色知识、未知拒绝、情绪连续性/衰减、直播延迟；LLM judge + 人工小样本。
3. **P2：记忆反思**：本场 Notebook、场次回顾、长期事实抽取；控制写入频率与隐私。
4. **P3：确有基线后微调**：优先使用原创/可商用的小鲸数据，参考 RoleLLM、Ditto、Character-LLM 的数据构造；不要直接采用非商用角色模型/数据。
