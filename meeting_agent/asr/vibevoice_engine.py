"""
VibeVoice ASR 引擎 - 基于 VibeVoice-ASR vLLM 服务

通过 OpenAI 兼容的 /v1/chat/completions 接口进行长音频转写，
支持说话人分离（Speaker ID）。
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meeting_agent.config import Config, TRANSCRIPT_FILE
from meeting_agent.progress import (
    update_chunks as _update_chunks_progress,
    update_audio as _update_audio_progress,
)
from meeting_agent.models import Transcript, TranscriptSegment

logger = logging.getLogger("meeting_agent.asr.vibevoice")

SYSTEM_PROMPT = (
    "You are a helpful assistant that transcribes audio input into "
    "text output in JSON format."
)

SHOW_KEYS = ["Start time", "End time", "Speaker ID", "Content"]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _shutil_which(command: str) -> Optional[str]:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for base in paths:
        candidate = Path(base) / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def guess_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    mime_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/mp4",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
    }
    if ext in mime_map:
        return mime_map[ext]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def get_duration_seconds(path: Path) -> Optional[float]:
    ffprobe = _shutil_which("ffprobe")
    if ffprobe:
        cmd = [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=30)
            return float(result.stdout.strip())
        except Exception as exc:
            logger.warning("ffprobe 获取时长失败: %s", exc)
    return None


def build_prompt(duration_seconds: Optional[float], hotwords: Optional[str]) -> str:
    if duration_seconds is not None:
        prefix = f"This is a {duration_seconds:.2f} seconds audio"
    else:
        prefix = "This is an audio recording"
    if hotwords and hotwords.strip():
        prefix += f", with extra info: {hotwords.strip()}"
    return (
        f"{prefix}\n\nPlease transcribe it with these keys: "
        + ", ".join(SHOW_KEYS)
    )


def pick_first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def parse_time_to_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------------------
# 重复检测
# ---------------------------------------------------------------------------

class RepetitionDetector:
    def __init__(
        self,
        min_pattern_len: int = 10,
        min_repeats: int = 10,
        window_size: int = 400,
    ) -> None:
        self.min_pattern_len = min_pattern_len
        self.min_repeats = min_repeats
        self.window_size = window_size

    def check(self, text: str) -> Tuple[bool, int]:
        if len(text) < self.min_pattern_len * self.min_repeats:
            return False, len(text)

        window = text[-self.window_size:] if len(text) > self.window_size else text
        for pattern_len in range(
            self.min_pattern_len,
            len(window) // self.min_repeats + 1,
        ):
            pattern = window[-pattern_len:]
            count = 0
            pos = len(window)
            while pos >= pattern_len:
                if window[pos - pattern_len:pos] == pattern:
                    count += 1
                    pos -= pattern_len
                else:
                    break
            if count >= self.min_repeats:
                repetition_start = len(text) - (count * pattern_len)
                if self._is_meaningful(pattern):
                    return True, repetition_start + pattern_len
                return True, repetition_start

        words = window.split()
        if len(words) >= self.min_repeats * 2:
            for phrase_len in range(2, 6):
                if len(words) < phrase_len * self.min_repeats:
                    continue
                phrase = " ".join(words[-phrase_len:])
                count = 0
                idx = len(words)
                while idx >= phrase_len:
                    candidate = " ".join(words[idx - phrase_len:idx])
                    if candidate == phrase:
                        count += 1
                        idx -= phrase_len
                    else:
                        break
                if count >= self.min_repeats:
                    repeated_text = (phrase + " ") * count
                    good_end = len(text) - len(repeated_text.rstrip()) + len(phrase)
                    return True, max(0, good_end)

        return False, len(text)

    def _is_meaningful(self, pattern: str) -> bool:
        clean = pattern.strip()
        if not clean:
            return False
        return len(set(clean)) >= 3


class PartialTranscriptionError(RuntimeError):
    def __init__(self, message: str, safe_text: str) -> None:
        super().__init__(message)
        self.safe_text = safe_text


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def _find_last_segment_boundary(text: str) -> int:
    pos = text.rfind("},")
    if pos != -1:
        return pos + 2
    return -1


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_json_output(text: str) -> Optional[Any]:
    stripped = strip_code_fences(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    candidates = []
    trimmed = stripped.rstrip(", \n\t")
    if trimmed:
        candidates.append(trimmed)
        if trimmed.startswith("[") and not trimmed.endswith("]"):
            candidates.append(trimmed + "]")
        if not trimmed.startswith("["):
            candidates.append("[" + trimmed + "]")

    boundary = _find_last_segment_boundary(stripped)
    if boundary > 0:
        prefix = stripped[:boundary].rstrip(", \n\t")
        if prefix:
            if prefix.startswith("["):
                candidates.append(prefix + "]")
            else:
                candidates.append("[" + prefix + "]")

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return _extract_json_objects(stripped)


def _extract_json_objects(text: str) -> Optional[List[Dict[str, Any]]]:
    objects: List[Dict[str, Any]] = []
    start_idx: Optional[int] = None
    depth = 0
    in_string = False
    escape = False

    for idx, ch in enumerate(text):
        if start_idx is None:
            if ch == "{":
                start_idx = idx
                depth = 1
                in_string = False
                escape = False
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start_idx:idx + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(parsed, dict):
                        objects.append(parsed)
                start_idx = None

    return objects if objects else None


def normalize_segments(data: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            data = data["segments"]
        elif isinstance(data.get("transcript"), list):
            data = data["transcript"]
    if not isinstance(data, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "start": pick_first(item, "Start time", "Start", "start", "start_time"),
            "end": pick_first(item, "End time", "End", "end", "end_time"),
            "speaker": pick_first(item, "Speaker ID", "Speaker", "speaker", "speaker_id"),
            "content": pick_first(item, "Content", "content", "text"),
        })
    return normalized


# ---------------------------------------------------------------------------
# 音频分片
# ---------------------------------------------------------------------------

def extract_audio_region(
    audio_path: Path,
    start_time: float,
    duration: float,
    output_path: Path,
) -> Path:
    ffmpeg = _shutil_which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 才能处理长音频")
    cmd = [
        ffmpeg, "-y", "-ss", f"{start_time:.3f}",
        "-i", str(audio_path),
        "-t", f"{duration:.3f}",
        "-ac", "1", "-ar", "16000",
        "-c:a", "flac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path


# ---------------------------------------------------------------------------
# 流式 API 调用
# ---------------------------------------------------------------------------

def _build_messages(data_url: str, prompt_text: str) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "audio_url", "audio_url": {"url": data_url}},
                {"type": "text", "text": prompt_text},
            ],
        },
    ]


def _open_chat_completion(
    base_url: str,
    payload: Dict[str, Any],
    timeout: int,
):
    import socket as _socket
    url = base_url.rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    # 设置 socket 级别读取超时，防止流式连接无限挂起
    # urlopen 的 timeout 只管连接建立，这里确保每次 read 也有超时
    try:
        sock = resp.fp
        if hasattr(sock, 'raw'):
            sock = sock.raw
        if hasattr(sock, '_sock'):
            sock._sock.settimeout(float(timeout))
    except Exception:
        pass
    return resp


def stream_chat_completion(
    base_url: str,
    data_url: str,
    prompt_text: str,
    model: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    timeout: int,
    max_retries: int = 3,
) -> str:
    detector = RepetitionDetector()
    accumulated_text = ""
    retry_count = 0
    request_start = None

    while retry_count <= max_retries:
        messages = _build_messages(data_url, prompt_text)
        current_temperature = temperature
        current_top_p = top_p

        if accumulated_text:
            messages.append({"role": "assistant", "content": accumulated_text})

        if retry_count > 0:
            current_temperature = 0.1 + 0.1 * retry_count
            current_top_p = 0.95
            logger.warning(
                "检测到输出回环，开始第 %d 次恢复：temperature=%.1f, top_p=%s",
                retry_count, current_temperature, current_top_p,
            )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": current_temperature,
            "top_p": current_top_p,
            "stream": True,
        }

        printed = ""
        new_text = ""
        request_start = datetime.now()
        logger.info(
            "VibeVoice API 请求: url=%s, model=%s, retry=%d, accumulated_chars=%d",
            base_url.rstrip("/") + "/v1/chat/completions", model, retry_count,
            len(accumulated_text),
        )
        try:
            with _open_chat_completion(base_url, payload, timeout) as response:
                for raw_line in response:
                    # 读取超时检测：urlopen 的 timeout 只管连接，流式读取需自行检测
                    if (datetime.now() - request_start).total_seconds() > timeout:
                        raise TimeoutError(
                            f"VibeVoice 流式读取超时（{timeout}s 无完整响应）"
                        )
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    body = line[6:]
                    if body == "[DONE]":
                        return accumulated_text + new_text

                    try:
                        event = json.loads(body)
                    except json.JSONDecodeError:
                        continue

                    choices = event.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}
                    content = delta.get("content", "")
                    if not content:
                        continue

                    if content.startswith(printed):
                        to_add = content[len(printed):]
                    else:
                        to_add = content

                    if not to_add:
                        continue

                    printed += to_add
                    new_text += to_add

                    # 修正续写的 JSON 格式
                    if accumulated_text and new_text:
                        stripped = new_text.lstrip()
                        if stripped.startswith("[{"):
                            new_text = stripped[1:]
                        elif stripped.startswith("["):
                            new_text = stripped[1:]
                        elif stripped.startswith("},"):
                            new_text = stripped[2:]
                        elif stripped.startswith("}") and not stripped.startswith("}]"):
                            new_text = stripped[1:]

                        malformed = re.match(r'^\{"(\d+\.?\d*),', new_text)
                        if malformed:
                            time_val = malformed.group(1)
                            new_text = (
                                '{"Start":' + time_val + "," + new_text[malformed.end():]
                            )

                    full_text = accumulated_text + new_text
                    is_looping, _ = detector.check(full_text)
                    if is_looping:
                        boundary = _find_last_segment_boundary(full_text)
                        safe_text = full_text[:boundary] if boundary > 0 else accumulated_text
                        accumulated_text = safe_text
                        retry_count += 1
                        if retry_count > max_retries:
                            raise PartialTranscriptionError(
                                "模型输出出现重复回环，超过最大恢复次数",
                                safe_text=safe_text,
                            )
                        break

                else:
                    result_text = accumulated_text + new_text
                    elapsed = (datetime.now() - request_start).total_seconds() if request_start else 0
                    logger.info(
                        "VibeVoice API 响应完成: elapsed=%.1fs, output_chars=%d",
                        elapsed, len(result_text),
                    )
                    return result_text
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError) as http_exc:
            elapsed = (datetime.now() - request_start).total_seconds() if request_start else 0
            logger.error(
                "VibeVoice API 请求失败: elapsed=%.1fs, error=%s",
                elapsed, http_exc,
            )
            raise

    raise RuntimeError("模型输出恢复失败")


def transcribe_single_audio(
    audio_path: Path,
    base_url: str,
    model: str,
    hotwords: Optional[str],
    max_tokens: int,
    temperature: float,
    top_p: float,
    timeout: int,
) -> str:
    duration_seconds = get_duration_seconds(audio_path)
    prompt_text = build_prompt(duration_seconds, hotwords)
    audio_bytes = audio_path.read_bytes()
    mime = guess_mime_type(audio_path)
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{audio_b64}"
    logger.info(
        "准备 VibeVoice 请求: audio=%s, size=%.2fKB, duration=%ss, base64=%.2fKB",
        audio_path.name, len(audio_bytes) / 1024,
        f"{duration_seconds:.2f}" if duration_seconds else "未知",
        len(audio_b64) / 1024,
    )
    return stream_chat_completion(
        base_url=base_url,
        data_url=data_url,
        prompt_text=prompt_text,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 片段合并
# ---------------------------------------------------------------------------

def merge_chunk_segments(
    chunk_segments: List[List[Dict[str, Any]]],
    chunk_offsets: List[float],
    overlap_seconds: int,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for idx, segments in enumerate(chunk_segments):
        offset = chunk_offsets[idx]
        for seg in segments:
            start_seconds = parse_time_to_seconds(seg.get("start"))
            end_seconds = parse_time_to_seconds(seg.get("end"))

            # 跳过重叠区域
            if idx > 0 and start_seconds is not None and start_seconds < overlap_seconds:
                continue

            merged.append({
                "start": round(offset + start_seconds, 3) if start_seconds is not None else seg.get("start"),
                "end": round(offset + end_seconds, 3) if end_seconds is not None else seg.get("end"),
                "speaker": seg.get("speaker"),
                "content": seg.get("content"),
            })
    return merged


# ---------------------------------------------------------------------------
# VibeVoice ASR 引擎
# ---------------------------------------------------------------------------

class VibeVoiceASREngine:
    """VibeVoice ASR 转写引擎"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        s = self.config.settings
        self.base_url = s.vibevoice_base_url
        self.model = s.vibevoice_model
        self.max_tokens = s.vibevoice_max_tokens
        self.timeout = s.vibevoice_timeout
        self.max_audio_seconds = s.vibevoice_max_audio_seconds
        self.overlap_seconds = s.vibevoice_overlap_seconds

    def transcribe(
        self,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
    ) -> Optional[Transcript]:
        """转写音频文件列表"""
        self._meeting_dir = meeting_dir
        if not audio_files:
            logger.warning("没有音频文件需要转写")
            return None

        transcript_file = meeting_dir / TRANSCRIPT_FILE
        if transcript_file.exists() and not force:
            logger.info("转写结果已存在: %s", transcript_file)
            return self._load_transcript(transcript_file)

        all_segments: list[TranscriptSegment] = []
        total_duration = 0.0
        audio_file_names = []
        time_offset = 0.0

        for audio_idx, audio_file in enumerate(sorted(audio_files), 1):
            logger.info("VibeVoice 处理音频: %s", audio_file.name)
            audio_file_names.append(audio_file.name)

            duration = get_duration_seconds(audio_file) or 0.0
            _update_audio_progress(
                self._meeting_dir,
                audio_index=audio_idx,
                audio_total=len(audio_files),
                audio_name=audio_file.name,
                audio_duration=duration,
            )
            if duration <= 0:
                logger.warning("无法获取音频时长，跳过: %s", audio_file)
                continue

            logger.info("音频时长: %.2f 秒", duration)

            try:
                segments = self._transcribe_audio(audio_file, time_offset)
            except Exception as e:
                logger.error("VibeVoice 转写失败: %s", e)
                raise

            all_segments.extend(segments)
            total_duration += duration
            time_offset += duration

        if not all_segments:
            logger.error("转写失败，没有产生任何内容")
            return None

        transcript = Transcript(
            meeting_dir=meeting_dir.name,
            audio_files=audio_file_names,
            segments=all_segments,
            duration=total_duration,
            created_at=datetime.now(),
            language=self.config.settings.default_language,
        )

        self._save_transcript(transcript_file, transcript)
        logger.info(
            "VibeVoice 转写完成: %d 个片段, 总时长 %.2f 秒",
            len(all_segments), total_duration,
        )
        return transcript

    def _transcribe_audio(
        self,
        audio_path: Path,
        time_offset: float = 0.0,
    ) -> list[TranscriptSegment]:
        """转写单个音频文件"""
        duration = get_duration_seconds(audio_path) or 0.0

        if duration <= self.max_audio_seconds:
            # 短音频，单次提交
            return self._transcribe_single(audio_path, time_offset)

        # 长音频，分片处理
        return self._transcribe_long(audio_path, time_offset, duration)

    def _transcribe_single(
        self,
        audio_path: Path,
        time_offset: float = 0.0,
    ) -> list[TranscriptSegment]:
        """单次提交转写"""
        logger.info("VibeVoice 短音频直接提交: %s", audio_path.name)
        raw_text = transcribe_single_audio(
            audio_path=audio_path,
            base_url=self.base_url,
            model=self.model,
            hotwords=None,
            max_tokens=self.max_tokens,
            temperature=0.0,
            top_p=1.0,
            timeout=self.timeout,
        )
        return self._parse_raw_text(raw_text, time_offset)

    def _transcribe_long(
        self,
        audio_path: Path,
        time_offset: float,
        total_duration: float,
    ) -> list[TranscriptSegment]:
        """长音频分片转写"""
        step_seconds = self.max_audio_seconds - self.overlap_seconds
        total_chunks = int(total_duration / step_seconds) + 1
        logger.info(
            "VibeVoice 长音频分片: duration=%.2fs, chunk_size=%ds, overlap=%ds, 预计%d片",
            total_duration, self.max_audio_seconds, self.overlap_seconds, total_chunks,
        )
        all_segments: list[TranscriptSegment] = []

        with tempfile.TemporaryDirectory(prefix="vibevoice_asr_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)

            regions: List[Tuple[float, float]] = []
            start = 0.0
            region_idx = 0
            while start < total_duration:
                dur = min(float(self.max_audio_seconds), total_duration - start)
                regions.append((start, dur))
                start += step_seconds

            chunk_segments_list: List[List[Dict[str, Any]]] = []
            chunk_offsets: List[float] = []

            while regions:
                chunk_offset, chunk_duration = regions.pop(0)
                chunk_path = temp_dir / f"chunk_{region_idx:03d}.flac"

                logger.info(
                    "VibeVoice 转录长片段 %d: offset=%.2fs, duration=%.2fs",
                    region_idx + 1, chunk_offset, chunk_duration,
                )

                extract_audio_region(
                    audio_path=audio_path,
                    start_time=chunk_offset,
                    duration=chunk_duration,
                    output_path=chunk_path,
                )

                try:
                    raw_text = transcribe_single_audio(
                        audio_path=chunk_path,
                        base_url=self.base_url,
                        model=self.model,
                        hotwords=None,
                        max_tokens=self.max_tokens,
                        temperature=0.0,
                        top_p=1.0,
                        timeout=self.timeout,
                    )
                except PartialTranscriptionError as exc:
                    if not exc.safe_text.strip():
                        raise RuntimeError(
                            f"第 {region_idx + 1} 个长片段恢复失败，没有可用安全输出"
                        ) from exc

                    parsed = parse_json_output(exc.safe_text)
                    normalized = normalize_segments(parsed) if parsed else None
                    if not normalized:
                        raise RuntimeError(
                            f"第 {region_idx + 1} 个长片段恢复失败，安全输出无法解析"
                        ) from exc

                    # 计算已完成的末尾位置，将剩余部分重新排队
                    last_local_end = max(
                        (parse_time_to_seconds(seg.get("end"))
                         for seg in normalized
                         if parse_time_to_seconds(seg.get("end")) is not None),
                        default=None,
                    )
                    if last_local_end is None:
                        raise RuntimeError(
                            f"第 {region_idx + 1} 个长片段恢复失败，无法确定已完成位置"
                        ) from exc

                    chunk_segments_list.append(normalized)
                    chunk_offsets.append(chunk_offset)

                    next_start = max(
                        chunk_offset,
                        chunk_offset + last_local_end - self.overlap_seconds,
                    )
                    remaining_duration = (chunk_offset + chunk_duration) - next_start
                    if remaining_duration > max(self.overlap_seconds, 1):
                        if next_start <= chunk_offset + 1:
                            raise RuntimeError(
                                f"第 {region_idx + 1} 个长片段恢复失败，无法安全推进切分边界"
                            ) from exc
                        logger.warning(
                            "片段尾部不稳定，继续细分: next=%.2fs, remaining=%.2fs",
                            next_start, remaining_duration,
                        )
                        regions.insert(0, (next_start, remaining_duration))

                    region_idx += 1
                    _update_chunks_progress(self._meeting_dir, region_idx, total_chunks)
                    continue

                parsed = parse_json_output(raw_text)
                if parsed is None:
                    raise RuntimeError(
                        f"第 {region_idx + 1} 个长片段输出不是合法 JSON"
                    )
                normalized = normalize_segments(parsed)
                if normalized is None:
                    raise RuntimeError(
                        f"第 {region_idx + 1} 个长片段 JSON 结构无法识别"
                    )

                chunk_segments_list.append(normalized)
                chunk_offsets.append(chunk_offset)
                region_idx += 1
                _update_chunks_progress(self._meeting_dir, region_idx, total_chunks)
            merged = merge_chunk_segments(
                chunk_segments=chunk_segments_list,
                chunk_offsets=chunk_offsets,
                overlap_seconds=self.overlap_seconds,
            )

            for seg in merged:
                start_s = parse_time_to_seconds(seg.get("start"))
                end_s = parse_time_to_seconds(seg.get("end"))
                content = seg.get("content") or ""
                speaker = seg.get("speaker")
                if isinstance(speaker, (int, float)):
                    speaker = str(int(speaker))

                all_segments.append(TranscriptSegment(
                    start=round(time_offset + (start_s or 0), 3),
                    end=round(time_offset + (end_s or 0), 3),
                    text=str(content).strip(),
                    speaker=speaker,
                ))

        return all_segments

    def _parse_raw_text(
        self,
        raw_text: str,
        time_offset: float = 0.0,
    ) -> list[TranscriptSegment]:
        """解析原始模型输出为 TranscriptSegment 列表"""
        parsed = parse_json_output(raw_text)
        segments = normalize_segments(parsed) if parsed else None

        if not segments:
            # 无法解析 JSON，整段作为纯文本
            if raw_text.strip():
                return [TranscriptSegment(
                    start=time_offset,
                    end=time_offset + 30.0,
                    text=raw_text.strip(),
                )]
            return []

        result = []
        for seg in segments:
            start_s = parse_time_to_seconds(seg.get("start"))
            end_s = parse_time_to_seconds(seg.get("end"))
            content = seg.get("content") or ""
            speaker = seg.get("speaker")
            if isinstance(speaker, (int, float)):
                speaker = str(int(speaker))

            if not str(content).strip():
                continue

            result.append(TranscriptSegment(
                start=round(time_offset + (start_s or 0), 3),
                end=round(time_offset + (end_s or 0), 3),
                text=str(content).strip(),
                speaker=speaker,
            ))

        return result

    def _load_transcript(self, transcript_file: Path) -> Optional[Transcript]:
        try:
            with open(transcript_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Transcript(**data)
        except Exception as e:
            logger.warning("加载转写结果失败: %s", e)
            return None

    def _save_transcript(self, transcript_file: Path, transcript: Transcript):
        data = transcript.model_dump()
        if "created_at" in data and not isinstance(data["created_at"], str):
            data["created_at"] = transcript.created_at.isoformat()
        with open(transcript_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
