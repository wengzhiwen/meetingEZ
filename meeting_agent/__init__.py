"""
Meeting Agent - 项目记忆系统
"""

__version__ = "0.1.0"

from meeting_agent.config import Config, get_settings
from meeting_agent.models import (
    ActionItem,
    ActionType,
    GPTAnalysisResult,
    MeetingMeta,
    MeetingTask,
    MeetingType,
    PeopleConfig,
    Person,
    ProjectConfig,
    ProjectStatus,
    TimelineEntry,
    Transcript,
    TranscriptSegment,
)

__all__ = [
    "Config",
    "get_settings",
    "ActionItem",
    "ActionType",
    "GPTAnalysisResult",
    "MeetingMeta",
    "MeetingTask",
    "MeetingType",
    "PeopleConfig",
    "Person",
    "ProjectConfig",
    "ProjectStatus",
    "TimelineEntry",
    "Transcript",
    "TranscriptSegment",
]
