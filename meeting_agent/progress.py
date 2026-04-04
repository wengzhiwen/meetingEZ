"""
处理进度跟踪模块 — 写入 _processing_progress.json 供 Web GUI 实时读取。
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from meeting_agent.config import PROCESSING_PROGRESS_FILE

logger = logging.getLogger("meeting_agent.progress")

STEP_ASR = "asr"
STEP_PRE_HINT = "pre_hint"
STEP_ANALYZING = "analyzing"
STEP_MEMORY = "memory"

ALL_STEPS = [
    {"key": STEP_ASR, "label": "ASR 转写"},
    {"key": STEP_PRE_HINT, "label": "生成提示"},
    {"key": STEP_ANALYZING, "label": "AI 纪要生成"},
    {"key": STEP_MEMORY, "label": "记忆写入"},
]


def _progress_file(meeting_dir: Path) -> Path:
    return meeting_dir / PROCESSING_PROGRESS_FILE


def _write_atomic(path: Path, data: dict) -> None:
    """原子写入：先写临时文件再 rename，防止读取到写了一半的 JSON。"""
    parent = path.parent
    try:
        fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".progress_", suffix=".json")
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        Path(tmp).rename(path)
    except Exception:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _read_progress(meeting_dir: Path) -> Optional[dict]:
    path = _progress_file(meeting_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def init_progress(meeting_dir: Path) -> None:
    """初始化进度文件，所有步骤为 pending。"""
    data = {
        "current_step": None,
        "steps": [
            {"key": s["key"], "label": s["label"], "status": "pending"}
            for s in ALL_STEPS
        ],
    }
    _write_atomic(_progress_file(meeting_dir), data)


def set_step(
    meeting_dir: Path,
    step_key: str,
    detail: Optional[str] = None,
    chunks_total: Optional[int] = None,
    audio_total: Optional[int] = None,
) -> None:
    """标记当前步骤为 in_progress，自动完成前序步骤。"""
    data = _read_progress(meeting_dir)
    if data is None:
        init_progress(meeting_dir)
        data = _read_progress(meeting_dir)

    now = datetime.now(timezone.utc).isoformat()
    data["current_step"] = step_key

    for step in data["steps"]:
        if step["key"] == step_key:
            step["status"] = "in_progress"
            step["started_at"] = now
            if detail:
                step["detail"] = detail
            if chunks_total is not None:
                step["chunks_total"] = chunks_total
                step["chunks_completed"] = 0
            if audio_total is not None:
                step["audio_total"] = audio_total
                step["audio_index"] = 0
        elif step["status"] == "pending":
            # 前序未执行的步骤直接跳过标记
            pass

    _write_atomic(_progress_file(meeting_dir), data)


def complete_step(meeting_dir: Path, step_key: str) -> None:
    """标记步骤为 completed。"""
    data = _read_progress(meeting_dir)
    if not data:
        return

    now = datetime.now(timezone.utc).isoformat()
    for step in data["steps"]:
        if step["key"] == step_key and step["status"] == "in_progress":
            step["status"] = "completed"
            step["finished_at"] = now
            # 计算耗时
            started = step.get("started_at")
            if started:
                try:
                    elapsed = (
                        datetime.fromisoformat(now) - datetime.fromisoformat(started)
                    ).total_seconds()
                    step["elapsed_seconds"] = round(elapsed, 1)
                except Exception:
                    pass
            break

    if data.get("current_step") == step_key:
        data["current_step"] = None

    _write_atomic(_progress_file(meeting_dir), data)


def update_chunks(meeting_dir: Path, completed: int, total: int) -> None:
    """更新 ASR 分片进度。"""
    data = _read_progress(meeting_dir)
    if not data:
        return

    for step in data["steps"]:
        if step["key"] == STEP_ASR and step["status"] == "in_progress":
            step["chunks_completed"] = completed
            step["chunks_total"] = total
            break

    _write_atomic(_progress_file(meeting_dir), data)


def update_audio(
    meeting_dir: Path,
    audio_index: int,
    audio_total: int,
    audio_name: str,
    audio_duration: Optional[float] = None,
) -> None:
    """更新 ASR 当前处理的音频文件信息。"""
    data = _read_progress(meeting_dir)
    if not data:
        return

    for step in data["steps"]:
        if step["key"] == STEP_ASR and step["status"] == "in_progress":
            step["audio_index"] = audio_index
            step["audio_total"] = audio_total
            step["audio_name"] = audio_name
            if audio_duration is not None:
                step["audio_duration"] = round(audio_duration, 1)
            break

    _write_atomic(_progress_file(meeting_dir), data)


def fail_step(meeting_dir: Path, step_key: str, error: str) -> None:
    """标记步骤为 failed。"""
    data = _read_progress(meeting_dir)
    if not data:
        return

    for step in data["steps"]:
        if step["key"] == step_key:
            step["status"] = "failed"
            step["error"] = error
            break

    data["current_step"] = step_key
    _write_atomic(_progress_file(meeting_dir), data)


def clear_progress(meeting_dir: Path) -> None:
    """处理完成后删除进度文件。"""
    path = _progress_file(meeting_dir)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
