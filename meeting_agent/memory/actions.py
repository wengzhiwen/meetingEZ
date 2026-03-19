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

    def reconcile(self, project_dir: Optional[Path] = None, auto_fix: bool = True) -> dict:
        """
        双向校验：从纪要中重新评估所有 actions 的重复性和状态

        流程：
        1. 收集所有纪要中的待办信息（新增 + 已完成）
        2. 对每个 action，找到纪要中最匹配的内容
        3. 检测重复：多个 actions 匹配到纪要中的同一任务
        4. 更新状态：如果 action 在纪要中被标记为已完成

        Args:
            project_dir: 项目目录
            auto_fix: 是否自动修复（合并重复、更新状态）

        Returns:
            {
                "duplicates": [[action_id, ...], ...],  # 重复组
                "should_complete": [action_id, ...],    # 应该完成的
                "fixed": {"merged": int, "completed": int}
            }
        """
        base_dir = project_dir or self.config.meetings_dir

        if not base_dir.exists():
            return {"duplicates": [], "should_complete": [], "fixed": {"merged": 0, "completed": 0}}

        # 1. 收集所有纪要中的待办信息
        all_new_actions = []  # [(meeting, action_data), ...]
        all_completed = []    # [(meeting, completed_text), ...]

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

                for action_data in result.get("new_actions", []):
                    all_new_actions.append((meeting_dir.name, action_data))
                for completed_text in result.get("completed", []):
                    all_completed.append((meeting_dir.name, completed_text))
            except Exception as e:
                logger.warning("解析会议纪要失败 %s: %s", meeting_dir.name, e)

        # 2. 加载现有 actions
        actions = self.load(project_dir)

        # 3. 建立 action → 纪要任务的映射
        action_to_minutes_task: dict[str, list[tuple[str, dict]]] = {}
        # 纪要任务 → 匹配的 actions
        minutes_task_to_actions: dict[int, list[ActionItem]] = {}

        for action in actions:
            action_to_minutes_task[action.id] = []

        for idx, (meeting, action_data) in enumerate(all_new_actions):
            task = action_data.get("task", "")
            matched_actions = []

            for action in actions:
                if self._is_same_task(task, action.task):
                    action_to_minutes_task[action.id].append((meeting, action_data))
                    matched_actions.append(action)

            if matched_actions:
                minutes_task_to_actions[idx] = matched_actions

        # 4. 检测重复：多个 actions 匹配到纪要中的同一任务
        duplicates = []
        seen_groups = set()

        for idx, matched in minutes_task_to_actions.items():
            if len(matched) > 1:
                # 创建重复组
                group_ids = tuple(sorted(a.id for a in matched))
                if group_ids not in seen_groups:
                    seen_groups.add(group_ids)
                    duplicates.append([a.id for a in matched])

        # 5. 检测应该完成但未完成的 actions
        should_complete = []

        for action in actions:
            if action.status == ActionType.COMPLETED:
                continue

            # 在已完成列表中查找
            for meeting, completed_text in all_completed:
                if self._is_same_task(action.task, completed_text):
                    should_complete.append(action.id)
                    break

        # 6. 自动修复
        fixed = {"merged": 0, "completed": 0}

        if auto_fix and (duplicates or should_complete):
            # 合并重复项：保留最早创建的，其他的标记为完成
            for group in duplicates:
                # 按创建时间排序，保留最早的
                group_actions = [a for a in actions if a.id in group]
                group_actions.sort(key=lambda x: x.created_at or datetime.max)

                keep = group_actions[0]
                for dup in group_actions[1:]:
                    # 合并 mentions
                    for m in dup.mentions:
                        if m not in keep.mentions:
                            keep.mentions.append(m)
                    # 标记为完成（实际上是删除）
                    dup.status = ActionType.COMPLETED
                    dup.task = f"[已合并到 {keep.id}] {dup.task}"
                    fixed["merged"] += 1

            # 标记应该完成的
            for action_id in should_complete:
                for action in actions:
                    if action.id == action_id and action.status != ActionType.COMPLETED:
                        action.status = ActionType.COMPLETED
                        fixed["completed"] += 1
                        break

            self.save(actions, project_dir)

        return {
            "duplicates": duplicates,
            "should_complete": should_complete,
            "fixed": fixed,
        }

    def _is_same_task(self, task1: str, task2: str) -> bool:
        """
        判断两个任务描述是否是同一个任务

        使用多种策略：
        1. 完全相同
        2. 包含关系（较短是较长的子串）
        3. 关键词高度重叠
        """
        t1 = task1.strip().lower()
        t2 = task2.strip().lower()

        if t1 == t2:
            return True

        # 包含关系
        shorter, longer = (t1, t2) if len(t1) < len(t2) else (t2, t1)
        if len(shorter) >= 8 and shorter in longer:
            return True

        # 关键词重叠
        keywords1 = self._extract_keywords(t1)
        keywords2 = self._extract_keywords(t2)

        if not keywords1 or not keywords2:
            return False

        common = keywords1 & keywords2
        min_len = min(len(keywords1), len(keywords2))

        # 5+ 共同关键词且重叠度 >= 50%
        if len(common) >= 5 and len(common) / min_len >= 0.5:
            return True

        # 重叠度 >= 70%
        if len(common) / min_len >= 0.7:
            return True

        return False

    def _extract_keywords(self, text: str) -> set[str]:
        """提取关键词（中文按字符，英文按单词）"""
        text = re.sub(r'[，。！？、；：""''（）【】 ]+', ' ', text)
        words = []
        for part in text.split():
            if part:
                if any('\u4e00' <= c <= '\u9fff' for c in part):
                    words.extend(c for c in part if '\u4e00' <= c <= '\u9fff')
                else:
                    words.append(part.lower())
        return set(words)

    # ==================== Delta 文件相关方法 ====================

    def generate_delta_file(
        self,
        meeting_dir: Path,
        project_dir: Optional[Path] = None,
        previous_meetings: Optional[list[Path]] = None,
    ) -> Path:
        """
        为单个会议生成 action 变化文件

        Args:
            meeting_dir: 当前会议目录
            project_dir: 项目目录
            previous_meetings: 之前的会议目录列表（用于确定截至当时的待办状态）

        Returns:
            生成的 delta 文件路径
        """
        minutes_file = meeting_dir / MINUTES_FILE
        if not minutes_file.exists():
            raise FileNotFoundError(f"会议纪要不存在: {minutes_file}")

        content = minutes_file.read_text(encoding="utf-8")
        meeting_name = meeting_dir.name

        # 提取待办变更
        extracted = self._extract_actions_from_minutes(content, meeting_name)

        # 获取"截至本次会议前"的待办状态
        # 只加载之前会议的 delta 并应用，得到当时的 actions 状态
        actions_at_this_point = self._get_actions_before_meeting(
            project_dir, previous_meetings or []
        )

        # 分析变更
        delta = self._analyze_delta(extracted, actions_at_this_point, meeting_name)

        # 生成 delta 文件
        delta_file = meeting_dir / "_actions_delta.md"
        delta_content = self._generate_delta_md_v2(delta, meeting_name)
        delta_file.write_text(delta_content, encoding="utf-8")

        logger.info("生成 delta 文件: %s", delta_file)
        return delta_file

    def _get_actions_before_meeting(
        self,
        project_dir: Optional[Path],
        previous_meetings: list[Path],
    ) -> list[ActionItem]:
        """
        获取"截至某次会议前"的待办状态

        按时间顺序应用之前所有会议的 delta，得到当时的 actions 状态

        Args:
            project_dir: 项目目录
            previous_meetings: 之前的会议目录列表（按时间排序）

        Returns:
            截至当时的待办列表
        """
        # 从空的 actions 开始
        actions: list[ActionItem] = []
        next_id = 1

        # 按顺序应用之前会议的 delta
        for meeting_dir in previous_meetings:
            delta_file = meeting_dir / "_actions_delta.md"
            if not delta_file.exists():
                continue

            # 应用该会议的 delta
            actions, next_id = self._apply_delta_to_actions(
                delta_file, actions, next_id, meeting_dir.name
            )

        return actions

    def _apply_delta_to_actions(
        self,
        delta_file: Path,
        actions: list[ActionItem],
        next_id: int,
        meeting_name: str,
    ) -> tuple[list[ActionItem], int]:
        """
        将单个 delta 应用到 actions 列表

        Returns:
            (更新后的 actions, 下一个可用 ID)
        """
        content = delta_file.read_text(encoding="utf-8")

        # 解析每个条目块
        # 格式: ## 条目 #N\n**类型**: ...\n...
        block_pattern = r"##\s*条目\s*#(\d+)\s*\n(.*?)(?=##\s*条目|$)"
        for match in re.finditer(block_pattern, content, re.DOTALL):
            block_content = match.group(2)

            # 解析类型
            type_match = re.search(r"\*\*类型\*\*:\s*(\S+)", block_content)
            item_type = type_match.group(1) if type_match else "未知"

            # 解析人工批注中的决策
            annotation_match = re.search(r"\*\*人工批注\*\*:\s*\n>\s*(.+?)(?:\n|$)", block_content)
            annotation = annotation_match.group(1).strip() if annotation_match else ""

            if item_type == "新增":
                # 解析任务信息
                task = self._extract_field(block_content, "任务")
                owner = self._extract_field(block_content, "负责人")
                due_date_str = self._extract_field(block_content, "截止")

                if annotation.startswith("合并到"):
                    # 合并到现有
                    target_id = annotation.replace("合并到", "").strip()
                    for action in actions:
                        if action.id == target_id:
                            if meeting_name not in action.mentions:
                                action.mentions.append(meeting_name)
                            break
                elif annotation == "忽略" or annotation == "删除":
                    pass  # 不处理
                else:
                    # 新增 - 分配新 ID
                    existing_ids = {a.id for a in actions}
                    while f"A{next_id:03d}" in existing_ids:
                        next_id += 1

                    action_id = f"A{next_id:03d}"
                    next_id += 1

                    new_action = ActionItem(
                        id=action_id,
                        task=task,
                        owner=owner,
                        due_date=self._parse_date(due_date_str),
                        status=ActionType.PENDING,
                        created_at=datetime.now(),
                        created_in_meeting=meeting_name,
                        mentions=[meeting_name],
                    )
                    actions.append(new_action)

            elif item_type == "完成":
                # 解析关联ID
                action_id = self._extract_field(block_content, "关联ID")

                if annotation == "确认" or annotation == "✓":
                    for action in actions:
                        if action.id == action_id:
                            action.status = ActionType.COMPLETED
                            break
                elif annotation.startswith("改为"):
                    # 修改关联 ID
                    new_id = annotation.replace("改为", "").strip()
                    for action in actions:
                        if action.id == new_id:
                            action.status = ActionType.COMPLETED
                            break

            elif item_type == "提及":
                # 记录提及
                action_id = self._extract_field(block_content, "关联ID")
                for action in actions:
                    if action.id == action_id:
                        if meeting_name not in action.mentions:
                            action.mentions.append(meeting_name)
                        break

        return actions, next_id

    def _extract_field(self, block: str, field_name: str) -> str:
        """从条目块中提取字段值"""
        pattern = rf"\*\*{re.escape(field_name)}\*\*:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, block)
        return match.group(1).strip() if match else ""

    def _analyze_delta(
        self,
        extracted: dict,
        existing_actions: list[ActionItem],
        meeting_name: str,
    ) -> list[dict]:
        """
        分析提取的待办，生成条目列表

        Returns:
            [
                {
                    "type": "新增" | "完成" | "提及",
                    "temp_id": "N1",
                    "task": "...",
                    "owner": "...",
                    "due_date": "...",
                    "related_id": "A001",  # 关联的现有待办 ID
                    "completed_desc": "...",  # 完成说明
                    "match_hint": "...",  # 匹配提示
                },
                ...
            ]
        """
        items = []
        new_idx = 1

        # 分析新增待办
        for action_data in extracted.get("new_actions", []):
            task = action_data.get("task", "").strip()
            if not task:
                continue

            # 检查是否与现有待办相似
            similar = self._find_similar_action(task, existing_actions)

            if similar:
                # 记录为提及
                items.append({
                    "type": "提及",
                    "temp_id": f"N{new_idx}",
                    "task": task,
                    "owner": action_data.get("owner", ""),
                    "due_date": str(action_data["due_date"]) if action_data.get("due_date") else "-",
                    "related_id": similar.id,
                    "related_task": similar.task,
                    "completed_desc": None,
                    "match_hint": None,
                })
            else:
                # 新增
                match_hint = self._find_potential_match(task, existing_actions)
                items.append({
                    "type": "新增",
                    "temp_id": f"N{new_idx}",
                    "task": task,
                    "owner": action_data.get("owner", ""),
                    "due_date": str(action_data["due_date"]) if action_data.get("due_date") else "-",
                    "related_id": None,
                    "related_task": None,
                    "completed_desc": None,
                    "match_hint": match_hint,
                })
            new_idx += 1

        # 分析已完成
        for completed_text in extracted.get("completed", []):
            similar = self._find_similar_action(completed_text, existing_actions)
            items.append({
                "type": "完成",
                "temp_id": None,
                "task": None,
                "owner": None,
                "due_date": None,
                "related_id": similar.id if similar else "?",
                "related_task": similar.task if similar else None,
                "completed_desc": completed_text,
                "match_hint": None,
            })

        return items

    def _generate_delta_md_v2(self, items: list[dict], meeting_name: str) -> str:
        """生成新版 delta 文件格式 - 每个条目一块"""
        lines = [
            f"# 待办变更记录",
            "",
            f"> 会议：{meeting_name}",
            f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
        ]

        if not items:
            lines.append("*本次会议无待办变更*")
            lines.append("")
        else:
            for idx, item in enumerate(items, 1):
                lines.append(f"## 条目 #{idx}")
                lines.append("")
                lines.append(f"**类型**: {item['type']}")
                lines.append("")

                if item['type'] == "新增":
                    lines.append(f"**任务**: {item['task']}")
                    lines.append(f"**负责人**: {item['owner'] or '-'}")
                    lines.append(f"**截止**: {item['due_date']}")
                    if item['match_hint']:
                        lines.append(f"**匹配提示**: {item['match_hint']}")
                    lines.append("")
                    lines.append("**人工批注**:")
                    lines.append("> _请填写：新增 / 合并到 AXXX / 忽略_")
                    lines.append("")

                elif item['type'] == "完成":
                    lines.append(f"**关联ID**: {item['related_id']}")
                    if item['related_task']:
                        lines.append(f"**原任务**: {item['related_task'][:50]}...")
                    lines.append(f"**完成说明**: {item['completed_desc']}")
                    lines.append("")
                    lines.append("**人工批注**:")
                    lines.append("> _请填写：确认 / 改为 AXXX / 忽略_")
                    lines.append("")

                elif item['type'] == "提及":
                    lines.append(f"**关联ID**: {item['related_id']}")
                    lines.append(f"**原任务**: {item['related_task'][:50]}...")
                    lines.append(f"**本次提及**: {item['task']}")
                    lines.append("")
                    lines.append("**人工批注**:")
                    lines.append("> _通常无需处理，系统会自动记录提及_")
                    lines.append("")

                lines.append("---")
                lines.append("")

        # 使用说明
        lines.append("## 批注说明")
        lines.append("")
        lines.append("**新增类型**：")
        lines.append("- `新增` = 创建新待办")
        lines.append("- `合并到 AXXX` = 合并到现有待办")
        lines.append("- `忽略` = 不处理")
        lines.append("")
        lines.append("**完成类型**：")
        lines.append("- `确认` = 确认关联 ID 正确并标记完成")
        lines.append("- `改为 AXXX` = 修改关联 ID 并标记完成")
        lines.append("- `忽略` = 不处理")
        lines.append("")

        return "\n".join(lines)

    def apply_all_deltas(self, project_dir: Optional[Path] = None) -> dict:
        """
        按时间顺序应用所有 delta 文件，生成最终的 actions.md

        这是正确的迭代方式：按会议时间顺序，依次应用每个 delta

        Returns:
            {"total_meetings": int, "added": int, "completed": int, "merged": int}
        """
        base_dir = project_dir or self.config.meetings_dir

        # 获取所有会议目录，按时间排序
        meeting_dirs = sorted([
            item for item in base_dir.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ], key=lambda x: x.name)

        # 从空开始，按顺序应用
        actions: list[ActionItem] = []
        next_id = 1
        stats = {"total_meetings": 0, "added": 0, "completed": 0, "merged": 0}

        for meeting_dir in meeting_dirs:
            delta_file = meeting_dir / "_actions_delta.md"
            if not delta_file.exists():
                continue

            before_count = len(actions)
            actions, next_id = self._apply_delta_to_actions(
                delta_file, actions, next_id, meeting_dir.name
            )

            stats["total_meetings"] += 1
            stats["added"] += len(actions) - before_count

        # 保存最终的 actions
        self.save(actions, project_dir)

        logger.info("应用所有 delta: %d 个会议，%d 个待办",
                    stats["total_meetings"], len(actions))

        return stats

    def generate_all_deltas_sequentially(self, project_dir: Optional[Path] = None) -> list[Path]:
        """
        按时间顺序为所有会议生成 delta 文件

        关键：每次生成时，只使用"之前会议"的信息来确定待办状态

        Returns:
            生成的 delta 文件列表
        """
        base_dir = project_dir or self.config.meetings_dir

        # 获取所有会议目录，按时间排序
        meeting_dirs = sorted([
            item for item in base_dir.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ], key=lambda x: x.name)

        delta_files = []
        previous_meetings = []

        for meeting_dir in meeting_dirs:
            minutes_file = meeting_dir / MINUTES_FILE
            if not minutes_file.exists():
                previous_meetings.append(meeting_dir)
                continue

            try:
                # 生成时只传入"之前的会议"
                delta_file = self.generate_delta_file(
                    meeting_dir, project_dir, previous_meetings
                )
                delta_files.append(delta_file)
                previous_meetings.append(meeting_dir)
            except Exception as e:
                logger.warning("生成 delta 失败 %s: %s", meeting_dir.name, e)
                previous_meetings.append(meeting_dir)

        return delta_files

    def _find_potential_match(self, task: str, actions: list[ActionItem]) -> Optional[str]:
        """找到可能匹配的现有待办（用于人工确认）"""
        task_lower = task.strip().lower()

        for action in actions:
            action_lower = action.task.strip().lower()

            # 部分匹配
            if len(task_lower) >= 5 and task_lower in action_lower:
                return f"可能是 {action.id}"
            if len(action_lower) >= 5 and action_lower in task_lower:
                return f"可能是 {action.id}"

            # 关键词重叠 > 30%
            keywords1 = self._extract_keywords(task)
            keywords2 = self._extract_keywords(action.task)
            if keywords1 and keywords2:
                common = keywords1 & keywords2
                min_len = min(len(keywords1), len(keywords2))
                if len(common) / min_len > 0.3:
                    return f"可能是 {action.id}"

        return None

    def _parse_date(self, date_str: str) -> Optional[date]:
        """解析日期字符串"""
        if not date_str or date_str == "-":
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None
