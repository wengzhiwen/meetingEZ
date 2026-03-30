"""
ASR 引擎 - 基于智谱 AI GLM-ASR-2512
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from meeting_agent.config import Config, TRANSCRIPT_FILE, TRANSCRIPT_PROGRESS_FILE
from meeting_agent.models import Transcript, TranscriptSegment

logger = logging.getLogger("meeting_agent.asr")


class ASREngine:
    """ASR 转写引擎"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.api_key = self.config.zhipu_api_key
        self.base_url = self.config.settings.zhipu_api_base_url
        self.asr_model = self.config.settings.zhipu_asr_model
        self.chunk_seconds = self.config.settings.asr_chunk_seconds
        self.overlap_seconds = self.config.settings.asr_overlap_seconds

    def transcribe(
        self,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
    ) -> Optional[Transcript]:
        """
        转写音频文件列表

        Args:
            audio_files: 音频文件路径列表
            meeting_dir: 会议目录
            force: 是否强制重新转写

        Returns:
            Transcript 对象或 None
        """
        if not audio_files:
            logger.warning("没有音频文件需要转写")
            return None

        transcript_file = meeting_dir / TRANSCRIPT_FILE
        progress_file = meeting_dir / TRANSCRIPT_PROGRESS_FILE
        chunks_dir = meeting_dir / ".chunks"  # 分片缓存目录

        # 检查是否已有转写结果
        if transcript_file.exists() and not force:
            logger.info("转写结果已存在: %s", transcript_file)
            return self._load_transcript(transcript_file)

        # 检查是否有未完成的进度
        existing_progress = []
        if progress_file.exists() and not force:
            existing_progress = self._load_progress(progress_file)
            logger.info("发现未完成的进度: %d 个片段", len(existing_progress))

        # 转写所有音频文件
        all_segments: list[TranscriptSegment] = []
        total_duration = 0.0
        audio_file_names = []
        all_success = True

        time_offset = 0.0

        for audio_file in sorted(audio_files):
            logger.info("处理音频文件: %s", audio_file.name)
            audio_file_names.append(audio_file.name)

            # 获取音频时长
            duration = self._get_audio_duration(audio_file)
            if duration <= 0:
                logger.warning("无法获取音频时长，跳过: %s", audio_file)
                continue

            logger.info("音频时长: %.2f 秒", duration)

            # 转写（传递分片目录）
            segments, success = self._transcribe_audio(
                audio_file,
                time_offset,
                existing_progress if time_offset == 0 else [],
                progress_file,
                chunks_dir,  # 传递分片缓存目录
            )

            if not success:
                all_success = False

            # 调整时间戳
            for seg in segments:
                all_segments.append(TranscriptSegment(
                    start=seg["start"] + time_offset,
                    end=seg["end"] + time_offset,
                    text=seg["text"],
                ))

            total_duration += duration
            time_offset += duration

        if not all_segments:
            logger.error("转写失败，没有产生任何内容")
            return None

        # 创建 Transcript 对象
        transcript = Transcript(
            meeting_dir=meeting_dir.name,
            audio_files=audio_file_names,
            segments=all_segments,
            duration=total_duration,
            created_at=datetime.now(),
            language=self.config.settings.default_language,
        )

        # 保存结果
        self._save_transcript(transcript_file, transcript)

        # 只有在完全成功后才清理进度文件和分片缓存
        if all_success:
            # 清理进度文件
            if progress_file.exists():
                progress_file.unlink()

            # 清理分片缓存
            if chunks_dir.exists():
                import shutil
                shutil.rmtree(chunks_dir, ignore_errors=True)
                logger.info("已清理分片缓存: %s", chunks_dir)

        logger.info("转写完成: %d 个片段, 总时长 %.2f 秒", len(all_segments), total_duration)

        return transcript

    def _transcribe_audio(
        self,
        audio_path: Path,
        time_offset: float = 0.0,
        existing_progress: Optional[list[dict]] = None,
        progress_file: Optional[Path] = None,
        chunks_dir: Optional[Path] = None,
    ) -> tuple[list[dict], bool]:
        """
        转写单个音频文件

        Args:
            audio_path: 音频文件路径
            time_offset: 时间偏移量
            existing_progress: 已有的进度数据
            progress_file: 进度保存文件路径
            chunks_dir: 分片存储目录

        Returns:
            (片段列表, 是否完全成功)
        """
        duration = self._get_audio_duration(audio_path)

        # 短音频直接转写
        if duration <= self.chunk_seconds:
            return self._transcribe_short(audio_path, time_offset), True

        # 长音频分块处理
        return self._transcribe_long(audio_path, time_offset, existing_progress, progress_file, chunks_dir)

    def _transcribe_short(self, audio_path: Path, time_offset: float = 0.0) -> list[dict]:
        """转写短音频（不超过 chunk_seconds）"""
        import requests

        logger.info("上传短音频到智谱 AI: %s", audio_path.name)

        endpoint = f"{self.base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f)}
                data = {"model": self.asr_model, "stream": "false"}
                response = requests.post(
                    endpoint,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=120,
                )
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            logger.error("转写失败: %s", e)
            return []

        # 解析结果
        segments = []
        if isinstance(result, dict):
            if "segments" in result:
                for seg in result.get("segments", []):
                    text = str(seg.get("text", "")).strip()
                    if text:
                        segments.append({
                            "start": float(seg.get("start", 0)) + time_offset,
                            "end": float(seg.get("end", 0)) + time_offset,
                            "text": text,
                        })
            elif "text" in result:
                text = str(result["text"]).strip()
                if text:
                    segments.append({
                        "start": time_offset,
                        "end": time_offset + 30.0,
                        "text": text,
                    })

        return segments

    def _transcribe_long(
        self,
        audio_path: Path,
        time_offset: float = 0.0,
        existing_progress: Optional[list[dict]] = None,
        progress_file: Optional[Path] = None,
        chunks_dir: Optional[Path] = None,
    ) -> tuple[list[dict], bool]:
        """
        转写长音频（分块处理）

        Args:
            audio_path: 音频文件路径
            time_offset: 时间偏移量
            existing_progress: 已有的进度数据
            progress_file: 进度保存文件路径
            chunks_dir: 分片存储目录（如果提供，分片会被缓存）

        Returns:
            (片段列表, 是否完全成功)
        """
        import requests

        duration = self._get_audio_duration(audio_path)
        step_seconds = self.chunk_seconds - self.overlap_seconds

        # 计算分块
        chunk_starts = []
        current = 0.0
        while current < duration:
            chunk_starts.append(current)
            current += step_seconds

        total_chunks = len(chunk_starts)
        logger.info("音频将分 %d 块处理 (每块 %.0f 秒, 重叠 %.0f 秒)",
                   total_chunks, self.chunk_seconds, self.overlap_seconds)

        # 恢复已处理的进度
        processed_indices = set()
        chunk_results = []
        if existing_progress:
            for p in existing_progress:
                processed_indices.add(p["idx"])
                chunk_results.append((p["idx"], p["result"]))
            logger.info("从进度文件恢复: 已处理 %d 块", len(processed_indices))

        # 确定分片存储位置
        if chunks_dir:
            chunks_dir.mkdir(parents=True, exist_ok=True)
            use_cache = True
            logger.info("分片缓存目录: %s", chunks_dir)
        else:
            chunks_dir = Path(tempfile.mkdtemp(prefix="meeting_asr_"))
            use_cache = False

        all_success = True

        try:
            for idx, chunk_start in enumerate(chunk_starts):
                # 跳过已处理的
                if idx in processed_indices:
                    logger.info("跳过已处理块: %d/%d", idx + 1, total_chunks)
                    continue

                chunk_duration = min(self.chunk_seconds, duration - chunk_start)
                chunk_file = chunks_dir / f"chunk_{idx:04d}.mp3"

                # 检查分片是否已存在（缓存复用）
                if use_cache and chunk_file.exists() and chunk_file.stat().st_size > 0:
                    logger.info("复用缓存分片: %d/%d", idx + 1, total_chunks)
                else:
                    logger.info("处理第 %d/%d 块: %.2f - %.2f 秒",
                               idx + 1, total_chunks, chunk_start, chunk_start + chunk_duration)

                    # 切割音频
                    if not self._split_chunk(audio_path, chunk_file, chunk_start, chunk_duration):
                        logger.error("切割音频块失败: %s", chunk_file)
                        all_success = False
                        continue

                # 转写
                try:
                    result = self._transcribe_chunk(chunk_file)
                    chunk_results.append((idx, result))

                    # 立即保存进度
                    if progress_file:
                        self._save_progress(progress_file, idx, chunk_start, chunk_start + chunk_duration, result)

                    # 显示预览
                    if isinstance(result, dict) and "text" in result:
                        preview = result["text"][:50]
                        if len(result.get("text", "")) > 50:
                            preview += "..."
                        logger.info("  -> %s", preview)

                except Exception as e:
                    logger.error("转写块失败: %s", e)
                    all_success = False

        except Exception as e:
            logger.error("分块处理异常: %s", e)
            all_success = False

        # 只有在不使用缓存时才清理临时目录
        if not use_cache:
            import shutil
            shutil.rmtree(chunks_dir, ignore_errors=True)

        # 合并结果
        return self._merge_results(chunk_results, chunk_starts, time_offset), all_success

    def _transcribe_chunk(self, chunk_path: Path) -> dict:
        """转写单个音频块"""
        import requests

        endpoint = f"{self.base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        with open(chunk_path, "rb") as f:
            files = {"file": (chunk_path.name, f)}
            data = {"model": self.asr_model, "stream": "false"}
            response = requests.post(
                endpoint,
                headers=headers,
                files=files,
                data=data,
                timeout=120,
            )
        response.raise_for_status()
        return response.json()

    def _split_chunk(
        self,
        audio_path: Path,
        output_path: Path,
        start_time: float,
        duration: float,
    ) -> bool:
        """切割音频片段"""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start_time),
            "-i", str(audio_path),
            "-t", str(duration),
            "-ar", "16000",
            "-ac", "1",
            "-f", "mp3",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.error("切割音频失败: %s", e)
            return False

    def _get_audio_duration(self, audio_path: Path) -> float:
        """获取音频时长"""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            logger.warning("获取音频时长失败: %s", e)

        return 0.0

    def _merge_results(
        self,
        chunk_results: list[tuple[int, dict]],
        chunk_starts: list[float],
        time_offset: float = 0.0,
    ) -> list[dict]:
        """合并多个块的转写结果"""
        if not chunk_results:
            return []

        # 按索引排序
        chunk_results.sort(key=lambda x: x[0])

        all_segments = []

        for idx, result in chunk_results:
            if not isinstance(result, dict):
                continue

            chunk_start = chunk_starts[idx] if idx < len(chunk_starts) else 0

            if "segments" in result:
                for seg in result.get("segments", []):
                    text = str(seg.get("text", "")).strip()
                    if not text:
                        continue

                    local_start = float(seg.get("start", 0))
                    local_end = float(seg.get("end", 0))

                    # 非第一个块，跳过开头的重叠部分
                    if idx > 0 and local_start < self.overlap_seconds:
                        continue

                    all_segments.append({
                        "global_start": time_offset + chunk_start + local_start,
                        "global_end": time_offset + chunk_start + local_end,
                        "text": text,
                    })
            elif "text" in result:
                text = str(result["text"]).strip()
                if text:
                    all_segments.append({
                        "global_start": time_offset + chunk_start,
                        "global_end": time_offset + chunk_start + 30.0,
                        "text": text,
                    })

        # 按时间排序并转换为最终格式
        all_segments.sort(key=lambda x: x["global_start"])

        return [
            {
                "start": seg["global_start"],
                "end": seg["global_end"],
                "text": seg["text"],
            }
            for seg in all_segments
        ]

    def _load_transcript(self, transcript_file: Path) -> Optional[Transcript]:
        """加载已有的转写结果"""
        try:
            with open(transcript_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Transcript(**data)
        except Exception as e:
            logger.warning("加载转写结果失败: %s", e)
            return None

    def _save_transcript(self, transcript_file: Path, transcript: Transcript):
        """保存转写结果"""
        data = transcript.model_dump()
        # 处理 datetime 序列化
        if "created_at" in data and not isinstance(data["created_at"], str):
            data["created_at"] = transcript.created_at.isoformat()

        with open(transcript_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_progress(self, progress_file: Path) -> list[dict]:
        """加载进度文件"""
        if not progress_file.exists():
            return []

        try:
            results = []
            with open(progress_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        results.append(json.loads(line))
            return results
        except Exception as e:
            logger.warning("加载进度文件失败: %s", e)
            return []

    def _save_progress(
        self,
        progress_file: Path,
        idx: int,
        chunk_start: float,
        chunk_end: float,
        result: dict,
    ):
        """保存进度"""
        entry = {
            "idx": idx,
            "chunk_start": chunk_start,
            "chunk_end": chunk_end,
            "result": result,
        }
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
