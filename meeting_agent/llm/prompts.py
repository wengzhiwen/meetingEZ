"""
Prompt 模板构建器
"""

from __future__ import annotations

from typing import Optional

from meeting_agent.models import MeetingMeta, MeetingType


class PromptBuilder:
    """Prompt 模板构建器"""

    SYSTEM_PROMPT = """你是一位专业的项目管理助手，负责分析会议录音转写文本并维护项目记忆。

## 你的职责

1. **生成会议纪要** - 根据会议类型使用对应模板，客观记录讨论内容
2. **提取行动项** - 识别明确的待办事项，包含负责人和截止时间
3. **追踪完成状态** - 对比现有待办，标记已完成或提及的事项
4. **更新时间线** - 提取关键决策、里程碑、风险事件
5. **维护项目上下文** - 更新项目状态、风险、进展

## 输出要求

1. **客观准确** - 只记录会议中实际讨论的内容，不臆测
2. **结构清晰** - 使用标准格式，便于阅读和检索
3. **关联追踪** - 将新待办与现有待办关联，识别重复或延续
4. **风险敏感** - 主动识别潜在风险和阻塞项
5. **智能提醒** - 指出未被跟进的重要事项

## 会议类型识别

- **review** (评审会): 侧重决策、结论、方案对比
- **weekly** (周会): 侧重要进度、计划、阻塞
- **brainstorm** (头脑风暴): 侧重创意、发散、不追求结论
- **retro** (复盘会): 侧重问题分析、改进措施
- **kickoff** (启动会): 侧重目标、分工、时间线
- **other** (其他): 通用模板

## 人名处理

- 使用标准名称，如果识别到别名请映射到标准名称
- 如果识别到新人，在输出中标注

## 说话人信息

- 转写文本中可能包含说话人标识（如 "Speaker 0", "Speaker 1" 等）
- 这些标识由 ASR 引擎自动推断，仅供参考，**不完全可靠**
- 可能出现同一人被分配多个 Speaker ID，或不同人被合并为同一 ID 的情况
- 请结合内容语义、上下文和参会人员名单综合判断实际发言者
- 在纪要和行动项中，优先使用参会人员姓名而非 Speaker ID
- 如果无法确定发言者，不要强行分配，可以用模糊描述
"""

    @classmethod
    def build_analysis_prompt(
        cls,
        transcript_text: str,
        meeting_meta: Optional[MeetingMeta] = None,
        project_context: Optional[str] = None,
        existing_actions: Optional[str] = None,
        recent_minutes: Optional[list[str]] = None,
        pre_hint: Optional[str] = None,
        people_info: Optional[str] = None,
        glossary_context: Optional[str] = None,
        has_speaker_info: bool = False,
    ) -> str:
        """构建分析 Prompt"""

        sections = []

        # 术语表和人工维护的上下文（_context.md）
        if glossary_context:
            sections.append(f"""## 术语与背景知识（供参考）

{glossary_context}""")

        # 项目上下文
        if project_context:
            sections.append(f"""## 项目上下文

{project_context}""")

        # 人员信息
        if people_info:
            sections.append(f"""## 人员角色

{people_info}""")

        # 现有待办
        if existing_actions:
            sections.append(f"""## 现有待办事项

{existing_actions}""")

        # 会议前提示
        if pre_hint:
            sections.append(f"""## 会议前提示（供参考）

{pre_hint}""")

        # 历史纪要
        if recent_minutes:
            history = "\n---\n".join(recent_minutes)
            sections.append(f"""## 历史纪要参考（最近{len(recent_minutes)}份）

{history}""")

        # 本次会议信息
        if meeting_meta:
            participants = "、".join(
                meeting_meta.participants) if meeting_meta.participants else "未指定"
            notes = meeting_meta.notes or "无特别说明"
            secondary_language = meeting_meta.effective_secondary_language or "无"

            sections.append(f"""## 本次会议信息

- **日期**: {meeting_meta.date}
- **名称**: {meeting_meta.title}
- **类型**: {meeting_meta.type.value}
- **语言模式**: {meeting_meta.language_mode.value}
- **主要语言**: {meeting_meta.effective_primary_language}
- **第二语言**: {secondary_language}
- **参会人员**: {participants}
- **特别关注**: {notes}""")

        # 转写文本
        speaker_note = ""
        if has_speaker_info:
            speaker_note = (
                "\n> **注意**：以下转写文本包含 ASR 引擎自动推断的说话人标识（Speaker 0/1/...），"
                "仅供参考，可能存在误判。请结合参会人员名单和上下文综合判断。\n"
            )
        sections.append(f"""## 转写文本
{speaker_note}
{transcript_text}""")

        # 输出要求
        sections.append("""---

请分析以上会议内容，输出以下信息（JSON 格式）：

```json
{
  "meeting_type": "会议类型 (review/weekly/brainstorm/retro/kickoff/other)",
  "summary": "会议概要（3-5 句话）",
  "key_decisions": [
    {"decision": "决策内容", "method": "决策方式（全员同意/投票/负责人决定）"}
  ],
  "risks": [
    {"risk": "风险描述", "level": "high/medium/low", "impact": "影响"}
  ],
  "minutes": "完整的会议纪要（Markdown 格式，使用对应类型的模板）",
  "new_actions": [
    {
      "task": "任务描述",
      "owner": "负责人",
      "due_date": "截止日期（YYYY-MM-DD）",
      "priority": "P0/P1/P2"
    }
  ],
  "completed_actions": ["已完成的待办 ID（如 ACT-001）"],
  "mentioned_actions": ["被提及的待办 ID"],
  "timeline_entry": {
    "decisions": ["决策1", "决策2"],
    "milestone": "里程碑描述（如果有）",
    "risks": [{"risk": "风险", "level": "high/medium/low"}]
  },
  "context_updates": {
    "new_decisions": [],
    "milestone_updates": [],
    "risk_updates": []
  },
  "term_suggestions": [
    {
      "canonical": "标准术语名称",
      "aliases": ["可能的错误识别/别名"],
      "type": "person/product/technical/project/abbreviation/other",
      "context": "该术语在会议中的上下文或解释"
    }
  ],
  "context_questions": [
    {
      "topic": "需要解释的主题/概念",
      "question": "具体问题，如'XX是什么？'或'XX和YY的关系是什么？'",
      "reason": "为什么需要人工解释（如：多次出现但含义不明确、涉及业务逻辑等）"
    }
  ]
}
```

**术语建议说明**：
- 发现会议中出现的专业术语、人名、产品名、缩写等
- 如果 ASR 可能识别错误（如人名被识别成其他字），在 aliases 中列出可能的错误形式
- 只建议有价值的术语，不要建议常见词汇
- 如果没有发现新术语，返回空数组

**需要人工解释的项目说明**：
- 识别会议中反复出现但含义不明确的概念
- 标记需要业务专家解释的内容
- 这些问题会被添加到项目的背景说明中，供人工补充
- 只提出真正需要解释的问题，不要提出通用问题
- **严格去重**：如果"术语与背景知识"中已有某个概念的解释或已有待解答的问题（无论措辞是否相同），则不要再提出该概念或其任何变体、子问题。宁可少提也不要重复。

请确保输出是有效的 JSON 格式。""")

        return "\n\n".join(sections)

    @classmethod
    def get_minutes_template(cls, meeting_type: MeetingType) -> str:
        """获取会议纪要模板"""
        templates = {
            MeetingType.REVIEW: cls.REVIEW_TEMPLATE,
            MeetingType.WEEKLY: cls.WEEKLY_TEMPLATE,
            MeetingType.BRAINSTORM: cls.BRAINSTORM_TEMPLATE,
            MeetingType.RETRO: cls.RETRO_TEMPLATE,
            MeetingType.KICKOFF: cls.KICKOFF_TEMPLATE,
            MeetingType.OTHER: cls.GENERAL_TEMPLATE,
        }
        return templates.get(meeting_type, cls.GENERAL_TEMPLATE)

    REVIEW_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**类型：** 评审会
