"""
项目上下文管理
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config, CONTEXT_FILE

logger = logging.getLogger("meeting_agent.memory")


class ContextManager:
    """项目上下文管理器"""

    TEMPLATE = """# 项目上下文

> 最后更新: {last_updated}
> 会议总数: {total_meetings}
> 待办总数: {total_actions}（完成 {completed_actions}，进行中 {in_progress_actions}，超期 {overdue_actions}）

---

## 项目概述

{project_overview}

---

## 核心决策记录

{decisions_table}

---

## 里程碑进度

{milestone_progress}

---

## 风险与阻塞

{risks_section}

---

## 近期关注

### 本周待办
{week_actions}

### 超期待办
{overdue_actions_list}

### 下次会议
{next_meeting}
"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def load(self, project_dir: Optional[Path] = None) -> Optional[str]:
        """加载项目上下文"""
        base_dir = project_dir or self.config.meetings_dir
        context_file = base_dir / CONTEXT_FILE

        if not context_file.exists():
            return None

        try:
            return context_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("加载项目上下文失败: %s", e)
            return None

    def save(self, content: str, project_dir: Optional[Path] = None):
        """保存项目上下文"""
        base_dir = project_dir or self.config.meetings_dir
        context_file = base_dir / CONTEXT_FILE

        try:
            context_file.write_text(content, encoding="utf-8")
            logger.info("保存项目上下文: %s", context_file)
        except Exception as e:
            logger.error("保存项目上下文失败: %s", e)

    def update(
        self,
        project_dir: Optional[Path] = None,
        new_decisions: Optional[list[dict]] = None,
        milestone_updates: Optional[list[dict]] = None,
        risk_updates: Optional[list[dict]] = None,
        stats: Optional[dict] = None,
    ):
        """更新项目上下文"""
        # TODO: 实现增量更新逻辑
        # 当前版本：简单地追加新信息到文件末尾
        base_dir = project_dir or self.config.meetings_dir
        context_file = base_dir / CONTEXT_FILE

        existing = self.load(project_dir) or ""

        # 构建更新内容
        updates = []
        updates.append(f"\n\n---\n\n**更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}**\n")

        if new_decisions:
            updates.append("\n### 新增决策\n")
            for d in new_decisions:
                if isinstance(d, dict):
                    updates.append(f"- {d.get('date', '')}: {d.get('decision', '')}\n")
                else:
                    updates.append(f"- {d}\n")

        if milestone_updates:
            updates.append("\n### 里程碑更新\n")
            for m in milestone_updates:
                if isinstance(m, dict):
                    updates.append(f"- {m.get('milestone', m)}\n")
                else:
                    updates.append(f"- {m}\n")

        if risk_updates:
            updates.append("\n### 风险更新\n")
            for r in risk_updates:
                if isinstance(r, dict):
                    level = r.get('level', 'medium')
                    emoji = "🔴" if level == "high" else "🟡" if level == "medium" else "🟢"
                    updates.append(f"- {emoji} {r.get('risk', '')}\n")
                else:
                    updates.append(f"- 🟡 {r}\n")

        # 追加到文件
        with open(context_file, "a", encoding="utf-8") as f:
            f.write("".join(updates))

    @staticmethod
    def _format_team_list(team) -> str:
        """将 TeamMember 列表格式化为 Markdown。"""
        if not team:
            return '- 待添加'
        lines = []
        for m in team:
            if hasattr(m, 'name'):
                parts = [m.name]
                if m.nickname:
                    parts[0] = f"{m.name}（{m.nickname}）"
                if m.role:
                    parts.append(m.role)
                lines.append(f"- {'：'.join(parts)}")
            else:
                lines.append(f"- {m}")
        return chr(10).join(lines)

    def create_initial(
        self,
        project_name: str,
        description: str = "",
        team=None,
        start_date: Optional[str] = None,
        project_dir: Optional[Path] = None,
    ) -> str:
        """创建初始项目上下文"""
        team_text = self._format_team_list(team)
        content = f"""# 项目上下文

> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}
> 会议总数: 0
> 待办总数: 0

---

## 项目概述

**名称**: {project_name}
**描述**: {description or '暂无描述'}
**启动日期**: {start_date or '待定'}

**团队**:
{team_text}

---

## 核心决策记录

| 日期 | 决策内容 | 决策方式 |
|------|----------|----------|
| *暂无* | | |

---

## 里程碑进度

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| *暂无* | | |

---

## 风险与阻塞

### 🔴 高风险
*暂无*

### 🟡 中风险
*暂无*

---

## 近期关注

### 本周待办
*暂无*

### 超期待办
*暂无*

### 下次会议
*待安排*
"""
        self.save(content, project_dir)
        return content
