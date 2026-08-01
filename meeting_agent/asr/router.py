"""
ASR 路由器 - 维护会议的 ASR 状态并调用 OpenAI gpt-transcribe。

全系统只剩 OpenAI 一个 ASR 供应商，这里不再做 provider 选择和降级，只保留
`_asr_state.json` 的状态跟踪（running / succeeded / failed）和手动重试入口。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from meeting_agent.asr.engine import OpenAIASREngine
from meeting_agent.config import (
    ASR_STATE_FILE,
    TRANSCRIPT_PROGRESS_FILE,
    Config,
)
from meeting_agent.models import ASRState, Transcript

logger = logging.getLogger("meeting_agent.asr.router")

ASR_PROVIDER = "openai"


class ASRRouter:
    """ASR 编排器：调用 OpenAI 引擎并维护会议级 ASR 状态。"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.engine = OpenAIASREngine(self.config)

    # ---- 公开接口 ----

    def transcribe(
        self,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
    ) -> Optional[Transcript]:
        """转写音频文件，并把过程写入 `_asr_state.json`。"""
        state = self._load_state(meeting_dir) or ASRState(
            provider=ASR_PROVIDER,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info("ASR 开始: meeting=%s, provider=%s, force=%s", meeting_dir.name,
                    ASR_PROVIDER, force)

        state.provider = ASR_PROVIDER
        state.status = "running"
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state(meeting_dir, state)

        try:
            result = self.engine.transcribe(audio_files, meeting_dir, force=force)
            if not result or not result.segments:
                raise RuntimeError("OpenAI ASR 未返回有效转写内容")

            logger.info("ASR 转写成功: segments=%d, duration=%.2fs", len(result.segments),
                        result.duration)
            state.status = "succeeded"
            state.last_error = None
            state.next_retry_at = None
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state(meeting_dir, state)
            return result

        except Exception as e:
            error_msg = str(e)
            logger.error("ASR 转写失败: %s", error_msg)
            state.status = "failed"
            state.last_error = error_msg
            state.next_retry_at = None
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state(meeting_dir, state)
            raise

    def retry_now(self, meeting_dir: Path) -> ASRState:
        """重置状态，允许立即重试。"""
        logger.info("手动重试: meeting=%s, 重置 ASR 状态", meeting_dir.name)
        state = self._load_state(meeting_dir) or ASRState(
            created_at=datetime.now(timezone.utc).isoformat())

        state.provider = ASR_PROVIDER
        state.status = "pending"
        state.next_retry_at = None
        state.retry_count = 0
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state(meeting_dir, state)
        return state

    def reset_progress(self, meeting_dir: Path) -> None:
        """丢弃分块进度和分片缓存，让下一次转写从头开始。"""
        (meeting_dir / TRANSCRIPT_PROGRESS_FILE).unlink(missing_ok=True)
        chunks_dir = meeting_dir / ".chunks"
        if chunks_dir.exists():
            import shutil
            shutil.rmtree(chunks_dir, ignore_errors=True)

    def get_state(self, meeting_dir: Path) -> Optional[ASRState]:
        """获取当前 ASR 状态"""
        return self._load_state(meeting_dir)

    # ---- 内部方法 ----

    def _load_state(self, meeting_dir: Path) -> Optional[ASRState]:
        state_file = meeting_dir / ASR_STATE_FILE
        if not state_file.exists():
            return None
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return ASRState(**data)
        except Exception as e:
            logger.warning("加载 ASR 状态失败: %s", e)
            return None

    def _save_state(self, meeting_dir: Path, state: ASRState) -> None:
        state_file = meeting_dir / ASR_STATE_FILE
        state_file.write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