**主持人：** {host}
**参会人员：** {participants}

---

## 一、会议概要

{summary}

## 二、评审内容

### 2.1 {topic1}

**方案对比：**
| 方案 | 优点 | 缺点 | 评分 |
|------|------|------|------|
| ... | ... | ... | ... |

**讨论要点：**
- ...

**决策结论：** ...

### 2.2 {topic2}

...

## 三、行动项 (Action Items)

| ID | 任务内容 | 负责人 | 截止日期 | 状态 |
|----|----------|--------|----------|------|
| ... | ... | ... | ... | 新增 |

## 四、风险与问题

- 🔴 高风险项
- 🟡 中风险项

## 五、下次会议预告

...

---

*本纪要由 AI 生成*
"""

    WEEKLY_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**类型：** 周会
**参会人员：** {participants}

---

## 一、上周进展回顾

| 任务 | 负责人 | 计划完成 | 实际状态 |
|------|--------|----------|----------|
| ... | ... | ... | ... |

## 二、本周工作计划

| 任务 | 负责人 | 计划完成 | 优先级 |
|------|--------|----------|--------|
| ... | ... | ... | ... |

## 三、阻塞与风险

- 🚫 阻塞项
- ⚠️ 风险项

## 四、同步信息

- ...

## 五、行动项

| ID | 任务内容 | 负责人 | 截止日期 |
|----|----------|--------|----------|
| ... | ... | ... | ... |

---

*本纪要由 AI 生成*
"""

    BRAINSTORM_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**类型：** 头脑风暴
