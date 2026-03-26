# MeetingEZ 文档完善计划

## 一、现状分析

### 1.1 现有文档清单

| 文档 | 类型 | 完备度 | 说明 |
|------|------|--------|------|
| `API.md` | API 文档 | ★★★☆☆ | 覆盖 API 接口，但缺少功能上下文 |
| `USAGE.md` | 用户指南 | ★★★★☆ | 覆盖基本操作，适合终端用户 |
| `TERMINOLOGY.md` | 术语规范 | ★★★★★ | 完善，术语边界清晰 |
| `AUDIO_ARCHITECTURE.md` | 技术架构 | ★★★★☆ | 覆盖音频链路，偏实现层 |
| `meeting_minutes_agent.md` | 功能设计 | ★★★★☆ | Agent 设计完整，但与 Web 端关联描述不足 |
| `realtime_agent_integration_design.md` | 概要设计 | ★★★★☆ | 融合方案完整，但缺少实现细节 |
| `realtime-transcription-best-practices.md` | 最佳实践 | ★★★★★ | 完善，OpenAI Realtime 使用指南 |
| `CHANGELOG.md` | 变更日志 | ★★★★★ | 完善 |

### 1.2 文档缺口

1. **缺少功能详细设计文档**：现有文档偏向 API 层面或用户操作层面，缺少"功能模块"级别的详细设计
2. **实时转写功能无独立文档**：核心功能散落在多个文档中
3. **Web 工作台无文档**：SPA 工作台功能未形成设计文档
4. **术语表系统无详细设计**：术语生命周期、状态流转未文档化
5. **项目记忆系统与 Web 端关联不清晰**：两套系统的数据流和交互关系未明确

---

## 二、目标与原则

### 2.1 文档目标

- 补全**功能详细设计级别**的文档（不涉及 API 设计）
- 每个文档聚焦一个功能模块，说明：
  - 功能目标与边界
  - 核心流程与状态机
  - 数据结构（非 API schema，而是业务对象）
  - 与其他模块的交互关系
  - 配置项与环境变量

### 2.2 文档原则

1. **面向开发者和产品**：同时服务于代码理解和功能讨论
2. **与代码同步**：文档应反映当前实现状态，而非理想状态
3. **避免重复**：已有文档中充分覆盖的内容不重复
4. **保持一致**：术语使用与 `TERMINOLOGY.md` 保持一致

---

## 三、文档完善计划

### 3.1 需要新建的文档

#### 3.1.1 `FEATURE_REALTIME_TRANSCRIPTION.md` — 实时转写功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：浏览器端实时会议转写工具
   - 核心价值：低延迟字幕显示 + 双语翻译

2. 功能边界
   - 职责：音频采集 → 实时转写 → 字幕显示 → 翻译
   - 不负责：会后纪要生成、项目记忆维护（由 Agent 负责）

3. 音频采集机制
   - 输入源类型：麦克风 / 标签页音频
   - 采集参数：单声道、24kHz、PCM
   - 权限处理：getUserMedia / getDisplayMedia
   - 音量监测：AudioContext + AnalyserNode

4. 实时转写链路
   - 连接方式：WebRTC transcription session
   - 后端职责：签发 client secret（/api/realtime-session）
   - 前端职责：创建 PeerConnection、处理事件
   - Session 配置：model、language、prompt、VAD、noise_reduction

5. 字幕状态机
   - 状态定义：idle → recognizing → completed
   - 数据结构：TranscriptItem（item_id, live_text, final_text）
   - 事件处理：speech_started / delta / completed
   - 按 item_id 管理条目（不按到达顺序）

6. 翻译后置处理
   - 触发时机：completed 事件后
   - 后端代理：/api/translate
   - 处理逻辑：语言检测 → 智能修正 → 双向翻译
   - 结果回填：原文先显示，翻译后插入

7. 项目增强模式
   - Context Pack：术语表 + 项目背景 + 近期待办
   - 注入位置：realtime session prompt
   - 快速模式 vs 项目模式：增强包差异

8. 前端 UI 架构
   - 页面结构：全屏字幕区 + 底部工具栏 + 设置浮层
   - 状态管理：meeting_state（is_active, duration, transcripts）
   - 本地存储：设备选择、语言设置、字幕记录

9. 配置项
   - 环境变量：TRANSLATION_MODEL、TRANSLATION_REASONING_EFFORT
   - 前端设置：语言模式、音频源、字体大小

10. 性能日志
    - 前端：Realtime [perf] / UI [perf]
    - 后端：[perf] 统一前缀
    - 关键指标：speech_started → first delta → completed
