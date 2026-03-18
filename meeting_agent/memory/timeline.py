"""
项目时间线管理
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config, TIMELINE_FILE
from meeting_agent.models import TimelineEntry, MeetingType

logger = logging.getLogger("meeting_agent.memory")


class TimelineManager:
    """项目时间线管理器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def load(self, project_dir: Optional[Path] = None) -> Optional[str]:
        """加载时间线"""
        base_dir = project_dir or self.config.meetings_dir
        timeline_file = base_dir / TIMELINE_FILE

        if not timeline_file.exists():
            return None

        try:
            return timeline_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("加载时间线失败: %s", e)
            return None

    def save(self, content: str, project_dir: Optional[Path] = None):
        """保存时间线"""
        base_dir = project_dir or self.config.meetings_dir
        timeline_file = base_dir / TIMELINE_FILE

        try:
            timeline_file.write_text(content, encoding="utf-8")
            logger.info("保存时间线: %s", timeline_file)
        except Exception as e:
            logger.error("保存时间线失败: %s", e)

    def add_entry(
        self,
        entry: TimelineEntry,
        project_dir: Optional[Path] = None,
    ):
        """添加时间线条目"""
        base_dir = project_dir or self.config.meetings_dir
        timeline_file = base_dir / TIMELINE_FILE

        # 生成条目内容
        entry_md = self._format_entry(entry)

        if not timeline_file.exists():
            # 创建新文件
            content = self._create_initial_with_entry(entry)
        else:
            # 读取现有内容并插入新条目
            content = self._insert_entry(timeline_file.read_text(encoding="utf-8"), entry)

        self.save(content, project_dir)

    def _format_entry(self, entry: TimelineEntry) -> str:
        """格式化单个条目"""
        type_names = {
            MeetingType.REVIEW: "评审会",
            MeetingType.WEEKLY: "周会",
            MeetingType.BRAINSTORM: "头脑风暴",
            MeetingType.RETRO: "复盘会",
            MeetingType.KICKOFF: "启动会",
            MeetingType.OTHER: "会议",
        }

        lines = [
            f"### {entry.date} {entry.title} 【{type_names.get(entry.type, '会议')}】",
            "",
        ]

        if entry.decisions:
            lines.append("**关键决策**")
            for d in entry.decisions:
                lines.append(f"- ✅ {d}")
            lines.append("")

        if entry.milestone:
            lines.append("**里程碑**")
            lines.append(f"- 📍 {entry.milestone}")
            lines.append("")

        if entry.risks:
            lines.append("**风险标记**")
            for r in entry.risks:
                level = r.get("level", "medium")
                emoji = "🔴" if level == "high" else "🟡" if level == "medium" else "🟢"
                lines.append(f"- {emoji} {r.get('risk', '')}")
            lines.append("")

        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def _create_initial_with_entry(self, entry: TimelineEntry) -> str:
        """创建包含第一个条目的时间线文件"""
        return f"""# 项目时间线

> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## {entry.date.year}年{entry.date.month}月

{self._format_entry(entry)}

## 里程碑总览

| 日期 | 里程碑 | 状态 |
|------|--------|------|
{"| " + str(entry.date) + " | " + (entry.milestone or entry.title) + " | ✅ 已完成 |}" if entry.milestone else ""}

---

## 待决策事项

| 事项 | 状态 | 预计决策时间 |
|------|------|--------------|
| *暂无* | | |
"""

    def _insert_entry(self, existing: str, entry: TimelineEntry) -> str:
        """在现有内容中插入新条目"""
        lines = existing.split("\n")

        # 找到对应月份的位置
        month_header = f"## {entry.date.year}年{entry.date.month}月"
        entry_md = self._format_entry(entry)

        # 查找月份位置
        insert_pos = -1
        for i, line in enumerate(lines):
            if line == month_header:
                insert_pos = i + 1
                break

        if insert_pos == -1:
            # 月份不存在，需要添加
            # 找到最后一个月份之前或文件开头
            last_month_pos = -1
            for i, line in enumerate(lines):
                if line.startswith("## ") and "年" in line and "月" in line:
                    last_month_pos = i

            month_section = f"\n{month_header}\n\n{entry_md}"

            if last_month_pos == -1:
                # 插入到 "---" 之后
                for i, line in enumerate(lines):
                    if line == "---":
                        lines.insert(i + 1, month_section)
                        break
            else:
                # 检查新月份是否应该在前面
                lines.insert(last_month_pos, month_section)
        else:
            # 插入到该月份的开头
            lines.insert(insert_pos, "\n" + entry_md)

        # 更新最后更新时间
        for i, line in enumerate(lines):
            if line.startswith("> 最后更新:"):
                lines[i] = f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                break

        return "\n".join(lines)
