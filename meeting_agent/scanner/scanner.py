"""
会议文件扫描器
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import (
    ASR_STATE_FILE,
    AUDIO_EXTENSIONS,
    MEETING_META_FILE,
    MINUTES_FILE,
    PROCESSING_LOCK_FILE,
    TRANSCRIPT_FILE,
    TRANSCRIPT_PROGRESS_FILE,
    Config,
)
from meeting_agent.models import (
    MeetingMeta,
    MeetingTask,
    PeopleConfig,
    ProjectConfig,
    ProjectStatus,
)

logger = logging.getLogger("meeting_agent.scanner")


class MeetingScanner:
    """会议文件扫描器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def scan_meetings(self, project_dir: Optional[Path] = None) -> list[MeetingTask]:
        """扫描所有会议目录，返回任务列表"""
        base_dir = project_dir or self.config.meetings_dir

        if not base_dir.exists():
            logger.warning("会议目录不存在: %s", base_dir)
            return []

        tasks = []
        for item in sorted(base_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                task = self._scan_meeting_dir(item)
                if task:
                    tasks.append(task)

        return tasks

    def _scan_meeting_dir(self, meeting_dir: Path) -> Optional[MeetingTask]:
        """扫描单个会议目录"""
        # 检查是否有 _meeting.json
        meta_file = meeting_dir / MEETING_META_FILE
        meeting_meta = None

        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                meeting_meta = MeetingMeta(**data)
            except Exception as e:
                logger.warning("解析会议元信息失败 %s: %s", meeting_dir.name, e)

        # 扫描音频文件
        audio_files = [
            f for f in meeting_dir.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        ]
        audio_files.sort()

        # 检查转写状态
        transcript_file = meeting_dir / TRANSCRIPT_FILE
        progress_file = meeting_dir / TRANSCRIPT_PROGRESS_FILE

        has_transcript = transcript_file.exists()
        has_incomplete_asr = progress_file.exists() and not has_transcript

        # 检查纪要状态
        minutes_file = meeting_dir / MINUTES_FILE
        has_minutes = minutes_file.exists()

        # 判断是否需要处理
        needs_asr = len(audio_files) > 0 and not has_transcript
        needs_minutes = has_transcript and not has_minutes

        # 如果纪要存在但比转写旧，也需要更新
        if has_transcript and has_minutes:
            if transcript_file.stat().st_mtime > minutes_file.stat().st_mtime:
                needs_minutes = True

        is_processing = (meeting_dir / PROCESSING_LOCK_FILE).exists()

        # 读取 ASR 重试/降级状态
        asr_state = None
        asr_state_file = meeting_dir / ASR_STATE_FILE
        if asr_state_file.exists():
            try:
                with open(asr_state_file, "r", encoding="utf-8") as f:
                    asr_state = json.load(f)
            except Exception as e:
                logger.warning("读取 ASR 状态失败 %s: %s", meeting_dir.name, e)

        return MeetingTask(
            meeting_dir=meeting_dir,
            meeting_meta=meeting_meta,
            has_audio=len(audio_files) > 0,
            audio_files=audio_files,
            has_transcript=has_transcript,
            has_minutes=has_minutes,
            needs_asr=needs_asr or has_incomplete_asr,
            needs_minutes=needs_minutes,
            is_processing=is_processing,
            asr_state=asr_state,
        )

    def load_project_config(self, project_dir: Optional[Path] = None) -> Optional[ProjectConfig]:
        """加载项目配置"""
        base_dir = project_dir or self.config.meetings_dir
        config_file = base_dir / "_project.json"

        if not config_file.exists():
            return None

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ProjectConfig(**data)
        except Exception as e:
            logger.warning("解析项目配置失败: %s", e)
            return None

    def load_people_config(self, project_dir: Optional[Path] = None) -> PeopleConfig:
        """加载人员配置"""
        base_dir = project_dir or self.config.meetings_dir
        config_file = base_dir / "_people.json"

        if not config_file.exists():
            return PeopleConfig()

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PeopleConfig(**data)
        except Exception as e:
            logger.warning("解析人员配置失败: %s", e)
            return PeopleConfig()

    def load_meeting_meta(self, meeting_dir: Path) -> Optional[MeetingMeta]:
        """加载会议元信息"""
        meta_file = meeting_dir / MEETING_META_FILE

        if not meta_file.exists():
            return None

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return MeetingMeta(**data)
        except Exception as e:
            logger.warning("解析会议元信息失败 %s: %s", meeting_dir.name, e)
            return None

    def get_audio_files(self, meeting_dir: Path) -> list[Path]:
        """获取会议目录下的所有音频文件"""
        audio_files = [
            f for f in meeting_dir.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        ]
        return sorted(audio_files)

    def get_project_status(self, project_dir: Optional[Path] = None) -> ProjectStatus:
        """获取项目状态统计"""
        tasks = self.scan_meetings(project_dir)

        total = len(tasks)
        processed = sum(1 for t in tasks if t.has_minutes)
        pending_asr = sum(1 for t in tasks if t.needs_asr)
        pending_minutes = sum(1 for t in tasks if t.needs_minutes)

        # 统计待办（需要读取 actions.md）
        actions_file = (project_dir or self.config.meetings_dir) / "actions.md"
        # TODO: 解析 actions.md 统计待办

        return ProjectStatus(
            total_meetings=total,
            processed_meetings=processed,
            pending_asr=pending_asr,
            pending_minutes=pending_minutes,
            last_updated=datetime.now(),
        )

    def list_projects(self) -> list[Path]:
        """列出所有项目（多项目模式）"""
        if not self.config.projects_dir:
            return []

        projects = []
        for item in self.config.projects_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                projects.append(item)

        return sorted(projects)