```

**预计字数**：3000-4000 字

---

#### 3.1.2 `FEATURE_WORKSPACE_SPA.md` — Web 工作台功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：项目管理与会议处理的 Web GUI
   - 核心价值：为命令行 Agent 提供可视化操作界面

2. 架构设计
   - SPA 架构：前端路由 + 组件化
   - 状态管理：全局 state 对象
   - API 交互：/api/workspace/* JSON 接口
   - 路由结构：/#project/:id、/#project/:id/glossary 等

3. 仪表盘（Dashboard）
   - 数据内容：项目列表、会议统计、待处理数量
   - 快速入口：快速模式（无项目）/ 项目模式
   - 新建会议：表单 → 创建会议目录 → 跳转实时页

4. 项目详情页
   - 项目信息：名称、描述、团队、启动日期
   - 会议列表：按日期排序、状态标记（ASR/纪要/处理中）
   - 操作入口：上传音频、触发处理、查看文件

5. 会议管理
   - 会议创建：日期、标题、类型、语言设置
   - 音频管理：上传、重命名、删除、试听
   - 处理触发：完整处理 vs 仅生成纪要
   - 文件查看：transcript.json、minutes.md 等

6. 术语管理页
   - 术语列表：confirmed / pending / rejected 三态
   - 操作：确认、拒绝、回退、编辑、删除
   - 新增术语：手动添加

7. 背景说明页
   - 条目列表：topic、question、answer
   - 来源追踪：source_meeting
   - 操作：新增、编辑、删除

8. 组件设计
   - Sidebar：项目导航
   - Topbar：用户信息、登录状态
   - SlideOver：右侧滑出面板（文件编辑、详情查看）
   - Modal：确认对话框、表单弹窗

9. 状态管理
   - 全局 state 结构
   - 路由切换与状态恢复
   - Toast 通知机制

10. 与 Agent 的协作
    - 会议处理：subprocess 调用 meeting_agent run
    - 状态查询：锁文件检测（_processing.lock）
    - 结果展示：处理日志、错误信息
```

**预计字数**：3500-4500 字

---

#### 3.1.3 `FEATURE_GLOSSARY_SYSTEM.md` — 术语表系统功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：项目级术语知识库
   - 核心价值：提升 ASR 识别精度、规范术语使用

2. 术语生命周期
   - 状态流转：pending → confirmed / rejected
   - 回退机制：confirmed/rejected → pending
   - 删除：永久移除

3. 数据结构
   - GlossaryEntry：canonical、aliases、type、context、source_meeting
   - TermType：product、person、tech_term、org、other
   - 存储文件：_glossary.json、_glossary_pending.json、_glossary_rejected.json

4. 术语来源
   - 自动提取：Agent 从会议纪要中识别新术语
   - 手动添加：Web 工作台手动录入
   - 待审核队列：pending 状态术语

5. 术语审核流程
   - 查看待审核术语
   - 确认：移入 confirmed 表
   - 拒绝：移入 rejected 表（记录拒绝原因）
   - 回退：从 confirmed/rejected 移回 pending

6. 术语应用场景
   - 实时转写：注入 realtime session prompt
   - 翻译后置处理：智能修正时的术语对齐
   - 会后 ASR：GLM-ASR 的术语提示
   - 纪要生成：人名、产品名的规范写法

7. 术语类型说明
   - product：产品名、模块名
   - person：人名（含别名）
   - tech_term：技术术语
   - org：组织名、团队名
   - other：其他

8. API 接口（功能视角）
   - 列表查询：GET /api/workspace/project/:id/glossary
   - 新增：POST /api/workspace/project/:id/glossary/entries
   - 更新：PUT /api/workspace/project/:id/glossary/entries/:canonical
   - 删除：DELETE /api/workspace/project/:id/glossary/entries/:canonical
   - 审核：POST /api/workspace/project/:id/glossary/approve
   - 拒绝：POST /api/workspace/project/:id/glossary/reject
   - 回退：POST /api/workspace/project/:id/glossary/revert

9. 与背景说明的关系
   - 术语表：短词、固定写法
   - 背景说明：长文本、概念解释、Q&A
   - 组合使用：build_context_pack()
