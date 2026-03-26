# 项目记忆系统功能详细设计

## 1. 功能概述

### 1.1 产品定位

项目记忆系统是 MeetingEZ 的差异化核心能力，是一个**跨会议持续沉淀的项目知识库**。它通过自动维护多份记忆资产，让 AI Agent 能够"记住"项目历史，为会议处理提供上下文支持。

### 1.2 核心价值

- **知识沉淀**：自动从会议纪要中提取决策、里程碑、待办事项，形成持续演进的项目档案
- **上下文增强**：为会议纪要生成、实时转写提供项目背景，提升输出质量
- **待办追踪**：跨会议追踪行动项状态，自动识别超期和已完成项
- **会前准备**：生成智能提示，帮助用户快速进入会议状态

### 1.3 目标用户

- 使用 MeetingEZ 管理长期项目的团队
- 需要跨会议追踪待办事项的项目经理
- 需要了解项目历史背景的会议参与者

---

## 2. 功能边界

### 2.1 职责范围

- 维护项目级记忆资产（context.md、timeline.md、actions.md）
- 在会议处理时提供上下文信息
- 自动更新记忆内容
- 生成会前智能提示

### 2.2 不负责的内容

- 会议转写和纪要生成（由 ASR 和 LLM Client 负责）
- 术语表管理（由 GlossaryManager 负责）
- 背景说明的人工编辑（由 Web 工作台提供界面）

### 2.3 与其他模块的关系

| 模块 | 关系 |
|------|------|
| 会议处理 | 记忆系统为会议处理提供上下文，会议处理后更新记忆 |
| 术语表 | 记忆系统读取术语表用于 Context Pack 构建 |
| 实时转写 | 通过 Context Pack 为实时转写提供增强提示 |
| Web 工作台 | 工作台展示记忆内容，提供人工编辑入口 |

---

## 3. 记忆资产清单

### 3.1 资产概览

| 文件 | 名称 | 维护方式 | 用途 |
|------|------|----------|------|
| `context.md` | 项目上下文 | 自动 + 增量 | 项目状态摘要，供 Agent 理解项目 |
| `timeline.md` | 项目时间线 | 自动 + 追加 | 按时间排列的会议记录、决策、里程碑 |
| `actions.md` | 待办事项追踪 | 自动 + 状态同步 | 跨会议的行动项状态管理 |
| `_context.md` | 项目背景说明 | 人工维护 | 概念解释、业务知识、Q&A |
| `pre_meeting_hint.md` | 会前智能提示 | 自动生成 | 面向用户的会议准备清单 |

### 3.2 存储位置

```
meetings/                          # 单项目模式
├── context.md
├── timeline.md
├── actions.md
├── _context.md
└── 2024-01-15_xxx/
    └── pre_meeting_hint.md        # 每次会议一份

projects/                          # 多项目模式
└── project-name/
    ├── context.md
    ├── timeline.md
    ├── actions.md
    ├── _context.md
    └── 2024-01-15_xxx/
        └── pre_meeting_hint.md
```

---

## 4. 详细设计

### 4.1 项目上下文（context.md）

#### 内容结构

```markdown
# 项目上下文

> 最后更新: 2024-01-15 14:30
> 会议总数: 12
> 待办总数: 8（完成 3，进行中 2，超期 1）

---

## 项目概述

**名称**: MeetingEZ
**描述**: 智能会议记录助手
**启动日期**: 2024-01-01

**团队**:
- 张三（老张）：技术负责人
- 李四：产品经理
- 王五：前端开发

---

## 核心决策记录

| 日期 | 决策内容 | 决策方式 |
|------|----------|----------|
| 2024-01-10 | 采用 OpenAI Realtime API | 周会讨论 |
| 2024-01-12 | 使用 Flask 作为后端框架 | 技术评审 |

---

## 里程碑进度

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| 2024-01-15 | MVP 完成 | ✅ 已完成 |
| 2024-02-01 | 内测启动 | 🔄 进行中 |

---

## 风险与阻塞

### 🔴 高风险
- API 调用成本可能超预算

### 🟡 中风险
- 第三方服务稳定性待验证

---

## 近期关注

### 本周待办
- A005: 完成翻译功能开发（张三，截止 2024-01-20）
- A006: 编写用户文档（李四，截止 2024-01-22）

### 超期待办
- A003: 性能优化（超期 3 天）

### 下次会议
*待安排*
```

#### 更新机制

- **触发时机**：每次会议处理完成后
- **更新方式**：增量追加新决策、里程碑、风险信息
- **数据来源**：GPTAnalysisResult.context_updates

