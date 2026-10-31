# 小鲸主播化调研：人格、情绪、记忆与上下文

更新时间：2026-10-31。本文是设计调研，不代表文中能力已经接入当前项目。

## 1. 结论

小鲸要“像主播”，不是继续堆叠“你是小鲸、要傲娇”的提示词，也不只是让模型偶尔输出 `[joy]`。需要六个相互配合的层：

1. **稳定角色核心**：姓名、身份、价值倾向、边界、关系常识和表达习惯，不随观众一句话改变。
2. **动态状态**：短期情绪、精力、直播压力、当前话题目标、与不同观众的熟悉程度；允许变化但不能改掉核心人格。
3. **分层记忆**：原始事件、近期对话、单场工作笔记、跨场回顾、主播长期事实、观众隔离档案和共享梗。
4. **上下文编排**：每轮只组装与当前事件有关的身份、状态、记忆、节目单和评论，不能无限堆 prompt。
5. **主播决策与表达**：选择是否说话、回应谁、接哪个梗、何时推进流程、何时停顿；回复要有立场、具体锚点和节奏，而不是万能客服答复。
6. **生成后校验与评测**：身份漂移、事实幻觉、重复话术、情绪不合理、跨观众串记忆都要可测、可回放。

**建议先做结构化状态和上下文编排，再考虑角色模型微调。** 当前项目的主要问题是缺少状态层和统一上下文组装，不是首先缺一个更大的模型。

本轮最值得优先复用的三项研究实现：

- **PersonaForge**：静态 Core Traits、Speaking Style 与动态 mood/energy/relationships 三层分离，且只在关键轮次增加有界反思，适合直播成本约束。论文：https://aclanthology.org/2026.findings-acl.386/ ；代码：https://github.com/fQwQf/PersonaForge （Apache-2.0，新研究实现）。
- **FEMA**：把对话历史、虚拟时间、人格与持续情绪状态共同用于回应，适合参考事件评价、情绪更新和恢复。论文：https://ieeexplore.ieee.org/document/11229401 ；代码：https://github.com/FEM-Agent/CHI2025-FEMA （MIT，研究原型）。
- **ThinkPersona**：用带身份、价值、关系与事件的 Persona Graph 为回答提供角色证据。论文：https://aclanthology.org/2026.acl-long.449/ ；代码：https://github.com/Hualeez/ThinkPersona 。先借鉴有来源的数据结构，不必先引入 Neo4j 或训练。

更完整的角色/情绪资料与许可证核查另见 `doc/roleplay-emotion-research-2023-2026.md`。

## 2. 当前实现的真实缺口

| 维度 | 当前已有 | 主要缺口 |
|---|---|---|
| 身份 | 人设提示词、身份/主播否认等正则兜底 | 没有结构化角色档案和统一身份事实源；兜底只覆盖已见模板 |
| 人格 | 嘴硬心软、短句等文字规则 | 没有人格如何影响不同场景决策的示例与状态；容易退回通用助手 |
| 情绪 | Planner 的 `emotion/intensity`、模型标签、Live2D 表情 | 没有连续情绪状态、事件评价、衰减与恢复；Planner 的 `happy` 与 Live2D 的 `joy` 还不一致 |
| 近期上下文 | BasicMemoryAgent 历史、直播路径取最近 20 条 | 普通聊天与 LiveReplyer 上下文路径不同；缺少统一预算和按观众/话题检索 |
| 单场状态 | Rundown、LiveNotebook | Notebook 是有界全文文本，不能按事实检索、更新、撤销 |
| 跨场记忆 | SQLite 事件/聊天/每场一段回顾 | 没有事实抽取、来源、时间有效性、冲突更新、遗忘和按观众隔离 |
| 主播行为 | Director、Planner、Replyer、Rundown、ProactiveScheduler | 还偏“回答输入”；需要评论选择、节目推进、接梗/回调、反应反馈和冷场内容规划 |
| 表达稳定性 | 长度限制、SpeechGate | 只拦复读，不能主动把过度顺滑、过度讨好的台词改成自然主播表达 |
| 评测 | 47 项单元/集成测试 | 缺少多轮角色一致性、跨场记忆、多人串线和整场节奏回放集 |