```

**预计字数**：2500-3500 字

---

#### 3.1.4 `FEATURE_PROJECT_MEMORY.md` — 项目记忆系统功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：跨会议持续沉淀的项目知识库
   - 核心价值：让 Agent "记住" 项目历史，提供会前提示

2. 记忆资产清单
   - context.md：项目上下文摘要（自动维护）
   - timeline.md：项目时间线（关键决策、里程碑）
   - actions.md：行动项追踪（待办状态、超期提醒）
   - _context.md：项目背景说明（人工维护）
   - pre_meeting_hint.md：会前智能提示

3. 项目上下文（context.md）
   - 内容：项目概述、核心决策、里程碑进度、风险与阻塞
   - 更新时机：每次会议处理后
   - 维护方式：Agent 自动生成 + 增量更新

4. 项目时间线（timeline.md）
   - 内容：按日期排列的会议条目、决策、里程碑
   - 结构：月份分组 → 会议条目 → 关键信息
   - 更新时机：每次会议处理后

5. 行动项追踪（actions.md）
   - 内容：行动项列表、负责人、截止日期、状态
   - 状态：新增、进行中、已完成、超期
   - 提醒机制：超期提醒、长期未跟进

6. 项目背景说明（_context.md）
   - 内容：概念解释、术语背景、业务知识、Q&A
   - 维护方式：人工编辑 / Web 工作台管理
   - 与术语表的关系：背景说明是术语的扩展解释

7. 会前提示（pre_meeting_hint.md）
   - 生成时机：处理新会议前
   - 内容：需跟进事项、超期待办、建议议题、近期决策
   - 用途：帮助用户快速进入会议状态

8. 记忆更新流程
   - 触发：Agent 处理会议后
   - 步骤：
     1. 读取现有记忆文件
     2. 分析会议纪要，提取新信息
     3. 增量更新 context.md、timeline.md、actions.md
     4. 生成 pre_meeting_hint.md 供下次会议使用

9. 与 Web 工作台的集成
   - 查看入口：项目详情页
   - 操作：查看 context、timeline、actions
   - 待办管理：标记完成、查看超期项

10. 与实时会议的集成
    - Context Pack：组合术语表 + 背景说明 + 近期待办
    - 注入位置：realtime session prompt
    - 反向流动：会议结束后更新记忆
```

**预计字数**：3000-4000 字

---

#### 3.1.5 `FEATURE_TRANSLATION.md` — 翻译功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：实时会议的双语翻译辅助
   - 核心价值：帮助跨语言会议参与者理解内容

2. 语言模式
   - single_primary：单主语言会议（如中文为主，夹杂英文术语）
   - bilingual：双语言会议（如中文+日语，两种都是会议语言）

3. 语言角色
   - primary_language：主要语言（如中文）
   - secondary_language：第二语言（如日语）
   - original_language：当前条目的实际语言

4. 翻译触发时机
   - 触发条件：realtime completed 事件后
   - 前置判断：是否配置了第二语言
   - 调用接口：/api/translate

5. 翻译处理流程
   - 输入：rawTranscript、语言配置、上下文
   - 步骤：
     1. 语言检测（判定 originalLanguage）
     2. 智能修正（可选）
     3. 双向翻译
   - 输出：primaryTranslation、secondaryTranslation

6. 智能修正
   - 触发条件：enableCorrection=true
   - 修正范围：ASR 错误、术语对齐、标点断句
   - 约束：不改写意图、不补充信息

7. 术语表增强
   - 触发条件：enableGlossary=true
   - 注入方式：glossary_entries 参数
   - 效果：将术语修正为标准写法

8. 翻译结果规则
   - 原文是第一语言：primaryTranslation=null，输出 secondaryTranslation
   - 原文是第二语言：输出 primaryTranslation，secondaryTranslation=null
   - 原文是其他语言：仅输出 primaryTranslation
   - 禁止同语种"翻译"

9. 前端展示逻辑
   - 原文先显示
   - 翻译完成后在原文后插入翻译行
   - 翻译行样式区分（缩进、颜色）
   - 上下文传递：最近 N 条原文+翻译

10. 配置项
    - 环境变量：TRANSLATION_MODEL、TRANSLATION_REASONING_EFFORT
    - 前端设置：语言模式、主要语言、第二语言
    - 模型能力：gpt-5 系列支持 reasoning.effort

11. 性能考量
    - 延迟目标：<1s 完成翻译
    - 并发控制：顺序处理，避免竞态
    - 上下文长度：最近 3-5 条
```

**预计字数**：2000-3000 字

---

#### 3.1.6 `FEATURE_MEETING_PROCESSING.md` — 会议处理功能详细设计

**内容大纲**：

```
1. 功能概述
   - 产品定位：会后自动化处理流程
   - 核心价值：录音 → 转写 → 纪要 → 记忆更新

2. 处理流程概览
   - 扫描 → ASR → 纪要生成 → 记忆更新
   - 触发方式：Web 工作台 / 命令行

3. 会议扫描
   - 扫描器：MeetingScanner
   - 扫描内容：会议目录、音频文件、状态文件
   - 状态判断：has_audio、has_transcript、has_minutes、is_processing

