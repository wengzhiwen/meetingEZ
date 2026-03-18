"""
数据模型定义
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class MeetingType(str, Enum):
    """会议类型"""
    REVIEW = "review"           # 评审会
    WEEKLY = "weekly"           # 周会
    BRAINSTORM = "brainstorm"   # 头脑风暴
    RETRO = "retro"             # 复盘会
    KICKOFF = "kickoff"         # 启动会
    OTHER = "other"             # 其他


class ActionType(str, Enum):
    """待办状态"""
    PENDING = "pending"         # 待处理
    IN_PROGRESS = "in_progress" # 进行中
    COMPLETED = "completed"     # 已完成
    OVERDUE = "overdue"         # 已超期
    BLOCKED = "blocked"         # 阻塞中


class RiskLevel(str, Enum):
    """风险等级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Person(BaseModel):
    """人员信息"""
    name: str
    role: Optional[str] = None
    team: Optional[str] = None
    email: Optional[str] = None
    alias: list[str] = Field(default_factory=list)


class PeopleConfig(BaseModel):
    """人员配置"""
    people: dict[str, Person] = Field(default_factory=dict)
    teams: dict[str, list[str]] = Field(default_factory=dict)

    def get_person(self, name: str) -> Optional[Person]:
        """根据名称或别名获取人员信息"""
        if name in self.people:
            return self.people[name]
        for person in self.people.values():
            if name in person.alias:
                return person
        return None

    def resolve_name(self, name: str) -> str:
        """解析名称，将别名映射到标准名称"""
        if name in self.people:
            return name
        for std_name, person in self.people.items():
            if name in person.alias:
                return std_name
        return name


class MeetingMeta(BaseModel):
    """会议元信息"""
    date: date
    title: str
    type: MeetingType = MeetingType.OTHER
    participants: list[str] = Field(default_factory=list)
    host: Optional[str] = None
    notes: Optional[str] = None
    expected_actions: list[str] = Field(default_factory=list)
    language: str = "zh-CN"
    created_at: Optional[datetime] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.created_at is None:
            self.created_at = datetime.now()


class ProjectConfig(BaseModel):
    """项目配置"""
    name: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    team: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    meeting_types: list[dict[str, str]] = Field(default_factory=list)


class ActionItem(BaseModel):
    """待办事项"""
    id: str
    task: str
    owner: str
    due_date: Optional[date] = None
    status: ActionType = ActionType.PENDING
    created_at: datetime
    created_in_meeting: str  # 会议目录名
    priority: str = "P1"
    mentions: list[str] = Field(default_factory=list)  # 后续提及的会议
    blocked_by: Optional[str] = None
    notes: Optional[str] = None


class TimelineEntry(BaseModel):
    """时间线条目"""
    date: date
    title: str
    type: MeetingType
    decisions: list[str] = Field(default_factory=list)
    milestone: Optional[str] = None
    risks: list[dict[str, str]] = Field(default_factory=list)
    meeting_dir: str


class TranscriptSegment(BaseModel):
    """转写片段"""
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    """转写结果"""
    meeting_dir: str
    audio_files: list[str]
    segments: list[TranscriptSegment]
    duration: float
    created_at: datetime
    language: str = "zh-CN"

    def get_full_text(self) -> str:
        """获取完整文本"""
        return "\n".join(seg.text for seg in self.segments)


class ASRProgress(BaseModel):
    """ASR 进度"""
    meeting_dir: str
    audio_file: str
    total_chunks: int
    completed_chunks: int
    chunk_results: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    updated_at: datetime


class MeetingTask(BaseModel):
    """会议处理任务"""
    meeting_dir: Path
    meeting_meta: Optional[MeetingMeta] = None
    has_audio: bool = False
    audio_files: list[Path] = Field(default_factory=list)
    has_transcript: bool = False
    has_minutes: bool = False
    needs_asr: bool = False
    needs_minutes: bool = False

    @property
    def dir_name(self) -> str:
        """目录名称"""
        return self.meeting_dir.name


class GPTAnalysisResult(BaseModel):
    """GPT 分析结果"""
    # 会议摘要
    meeting_type: MeetingType = MeetingType.OTHER
    summary: str = ""
    key_decisions: list[dict[str, str]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)

    # 会议纪要
    minutes: str = ""

    # 新增待办
    new_actions: list[dict[str, Any]] = Field(default_factory=list)

    # 已完成/提及的待办 ID
    completed_actions: list[str] = Field(default_factory=list)
    mentioned_actions: list[str] = Field(default_factory=list)

    # 时间线条目
    timeline_entry: Optional[dict[str, Any]] = None

    # 上下文更新
    context_updates: dict[str, Any] = Field(default_factory=dict)

    # 术语建议
    term_suggestions: list[dict[str, Any]] = Field(default_factory=list)

    # 需要人工解释的项目
    context_questions: list[dict[str, str]] = Field(default_factory=list)


class ProjectStatus(BaseModel):
    """项目状态"""
    total_meetings: int = 0
    processed_meetings: int = 0
    pending_asr: int = 0
    pending_minutes: int = 0
    total_actions: int = 0
    completed_actions: int = 0
    overdue_actions: int = 0
    last_updated: Optional[datetime] = None