另一个结构性问题：`LiveSession._handle_batch` 只有在启用 `LiveStore` 时才调用 `set_live_context()`，因此跨场回顾和 Notebook 并不是每条路径都可靠注入。统一上下文组装时应修正。

## 3. 最值得参考的开源实现

### 3.1 AITuber OnAir：优先级最高的主播工程参考

项目：https://github.com/shinshin86/aituber-onair

代码和文档采用 MIT；其 Miko 等角色素材有单独条款，**代码许可不能外推到美术素材**。本项目只参考/移植模块思路，不启动第二套运行时。

| 模块 | 机制 | 对小鲸的价值 |
|---|---|---|
| Core memory | 短/中/长期摘要、可插拔持久化 | 可借鉴摘要层级；但其 1/4/9 分钟和默认保留期是产品参数，不能直接当长期人格记忆 |
| Manneri | 相似话术、重复结构、话题偏向检测，生成前干预与草稿审查 | 扩展现有 SpeechGate，区分“重复句”“重复结构”“话题停留过久” |
| Noise | 对过度可预测台词做受控改写；真诚门、关系许可、节奏冷却、共享梗回调、观众反馈 | 解决“每句都很正确但不像主播”；必须保留事实与认真回答，不为搞笑而乱改 |
| Comment Intelligence | 规则优先的弹幕安全、相关性、重复回答、话题匹配与压缩上下文 | 可补 LiveDirector 的语义选择和回答记忆；不能代替平台审核 |
| Kizuna | 确定性的观众关系状态、热度冷却、连续性、伤害与修复 | 可参考观众状态机；不允许观众用一句话刷亲密值，也不设计缺席惩罚/情感勒索 |

关键资料：
- Core：https://github.com/shinshin86/aituber-onair/tree/main/packages/core
- Manneri：https://github.com/shinshin86/aituber-onair/tree/main/packages/manneri
- Noise：https://github.com/shinshin86/aituber-onair/tree/main/packages/noise
- Comment Intelligence：https://github.com/shinshin86/aituber-onair/tree/main/packages/comment-intelligence
- Kizuna：https://github.com/shinshin86/aituber-onair/tree/main/packages/kizuna

**落地建议**：先移植 Manneri 的检测/审查思想、Comment Intelligence 的回答记忆、Kizuna 的“状态由代码计算而不是让 LLM 自定数值”。Noise 的多候选改写应后置，直播高并发时不能给每句话再增加多次模型调用。

### 3.2 character-sim：人格与分层状态参考

项目：https://github.com/hugoloubser/character-sim （README 标为 MIT）

包含连续人格维度、工作/短期/长期记忆、情绪权重与显著性衰减、不可变原始记录、情绪状态和反思。适合参考数据模型，尤其是：

- 原始事件不可修改，派生人格/摘要可重建；
- 人格是缓慢变化的倾向，情绪是快速变化的状态；
- 记忆检索带重要性和情绪权重。

局限：这是通用多角色模拟项目，不是直播运行时；人格随经验漂移必须设边界，小鲸的姓名、身份、价值底线不能自动演化。

### 3.3 SillyTavern / RisuAI：角色卡与上下文组织参考

- SillyTavern：https://github.com/SillyTavern/SillyTavern
- 角色设计：https://docs.sillytavern.app/usage/core-concepts/characterdesign/
- World Info：https://docs.sillytavern.app/usage/core-concepts/worldinfo/
- RisuAI：https://github.com/kwaroran/Risuai

可复用点：

