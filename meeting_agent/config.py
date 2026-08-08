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

    # OpenAI（转写 + 纪要，全系统唯一的模型供应商）
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-sol"
    # 纪要生成的推理档位：none/low/medium/high/xhigh/max
    openai_reasoning_effort: str = "high"

    # 离线文件转写：gpt-transcribe（/v1/audio/transcriptions）
    openai_asr_model: str = "gpt-transcribe"
    # 预期输入语言，逗号分隔的 ISO-639-1 短码；留空则由模型自动检测。
    openai_asr_languages: str = ""
    openai_asr_timeout: int = 300

    # 目录配置
    meetings_dir: Path = Path("./meetings")
    projects_dir: Optional[Path] = None

    # Agent 行为
    recent_minutes_count: int = 5
    default_language: str = "zh-CN"
    timezone: str = "Asia/Shanghai"

    # ASR 分块配置。gpt-transcribe 不返回时间戳，分块边界就是时间戳精度来源；
    # 块内再按字符数把时间摊到句子上。不使用重叠——没有时间戳无法去重。
    asr_chunk_seconds: float = 120.0
    # 传给下一块 prompt 的上文长度（字符）
    asr_context_chars: int = 400
    # 单次请求下发的 keywords 上限
    asr_keywords_limit: int = 100

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
    def openai_api_key(self) -> str:
        return self.settings.openai_api_key

    @property
    def is_configured(self) -> bool:
        """检查是否已配置必要的 API Key"""
        return bool(self.settings.openai_api_key)


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
STATE_FILE = "_state.json"
PROCESSING_LOCK_FILE = "_processing.lock"
PROCESSING_PROGRESS_FILE = "_processing_progress.json"
ASR_STATE_FILE = "_asr_state.json"

# 支持的音频格式
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".wma"}
