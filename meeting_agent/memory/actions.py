"""
待办事项追踪管理
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config, ACTIONS_FILE
from meeting_agent.models import ActionItem, ActionType

logger = logging.getLogger("meeting_agent.memory")


class ActionsManager:
    """待办事项管理器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._actions: Optional[list[ActionItem]] = None

    def load(self, project_dir: Optional[Path] = None) -> list[ActionItem]:
        """加载待办列表"""
        if self._actions is not None:
            return self._actions

        base_dir = project_dir or self.config.meetings_dir
        actions_file = base_dir / ACTIONS_FILE

        if not actions_file.exists():
            self._actions = []
            return self._actions

        try:
            content = actions_file.read_text(encoding="utf-8")
            self._actions = self._parse_actions_md(content)
            return self._actions
        except Exception as e:
            logger.warning("加载待办列表失败: %s", e)
            self._actions = []
            return self._actions

    def save(self, actions: list[ActionItem], project_dir: Optional[Path] = None):
        """保存待办列表"""
        base_dir = project_dir or self.config.meetings_dir
        actions_file = base_dir / ACTIONS_FILE

        content = self._generate_actions_md(actions)

        try:
            actions_file.write_text(content, encoding="utf-8")
            self._actions = actions
            logger.info("保存待办列表: %s (%d 项)", actions_file, len(actions))
        except Exception as e:
            logger.error("保存待办列表失败: %s", e)

    def add(
        self,
        task: str,
        owner: str,
        due_date: Optional[date],
        meeting_dir: str,
        priority: str = "P1",
        project_dir: Optional[Path] = None,
    ) -> ActionItem:
        """添加新待办"""
        actions = self.load(project_dir)

        # 检查是否已存在相同任务（去重）
        task_normalized = task.strip().lower()
        for existing in actions:
            if existing.task.strip().lower() == task_normalized:
                # 任务已存在，更新提及记录并返回
                if meeting_dir and meeting_dir not in existing.mentions:
                    existing.mentions.append(meeting_dir)
                if due_date and not existing.due_date:
                    existing.due_date = due_date
                self.save(actions, project_dir)
                logger.debug("待办已存在，跳过添加: %s", task[:50])
                return existing

        # 生成 ID
        existing_ids = [a.id for a in actions]
        next_num = 1
        while f"A{next_num:03d}" in existing_ids:
            next_num += 1

        action = ActionItem(
            id=f"A{next_num:03d}",
            task=task,
            owner=owner,
            due_date=due_date,
            status=ActionType.PENDING,
            created_at=datetime.now(),
            created_in_meeting=meeting_dir,
            priority=priority,
        )

        actions.append(action)
        self.save(actions, project_dir)

        return action

    def mark_completed(self, action_id: str, project_dir: Optional[Path] = None) -> bool:
        """标记待办为已完成"""
        actions = self.load(project_dir)

        for action in actions:
            if action.id == action_id:
                action.status = ActionType.COMPLETED
                self.save(actions, project_dir)
                return True

        return False

    def mark_mentioned(self, action_id: str, meeting_dir: str, project_dir: Optional[Path] = None):
        """标记待办被提及"""
        actions = self.load(project_dir)

        for action in actions:
            if action.id == action_id:
                if meeting_dir not in action.mentions:
                    action.mentions.append(meeting_dir)
                self.save(actions, project_dir)
                return

    def get_overdue(self, project_dir: Optional[Path] = None) -> list[ActionItem]:
        """获取超期待办"""
        actions = self.load(project_dir)
        today = date.today()

        return [
            a for a in actions
            if a.due_date and a.due_date < today and a.status not in [ActionType.COMPLETED]
        ]

    def get_unmentioned_days(self, action: ActionItem) -> int:
        """获取待办未被提及的天数"""
        if not action.mentions:
            return (datetime.now() - action.created_at).days

        # 假设 mentions 中的是会议目录名，格式为 YYYY-MM-DD_*
        latest_mention = None
        for m in action.mentions:
            try:
                mention_date = datetime.strptime(m[:10], "%Y-%m-%d")
                if latest_mention is None or mention_date > latest_mention:
                    latest_mention = mention_date
            except ValueError:
                continue

        if latest_mention:
            return (datetime.now() - latest_mention).days
        return (datetime.now() - action.created_at).days

    def get_stats(self, project_dir: Optional[Path] = None) -> dict:
        """获取待办统计"""
        actions = self.load(project_dir)

        return {
            "total": len(actions),
            "completed": sum(1 for a in actions if a.status == ActionType.COMPLETED),
            "in_progress": sum(1 for a in actions if a.status == ActionType.IN_PROGRESS),
            "pending": sum(1 for a in actions if a.status == ActionType.PENDING),
            "overdue": len(self.get_overdue(project_dir)),
        }

    def _parse_actions_md(self, content: str) -> list[ActionItem]:
        """解析 actions.md 文件"""
        actions = []

        # 简单解析：查找表格行
        # 格式: | A001 | 任务描述 | 负责人 | 截止日期 | 状态 |
        table_pattern = r"\|\s*([A-Z]\d{3})\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|"

        for match in re.finditer(table_pattern, content):
            action_id = match.group(1).strip()
            task = match.group(2).strip()
            owner = match.group(3).strip()
            due_str = match.group(4).strip()
            status_str = match.group(5).strip()

            # 解析日期
            due_date = None
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
            except ValueError:
                pass

            # 解析状态
            status = ActionType.PENDING
            if "完成" in status_str or "✅" in status_str:
                status = ActionType.COMPLETED
            elif "进行中" in status_str or "🔄" in status_str:
                status = ActionType.IN_PROGRESS
            elif "超期" in status_str or "🔴" in status_str:
                status = ActionType.OVERDUE

            actions.append(ActionItem(
                id=action_id,
                task=task,
                owner=owner,
                due_date=due_date,
                status=status,
                created_at=datetime.now(),  # 无法从文件获取
                created_in_meeting="",
            ))

        return actions

    def _generate_actions_md(self, actions: list[ActionItem]) -> str:
        """生成 actions.md 内容"""
        today = date.today()

        # 分类
        overdue = [a for a in actions if a.due_date and a.due_date < today and a.status != ActionType.COMPLETED]
        in_progress = [a for a in actions if a.status == ActionType.IN_PROGRESS]
        pending = [a for a in actions if a.status == ActionType.PENDING and a not in overdue]
        completed = [a for a in actions if a.status == ActionType.COMPLETED]

        lines = [
            "# 待办事项追踪",
            "",
            f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
            "## 状态统计",
            "",
            "| 状态 | 数量 |",
            "|------|------|",
            f"| 🔴 超期 | {len(overdue)} |",
            f"| 🟡 进行中 | {len(in_progress)} |",
            f"| ⏳ 待处理 | {len(pending)} |",
            f"| ✅ 已完成 | {len(completed)} |",
            "",
            "---",
        ]

        # 超期待办
        if overdue:
            lines.extend(["", "## 🔴 超期待办", ""])
            for a in sorted(overdue, key=lambda x: x.due_date or date.min):
                overdue_days = (today - a.due_date).days if a.due_date else 0
                lines.extend([
                    f"### {a.id} - {a.task}",
                    f"- **负责人**: {a.owner}",
                    f"- **截止日期**: {a.due_date}",
                    f"- **超期天数**: {overdue_days} 天",
                    f"- **来源会议**: {a.created_in_meeting}",
                    "",
                ])

        # 进行中
        if in_progress:
            lines.extend(["", "## 🟡 进行中", ""])
            for a in in_progress:
                due_info = f"（截止 {a.due_date}）" if a.due_date else ""
                lines.append(f"- **{a.id}** {a.task} - {a.owner} {due_info}")
            lines.append("")

        # 待处理
        if pending:
            lines.extend(["", "## ⏳ 待处理", ""])
            for a in sorted(pending, key=lambda x: x.due_date or date.max):
                due_info = f"（截止 {a.due_date}）" if a.due_date else ""
                lines.append(f"- **{a.id}** {a.task} - {a.owner} {due_info}")
            lines.append("")

        # 全部列表
        lines.extend([
            "---",
            "",
            "## 全部待办列表",
            "",
            "| ID | 任务 | 负责人 | 截止 | 状态 |",
            "|----|------|--------|------|------|",
        ])

        for a in actions:
            status_emoji = {
                ActionType.COMPLETED: "✅",
                ActionType.IN_PROGRESS: "🟡",
                ActionType.OVERDUE: "🔴",
                ActionType.PENDING: "⏳",
            }.get(a.status, "⏳")

            due_str = str(a.due_date) if a.due_date else "-"
            lines.append(f"| {a.id} | {a.task} | {a.owner} | {due_str} | {status_emoji} |")

        return "\n".join(lines)