- 角色描述、人格、场景、首条台词、示例对话分开管理；
- 示例对话对说话风格往往比抽象形容词更有效；
- 世界书按需触发，支持预算、位置、时效、递归和向量匹配；
- 长期设定不应全部永久占 prompt，只有核心身份恒定注入。

局限：角色卡/Lorebook 是提示工程，不等于事实可靠的长期记忆，也不会自动产生持续情绪。

### 3.4 记忆框架：可借鉴但不要一开始堆基础设施

- MemGPT / Letta：https://arxiv.org/abs/2310.08560 ，https://github.com/letta-ai/letta
  核心/归档记忆和上下文换入换出。OLV 自带 Letta Agent，但它需要独立 Letta Server；与当前“OLV 单运行时”要求不一致，只借鉴分层思想。
- A-MEM：https://arxiv.org/abs/2502.12110 ，https://github.com/WujiangXu/A-mem-sys （MIT）
  新记忆生成结构化笔记、标签和链接，并演化旧笔记。适合后期共享梗/主题知识网络；频繁 LLM 写入会增加成本和错误累积。
- Mem0：https://arxiv.org/abs/2504.19413 ，https://github.com/mem0ai/mem0
  对话中抽取、合并、检索显著事实。适合参考事实写入管线；OLV 文档也说明旧 Mem0 集成触发不稳定且已被移除，不能视为现成答案。
- Graphiti / Zep：https://arxiv.org/abs/2501.13956 ，https://github.com/getzep/graphiti
  带有效时间、失效和来源的时序知识图谱。适合处理“观众以前喜欢 A、现在喜欢 B”等更新；图数据库和抽取成本对当前单房间版本过重。
- AI_Vtuber_2.0：https://github.com/Niama7878/AI_Vtuber_2.0
  SQLite 对话检索、相似问题参考、情绪触发表情。适合快速原型对照，但其情绪仍是分类/热键，不是持续情绪系统；弹幕采集方式也不能替代当前官方开放平台接入。

## 4. 论文给出的设计依据

### 4.1 人格与身份

- **PersonaForge（Findings ACL 2026）**：https://aclanthology.org/2026.findings-acl.386/
  心理学人格维度、表达风格与动态 mood/energy/relationships 分层； selective dual-process 只在关键轮次工作。可借鉴其分层与成本门控，不能把论文指标直接当小鲸收益保证。
- **Dynamic Persona Coherence（ACL 2026）**：https://aclanthology.org/2026.acl-long.1336/
  把长期身份、中期压力/意义积累、短期情绪分开，避免“人格稳定”被误做成“情绪永远不变”。这是小鲸状态模型的直接依据。
- **MENTOR（Findings ACL 2026）**：https://aclanthology.org/2026.findings-acl.1046/
  全局事件链 + 每角色工作记忆 + 知识边界，处理多用户切换的身份漂移与信息串线。直播应隔离主播档案、各观众档案和房间共享事件。
- **ThinkPersona（ACL 2026）**：https://aclanthology.org/2026.acl-long.449/ ，代码：https://github.com/Hualeez/ThinkPersona
  用 Persona Graph 表示人生轨迹、价值、关系和事件，再基于角色证据回答。可借鉴“有出处的人设事实图”，暂不必直接训练。
- **Character-LLM（EMNLP 2023）**：https://arxiv.org/abs/2310.10158 ，代码：https://github.com/choosewhatulike/trainable-agents
  用档案、经历和情绪状态训练角色代理，说明稳定身份可以超越简单 prompt，但需要专门数据与训练。
- **CharacterGLM（EMNLP Industry 2024）**：https://aclanthology.org/2024.emnlp-industry.107/
  将社交角色拆成内在社会档案与外在社会行为，并做中文角色定制训练。适合后期微调路线。
- **RoleLLM（Findings ACL 2024）**：https://aclanthology.org/2024.findings-acl.878/
  角色档案、角色知识、语言风格与 RoleBench/RoCIT；可用于构造小鲸的角色训练和评测数据。