```python
# ContextManager.update() 方法
def update(
    self,
    project_dir: Optional[Path] = None,
    new_decisions: Optional[list[dict]] = None,
    milestone_updates: Optional[list[dict]] = None,
    risk_updates: Optional[list[dict]] = None,
):
    # 追加更新内容到文件末尾
    updates = []
    if new_decisions:
        updates.append("\n### 新增决策\n")
        for d in new_decisions:
            updates.append(f"- {d.get('date', '')}: {d.get('decision', '')}\n")
    # ...
```

### 4.2 项目时间线（timeline.md）

#### 内容结构

```markdown
# 项目时间线

> 最后更新: 2024-01-15 14:30

---

## 2024年1月

### 2024-01-15 周会 【周会】

**关键决策**
- ✅ 确定 MVP 功能范围
- ✅ 分配本周开发任务

**里程碑**
- 📍 MVP 原型完成

**风险标记**
- 🟡 测试覆盖率不足

---

### 2024-01-12 技术评审 【评审会】

**关键决策**
- ✅ 采用 OpenAI Realtime API 进行实时转写

---

## 里程碑总览

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| 2024-01-15 | MVP 原型完成 | ✅ 已完成 |

---

## 待决策事项

| 事项 | 状态 | 预计决策时间 |
|------|------|--------------|
| *暂无* | | |
```

#### 条目插入逻辑

TimelineManager 按月份组织条目，新条目插入到对应月份的开头：

```python
def add_entry(self, entry: TimelineEntry, project_dir: Optional[Path] = None):
    # 1. 查找对应月份的位置
    month_header = f"## {entry.date.year}年{entry.date.month}月"

    # 2. 如果月份存在，插入到该月份开头
    # 3. 如果月份不存在，创建新月份区域
    # 4. 更新最后更新时间
```

### 4.3 待办事项追踪（actions.md）

#### 内容结构

```markdown
# 待办事项追踪

> 最后更新: 2024-01-15 14:30 | 🔴 1 | 🟡 2 | ⏳ 3 | ✅ 5

| ID | 任务 | 负责人 | 截止 | 状态 |
|----|------|--------|------|------|
| A003 | 性能优化 | 张三 | 2024-01-12 | 🔴 |
| A005 | 完成翻译功能 | 张三 | 2024-01-20 | 🟡 |
| A006 | 编写用户文档 | 李四 | 2024-01-22 | ⏳ |
| A007 | 集成测试 | 王五 | 2024-01-25 | ⏳ |
| A001 | 搭建开发环境 | 张三 | 2024-01-05 | ✅ |
```

#### 状态类型

| 状态 | 图标 | 含义 |
|------|------|------|
| PENDING | ⏳ | 待处理 |
| IN_PROGRESS | 🟡 | 进行中 |
| COMPLETED | ✅ | 已完成 |
| OVERDUE | 🔴 | 已超期（动态计算） |

#### 核心操作

```python
class ActionsManager:
    def add(self, task, owner, due_date, meeting_dir, priority="P1"):
        """添加新待办，自动去重"""
        # 检查是否存在相似任务
        # 分配递增 ID（A001, A002, ...）
        # 保存到 actions.md

    def mark_completed(self, action_id, project_dir):
        """标记待办为已完成"""

    def mark_mentioned(self, action_id, meeting_dir, project_dir):
        """记录待办在某会议中被提及"""

    def get_overdue(self, project_dir):
        """获取所有超期待办"""

    def sync_from_minutes(self, project_dir):
        """从所有会议纪要中同步待办事项"""
```

#### 去重机制

添加新待办时，使用多种匹配策略检测重复：

1. **完全匹配**：任务文本完全相同
2. **包含关系**：较短任务是较长任务的子串（长度 ≥ 10）
3. **关键词重叠**：共同关键词 ≥ 5 且重叠度 ≥ 50%，或重叠度 ≥ 70%

```python
def _find_similar_action(self, task, actions, threshold=0.4):
    # 提取关键词（中文按字符，英文按单词）
    # 计算重叠度
    # 返回最相似的待办或 None
```

### 4.4 项目背景说明（_context.md）

#### 定位

与自动维护的 context.md 不同，_context.md 是**人工编辑**的背景材料，包含：

- 概念解释和业务知识
- 术语的扩展说明
- 常见问题 Q&A
- 项目特殊约定

#### 与术语表的关系

| 对比项 | 术语表 | 背景说明 |
|--------|--------|----------|
| 内容形式 | 短词、固定写法 | 长文本、概念解释 |
| 维护方式 | AI 建议 + 人工审核 | 完全人工编辑 |
| 应用场景 | ASR 提示、翻译修正 | 纪要生成、Context Pack |

#### 内容示例

```markdown
# 项目背景说明

## 核心概念

### MeetingEZ
智能会议记录助手，支持实时转写和会后纪要生成。

### 实时转写 vs 会后处理
- 实时转写：会议进行中的低延迟字幕显示
- 会后处理：录音 → ASR → 纪要 → 记忆更新

## 常见问题

Q: 为什么实时转写和会后 ASR 结果不同？
A: 实时转写使用 OpenAI Realtime API，会后 ASR 使用 GLM-ASR，引擎和模型不同。
```

