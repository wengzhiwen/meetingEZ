"""
OpenRouter ASR 引擎 - Google Chirp 3

使用 OpenRouter /audio/transcriptions JSON 接口，按 Chirp 3 推荐的
base64 input_audio 形式提交音频。
"""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

from meeting_agent.asr.engine import ASREngine
from meeting_agent.config import Config

logger = logging.getLogger("meeting_agent.asr.openrouter")


class OpenRouterASREngine(ASREngine):
    """通过 OpenRouter 调用 Google Chirp 3 的 ASR 转写引擎。"""

    def __init__(self, config: Optional[Config] = None):
        super().__init__(config)
        self.api_key = self.config.openrouter_api_key
        self.base_url = self.config.settings.openrouter_base_url
        self.asr_model = self.config.settings.openrouter_asr_model
        self.language = self.config.settings.openrouter_asr_language.strip()
        self.timeout = self.config.settings.openrouter_asr_timeout
        self.site_url = self.config.settings.openrouter_site_url.strip()
        self.site_name = self.config.settings.openrouter_site_name.strip()

    def _transcribe_short(
        self,
        audio_path: Path,
        time_offset: float = 0.0,
    ) -> list[dict]:
        """转写短音频（不超过 chunk_seconds）。"""
        logger.info("上传短音频到 OpenRouter Chirp 3: %s", audio_path.name)

        with tempfile.TemporaryDirectory(prefix="openrouter_asr_") as temp_dir:
            wav_path = Path(temp_dir) / f"{audio_path.stem}.wav"
            if not self._convert_to_wav(audio_path, wav_path):
                logger.error("OpenRouter ASR 音频转 WAV 失败: %s", audio_path)
                return []
            result = self._request_transcription(wav_path)

        return self._segments_from_result(result, time_offset)

    def _transcribe_chunk(self, chunk_path: Path) -> dict:
        """转写单个音频块。"""
        return self._request_transcription(chunk_path)

    def _split_chunk(
        self,
        audio_path: Path,
        output_path: Path,
        start_time: float,
        duration: float,
    ) -> bool:
        """切割音频片段为 Chirp 3 示例兼容的 WAV。"""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start_time),
            "-i", str(audio_path),
            "-t", str(duration),
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-f", "wav",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.error("OpenRouter ASR 切割音频失败: %s", e)
            return False

    def _chunk_extension(self) -> str:
        """OpenRouter Chirp 3 分片使用 WAV。"""
        return ".wav"

    def _request_transcription(self, audio_path: Path) -> dict:
        if not self.api_key:
            raise RuntimeError("未配置 OPENROUTER_API_KEY")

        endpoint = f"{self.base_url.rstrip('/')}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-OpenRouter-Title"] = self.site_name

        payload = {
            "model": self.asr_model,
            "input_audio": {
                "data": base64.b64encode(audio_path.read_bytes()).decode("utf-8"),
                "format": self._openrouter_audio_format(audio_path),
            },
        }
        if self.language:
            payload["language"] = self._normalize_language(self.language)

        response = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("OpenRouter ASR 返回格式异常")
        return result

    def _convert_to_wav(self, audio_path: Path, output_path: Path) -> bool:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(audio_path),
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-f", "wav",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.error("OpenRouter ASR 音频转码失败: %s", e)
            return False

    def _openrouter_audio_format(self, audio_path: Path) -> str:
        suffix = audio_path.suffix.lower().lstrip(".")
        return "wav" if suffix == "wave" else (suffix or "wav")

    def _normalize_language(self, language: str) -> str:
        return language.split("-")[0].lower()

    def _segments_from_result(
        self,
        result: dict,
        time_offset: float,
    ) -> list[dict]:
        if "segments" in result:
            segments = []
            for seg in result.get("segments", []):
                text = str(seg.get("text", "")).strip()
                if text:
                    segments.append({
                        "start": float(seg.get("start", 0)) + time_offset,
                        "end": float(seg.get("end", 0)) + time_offset,
                        "text": text,
                    })
            return segments

        text = str(result.get("text", "")).strip()
        if not text:
            return []
        return [{
            "start": time_offset,
            "end": time_offset + 30.0,
            "text": text,
        }]