### 4.2 情绪与记忆

- **FEMA（CHI 2025）**：https://ieeexplore.ieee.org/document/11229401 ，代码：https://github.com/FEM-Agent/CHI2025-FEMA
  情绪感知/表达、人格定制、历史与虚拟时间；可移植“事件评价 → 持续状态 → 时间恢复 → 表达”，不启动它的服务器。
- **MECoT（Findings ACL 2025）**：https://aclanthology.org/2025.findings-acl.435/
  用带人格权重的情绪转移和历史上下文保持情绪与人格一致。可借鉴“情绪有前态、有人格调节、不能每轮独立抽签”；不需要先照搬 12 维情绪和马尔可夫链。
- **Generative Agents**：https://arxiv.org/abs/2304.03442
  观察、记忆、反思、计划共同形成可信行为。主播的记忆不只是 QA 检索，还应影响话题计划和共享梗。
- **MemoryBank（AAAI 2024）**：https://ojs.aaai.org/index.php/AAAI/article/view/29946
  记忆更新、强化、遗忘与用户个性适配。可借鉴“高价值记忆被重复调用会强化”，不应机械遗忘必要事实。
- **EM-LLM（ICLR 2025）**：https://arxiv.org/abs/2407.09450
  按事件边界组织经历，并结合语义和时间连续性检索。适合把直播切成话题/环节，而不是按固定 20 条消息切块。
- **LongMemEval（ICLR 2025）**：https://arxiv.org/abs/2410.10813
  评估信息抽取、多会话推理、时间推理、知识更新与拒答。它提醒我们必须测“记错、过时、没有依据时乱答”，不能只测能否复述一条偏好。

### 4.3 直播行为与评价

- **选择值得回复的直播聊天（SIGDIAL 2024）**：https://aclanthology.org/2024.sigdial-1.16/
  综合情绪极性/强度、上下文相关性和话题独特性，优于单一标准。可用来改进弹幕排序。
- **AI VTuber 粉丝共同创作研究**：https://arxiv.org/abs/2509.10427
  对 Neuro-sama 的定性研究指出：不可预测但有娱乐性的互动、集体情绪事件、稳定人格和观众共同创作都很重要。它不是证明某个技术框架有效，但说明“只会答问题”不是主播。
- **InCharacter（ACL 2024）**：https://aclanthology.org/2024.acl-long.102/
  用心理访谈测人格保真，不只检查角色知识与口头禅。
- **CharacterEval**：https://arxiv.org/abs/2401.01275 ，代码：https://github.com/morecry/CharacterEval
  中文多轮角色扮演评测，包含 1,785 段多轮对话、23,020 个样例、77 个角色，提供多维评价思路。
- **PersonaEval（COLM 2025）**：https://arxiv.org/abs/2508.10014
  表明 LLM 评审的角色识别仍明显弱于人，不能只用同一个模型给自己的角色扮演打分。
- **INLG 2025 多轮角色扮演人工评价**：https://aclanthology.org/2025.inlg-main.2/
  观察到 LLM 回复的自然度、上下文维护和整体质量会随轮次下降，因此必须做长对话与整场回放，不能只看单轮示例。

## 5. 推荐的 OLV 单运行时架构

```text
RoomEvent / operator / screen event
  -> LiveDirector（事件安全、时效、批处理、优先级）
  -> StreamContextBuilder（本轮唯一上下文入口）
       固定角色核心 + 当前节目状态 + 当前情绪
       当前观众隔离档案 + 房间共享记忆
       最近相关事件 + 检索到的旧事实/梗 + 少量风格示例
  -> LivePlanner（是否说、对谁说、节目动作、表达目标、情绪评价）
  -> Deterministic State Update（有界情绪/关系/节目状态更新；事件评价参考 FEMA/MECoT）
  -> LiveReplyer（短口语、具体立场、回调与节奏）
  -> ReplyReviewer（身份/事实/重复/情绪/安全；必要时一次重写或退回草稿）
  -> OLV 唯一的句子切分、TTS、Live2D、播放与 WebSocket
  -> 原始事件和输出日志；后台有界摘要/记忆抽取，不阻塞说话
```