### 4.5 会前智能提示（pre_meeting_hint.md）

#### 生成时机

在处理新会议的 ASR **之前**生成，保存到会议目录下。

#### 内容结构

```markdown
# 会议前提示

> 生成时间: 2024-01-20 09:00
> 目标会议: 2024-01-20 周会

---

## 需跟进事项

- [ ] A003 性能优化（超期 8 天，负责人：张三）
- [ ] A005 翻译功能（截止今天，负责人：张三）

## 建议议题

1. MVP 功能验收
2. 性能优化方案讨论
3. 用户文档分工

## 近期决策回顾

- 2024-01-15: 确定 MVP 功能范围
- 2024-01-12: 采用 OpenAI Realtime API
```

#### 生成流程

```python
def generate_pre_meeting_hint(self, meeting_meta, context_md, actions_md):
    """生成会前提示"""
    prompt = PromptBuilder.build_pre_meeting_hint_prompt(
        meeting_meta=meeting_meta,
        context_md=context_md,
        actions_md=actions_md,
    )
    response = self.client.chat.completions.create(...)
    return response.choices[0].message.content
```

---

## 5. 记忆更新流程

### 5.1 完整流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        会议处理流程                              │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 加载项目记忆                                                  │
│    - context.md（项目上下文）                                    │
│    - actions.md（现有待办）                                      │
│    - 最近 5 份会议纪要                                           │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. 生成会前提示                                                  │
│    - 基于 meeting_meta 和项目记忆                                │
│    - 保存到 pre_meeting_hint.md                                  │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. ASR 转写                                                      │
│    - 使用 GLM-ASR 处理音频                                       │
│    - 生成 transcript.json                                        │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. GPT 分析                                                      │
│    - 输入：转写文本 + 项目上下文 + 现有待办                       │
│    - 输出：纪要 + 新待办 + 时间线条目 + 上下文更新                │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. 更新项目记忆                                                  │
│    - 保存 minutes.md                                             │
│    - 添加新待办到 actions.md                                     │
│    - 标记已完成/提及的待办                                       │
│    - 添加时间线条目到 timeline.md                                │
│    - 增量更新 context.md                                         │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 MemoryWriter 核心方法

```python
class MemoryWriter:
    def process_analysis_result(self, result, meeting_meta, meeting_dir, project_dir):
        """处理 GPT 分析结果，更新所有记忆文件"""

        # 1. 保存会议纪要
        self._save_minutes(result.minutes, meeting_dir)

        # 2. 添加新待办
        for action_data in result.new_actions:
            self.actions_mgr.add(
                task=action_data["task"],
                owner=action_data["owner"],
                due_date=action_data["due_date"],
                meeting_dir=meeting_dir.name,
                priority=action_data.get("priority", "P1"),
                project_dir=project_dir,
            )

        # 3. 标记已完成/提及的待办
        for action_id in result.completed_actions:
            self.actions_mgr.mark_completed(action_id, project_dir)

        for action_id in result.mentioned_actions:
            self.actions_mgr.mark_mentioned(action_id, meeting_dir.name, project_dir)

        # 4. 添加时间线条目
        if result.timeline_entry:
            entry = TimelineEntry(...)
            self.timeline_mgr.add_entry(entry, project_dir)

        # 5. 更新项目上下文
        self.context_mgr.update(
            project_dir=project_dir,
            new_decisions=result.context_updates.get("new_decisions", []),
            milestone_updates=result.context_updates.get("milestone_updates", []),
            risk_updates=result.context_updates.get("risk_updates", []),
        )

    def get_context_for_meeting(self, project_dir):
        """获取会议所需的上下文信息"""
        context_md = self.context_mgr.load(project_dir)
        actions = self.actions_mgr.load(project_dir)
        actions_md = self.actions_mgr._generate_actions_md(actions) if actions else None
        recent_minutes = self._get_recent_minutes(project_dir)
        return context_md, actions_md, recent_minutes
```

---

## 6. Context Pack 构建

### 6.1 概述

Context Pack 是为实时转写和会后处理构建的增强上下文包，组合多个来源的信息：

- 术语表（已确认术语）
- 项目背景说明（_context.md）
- 近期待办（未完成的 actions）
- 团队成员（昵称映射）
- 近期会议列表

### 6.2 构建函数

```python
def build_context_pack(
    project_id: Optional[str],
    primary_language: Optional[str],
    secondary_language: Optional[str],
    language_mode: Optional[str],
    base_config: Optional[Config] = None
) -> dict:
    """构建给实时会议页使用的上下文增强包"""
```

### 6.3 输出结构

