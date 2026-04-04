"""
ASR 路由器 - 管理 VibeVoice（首选）与智谱（降级）之间的切换，
支持指数退避重试和用户手动干预。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from meeting_agent.asr.engine import ASREngine
from meeting_agent.asr.vibevoice_engine import VibeVoiceASREngine
from meeting_agent.config import ASR_STATE_FILE, Config
from meeting_agent.models import ASRState, Transcript

logger = logging.getLogger("meeting_agent.asr.router")


class ASRBlockedException(Exception):
    """ASR 失败后进入阻塞/重试状态时抛出"""

    def __init__(self, message: str, state: ASRState) -> None:
        super().__init__(message)
        self.state = state


class ASRRouter:
    """ASR 路由器：编排 VibeVoice（首选）与智谱（降级）"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.vibevoice = VibeVoiceASREngine(self.config)
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
            ASRBlockedException: VibeVoice 失败且未到重试时间
        """
        state = self._load_state(meeting_dir)

        # 确定本次使用的引擎
        provider = provider_override or (state.provider if state else "vibevoice")
        logger.info(
            "ASR 路由决策: meeting=%s, provider=%s, state=%s, force=%s",
            meeting_dir.name, provider,
            state.status if state else "无", force,
        )

        # 如果状态为 blocked，检查是否到了重试时间
        if state and state.status == "blocked" and provider == "vibevoice":
            if state.next_retry_at:
                next_retry = datetime.fromisoformat(state.next_retry_at)
                if datetime.now(timezone.utc) < next_retry:
                    logger.warning(
                        "VibeVoice 处于 blocked 状态，尚未到重试时间: next_retry=%s, retry_count=%d",
                        state.next_retry_at, state.retry_count,
                    )
                    raise ASRBlockedException(
                        f"VibeVoice ASR 失败，等待重试（下次: {state.next_retry_at}）",
                        state=state,
                    )
                logger.info("blocked 状态已到重试时间，继续执行")

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
                result = self.zhipu.transcribe(audio_files, meeting_dir, force=force)
            else:
                logger.info("使用 VibeVoice ASR（首选模式）")
                result = self.vibevoice.transcribe(audio_files, meeting_dir, force=force)

            # 成功
            seg_count = len(result.segments) if result else 0
            logger.info(
                "ASR 转写成功: provider=%s, segments=%d, duration=%.2fs",
                provider, seg_count, result.duration if result else 0,
            )
            state.status = "succeeded"
            state.last_error = None
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state(meeting_dir, state)
            return result

        except ASRBlockedException:
            raise  # 不拦截，直接上抛

        except Exception as e:
            error_msg = str(e)
            logger.error("ASR 转写失败 [%s]: %s", provider, error_msg)

            if provider == "vibevoice":
                # VibeVoice 失败 → 进入 blocked + 指数退避
                logger.warning(
                    "VibeVoice 失败，进入指数退避: retry_count=%d, error=%s",
                    state.retry_count + 1, error_msg[:200],
                )
                state.retry_count += 1
                delay = min(
                    self.config.settings.asr_initial_retry_delay * (2 ** (state.retry_count - 1)),
                    self.config.settings.asr_max_retry_delay,
                )
                state.status = "blocked"
                state.last_error = error_msg
                state.next_retry_at = datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + delay,
                    tz=timezone.utc,
                ).isoformat()
                state.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_state(meeting_dir, state)

                raise ASRBlockedException(
                    f"VibeVoice ASR 失败: {error_msg}（第 {state.retry_count} 次，"
                    f"下次重试: {state.next_retry_at}）",
                    state=state,
                ) from e
            else:
                # 智谱失败 → 直接报错，不进入重试循环
                state.status = "failed"
                state.last_error = error_msg
                state.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_state(meeting_dir, state)
                raise

    def retry_now(self, meeting_dir: Path) -> ASRState:
        """重置重试计时器，允许立即重试 VibeVoice"""
        logger.info("手动重试: meeting=%s, 重置 blocked 状态", meeting_dir.name)
        state = self._load_state(meeting_dir)
        if not state:
            state = ASRState(created_at=datetime.now(timezone.utc).isoformat())

        state.status = "pending"
        state.next_retry_at = None
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
