"""
ASR 引擎 - OpenAI gpt-transcribe（/v1/audio/transcriptions）

模型能力决定了这里的实现方式：

- gpt-transcribe 只支持 response_format=json / text，不返回 segment 或 word
  级时间戳（verbose_json 会被拒绝）。时间戳因此由分块边界推导，块内再按字符
  数比例摊到句子上。
- 支持 prompt（自由文本描述录音场景）、keywords（人名/产品名等字面词）、
  languages（预期输入语言），三者都从项目的 _context.json / _glossary.json /
  _people.json 自动构造。
- 分块不使用重叠：没有时间戳就无法去重重叠文本。改为把上一块结尾的文本作为
  下一块的 prompt 上下文，保证跨块的称谓和术语连贯。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from meeting_agent.config import Config, Settings, TRANSCRIPT_FILE, TRANSCRIPT_PROGRESS_FILE
from meeting_agent.models import Transcript, TranscriptSegment

logger = logging.getLogger("meeting_agent.asr")

# 句子切分：中英文句末标点 + 换行。保留标点在句尾。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")
_UNSAFE_STEM_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _safe_stem(stem: str) -> str:
    """把音频文件名压成可安全用于分片文件名的短标识。"""
    normalized = _UNSAFE_STEM_RE.sub("_", stem).strip("_")
    return (normalized or "audio")[:40]


class OpenAIASREngine:
    """基于 OpenAI gpt-transcribe 的离线转写引擎。"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.api_key = self.config.openai_api_key
        self.base_url = self.config.settings.openai_base_url
        self.asr_model = self.config.settings.openai_asr_model
        self.chunk_seconds = self.config.settings.asr_chunk_seconds
        self.timeout = self.config.settings.openai_asr_timeout
        self.context_chars = self.config.settings.asr_context_chars
        self.keywords_limit = self.config.settings.asr_keywords_limit

    # ---- 公开接口 ----

    def transcribe(
        self,
        audio_files: list[Path],
        meeting_dir: Path,
        force: bool = False,
    ) -> Optional[Transcript]:
        """转写音频文件列表，产出 transcript.json。"""
        if not audio_files:
            logger.warning("没有音频文件需要转写")
            return None
        if not self.api_key:
            raise RuntimeError("未配置 OPENAI_API_KEY")

        transcript_file = meeting_dir / TRANSCRIPT_FILE
        progress_file = meeting_dir / TRANSCRIPT_PROGRESS_FILE
        chunks_dir = meeting_dir / ".chunks"

        if transcript_file.exists() and not force:
            logger.info("转写结果已存在: %s", transcript_file)
            return self._load_transcript(transcript_file)

        if force:
            progress_file.unlink(missing_ok=True)

        base_prompt, keywords, languages = self._build_context_hints(meeting_dir)
        logger.info(
            "ASR 上下文: prompt=%d 字, keywords=%d 条, languages=%s",
            len(base_prompt),
            len(keywords),
            languages or "自动检测",
        )

        existing_progress = self._load_progress(progress_file)
        if existing_progress:
            logger.info("发现未完成的进度: %d 块", len(existing_progress))

        all_segments: list[TranscriptSegment] = []
        audio_file_names: list[str] = []
        detected_languages: list[str] = []
        total_duration = 0.0
        time_offset = 0.0
        all_success = True

        for audio_file in sorted(audio_files):
            logger.info("处理音频文件: %s", audio_file.name)
            audio_file_names.append(audio_file.name)

            duration = self._get_audio_duration(audio_file)
            if duration <= 0:
                logger.warning("无法获取音频时长，跳过: %s", audio_file)
                all_success = False
                continue

            logger.info("音频时长: %.2f 秒", duration)

            segments, languages_seen, success = self._transcribe_file(
                audio_path=audio_file,
                duration=duration,
                time_offset=time_offset,
                existing_progress=existing_progress,
                progress_file=progress_file,
                chunks_dir=chunks_dir,
                base_prompt=base_prompt,
                keywords=keywords,
                languages=languages,
            )

            if not success:
                all_success = False
            all_segments.extend(segments)
            detected_languages.extend(languages_seen)

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
            language=(detected_languages[0]
                      if detected_languages else self.config.settings.default_language),
        )

        self._save_transcript(transcript_file, transcript)

        if all_success:
            progress_file.unlink(missing_ok=True)
            if chunks_dir.exists():
                import shutil
                shutil.rmtree(chunks_dir, ignore_errors=True)
                logger.info("已清理分片缓存: %s", chunks_dir)

        logger.info("转写完成: %d 个片段, 总时长 %.2f 秒", len(all_segments), total_duration)
        return transcript

    # ---- 上下文构造 ----

    def _build_context_hints(
        self,
        meeting_dir: Path,
    ) -> tuple[str, list[str], list[str]]:
        """从项目目录构造 prompt / keywords / languages。

        ASRRouter 在 CLI 多项目模式下拿到的是全局 Config，meetings_dir 并不指向
        当前项目，所以这里统一按 meeting_dir.parent 推导项目目录。
        """
        project_dir = meeting_dir.parent
        try:
            project_config = self._clone_config_for_dir(project_dir)
        except Exception as exc:  # 上下文是增强项，构造失败不应阻断转写
            logger.warning("构造 ASR 上下文失败，改为无上下文转写: %s", exc)
            return "", [], self._configured_languages()

        prompt_parts: list[str] = []
        keywords: list[str] = []
        seen: set[str] = set()

        def push_keyword(value: Optional[str]) -> None:
            keyword = (value or "").strip()
            if not keyword or len(keywords) >= self.keywords_limit:
                return
            dedupe_key = keyword.lower()
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            keywords.append(keyword)

        try:
            from meeting_agent.scanner import MeetingScanner

            people = MeetingScanner(project_config).load_people_config(project_dir)
            for person in (people.people or {}).values():
                push_keyword(person.name)
        except Exception as exc:
            logger.warning("加载人员配置失败: %s", exc)

        try:
            from meeting_agent.glossary import GlossaryManager

            glossary = GlossaryManager(project_config).load_glossary()
            confirmed = [e for e in glossary.entries if e.confirmed_at]
            # keywords 只收 canonical：别名是已知的识别错误，喂进去会强化错误拼写。
            for entry in confirmed or glossary.entries:
                push_keyword(entry.canonical)
        except Exception as exc:
            logger.warning("加载术语表失败: %s", exc)

        try:
            from meeting_agent.glossary.context_manager import ContextManager

            # 只取已回答的背景条目。ContextManager.build_context_prompt() 还会附上
            # "待人工解答"的问题列表，那是给纪要 LLM 的，对 ASR 只是噪声。
            for entry in ContextManager(project_config).list_entries():
                if not entry.is_answered:
                    continue
                topic = (entry.topic or "").strip()
                answer = (entry.answer or "").strip()
                if answer:
                    prompt_parts.append(f"{topic}：{answer}" if topic else answer)
        except Exception as exc:
            logger.warning("加载项目背景失败: %s", exc)

        prompt = "\n".join(prompt_parts).strip()
        if len(prompt) > 2000:
            prompt = prompt[:1999].rstrip() + "…"

        return prompt, keywords, self._configured_languages()

    def _clone_config_for_dir(self, project_dir: Path) -> Config:
        settings = Settings(**self.config.settings.model_dump())
        settings.meetings_dir = project_dir
        return Config(settings)

    def _configured_languages(self) -> list[str]:
        raw = (self.config.settings.openai_asr_languages or "").strip()
        if not raw:
            return []
        languages = []
        for part in raw.replace("\n", ",").split(","):
            code = part.strip().lower().split("-")[0]
            if code and code not in languages:
                languages.append(code)
        return languages

    # ---- 分块转写 ----

    def _transcribe_file(
        self,
        audio_path: Path,
        duration: float,
        time_offset: float,
        existing_progress: dict[tuple[str, int], dict],
        progress_file: Path,
        chunks_dir: Path,
        base_prompt: str,
        keywords: list[str],
        languages: list[str],
    ) -> tuple[list[TranscriptSegment], list[str], bool]:
        """分块转写单个音频文件。块边界尽量落在静音上，避免切断词。"""
        boundaries = self._build_chunk_boundaries(audio_path, duration)
        total_chunks = len(boundaries)
        logger.info("音频将分 %d 块处理 (目标每块 %.0f 秒, 无重叠)", total_chunks, self.chunk_seconds)

        chunks_dir.mkdir(parents=True, exist_ok=True)
        # 分片文件名带上音频名和分块长度，避免多音频文件互相覆盖，也避免复用
        # 上一次不同分块长度留下的缓存。
        chunk_prefix = f"{_safe_stem(audio_path.stem)}_{self.chunk_seconds:g}s"

        segments: list[TranscriptSegment] = []
        detected_languages: list[str] = []
        previous_text = ""
        all_success = True

        for idx, (chunk_start, chunk_end) in enumerate(boundaries):
            cached = existing_progress.get((audio_path.name, idx))
            if cached is not None:
                result = cached.get("result") or {}
                logger.info("复用已完成块: %d/%d", idx + 1, total_chunks)
            else:
                chunk_file = chunks_dir / f"{chunk_prefix}_{idx:04d}.mp3"
                if not (chunk_file.exists() and chunk_file.stat().st_size > 0):
                    logger.info("处理第 %d/%d 块: %.2f - %.2f 秒", idx + 1, total_chunks,
                                chunk_start, chunk_end)
                    if not self._split_chunk(audio_path, chunk_file, chunk_start,
                                             chunk_end - chunk_start):
                        logger.error("切割音频块失败: %s", chunk_file)
                        all_success = False
                        continue

                try:
                    result = self._request_transcription(
                        chunk_file,
                        prompt=self._chunk_prompt(base_prompt, previous_text),
                        keywords=keywords,
                        languages=languages,
                    )
                except Exception as exc:
                    logger.error("转写块失败 [%d/%d]: %s", idx + 1, total_chunks, exc)
                    all_success = False
                    continue

                self._save_progress(progress_file, audio_path.name, idx, chunk_start,
                                    chunk_end, result)

            text = str(result.get("text", "")).strip()
            for lang in result.get("languages") or []:
                code = (lang.get("code") if isinstance(lang, dict) else str(lang)) or ""
                if code and code not in detected_languages:
                    detected_languages.append(code)

            if not text:
                continue

            preview = text[:50] + ("…" if len(text) > 50 else "")
            logger.info("  -> %s", preview)
            previous_text = text

            segments.extend(
                self._split_text_into_segments(
                    text,
                    start=time_offset + chunk_start,
                    end=time_offset + chunk_end,
                ))

        return segments, detected_languages, all_success

    def _build_chunk_boundaries(
        self,
        audio_path: Path,
        duration: float,
    ) -> list[tuple[float, float]]:
        """规划分块区间，块边界尽量吸附到最近的静音中点。

        分块不重叠，所以边界正好落在词中间时那个词会被切成两半（"下周之前" 变成
        "下周" + "之前"）。把边界挪到附近的静音里能基本消除这种情况。
        silencedetect 失败时退回等长切分。
        """
        window = min(self.chunk_seconds * 0.15, 10.0)
        silences = self._detect_silences(audio_path) if window > 0 else []

        boundaries: list[tuple[float, float]] = []
        start = 0.0
        while start < duration:
            target = start + self.chunk_seconds
            if target >= duration - window:
                boundaries.append((start, duration))
                break
            end = self._snap_to_silence(target, silences, window)
            # 兜底：吸附结果必须真的往前走，否则会死循环。
            if end <= start + 1.0:
                end = target
            boundaries.append((start, end))
            start = end

        if not boundaries:
            boundaries.append((0.0, duration))
        return boundaries

    def _snap_to_silence(
        self,
        target: float,
        silences: list[tuple[float, float]],
        window: float,
    ) -> float:
        """把边界吸附到 target ± window 内最近的静音中点。"""
        best = target
        best_distance = window
        for silence_start, silence_end in silences:
            middle = (silence_start + silence_end) / 2
            distance = abs(middle - target)
            if distance < best_distance:
                best = middle
                best_distance = distance
        return best

    def _detect_silences(self, audio_path: Path) -> list[tuple[float, float]]:
        """用 ffmpeg silencedetect 找出静音区间。失败时返回空列表。"""
        cmd = [
            "ffmpeg",
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-30dB:d=0.3",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception as e:
            logger.warning("静音检测失败，改用等长切分: %s", e)
            return []

        silences: list[tuple[float, float]] = []
        pending_start: Optional[float] = None
        for line in (result.stderr or "").splitlines():
            start_match = re.search(r"silence_start:\s*(-?[\d.]+)", line)
            if start_match:
                pending_start = float(start_match.group(1))
                continue
            end_match = re.search(r"silence_end:\s*(-?[\d.]+)", line)
            if end_match and pending_start is not None:
                silences.append((pending_start, float(end_match.group(1))))
                pending_start = None

        logger.info("静音检测: %d 段", len(silences))
        return silences

    def _chunk_prompt(self, base_prompt: str, previous_text: str) -> str:
        """把项目背景和上一块结尾拼成本块的 prompt。"""
        parts = []
        if base_prompt:
            parts.append(base_prompt)
        tail = (previous_text or "").strip()
        if tail:
            if len(tail) > self.context_chars:
                tail = tail[-self.context_chars:]
            parts.append(f"这段录音紧接着以下内容：{tail}")
        return "\n\n".join(parts)

    def _request_transcription(
        self,
        audio_path: Path,
        prompt: str,
        keywords: list[str],
        languages: list[str],
    ) -> dict:
        """调用 /v1/audio/transcriptions。"""
        endpoint = f"{self.base_url.rstrip('/')}/audio/transcriptions"

        # keywords / languages 是重复字段的多值表单项，必须用元组列表而非 dict。
        data: list[tuple[str, str]] = [
            ("model", self.asr_model),
            ("response_format", "json"),
        ]
        if prompt:
            data.append(("prompt", prompt))
        for keyword in keywords:
            data.append(("keywords[]", keyword))
        for language in languages:
            data.append(("languages[]", language))

        with open(audio_path, "rb") as f:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (audio_path.name, f)},
                data=data,
                timeout=self.timeout,
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("OpenAI ASR 返回格式异常")
        return result

    def _split_text_into_segments(
        self,
        text: str,
        start: float,
        end: float,
    ) -> list[TranscriptSegment]:
        """把一整块文本按句子切开，时间按字符数比例摊到句子上。

        gpt-transcribe 不返回时间戳，这里得到的是估算值：假设块内语速均匀。
        对纪要和 timeline 的分钟级定位足够，不适合做逐词对齐。
        """
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        if not sentences:
            return []

        span = max(end - start, 0.0)
        total_chars = sum(len(s) for s in sentences)
        if span <= 0 or total_chars <= 0:
            return [TranscriptSegment(start=start, end=end, text=text.strip())]

        segments = []
        cursor = start
        for index, sentence in enumerate(sentences):
            if index == len(sentences) - 1:
                sentence_end = end
            else:
                sentence_end = cursor + span * (len(sentence) / total_chars)
            segments.append(
                TranscriptSegment(start=round(cursor, 3),
                                  end=round(sentence_end, 3),
                                  text=sentence))
            cursor = sentence_end
        return segments

    # ---- ffmpeg ----

    def _split_chunk(
        self,
        audio_path: Path,
        output_path: Path,
        start_time: float,
        duration: float,
    ) -> bool:
        """切出一块 16kHz 单声道 mp3，控制上传体积。"""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-i",
            str(audio_path),
            "-t",
            str(duration),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "mp3",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.error("ffmpeg 切割失败: %s", result.stderr[-500:])
            return result.returncode == 0
        except Exception as e:
            logger.error("切割音频失败: %s", e)
            return False

    def _get_audio_duration(self, audio_path: Path) -> float:
        """获取音频时长"""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            logger.warning("获取音频时长失败: %s", e)
        return 0.0

    # ---- 持久化 ----

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

    def _progress_signature(self) -> str:
        """进度/分片的有效性签名：模型或分块长度变了，旧进度就不能复用。"""
        return f"{self.asr_model}@{self.chunk_seconds:g}s"

    def _load_progress(self, progress_file: Path) -> dict[tuple[str, int], dict]:
        """加载进度文件，按 (音频文件名, 块序号) 索引。

        签名不匹配的条目直接丢弃：换模型或改分块长度后，旧块的时间边界和文本
        都对不上，复用会产出错位的转写稿。
        """
        if not progress_file.exists():
            return {}
        signature = self._progress_signature()
        progress: dict[tuple[str, int], dict] = {}
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if "idx" not in entry or entry.get("signature") != signature:
                        continue
                    progress[(entry.get("audio") or "", int(entry["idx"]))] = entry
        except Exception as e:
            logger.warning("加载进度文件失败: %s", e)
            return {}
        return progress

    def _save_progress(
        self,
        progress_file: Path,
        audio_name: str,
        idx: int,
        chunk_start: float,
        chunk_end: float,
        result: dict,
    ):
        entry = {
            "signature": self._progress_signature(),
            "audio": audio_name,
            "idx": idx,
            "chunk_start": chunk_start,
            "chunk_end": chunk_end,
            "result": result,
        }
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
