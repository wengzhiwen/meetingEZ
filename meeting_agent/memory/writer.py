"""
记忆写入器 - 整合所有记忆管理功能
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import (
    Config,
    CONTEXT_FILE,
    TIMELINE_FILE,
    ACTIONS_FILE,
    MINUTES_FILE,
    PRE_HINT_FILE,
)
from meeting_agent.memory.context import ContextManager
from meeting_agent.memory.actions import ActionsManager
from meeting_agent.memory.timeline import TimelineManager
from meeting_agent.models import (
    ActionItem,
    GPTAnalysisResult,
    MeetingMeta,
    TimelineEntry,
    MeetingType,
)

logger = logging.getLogger("meeting_agent.memory")


class MemoryWriter:
    """记忆写入器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.context_mgr = ContextManager(config)
        self.actions_mgr = ActionsManager(config)
        self.timeline_mgr = TimelineManager(config)

    def process_analysis_result(
        self,
        result: GPTAnalysisResult,
        meeting_meta: MeetingMeta,
        meeting_dir: Path,
        project_dir: Optional[Path] = None,
    ):
        """
        处理 GPT 分析结果，更新所有记忆文件

        Args:
            result: GPT 分析结果
            meeting_meta: 会议元信息
            meeting_dir: 会议目录
            project_dir: 项目目录
        """
        # 1. 保存会议纪要
        self._save_minutes(result.minutes, meeting_dir)

        # 2. 添加新待办
        for action_data in result.new_actions:
            due_date = None
            if action_data.get("due_date"):
                try:
                    due_date = datetime.strptime(action_data["due_date"], "%Y-%m-%d").date()
                except ValueError:
                    pass

            self.actions_mgr.add(
                task=action_data.get("task", ""),
                owner=action_data.get("owner", ""),
                due_date=due_date,
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
            entry = TimelineEntry(
                date=meeting_meta.date,
                title=meeting_meta.title,
                type=result.meeting_type,
                decisions=result.timeline_entry.get("decisions", []),
                milestone=result.timeline_entry.get("milestone"),
                risks=result.timeline_entry.get("risks", []),
                meeting_dir=meeting_dir.name,
            )
            self.timeline_mgr.add_entry(entry, project_dir)

        # 5. 更新项目上下文
        self.context_mgr.update(
            project_dir=project_dir,
            new_decisions=result.context_updates.get("new_decisions", []),
            milestone_updates=result.context_updates.get("milestone_updates", []),
            risk_updates=result.context_updates.get("risk_updates", []),
        )

        logger.info("记忆更新完成: %s", meeting_dir.name)

    def _save_minutes(self, minutes: str, meeting_dir: Path):
        """保存会议纪要"""
        minutes_file = meeting_dir / MINUTES_FILE

        # 添加生成时间戳
        if not minutes.endswith("*"):
            minutes += f"\n\n---\n\n*本纪要由 AI 生成，生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*"

        minutes_file.write_text(minutes, encoding="utf-8")
        logger.info("保存会议纪要: %s", minutes_file)

    def save_pre_meeting_hint(self, hint: str, meeting_dir: Path):
        """保存会议前提示"""
        hint_file = meeting_dir / PRE_HINT_FILE
        hint_file.write_text(hint, encoding="utf-8")
        logger.info("保存会议前提示: %s", hint_file)

    def get_context_for_meeting(
        self,
        project_dir: Optional[Path] = None,
    ) -> tuple[Optional[str], Optional[str], list[str]]:
        """
        获取会议所需的上下文信息

        Returns:
            (context_md, actions_md, recent_minutes_list)
        """
        context_md = self.context_mgr.load(project_dir)

        actions = self.actions_mgr.load(project_dir)
        actions_md = self.actions_mgr._generate_actions_md(actions) if actions else None

        # 获取最近的纪要
        recent_minutes = self._get_recent_minutes(project_dir)

        return context_md, actions_md, recent_minutes

    def _get_recent_minutes(
        self,
        project_dir: Optional[Path] = None,
        count: int = 5,
    ) -> list[str]:
        """获取最近的会议纪要"""
        base_dir = project_dir or self.config.meetings_dir

        if not base_dir.exists():
            return []

        # 查找所有会议目录
        meeting_dirs = []
        for item in base_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                minutes_file = item / MINUTES_FILE
                if minutes_file.exists():
                    meeting_dirs.append((item, minutes_file.stat().st_mtime))

        # 按修改时间排序
        meeting_dirs.sort(key=lambda x: x[1], reverse=True)

        # 读取最近的纪要
        recent = []
        for meeting_dir, _ in meeting_dirs[:count]:
            minutes_file = meeting_dir / MINUTES_FILE
            try:
                content = minutes_file.read_text(encoding="utf-8")
                recent.append(f"## {meeting_dir.name}\n\n{content}")
            except Exception:
                continue

        return recent

    def initialize_project(
        self,
        project_name: str,
        description: str = "",
        team=None,
        start_date: Optional[str] = None,
        project_dir: Optional[Path] = None,
    ):
        """初始化项目记忆文件"""
        base_dir = project_dir or self.config.meetings_dir
        base_dir.mkdir(parents=True, exist_ok=True)

        # 创建项目上下文
        self.context_mgr.create_initial(
            project_name=project_name,
            description=description,
            team=team,
            start_date=start_date,
            project_dir=project_dir,
        )

        # 创建空的待办列表
        self.actions_mgr.save([], project_dir)

        # 创建初始时间线
        self.timeline_mgr.save(f"""# 项目时间线

> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## {datetime.now().year}年{datetime.now().month}月

*暂无记录*

---

## 里程碑总览

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| *暂无* | | |

---

## 待决策事项

| 事项 | 状态 | 预计决策时间 |
|------|------|--------------|
| *暂无* | | |
""", project_dir)

        logger.info("项目初始化完成: %s", base_dir)
