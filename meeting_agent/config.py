"""
配置管理模块
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# 加载 .env 文件
load_dotenv()


class Settings(BaseSettings):
    """应用配置"""

    # 智谱 AI ASR
    zhipu_api_key: str = ""
    zhipu_api_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # OpenAI GPT
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.4"

    # 目录配置
    meetings_dir: Path = Path("./meetings")
    projects_dir: Optional[Path] = None

    # Agent 行为
    recent_minutes_count: int = 5
    default_language: str = "zh-CN"
    timezone: str = "Asia/Shanghai"

    # ASR 配置
    asr_chunk_seconds: float = 30.0
    asr_overlap_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中的额外字段
    )


# 全局配置实例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置实例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings():
    """重新加载配置"""
    global _settings
    _settings = Settings()


class Config:
    """配置管理类"""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    @property
    def meetings_dir(self) -> Path:
        """会议目录"""
        path = self.settings.meetings_dir
        if not path.is_absolute():
            # 相对路径转换为绝对路径
            path = Path.cwd() / path
        return path

    @property
    def projects_dir(self) -> Optional[Path]:
        """项目根目录（多项目模式）"""
        if self.settings.projects_dir:
            path = self.settings.projects_dir
            if not path.is_absolute():
                path = Path.cwd() / path
            return path
        return None

    def get_project_dir(self, project_name: Optional[str] = None) -> Path:
        """获取项目目录"""
        if project_name and self.projects_dir:
            return self.projects_dir / project_name
        return self.meetings_dir

    def ensure_dirs(self):
        """确保必要目录存在"""
        self.meetings_dir.mkdir(parents=True, exist_ok=True)
        if self.projects_dir:
            self.projects_dir.mkdir(parents=True, exist_ok=True)

    @property
    def zhipu_api_key(self) -> str:
        return self.settings.zhipu_api_key

    @property
    def openai_api_key(self) -> str:
        return self.settings.openai_api_key

    @property
    def is_configured(self) -> bool:
        """检查是否已配置必要的 API Key"""
        return bool(self.settings.zhipu_api_key and self.settings.openai_api_key)


# 常量
MEETING_META_FILE = "_meeting.json"
PROJECT_CONFIG_FILE = "_project.json"
PEOPLE_CONFIG_FILE = "_people.json"

TRANSCRIPT_FILE = "transcript.json"
TRANSCRIPT_PROGRESS_FILE = "transcript.json.progress"
MINUTES_FILE = "minutes.md"
PRE_HINT_FILE = "pre_meeting_hint.md"

CONTEXT_FILE = "context.md"
TIMELINE_FILE = "timeline.md"
ACTIONS_FILE = "actions.md"
STATE_FILE = "_state.json"
PROCESSING_LOCK_FILE = "_processing.lock"

# 支持的音频格式
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".wma"}
