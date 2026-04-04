"""
数据模型定义
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class MeetingType(str, Enum):
    """会议类型"""
    REVIEW = "review"  # 评审会
    WEEKLY = "weekly"  # 周会
    BRAINSTORM = "brainstorm"  # 头脑风暴
    RETRO = "retro"  # 复盘会
    KICKOFF = "kickoff"  # 启动会
    OTHER = "other"  # 其他


class LanguageMode(str, Enum):
    """会议语言模式"""
    SINGLE_PRIMARY = "single_primary"  # 单主语言会议
    BILINGUAL = "bilingual"  # 双语言会议


class ActionType(str, Enum):
    """待办状态"""
    PENDING = "pending"  # 待处理
    IN_PROGRESS = "in_progress"  # 进行中
    COMPLETED = "completed"  # 已完成
    OVERDUE = "overdue"  # 已超期
    BLOCKED = "blocked"  # 阻塞中


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

    def get_person(self, name: str) -> Optional[Person]:  # pylint: disable=no-member
        """根据名称或别名获取人员信息"""
        if name in self.people:
            return self.people[name]
        for person in self.people.values():  # pylint: disable=no-member
            if name in person.alias:
                return person
        return None

    def resolve_name(self, name: str) -> str:  # pylint: disable=no-member
        """解析名称，将别名映射到标准名称"""
        if name in self.people:
            return name
        for std_name, person in self.people.items():  # pylint: disable=no-member
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
    language: str = "zh-CN"  # 兼容旧字段，等价于 primary_language 的默认来源
    language_mode: LanguageMode = LanguageMode.SINGLE_PRIMARY
    primary_language: Optional[str] = None
    secondary_language: Optional[str] = None
    created_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _normalize_language_profile(self):
        primary = (self.primary_language or self.language or "zh-CN").strip()
        secondary = (self.secondary_language or "").strip() or None
        self.primary_language = primary
        self.language = primary
        self.secondary_language = secondary

        if secondary and self.language_mode == LanguageMode.SINGLE_PRIMARY:
            self.language_mode = LanguageMode.BILINGUAL
        if not secondary and self.language_mode == LanguageMode.BILINGUAL:
            self.language_mode = LanguageMode.SINGLE_PRIMARY

        if self.created_at is None:
            self.created_at = datetime.now()
        return self

    @property
    def effective_primary_language(self) -> str:
        """获取有效的主要语言"""
        return (self.primary_language or self.language or "zh-CN").strip()

    @property
    def effective_secondary_language(self) -> Optional[str]:
        """获取有效的第二语言"""
        return (self.secondary_language or "").strip() or None

    @property
    def is_bilingual(self) -> bool:
        """是否为双语言会议"""
        return self.language_mode == LanguageMode.BILINGUAL and bool(
            self.effective_secondary_language)

    def language_profile_label(self) -> str:
        """用于界面展示的语言画像摘要"""
        primary = self.effective_primary_language
        secondary = self.effective_secondary_language
        if self.is_bilingual and secondary:
            return f"双语言 ({primary} / {secondary})"
        return f"单主语言 ({primary})"


class TeamMember(BaseModel):
    """团队成员"""
    name: str
    nickname: Optional[str] = None
    role: Optional[str] = None


class ProjectConfig(BaseModel):
    """项目配置"""
    name: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    team: list[TeamMember] = Field(default_factory=list)
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
    speaker: Optional[str] = None  # 说话人标识（VibeVoice 提供，仅供参考）


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
    is_processing: bool = False
    asr_state: Optional[dict] = None  # ASR 重试/降级状态

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


class ASRState(BaseModel):
    """ASR 处理状态（重试/降级跟踪）"""
    provider: str = "vibevoice"           # vibevoice | zhipu
    status: str = "pending"               # pending | running | failed | blocked | succeeded
    retry_count: int = 0
    next_retry_at: Optional[str] = None   # ISO timestamp
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
