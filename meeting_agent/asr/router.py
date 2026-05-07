"""
ASR 路由器 - 管理 OpenRouter Chirp 3（首选）与智谱（降级）之间的切换。

VibeVoice 相关代码暂时保留，但不再实例化、不再路由调用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from meeting_agent.asr.engine import ASREngine
from meeting_agent.asr.openrouter_engine import OpenRouterASREngine
from meeting_agent.config import (
    ASR_STATE_FILE,
    TRANSCRIPT_PROGRESS_FILE,
    Config,
)
from meeting_agent.models import ASRState, Transcript

logger = logging.getLogger("meeting_agent.asr.router")


class ASRBlockedException(Exception):
    """ASR 失败后进入阻塞/重试状态时抛出"""

    def __init__(self, message: str, state: ASRState) -> None:
        super().__init__(message)
        self.state = state


class ASRRouter:
    """ASR 路由器：编排 OpenRouter Chirp 3（首选）与智谱（降级）"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        # VibeVoice 暂时屏蔽：保留实现文件，但这里不实例化、不调用。
        self.openrouter = OpenRouterASREngine(self.config)
        self.zhipu = ASREngine(self.config)

    # ---- 公开接口 ----

    def transcribe(
        self,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
        provider_override: Optional[str] = None,
    ) -> Optional[Transcript]:
        """
        转写音频文件，根据状态自动选择引擎。

        Raises:
            ASRBlockedException: 兼容旧调用；当前 OpenRouter -> 智谱路由不再抛出
        """
        state = self._load_state(meeting_dir)

        # 确定本次使用的引擎
        provider = self._resolve_provider(provider_override, state)
        logger.info(
            "ASR 路由决策: meeting=%s, provider=%s, state=%s, force=%s",
            meeting_dir.name, provider,
            state.status if state else "无", force,
        )

        # 已成功的不再重复
        if state and state.status == "succeeded" and not force:
            # 交由引擎自身判断 transcript.json 是否存在
            pass

        # 更新状态为 running
        logger.info("更新 ASR 状态为 running: provider=%s", provider)
        state = state or ASRState(
            provider=provider,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state.provider = provider
        state.status = "running"
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state(meeting_dir, state)

        try:
            if provider == "zhipu":
                logger.info("使用智谱 ASR（降级模式）")
                result = self._run_provider(
                    "zhipu", audio_files, meeting_dir, force=force)
            else:
                logger.info("使用 OpenRouter Chirp 3 ASR（首选模式）")
                try:
                    result = self._run_provider(
                        "openrouter", audio_files, meeting_dir, force=force)
                except Exception as primary_error:
                    logger.warning(
                        "OpenRouter Chirp 3 ASR 不可用，降级到智谱: %s",
                        primary_error,
                    )
                    state.provider = "zhipu"
                    state.last_error = f"OpenRouter ASR 失败: {primary_error}"
                    state.updated_at = datetime.now(timezone.utc).isoformat()
                    self._save_state(meeting_dir, state)
                    self._clear_partial_asr_progress(meeting_dir)
                    result = self._run_provider(
                        "zhipu", audio_files, meeting_dir, force=force)
                    provider = "zhipu"

            # 成功
            seg_count = len(result.segments) if result else 0
            logger.info(
                "ASR 转写成功: provider=%s, segments=%d, duration=%.2fs",
                provider, seg_count, result.duration if result else 0,
            )
            state.status = "succeeded"
            state.provider = provider
            state.last_error = None
            state.next_retry_at = None
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state(meeting_dir, state)
            return result

        except ASRBlockedException:
            raise  # 不拦截，直接上抛

        except Exception as e:
            error_msg = str(e)
            logger.error("ASR 转写失败 [%s]: %s", provider, error_msg)

            state.status = "failed"
            state.last_error = error_msg
            state.next_retry_at = None
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state(meeting_dir, state)
            raise

    def retry_now(self, meeting_dir: Path) -> ASRState:
        """重置状态，允许立即重试 OpenRouter Chirp 3"""
        logger.info("手动重试: meeting=%s, 重置 ASR 状态", meeting_dir.name)
        state = self._load_state(meeting_dir)
        if not state:
            state = ASRState(created_at=datetime.now(timezone.utc).isoformat())

        state.provider = "openrouter"
        state.status = "pending"
        state.next_retry_at = None
        state.retry_count = 0
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state(meeting_dir, state)
        return state

    def fallback_to_zhipu(self, meeting_dir: Path) -> ASRState:
        """切换到智谱 ASR"""
        logger.warning("手动降级: meeting=%s, 切换到智谱 ASR", meeting_dir.name)
        state = self._load_state(meeting_dir)
        if not state:
            state = ASRState(created_at=datetime.now(timezone.utc).isoformat())

        state.provider = "zhipu"
        state.status = "pending"
        state.next_retry_at = None
        state.retry_count = 0
        state.last_error = None
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state(meeting_dir, state)
        return state

    def get_state(self, meeting_dir: Path) -> Optional[ASRState]:
        """获取当前 ASR 状态"""
        return self._load_state(meeting_dir)

    # ---- 内部方法 ----

    def _resolve_provider(
        self,
        provider_override: Optional[str],
        state: Optional[ASRState],
    ) -> str:
        provider = provider_override
        if provider is None and state and state.status in {"pending", "running"}:
            provider = state.provider
        if provider in {"openrouter", "zhipu"}:
            return provider
        if provider == "vibevoice":
            logger.info("ASR provider=%s 已屏蔽，改用 openrouter", provider)
        return "openrouter"

    def _run_provider(
        self,
        provider: str,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
    ) -> Transcript:
        engine = self.zhipu if provider == "zhipu" else self.openrouter
        result = engine.transcribe(audio_files, meeting_dir, force=force)
        if not result or not result.segments:
            raise RuntimeError(f"{provider} ASR 未返回有效转写内容")
        return result

    def _clear_partial_asr_progress(self, meeting_dir: Path) -> None:
        """自动切换 ASR provider 前清理共享分片进度。"""
        progress_file = meeting_dir / TRANSCRIPT_PROGRESS_FILE
        if progress_file.exists():
            progress_file.unlink(missing_ok=True)

        chunks_dir = meeting_dir / ".chunks"
        if chunks_dir.exists():
            import shutil
            shutil.rmtree(chunks_dir, ignore_errors=True)

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