**参会人员：** {participants}

---

## 一、会议主题

{summary}

## 二、创意收集

### 主题 1: {topic1}
- 想法 1
- 想法 2
- ...

### 主题 2: {topic2}
- ...

## 三、高价值想法

| 想法 | 提出者 | 可行性 | 价值 |
|------|--------|--------|------|
| ... | ... | ⭐⭐⭐ | ⭐⭐⭐⭐ |

## 四、后续跟进

- 需要进一步讨论的想法
- ...

---

*本纪要由 AI 生成*
"""

    RETRO_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**类型：** 复盘会
**参会人员：** {participants}

---

## 一、回顾目标

{summary}

## 二、做得好的 (What went well)

- ...
- ...

## 三、需要改进的 (What could be improved)

- ...
- ...

## 四、行动方案 (Action items)

| 问题 | 改进措施 | 负责人 | 截止日期 |
|------|----------|--------|----------|
| ... | ... | ... | ... |

## 五、经验总结

- ...

---

*本纪要由 AI 生成*
"""

    KICKOFF_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**类型：** 启动会
**参会人员：** {participants}

---

## 一、项目背景

{summary}

## 二、项目目标

- 目标 1
- 目标 2

## 三、团队分工

| 成员 | 角色 | 职责 |
|------|------|------|
| ... | ... | ... |

## 四、里程碑计划

| 里程碑 | 预计时间 | 负责人 |
|--------|----------|--------|
| ... | ... | ... |

## 五、风险与依赖

- ...
- ...

## 六、下一步行动

| 任务 | 负责人 | 截止日期 |
|------|--------|----------|
| ... | ... | ... |

---

*本纪要由 AI 生成*
"""

    GENERAL_TEMPLATE = """# 会议纪要：{title}

**日期：** {date}
**参会人员：** {participants}

---

## 一、会议概要

{summary}

## 二、讨论内容

### 2.1 {topic1}

- ...

### 2.2 {topic2}

- ...

## 三、关键决策

- ...

## 四、行动项

| ID | 任务内容 | 负责人 | 截止日期 |
|----|----------|--------|----------|
| ... | ... | ... | ... |

## 五、风险与问题

- ...

---

*本纪要由 AI 生成*
"""

    @classmethod
    def build_pre_meeting_hint_prompt(
        cls,
        meeting_meta: MeetingMeta,
        context_md: Optional[str] = None,
        actions_md: Optional[str] = None,
    ) -> str:
        """构建会议前提示的 Prompt"""
        sections = [
            f"""请为以下会议生成会议前提示清单。

## 会议信息

- 日期: {meeting_meta.date}
- 名称: {meeting_meta.title}
- 语言模式: {meeting_meta.language_mode.value}
- 主要语言: {meeting_meta.effective_primary_language}
- 第二语言: {meeting_meta.effective_secondary_language or '无'}
- 参会人员: {', '.join(meeting_meta.participants) if meeting_meta.participants else '未指定'}
- 特别关注: {meeting_meta.notes or '无'}
"""
        ]

        if context_md:
            sections.append(f"""## 项目上下文

{context_md}""")

        if actions_md:
            sections.append(f"""## 现有待办

{actions_md}""")

        sections.append("""
请输出会议前提示，包括：

1. **上次承诺本次汇报的事项** - 如果有的话
2. **超期待办** - 截止日期已过的待办
3. **长期未提及的待办** - 创建后未在后续会议中被提及
4. **建议讨论议题** - 基于项目状态的建议
5. **近期关键决策回顾** - 最近的重要决策

输出格式为 Markdown。""")

        return "\n\n".join(sections)