```json
{
    "projectId": "project-name",
    "projectName": "MeetingEZ",
    "glossary": [
        "OpenAI Realtime API | Realtime API",
        "GLM-ASR",
        "MeetingEZ"
    ],
    "backgroundContext": "项目背景说明内容...",
    "pendingActions": [
        {
            "id": "A005",
            "task": "完成翻译功能",
            "owner": "张三",
            "dueDate": "2024-01-20"
        }
    ],
    "recentMeetings": [
        "2024-01-15 周会",
        "2024-01-12 技术评审"
    ],
    "realtimePrompt": "你正在执行会议实时转写...\n高优先级术语：OpenAI Realtime API、GLM-ASR..."
}
```

### 6.4 Realtime Prompt 构建

为实时转写构建的提示包含：

1. **语言模式说明**：单主语言 vs 双语言
2. **高优先级术语**：前 12 个已确认术语
3. **团队成员别名映射**：昵称 → 标准名
4. **近期会议**：最近 4 次会议标题

---

## 7. 与其他模块的交互

### 7.1 与会议处理的交互

```
┌──────────────────┐     读取上下文      ┌──────────────────┐
│   会议处理 Agent  │ ──────────────────▶ │   记忆系统        │
│                  │                     │                  │
│  (meeting_agent) │ ◀────────────────── │ (memory/writer)  │
└──────────────────┘     更新记忆        └──────────────────┘
```

- **会议处理前**：读取 context.md、actions.md、最近纪要
- **会议处理后**：更新 context.md、timeline.md、actions.md

### 7.2 与实时转写的交互

```
┌──────────────────┐     Context Pack     ┌──────────────────┐
│   实时转写页面    │ ◀─────────────────── │   后端 API       │
│                  │   /api/workspace/    │                  │
│  (realtime.js)   │   context-pack       │ (routes.py)      │
└──────────────────┘                      └──────────────────┘
```

- 实时转写启动时请求 Context Pack
- Pack 中的 realtimePrompt 注入到 OpenAI Realtime Session

### 7.3 与 Web 工作台的交互

- **查看**：项目详情页展示 context.md、timeline.md、actions.md
- **编辑**：通过 SlideOver 面板编辑 _context.md
- **同步**：手动触发待办同步（从纪要重新提取）

---

## 8. 配置项

### 8.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MEETINGS_DIR` | `./meetings` | 会议目录（单项目模式） |
| `PROJECTS_DIR` | - | 项目根目录（多项目模式） |
| `RECENT_MINUTES_COUNT` | `5` | 最近纪要数量 |

### 8.2 文件常量

```python
CONTEXT_FILE = "context.md"         # 项目上下文
TIMELINE_FILE = "timeline.md"       # 项目时间线
ACTIONS_FILE = "actions.md"         # 待办事项
PRE_HINT_FILE = "pre_meeting_hint.md"  # 会前提示
```

---

## 9. 错误处理

### 9.1 常见错误

| 错误 | 处理方式 |
|------|----------|
| 记忆文件不存在 | 返回空内容或初始化空结构 |
| 文件解析失败 | 记录警告日志，返回空列表 |
| 日期格式错误 | 忽略该字段，设为 None |

### 9.2 日志记录

```python
logger = logging.getLogger("meeting_agent.memory")

# 成功操作
logger.info("保存待办列表: %s (%d 项)", actions_file, len(actions))
logger.info("记忆更新完成: %s", meeting_dir.name)

# 警告
logger.warning("加载项目上下文失败: %s", e)
logger.warning("解析会议纪要失败 %s: %s", meeting_dir.name, e)

# 错误
logger.error("保存待办列表失败: %s", e)
```

---

## 10. 性能考量

### 10.1 性能目标

- 记忆加载：< 100ms
- 记忆更新：< 500ms
- Context Pack 构建：< 200ms

### 10.2 优化策略

1. **缓存**：ActionsManager 使用实例缓存 `_actions` 避免重复解析
2. **增量更新**：context.md 采用追加方式而非全量重写
3. **限制数量**：
   - Context Pack 最多包含 30 个术语
   - 最近纪要最多 5 份
   - 近期会议最多 4 次

### 10.3 瓶颈分析

- 大量会议时扫描目录可能较慢
- 解析大型 actions.md 文件需要正则匹配

---

## 11. 未来规划

### 11.1 已知限制

1. context.md 的增量更新目前是简单追加，可能产生冗余
2. 待办去重依赖文本相似度，可能误判
3. 时间线按月份组织，不支持自定义分组

### 11.2 待优化项

1. 实现 context.md 的智能合并（而非追加）
2. 支持待办的依赖关系和阻塞标记
3. 增加记忆文件的版本历史

### 11.3 扩展方向

1. **知识图谱**：从会议纪要中提取实体关系
2. **智能问答**：基于项目记忆的 RAG 问答
3. **趋势分析**：分析项目进度和风险趋势