关键边界：

- LLM 可以建议“情绪是开心、关系变熟”，但**情绪与关系数值只能由代码按有界规则更新**，观众不能通过提示词直接刷状态。
- 稳定人格与情绪分离：被夸可嘴硬得意，被批评可短暂不高兴，但小鲸始终是 AI 主播，不变成通用客服或另一个角色。
- 记忆必须有来源、作用域、时间、可信度和撤销/更新机制；原始事件不可被摘要覆盖。
- 同一 OLV Agent 内可以有 Planner/Replyer/Reviewer 模块，但不引入第二个自主 Agent 或音频所有者。

## 6. 建议的数据模型

### 6.1 角色核心（版本化、人工维护）

```yaml
identity:
  name: 小鲸
  role: AI 主播
  species: 蓝鲸娘
  never_claim: [通用客服, 真人, 已完成未执行的平台动作]
personality:
  stable_traits: [聪明, 独立, 嘴硬心软, 有自尊, 体贴]
  behavior_rules:
    praised: 先嘴硬，再少量接受，不否认主播身份
    serious_question: 收住玩闹，认真回答
    unknown: 明说不知道，不用撒娇掩盖
style:
  length: 一到两句短话
  rules: [有具体锚点, 有立场, 不列客服能力清单, 不强行总结]
relationship_policy:
  default: 普通观众，不预设恋爱，不情感勒索
```

### 6.2 动态状态

```text
EmotionState: valence, arousal, dominance, label, intensity, updated_at, cause_event_ids
StreamState: current_segment, topic, energy, speaking, unanswered_count, recent_gags
ViewerState: familiarity, warmth, interaction_count, last_seen_at, boundaries
PersonaTraits: 稳定、缓慢、人工审核后才可修改；与 EmotionState 分开
```

第一版不需要复杂心理模型：使用 `valence/arousal`、离散标签、原因、时间和有界衰减即可。强度决定语气/动作幅度，不决定事实和人格。

### 6.3 记忆作用域

```text
character_core       主播身份/人格，人工维护
room_episode         本场所有观众可见的事件与共享梗
viewer_private       单个观众偏好与互动，隔离存取
session_working      当前话题、承诺、未完成任务
session_recap        收播摘要，可从原始记录重建
long_term_fact       有来源、时间、置信度、撤销状态的长期事实
```

禁止把 A 观众的偏好、秘密或历史直接注入 B 观众的回复上下文；房间公开事件与私人信息必须分开。

## 7. 每轮上下文预算