4. ASR 处理
   - 引擎：GLM-ASR（智谱 AI）
   - 输入：音频文件（支持多段合并）
   - 输出：transcript.json
   - 特性：断点续传、重叠分块

5. 纪要生成
   - 模型：GPT-5.4
   - 输入：transcript + meeting_meta + project_context
   - 输出：minutes.md
   - 模板：按会议类型选择（review/weekly/brainstorm/retro）

6. 记忆更新
   - 更新内容：context.md、timeline.md、actions.md
   - 更新方式：增量追加 + 状态同步
   - 术语提取：自动识别新术语 → pending 队列

7. Web 工作台集成
   - 触发入口：会议列表页"处理"按钮
   - 处理模式：完整处理 / 仅生成纪要
   - 异步执行：后台线程 + 锁文件
   - 状态查询：/api/workspace/.../process/status

8. 命令行接口
   - python -m meeting_agent run
   - 参数：--project、--meeting、--force、--force-minutes
   - 监控模式：--watch

9. 处理状态
   - 锁文件：_processing.lock
   - 日志文件：_processing.log
   - 错误文件：_processing.error

10. 错误处理
    - 超时：30 分钟
    - 重试：需手动触发
    - 日志：保留在会议目录
```

**预计字数**：2500-3500 字

---

### 3.2 需要更新的文档

#### 3.2.1 `API.md` 更新

- 增加"功能模块索引"章节，指向各功能详细设计文档
- 精简接口描述，将功能上下文移至功能文档

#### 3.2.2 `USAGE.md` 更新

- 保持现有内容
- 增加"更多文档"章节，指向功能详细设计文档

---

## 四、执行计划

### 4.1 优先级排序

| 优先级 | 文档 | 理由 |
|--------|------|------|
| P0 | `FEATURE_REALTIME_TRANSCRIPTION.md` | 核心功能，使用频率最高 |
| P0 | `FEATURE_WORKSPACE_SPA.md` | 主入口，用户最先接触 |
| P1 | `FEATURE_GLOSSARY_SYSTEM.md` | 影响转写精度，关键能力 |
| P1 | `FEATURE_PROJECT_MEMORY.md` | 差异化能力，长期价值 |
| P2 | `FEATURE_TRANSLATION.md` | 可选功能，双语场景需要 |
| P2 | `FEATURE_MEETING_PROCESSING.md` | 会后流程，可参考现有 Agent 文档 |

### 4.2 依赖关系

```
FEATURE_REALTIME_TRANSCRIPTION.md
    └── FEATURE_TRANSLATION.md

FEATURE_WORKSPACE_SPA.md
    ├── FEATURE_GLOSSARY_SYSTEM.md
    ├── FEATURE_PROJECT_MEMORY.md
    └── FEATURE_MEETING_PROCESSING.md
```

### 4.3 建议执行顺序

1. **第一批**（核心功能）
   - `FEATURE_REALTIME_TRANSCRIPTION.md`
   - `FEATURE_WORKSPACE_SPA.md`

2. **第二批**（支撑能力）
   - `FEATURE_GLOSSARY_SYSTEM.md`
   - `FEATURE_PROJECT_MEMORY.md`

3. **第三批**（扩展功能）
   - `FEATURE_TRANSLATION.md`
   - `FEATURE_MEETING_PROCESSING.md`

---

## 五、文档模板

每个功能文档应遵循以下结构：

```markdown
# [功能名称] 功能详细设计

## 1. 功能概述
- 产品定位
- 核心价值
- 目标用户

## 2. 功能边界
- 职责范围
- 不负责的内容
- 与其他功能的关系

## 3. 核心流程
- 流程图/时序图
- 关键步骤说明

## 4. 数据结构
- 核心对象定义
- 状态定义
- 文件结构

## 5. 详细设计
- 子功能拆解
- 关键算法/逻辑
- 边界条件处理

## 6. 与其他模块的交互
- 依赖关系
- 数据流向
- 接口调用

## 7. 配置项
- 环境变量
- 前端设置
- 可调参数

## 8. 错误处理
- 常见错误
- 恢复策略
- 日志记录

## 9. 性能考量
- 性能目标
- 瓶颈分析
- 优化建议

## 10. 未来规划
- 已知限制
- 待优化项
- 扩展方向
```

---

## 六、验收标准

- [ ] 每个文档覆盖完整的业务流程
- [ ] 术语使用与 `TERMINOLOGY.md` 一致
- [ ] 代码示例可直接运行
- [ ] 流程图清晰可读
- [ ] 与现有文档无冲突或重复
- [ ] 通过开发者和产品同学的评审
