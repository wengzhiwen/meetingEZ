"""
待办事项追踪管理
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config, ACTIONS_FILE, MINUTES_FILE
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

        # 统计
        overdue = [a for a in actions if a.due_date and a.due_date < today and a.status != ActionType.COMPLETED]
        in_progress = [a for a in actions if a.status == ActionType.IN_PROGRESS]
        pending = [a for a in actions if a.status == ActionType.PENDING and a not in overdue]
        completed = [a for a in actions if a.status == ActionType.COMPLETED]

        lines = [
            "# 待办事项追踪",
            "",
            f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"🔴 {len(overdue)} | 🟡 {len(in_progress)} | ⏳ {len(pending)} | ✅ {len(completed)}",
            "",
            "| ID | 任务 | 负责人 | 截止 | 状态 |",
            "|----|------|--------|------|------|",
        ]

        # 按状态排序：超期 > 进行中 > 待处理 > 已完成
        def sort_key(a: ActionItem) -> tuple:
            status_order = {
                ActionType.OVERDUE: 0,
                ActionType.IN_PROGRESS: 1,
                ActionType.PENDING: 2,
                ActionType.COMPLETED: 3,
            }
            # 超期待办按截止日期升序，其他按截止日期升序
            is_overdue = a.due_date and a.due_date < today and a.status != ActionType.COMPLETED
            if is_overdue:
                return (0, a.due_date or date.min)
            return (status_order.get(a.status, 2), a.due_date or date.max)

        for a in sorted(actions, key=sort_key):
            # 动态计算状态
            if a.status == ActionType.COMPLETED:
                status_str = "✅"
            elif a.status == ActionType.IN_PROGRESS:
                status_str = "🟡"
            elif a.due_date and a.due_date < today:
                status_str = "🔴"
            else:
                status_str = "⏳"

            due_str = str(a.due_date) if a.due_date else "-"
            lines.append(f"| {a.id} | {a.task} | {a.owner} | {due_str} | {status_str} |")

        return "\n".join(lines)

    def _find_similar_action(
        self, task: str, actions: list[ActionItem], threshold: float = 0.4
    ) -> Optional[ActionItem]:
        """
        查找相似的任务

        使用多种匹配策略：
        1. 完全匹配
        2. 包含关系匹配
        3. 关键词重叠度匹配

        Args:
            task: 要查找的任务
            actions: 现有待办列表
            threshold: 相似度阈值 (0-1)

        Returns:
            找到的相似任务，或 None
        """
        import re

        task_lower = task.strip().lower()

        # 提取关键词（中文按字符，英文按单词）
        def extract_keywords(text: str) -> set[str]:
            # 移除标点符号，替换为空格
            text = re.sub(r'[，。！？、；：""''（）【】 ]+', ' ', text)
            words = []
            for part in text.split():
                if part:
                    # 如果包含中文，按字符拆分
                    if any('\u4e00' <= c <= '\u9fff' for c in part):
                        words.extend(c for c in part if '\u4e00' <= c <= '\u9fff')
                    else:
                        words.append(part.lower())
            return set(words)

        task_keywords = extract_keywords(task_lower)

        best_match = None
        best_score = threshold

        for action in actions:
            action_lower = action.task.strip().lower()

            # 1. 完全匹配
            if task_lower == action_lower:
                return action

            # 2. 包含关系匹配（较短的是较长的子串）
            shorter = min(task_lower, action_lower, key=len)
            longer = max(task_lower, action_lower, key=len)
            if len(shorter) >= 10 and shorter in longer:
                return action

            # 3. 关键词重叠度匹配
            action_keywords = extract_keywords(action_lower)
            common = task_keywords & action_keywords
            if not common:
                continue

            # 计算重叠度（相对于较短任务的关键词数量）
            min_len = min(len(task_keywords), len(action_keywords))
            if min_len == 0:
                continue

            score = len(common) / min_len

            # 如果有 5 个以上共同关键词且重叠度 >= 50%，也认为是匹配
            if len(common) >= 5 and score >= 0.5:
                return action

            # 如果重叠度很高，认为是同一个任务
            if score >= 0.7:
                return action

            if score > best_score:
                best_score = score
                best_match = action

        return best_match

    def sync_from_minutes(self, project_dir: Optional[Path] = None) -> dict:
        """
        从所有会议纪要中同步待办事项

        扫描项目目录下的所有会议纪要，解析待办事项并更新到 actions.md

        Returns:
            统计信息 {"added": int, "updated": int, "completed": int, "meetings": int}
        """
        base_dir = project_dir or self.config.meetings_dir

        if not base_dir.exists():
            logger.warning("项目目录不存在: %s", base_dir)
            return {"added": 0, "updated": 0, "completed": 0, "meetings": 0}

        # 加载现有待办
        actions = self.load(project_dir)

        stats = {"added": 0, "updated": 0, "completed": 0, "meetings": 0}

        # 扫描所有会议目录
        meeting_dirs = [
            item for item in sorted(base_dir.iterdir())
            if item.is_dir() and not item.name.startswith(".")
        ]

        for meeting_dir in meeting_dirs:
            minutes_file = meeting_dir / MINUTES_FILE
            if not minutes_file.exists():
                continue

            try:
                content = minutes_file.read_text(encoding="utf-8")
                result = self._extract_actions_from_minutes(content, meeting_dir.name)

                # 处理新增待办
                for action_data in result.get("new_actions", []):
                    task = action_data.get("task", "").strip()
                    if not task:
                        continue

                    # 查找相似任务
                    similar = self._find_similar_action(task, actions)

                    if similar:
                        # 已存在相似任务，更新信息
                        if action_data.get("due_date") and not similar.due_date:
                            similar.due_date = action_data["due_date"]
                        if meeting_dir.name not in similar.mentions:
                            similar.mentions.append(meeting_dir.name)
                        stats["updated"] += 1
                    else:
                        # 新增 - 直接创建 ActionItem，不调用 add 方法
                        existing_ids = {a.id for a in actions}
                        next_num = 1
                        while f"A{next_num:03d}" in existing_ids:
                            next_num += 1

                        new_action = ActionItem(
                            id=f"A{next_num:03d}",
                            task=task,
                            owner=action_data.get("owner", ""),
                            due_date=action_data.get("due_date"),
                            status=ActionType.PENDING,
                            created_at=datetime.now(),
                            created_in_meeting=meeting_dir.name,
                            mentions=[meeting_dir.name],
                        )
                        actions.append(new_action)
                        stats["added"] += 1

                # 处理已完成的待办
                for task_text in result.get("completed", []):
                    # 尝试匹配现有待办
                    similar = self._find_similar_action(task_text, actions)
                    if similar and similar.status != ActionType.COMPLETED:
                        similar.status = ActionType.COMPLETED
                        stats["completed"] += 1

                stats["meetings"] += 1

            except Exception as e:
                logger.warning("解析会议纪要失败 %s: %s", meeting_dir.name, e)
                continue

        # 保存更新后的待办
        if stats["added"] > 0 or stats["updated"] > 0 or stats["completed"] > 0:
            self.save(actions, project_dir)

        logger.info(
            "同步完成: 扫描 %d 个会议，新增 %d，更新 %d，完成 %d",
            stats["meetings"], stats["added"], stats["updated"], stats["completed"]
        )

        return stats

    def _extract_actions_from_minutes(self, content: str, meeting_name: str) -> dict:
        """
        从会议纪要内容中提取待办事项

        支持的格式：
        - 负责人：任务描述
        - 负责人：任务描述（截止 YYYY-MM-DD）
        - 负责人：任务描述（截止日期：YYYY-MM-DD）
        """
        result = {
            "new_actions": [],
            "completed": [],
        }

        # 查找待办事项部分 - 匹配 "## 六、待办事项" 或 "## 待办事项"
        # 使用非贪婪匹配，直到下一个 ## 开头的行或文件结束
        action_section_match = re.search(
            r"^##\s*[^#\n]*待办[^#\n]*\n(.*?)(?=^##\s[^#]|\Z)",
            content,
            re.MULTILINE | re.DOTALL
        )

        if not action_section_match:
            return result

        action_section = action_section_match.group(1)

        # 提取新增待办 - 匹配 "### 新增待办" 部分
        new_action_match = re.search(
            r"###\s*新增待办\s*\n(.*?)(?=###|$)",
            action_section,
            re.DOTALL
        )

        if new_action_match:
            new_section = new_action_match.group(1)
            # 解析每行待办
            for line in new_section.split("\n"):
                line = line.strip()
                if not line.startswith("-"):
                    continue

                # 移除开头的 "- "
                line = line[1:].strip()
                if not line:
                    continue

                # 尝试解析格式: 负责人：任务描述（截止 日期）
                # 或: 负责人：任务描述
                match = re.match(
                    r"^([^：:]+)[：:]\s*(.+?)(?:\s*[（(]\s*截止[^）)]*?(\d{4}-\d{2}-\d{2})\s*[）)])?$",
                    line
                )

                if match:
                    owner = match.group(1).strip()
                    task = match.group(2).strip()
                    due_date = None

                    if match.group(3):
                        try:
                            due_date = datetime.strptime(match.group(3), "%Y-%m-%d").date()
                        except ValueError:
                            pass

                    result["new_actions"].append({
                        "owner": owner,
                        "task": task,
                        "due_date": due_date,
                    })
                elif line:
                    # 无法解析格式，作为纯任务处理
                    result["new_actions"].append({
                        "owner": "",
                        "task": line,
                        "due_date": None,
                    })

        # 提取已完成待办
        completed_match = re.search(
            r"###\s*已完成[^#\n]*\n(.*?)(?=###|$)",
            action_section,
            re.DOTALL
        )

        if completed_match:
            completed_section = completed_match.group(1)
            for line in completed_section.split("\n"):
                line = line.strip()
                if line.startswith("-"):
                    result["completed"].append(line[1:].strip())

        return result