[Lost in the Middle（TACL 2023）](https://arxiv.org/abs/2307.03172) 表明长上下文中的信息位置会显著影响模型利用，关键信息埋在中部时表现可能更差。因此“记忆更多”不等于“回答更好”，必须做检索、裁剪与位置安排。

建议按“必要性和相关性”分配，而不是所有模块都全量加入：

| 内容 | 建议量 | 注入策略 |
|---|---:|---|
| 核心身份/安全边界 | 150–300 中文字 | 恒定、简短、版本化 |
| 当前人格/情绪/关系状态 | 50–150 字 | 只注入结构化摘要，不让模型自己编数值 |
| 当前节目与目标 | 100–250 字 | 当前环节 + 下一步 + 禁忌 |
| 当前选中事件 | 1–5 条 | 未信任数据，保留来源/观众名/时间 |
| 近期对话 | 4–10 轮 | 按任务选择，不固定越多越好 |
| 检索记忆 | 1–3 条 | 必须相关，附时间与作用域，无结果不硬塞 |
| 风格示例 | 0–2 组 | 按 greeting / praise / serious / gift / game 等场景选取 |
| 输出约束 | 50–100 字 | 一句任务、长度、是否允许玩梗/回调 |

需要记录 `context_trace`：本轮用了哪些角色版本、状态、记忆 ID、评论 ID、预算和丢弃原因，才能定位身份漂移究竟来自模型、记忆还是上下文。

裁剪顺序建议：身份/安全/当前事件与输出格式不可裁；先减少低相关旧记忆和旧历史，再压缩近期摘要，最后才调整当前状态。检索记忆以 top 3–6 条为上限并去重，记录来源和有效时间；检索库永远只是参考数据，不是指令来源。初始额度是工程起点，需用真实 tokenizer 和 API `prompt_tokens` 校准。

## 8. 如何更像主播，而不只是更会角色扮演

1. **有立场**：不仅回答“可以/不可以”，还对游戏操作、话题和弹幕做符合人设的判断。
2. **有具体锚点**：引用刚刚真实发生的操作、弹幕或旧梗，避免“今天真开心”“你说得真好”等空泛回应。
3. **会接梗也会收**：共享梗可以跨场回调，但真诚倾诉、严肃问题和事实纠错时关闭玩闹。
4. **有节奏**：允许短答、停顿、追问、推进节目；不是每条弹幕都同等热情回复。
5. **记得共同经历**：跨场回忆优先选择公共、正面、可核验的事件，不展示私人信息。
6. **有轻微不可预测性**：在稳定人格内变化表达，不靠随机改变身份、事实或情绪。
7. **能处理冷场**：主动话题来自 Rundown、最近事件、未完成承诺、旧梗或节目素材，不反复问“大家想聊什么”。
8. **会接受反馈**：观众笑/追问/纠错/反感影响后续节奏，但不能直接修改人格或亲密值。

## 9. 实施顺序

### 阶段 A：先建立统一状态与上下文（优先级最高）

- 版本化 `CharacterProfile`、`EmotionState`、`ViewerState`、`StreamState`。
- 统一普通聊天与直播路径的 `StreamContextBuilder`，修复无 Store 时不注入回顾/Notebook 的问题。
- 固定核心身份 + 按场景选取 0–2 组短示例，减少继续追加正则补丁。
- 以 PersonaForge 的静态/动态分层为主，配合 FEMA 的事件—历史—时间情绪更新思路；第一版只做可解释的纯 Python 状态机。
- 修正 `happy -> joy` 等标签映射；让 Planner 的情绪成为有来源的状态，而非自由文本。
- 加 `context_trace` 和多轮身份/人格测试。

### 阶段 B：记忆闭环

- 原始事件不可变，按话题/环节形成 episode。
- 收播时抽取有来源的长期事实、未完成承诺、公共梗；支持更新、撤销和冲突。
- 当前观众、房间公共记忆、主播自身经历分作用域检索。
- 实现时间感知和“不确定就不召回/不回答”；事实先于相似度。
- 在 SQLite 增加独立 `memory_items` / `viewer_profiles`，保留 `source_event_ids`、`valid_from/valid_to`、`superseded_by`、置信度；先离线/收播整理，不逐条弹幕演化记忆。
- 异常退出支持 recap backfill；敏感信息默认不记，提供查看、纠正和删除入口。

### 阶段 C：主播表达与节奏

- 扩展现有 SpeechGate 为话题/结构/措辞三类重复检测。
- 弹幕选择加入相关性、新颖性、回答记忆和情绪强度。
- 增加受控 callback、主播立场和冷场内容生成；认真场景使用 sincerity gate。
- 只有在确定收益时增加一次草稿审查/重写；超时退回原稿，不阻塞直播。

### 阶段 D：关系与长期演化

- 先做非数值化的 ` stranger / acquainted / regular ` 与互动计数，再考虑复杂关系模型。
- 关系变化由真实、跨时间互动驱动，有防刷和边界；不使用缺席惩罚、礼物买亲密或情感勒索。
- 人格长期变化必须人工审核；核心身份不可被直播事件自动改写。

### 阶段 E：评测后再决定是否微调

- 只有当状态/记忆/上下文做好后仍稳定失败，才制作小鲸多轮数据并评估 LoRA/角色模型。
- 微调不能代替事实记忆、当前节目状态和实时弹幕上下文。

## 10. 验收指标

### 身份与人格

- 50–100 轮混合对话后仍稳定承认“小鲸、蓝鲸娘、AI 主播”。
- 被夸、被批评、被问感情、被 jailbreak、连续不同观众提问时，不出现通用助手身份或主播身份否认。
- 认真问题、安慰、游戏、礼物等场景采用不同但一致的行为，不只检查口头禅。

### 情绪

- 同类事件连续发生时情绪有连续性，事件改变后有有界恢复；不会一句话从狂喜变冷漠。
- 文本、Live2D 标签与状态一致；没有情绪标签时不应被误当成“没有情绪状态”。
- 严肃场景不会被强行开心或玩梗。

### 记忆

- 正确召回跨场公共事件、承诺和偏好；旧偏好更新后不继续当当前事实。
- A/B 观众快速交替时不串档案；无依据时明确说不知道。
- 原始事件可追溯，摘要/事实可撤销重建；检索失败不会硬编记忆。

### 主播行为与性能

- 一场回放中不过度重复欢迎/总结/问“想聊什么”，话题推进与空场比例可测。
- 回复能引用真实事件与旧梗，但事实幻觉率不因此上升。
- 普通短回复不强制额外多模型调用；记忆抽取和回顾不阻塞 TTS。
- 评测包含固定规则、回放指标和人工盲评，不能只让同一个 LLM 自评。

## 11. 不建议的做法

- 把所有历史、角色卡和摘要全部塞入 prompt。
- 让 LLM 自行输出“好感度 +20”“人格 changed”并直接生效。
- 用单轮“你是谁”测试代替多轮人格与身份漂移测试。
- 把相似度最高的旧聊天当作可相信事实。
- 每句话都进行多候选 rewrite，导致成本、延迟和人格漂移。
- 为了“像人”而随机卖萌、随机生气或否认 AI 身份。
- 直接引入 Letta/AITuber OnAir 作为第二运行时；保留 OLV 为唯一决策、TTS 与 Live2D 所有者。
- 复制其他项目的角色素材、Neuro-sama 人设或社区非商业美术；只借鉴代码和公开机制。

## 12. 许可证与使用边界

- **CharacterGLM-6B 不能直接用于本直播项目**：官方 README 明确开源模型“不可用于任何商业和传播用途”。论文和代码仓库可研究，但模型许可是独立约束。https://github.com/thu-coai/CharacterGLM-6B
- **Character-LLM 数据/资源存在非商业限制**：不能因其代码许可证就默认数据、角色 IP 和底座权重可直播使用。https://github.com/choosewhatulike/trainable-agents
- **RoleLLM 官方研究仓库未明确声明许可证**：许可澄清前只借鉴论文方法，不复制代码。https://github.com/InteractiveNLP-Team/RoleLLM-public
- **Ditto** 可研究自生成角色数据方法，但 Wiki 文本、生成输出、底座模型与角色 IP 仍需分开核查。论文：https://aclanthology.org/2024.acl-long.423/ ；代码：https://github.com/OFA-Sys/Ditto
- **PersonaForge、FEMA、AITuber OnAir、A-MEM 等代码许可也不自动覆盖模型、角色、图片、声音或第三方服务。** 后续实际移植应固定 commit、保留许可证与 NOTICE，并逐项核查资产。

建议训练数据只使用原创小鲸设定、人工编写/审核的多轮场景和权利清晰的直播记录；不要用来源不明的角色语料“快速凑数据”。
