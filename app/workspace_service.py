"""
Workspace / Agent bridge helpers.

将离线 Agent 的项目状态、会议列表、术语和上下文整理成
Web 工作台、项目详情页和实时会议页可消费的数据结构。
"""
# pylint: disable=too-many-locals,too-many-statements

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Optional

from meeting_agent.config import (AUDIO_EXTENSIONS, MEETING_META_FILE,
                                  MINUTES_FILE, PRE_HINT_FILE,
                                  PROJECT_CONFIG_FILE, TRANSCRIPT_FILE,
                                  Config, Settings)
from meeting_agent.glossary import GlossaryManager
from meeting_agent.glossary.context_manager import ContextManager as BackgroundContextManager
from meeting_agent.memory import ActionsManager, MemoryWriter
from meeting_agent.models import LanguageMode, MeetingMeta, MeetingType, ProjectConfig
from meeting_agent.models_glossary import GlossaryEntry, TermType
from meeting_agent.scanner import MeetingScanner

DEFAULT_PROJECT_ID = "__default__"
NO_PROJECT_ID = "__none__"
EDITABLE_TEXT_EXTENSIONS = {".json", ".md", ".txt", ".csv", ".log"}
DEFAULT_FILE_LABELS = {
    MEETING_META_FILE: "会议配置",
    TRANSCRIPT_FILE: "正式转写",
    MINUTES_FILE: "会议纪要",
    PRE_HINT_FILE: "会前提示",
}


@dataclass
class ProjectHandle:
    """工作区中的项目句柄"""

    project_id: str
    name: str
    path: Path
    is_default: bool = False


def clone_config_for_dir(base_config: Config, meetings_dir: Path) -> Config:
    """克隆配置并切换到指定项目目录。"""
    settings_data = {
        key: value
        for key, value in base_config.settings.model_dump().items() if value is not None
    }
    settings = Settings(**settings_data)
    settings.meetings_dir = meetings_dir
    settings.projects_dir = None
    return Config(settings)


def list_project_handles(base_config: Optional[Config] = None) -> list[ProjectHandle]:
    """列出当前工作区可见的项目。"""
    config = base_config or Config()
    scanner = MeetingScanner(config)

    if config.projects_dir:
        handles = []
        for project_dir in scanner.list_projects():
            handles.append(
                ProjectHandle(
                    project_id=project_dir.name,
                    name=project_dir.name,
                    path=project_dir,
                    is_default=False,
                ))
        return handles

    root_name = config.meetings_dir.name or "current"
    return [
        ProjectHandle(
            project_id=DEFAULT_PROJECT_ID,
            name=root_name,
            path=config.meetings_dir,
            is_default=True,
        )
    ]


def resolve_project_handle(project_id: Optional[str],
                           base_config: Optional[Config] = None) -> ProjectHandle:
    """按 project_id 解析项目句柄。"""
    handles = list_project_handles(base_config)
    if not handles:
        raise FileNotFoundError("未找到可用项目")

    if not project_id or project_id == DEFAULT_PROJECT_ID:
        return handles[0]

    for handle in handles:
        if handle.project_id == project_id:
            return handle

    raise FileNotFoundError(f"未找到项目: {project_id}")


def resolve_meeting_dir(project_id: Optional[str],
                        meeting_dir_name: str,
                        base_config: Optional[Config] = None) -> tuple[ProjectHandle, Config, Path]:
    """解析会议目录并保证路径安全。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    meeting_dir = (handle.path / (meeting_dir_name or "").strip()).resolve()
    base_dir = handle.path.resolve()

    if not meeting_dir_name:
        raise FileNotFoundError("未指定会议目录")
    if not meeting_dir.is_dir() or not meeting_dir.is_relative_to(base_dir):
        raise FileNotFoundError(f"未找到会议: {meeting_dir_name}")

    return handle, project_config, meeting_dir


def resolve_meeting_audio_file(project_id: Optional[str],
                               meeting_dir_name: str,
                               filename: str,
                               base_config: Optional[Config] = None) -> tuple[ProjectHandle, Config, Path, Path]:
    """解析会议音频文件。"""
    handle, project_config, meeting_dir = resolve_meeting_dir(project_id,
                                                              meeting_dir_name,
                                                              base_config)
    file_name = Path(filename or "").name
    audio_path = (meeting_dir / file_name).resolve()
    if (not audio_path.is_file() or audio_path.suffix.lower() not in AUDIO_EXTENSIONS
            or not audio_path.is_relative_to(meeting_dir.resolve())):
        raise FileNotFoundError(f"未找到音频文件: {file_name}")
    return handle, project_config, meeting_dir, audio_path


def resolve_meeting_file(project_id: Optional[str],
                         meeting_dir_name: str,
                         filename: str,
                         base_config: Optional[Config] = None) -> tuple[ProjectHandle, Config, Path, Path]:
    """解析会议相关文件。"""
    handle, project_config, meeting_dir = resolve_meeting_dir(project_id,
                                                              meeting_dir_name,
                                                              base_config)
    file_name = Path(filename or "").name
    file_path = (meeting_dir / file_name).resolve()
    if (not file_path.is_file() or file_path.suffix.lower() in AUDIO_EXTENSIONS
            or not file_path.is_relative_to(meeting_dir.resolve())):
        raise FileNotFoundError(f"未找到文件: {file_name}")
    return handle, project_config, meeting_dir, file_path


def create_project_workspace(
    name: str,
    description: Optional[str] = None,
    team: Optional[str] = None,
    start_date: Optional[str] = None,
    base_config: Optional[Config] = None,
) -> dict:
    """创建项目目录及项目级基础文件。"""
    config = base_config or Config()
    if not config.projects_dir:
        raise ValueError("当前工作区未启用多项目模式，暂时不能创建新项目。")

    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("项目名称不能为空")

    project_slug = _sanitize_project_slug(normalized_name)
    project_dir = config.projects_dir / project_slug
    if project_dir.exists():
        raise FileExistsError(f"项目已存在: {project_slug}")

    project_dir.mkdir(parents=True, exist_ok=False)
    members = _parse_team_members(team)
    project_config = ProjectConfig(
        name=normalized_name,
        description=(description or "").strip() or None,
        start_date=(start_date or "").strip() or None,
        team=members,
    )
    _save_project_config(project_dir, project_config)

    memory_writer = MemoryWriter(config)
    memory_writer.initialize_project(
        project_name=normalized_name,
        description=(description or "").strip(),
        team=members,
        start_date=(start_date or "").strip() or None,
        project_dir=project_dir,
    )

    background_config = clone_config_for_dir(config, project_dir)
    background_mgr = BackgroundContextManager(background_config)
    if not background_mgr.context_file.exists():
        background_mgr.initialize()

    return {
        "project_id": project_slug,
        "project_name": normalized_name,
        "project_path": str(project_dir),
    }


def update_project_background(
    project_id: Optional[str],
    content: str,
    base_config: Optional[Config] = None,
) -> None:
    """更新项目背景说明。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    background_mgr = BackgroundContextManager(project_config)
    background_mgr.save((content or "").rstrip() + "\n")


def update_project_glossary(
    project_id: Optional[str],
    editor_text: str,
    base_config: Optional[Config] = None,
) -> None:
    """按逐行编辑器内容保存术语表。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    glossary_mgr = GlossaryManager(project_config)
    existing = glossary_mgr.load_glossary()
    existing_map = {entry.canonical.lower(): entry for entry in existing.entries}

    next_entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for raw_line in (editor_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|") if part.strip()]
        if not parts:
            continue

        canonical = parts[0]
        key = canonical.lower()
        if key in seen:
            continue

        aliases = _dedupe_aliases(parts[1:], canonical)
        previous = existing_map.get(key)
        entry = previous.model_copy(deep=True) if previous else GlossaryEntry(
            canonical=canonical,
            aliases=[],
            type=TermType.OTHER,
            auto_generated=False,
        )
        entry.canonical = canonical
        entry.aliases = aliases
        entry.auto_generated = False
        if not entry.confirmed_at:
            entry.confirmed_at = datetime.now()
        next_entries.append(entry)
        seen.add(key)

    existing.entries = next_entries
    existing.last_updated = datetime.now()
    glossary_mgr._glossary = existing
    glossary_mgr.save_glossary()

    pending = glossary_mgr.load_pending()
    changed = False
    for entry in next_entries:
        changed = pending.remove(entry.canonical) or changed
    if changed:
        glossary_mgr.save_pending()


def build_workspace_view_model(base_config: Optional[Config] = None) -> dict:
    """构建卡片式控制台首页的视图模型。"""
    config = base_config or Config()
    projects = [_build_project_card(config, handle) for handle in list_project_handles(config)]
    projects.sort(key=lambda item: item["name"].lower())

    total_meetings = sum(project["meeting_count"] for project in projects)
    total_pending = sum(project["pending_asr"] + project["pending_minutes"] for project in projects)

    return {
        "projects": projects,
        "can_create_project": bool(config.projects_dir),
        "workspace_summary": {
            "project_count": len(projects),
            "meeting_count": total_meetings,
            "pending_count": total_pending,
        },
        "quick_entry": {
            "project_id": NO_PROJECT_ID,
            "title": "快速模式",
            "description": "适合临时会议、外部通话和只想立刻开始字幕的场景。",
        },
    }


def build_project_detail_view_model(project_id: Optional[str],
                                    base_config: Optional[Config] = None) -> dict:
    """构建项目详情页视图模型。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    scanner = MeetingScanner(project_config)
    actions_mgr = ActionsManager(project_config)
    glossary_mgr = GlossaryManager(project_config)
    background_mgr = BackgroundContextManager(project_config)

    project_meta = _load_or_build_project_meta(handle, scanner)
    project_card = _build_project_card(config, handle)
    background_content = background_mgr.load() or ""
    pending_terms = glossary_mgr.load_pending()
    meetings = [_build_meeting_card(task) for task in scanner.scan_meetings()]
    meetings.sort(key=lambda item: item["date_sort"], reverse=True)

    recent_actions = []
    for action in actions_mgr.load()[:8]:
        recent_actions.append({
            "id": action.id,
            "task": action.task,
            "owner": action.owner or "未分配",
            "status": action.status.value,
            "due_date": str(action.due_date) if action.due_date else "未设置",
        })

    project = {
        **project_card,
        "start_date": str(project_meta.start_date) if project_meta.start_date else "未设置",
        "team": project_meta.team or [],
        "tags": project_meta.tags or [],
        "background_excerpt": _truncate_text(background_content, limit=260),
        "background_exists": bool(background_content.strip()),
        "pending_term_count": len(pending_terms.suggestions),
    }

    return {
        "project": project,
        "meetings": meetings,
        "recent_actions": recent_actions,
    }


def build_glossary_editor_view_model(project_id: Optional[str],
                                     base_config: Optional[Config] = None) -> dict:
    """构建术语维护页视图模型。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    glossary_mgr = GlossaryManager(project_config)
    scanner = MeetingScanner(project_config)

    project_meta = _load_or_build_project_meta(handle, scanner)
    glossary = glossary_mgr.load_glossary()
    confirmed_terms = sorted(
        [entry for entry in glossary.entries if entry.confirmed_at],
        key=lambda entry: entry.canonical.lower(),
    )
    pending_terms = sorted(glossary_mgr.load_pending().suggestions,
                           key=lambda item: (-item.frequency, item.canonical.lower()))
    rejected_terms = sorted(glossary_mgr.load_rejected().rejected,
                            key=lambda item: item.canonical.lower())

    editor_lines = []
    for entry in confirmed_terms:
        parts = [entry.canonical, *entry.aliases]
        editor_lines.append(" | ".join(parts))

    return {
        "project": {
            "id": handle.project_id,
            "name": project_meta.name,
            "description": project_meta.description or "",
        },
        "editor_text": "\n".join(editor_lines),
        "confirmed_terms": confirmed_terms,
        "pending_terms": pending_terms,
        "rejected_terms": rejected_terms,
    }


def build_background_editor_view_model(project_id: Optional[str],
                                       base_config: Optional[Config] = None) -> dict:
    """构建背景说明维护页视图模型。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)
    background_mgr = BackgroundContextManager(project_config)
    scanner = MeetingScanner(project_config)
    project_meta = _load_or_build_project_meta(handle, scanner)

    return {
        "project": {
            "id": handle.project_id,
            "name": project_meta.name,
            "description": project_meta.description or "",
        },
        "content": background_mgr.load() or "",
        "file_path": str(background_mgr.context_file),
    }


def build_audio_manager_view_model(project_id: Optional[str],
                                   meeting_dir_name: str,
                                   base_config: Optional[Config] = None) -> dict:
    """构建会议音频管理页视图模型。"""
    handle, project_config, meeting_dir = resolve_meeting_dir(project_id,
                                                              meeting_dir_name,
                                                              base_config)
    scanner = MeetingScanner(project_config)
    meta = scanner.load_meeting_meta(meeting_dir)
    audio_files = [_build_audio_file_item(path) for path in scanner.get_audio_files(meeting_dir)]

    return {
        "project": {
            "id": handle.project_id,
            "name": _load_or_build_project_meta(handle, scanner).name,
        },
        "meeting": _build_meeting_summary(meeting_dir, meta),
        "audio_files": audio_files,
    }


def build_meeting_file_editor_view_model(project_id: Optional[str],
                                         meeting_dir_name: str,
                                         filename: str,
                                         base_config: Optional[Config] = None) -> dict:
    """构建会议文件查看/编辑页视图模型。"""
    handle, project_config, meeting_dir, file_path = resolve_meeting_file(project_id,
                                                                          meeting_dir_name,
                                                                          filename,
                                                                          base_config)
    scanner = MeetingScanner(project_config)
    meta = scanner.load_meeting_meta(meeting_dir)
    content = file_path.read_text(encoding="utf-8")

    return {
        "project": {
            "id": handle.project_id,
            "name": _load_or_build_project_meta(handle, scanner).name,
        },
        "meeting": _build_meeting_summary(meeting_dir, meta),
        "file": {
            "name": file_path.name,
            "label": _build_file_label(file_path.name),
            "size_label": _format_file_size(file_path.stat().st_size),
            "updated_at": _format_timestamp(file_path.stat().st_mtime),
            "editable": file_path.suffix.lower() in EDITABLE_TEXT_EXTENSIONS,
            "content": content,
        },
    }


def create_meeting_workspace(
    project_id: Optional[str],
    title: str,
    meeting_date: str,
    meeting_type: Optional[str],
    primary_language: Optional[str],
    secondary_language: Optional[str],
    language_mode: Optional[str],
    notes: Optional[str] = None,
    base_config: Optional[Config] = None,
) -> dict:
    """在指定项目下创建会议目录和 _meeting.json。"""
    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_dir = handle.path
    project_dir.mkdir(parents=True, exist_ok=True)

    normalized_title = (title or "").strip()
    if not normalized_title:
        raise ValueError("会议标题不能为空")

    normalized_date = (meeting_date or "").strip()
    if not normalized_date:
        raise ValueError("会议日期不能为空")

    try:
        parsed_type = MeetingType((meeting_type or "other").strip().lower())
    except ValueError:
        parsed_type = MeetingType.OTHER

    secondary = (secondary_language or "").strip() or None
    normalized_mode = _normalize_language_mode(language_mode, secondary)
    parsed_mode = LanguageMode(normalized_mode)
    parsed_primary = (primary_language or "zh-CN").strip() or "zh-CN"

    meta = MeetingMeta(
        date=meeting_date,
        title=normalized_title,
        type=parsed_type,
        notes=(notes or "").strip() or None,
        language_mode=parsed_mode,
        primary_language=parsed_primary,
        secondary_language=secondary,
    )

    base_dir_name = f"{meta.date.isoformat()}_{_sanitize_meeting_slug(meta.title)}"
    meeting_dir = project_dir / base_dir_name
    suffix = 2
    while meeting_dir.exists():
        meeting_dir = project_dir / f"{base_dir_name}-{suffix}"
        suffix += 1

    meeting_dir.mkdir(parents=True, exist_ok=False)
    meta_file = meeting_dir / MEETING_META_FILE
    with open(meta_file, "w", encoding="utf-8") as file_obj:
        json.dump(meta.model_dump(),
                  file_obj,
                  ensure_ascii=False,
                  indent=2,
                  default=str)

    return {
        "project_id": handle.project_id,
        "project_name": handle.name,
        "meeting_dir_name": meeting_dir.name,
        "meeting_title": meta.title,
        "meeting_date": meta.date.isoformat(),
        "language_mode": meta.language_mode.value,
        "primary_language": meta.effective_primary_language,
        "secondary_language": meta.effective_secondary_language or "",
    }


def build_empty_context_pack(
    primary_language: Optional[str],
    secondary_language: Optional[str],
    language_mode: Optional[str],
) -> dict:
    """构建不关联项目时的空增强包。"""
    normalized_primary = (primary_language or "zh-CN").strip() or "zh-CN"
    normalized_secondary = (secondary_language or "").strip()
    normalized_mode = _normalize_language_mode(language_mode, normalized_secondary)
    return {
        "projectId": NO_PROJECT_ID,
        "projectName": "",
        "languageMode": normalized_mode,
        "primaryLanguage": normalized_primary,
        "secondaryLanguage": normalized_secondary,
        "projectSummary": "",
        "backgroundSummary": "",
        "confirmedTermsCount": 0,
        "glossaryLines": [],
        "pendingActions": [],
        "recentMeetings": [],
        "realtimePrompt": "",
    }


def build_context_pack(
    project_id: Optional[str],
    primary_language: Optional[str],
    secondary_language: Optional[str],
    language_mode: Optional[str],
    base_config: Optional[Config] = None
) -> dict:  # pylint: disable=too-many-locals,too-many-statements
    """构建给实时会议页使用的上下文增强包。"""
    if (project_id or "").strip() in {"", NO_PROJECT_ID}:
        return build_empty_context_pack(primary_language, secondary_language,
                                        language_mode)

    config = base_config or Config()
    handle = resolve_project_handle(project_id, config)
    project_config = clone_config_for_dir(config, handle.path)

    scanner = MeetingScanner(project_config)
    actions_mgr = ActionsManager(project_config)
    glossary_mgr = GlossaryManager(project_config)
    background_mgr = BackgroundContextManager(project_config)

    project_meta = _load_or_build_project_meta(handle, scanner)
    glossary = glossary_mgr.load_glossary()
    background_context = background_mgr.load() or ""
    actions = actions_mgr.load()
    pending_actions = [
        action for action in actions if action.status.value != "completed"
    ]

    confirmed_terms = [entry for entry in glossary.entries if entry.confirmed_at]
    confirmed_terms.sort(key=lambda entry: entry.canonical.lower())
    glossary_lines = []
    for entry in confirmed_terms[:30]:
        if entry.aliases:
            glossary_lines.append(f"{entry.canonical} | {' | '.join(entry.aliases)}")
        else:
            glossary_lines.append(entry.canonical)

    meetings = scanner.scan_meetings()
    recent_meetings = []
    for task in meetings[-5:]:
        meta = task.meeting_meta
        if not meta:
            continue
        recent_meetings.append(f"{meta.date} {meta.title}")

    normalized_language_mode = _normalize_language_mode(language_mode,
                                                        secondary_language)
    primary = (primary_language or "zh-CN").strip()
    secondary = (secondary_language or "").strip()

    realtime_prompt_parts = [
        "你正在执行会议实时转写，请优先保证术语和人名的准确性。",
    ]
    if normalized_language_mode == "single_primary":
        realtime_prompt_parts.append(f"这是一场单主语言会议，主要语言是 {primary}。")
        realtime_prompt_parts.append("允许出现少量外语技术词、缩写或产品名，应尽量保留原始写法。")
        realtime_prompt_parts.append("不要为了看起来更自然而擅自把术语意译或汉化。")
    else:
        realtime_prompt_parts.append(
            f"这是一场双语言会议，主要语言是 {primary}，第二语言是 {secondary or '未指定'}。")
        realtime_prompt_parts.append("请尽量保持原始语言，不要把中文和日语等混淆。")
        realtime_prompt_parts.append("人名、产品名、缩写优先按术语表中的标准写法输出。")

    if confirmed_terms:
        top_terms = "、".join(entry.canonical for entry in confirmed_terms[:12])
        realtime_prompt_parts.append(f"高优先级术语：{top_terms}")

    if recent_meetings:
        realtime_prompt_parts.append(f"近期相关会议：{'；'.join(recent_meetings[:4])}")

    action_lines = []
    for action in pending_actions[:6]:
        owner = action.owner or "未指定"
        action_lines.append(f"{action.id}: {action.task}（负责人: {owner}）")

    project_summary_parts = [project_meta.name]
    if project_meta.description:
        project_summary_parts.append(project_meta.description)
    project_summary = " - ".join(project_summary_parts)

    background_excerpt = _truncate_text(background_context, limit=400)

    return {
        "projectId": handle.project_id,
        "projectName": project_meta.name,
        "languageMode": normalized_language_mode,
        "primaryLanguage": primary,
        "secondaryLanguage": secondary or None,
        "projectSummary": project_summary,
        "backgroundSummary": background_excerpt,
        "confirmedTermsCount": len(confirmed_terms),
        "glossaryLines": glossary_lines,
        "pendingActions": action_lines,
        "recentMeetings": recent_meetings,
        "realtimePrompt": " ".join(realtime_prompt_parts),
    }


def _build_project_card(config: Config, handle: ProjectHandle) -> dict:
    """构建控制台项目卡片数据。"""
    project_config = clone_config_for_dir(config, handle.path)
    scanner = MeetingScanner(project_config)
    actions_mgr = ActionsManager(project_config)
    glossary_mgr = GlossaryManager(project_config)
    background_mgr = BackgroundContextManager(project_config)

    status = scanner.get_project_status()
    action_stats = actions_mgr.get_stats()
    glossary = glossary_mgr.load_glossary()
    pending_terms = glossary_mgr.load_pending()
    rejected_terms = glossary_mgr.load_rejected()
    background_context = background_mgr.load() or ""
    project_meta = _load_or_build_project_meta(handle, scanner)
    meetings = scanner.scan_meetings()
    latest_meta = meetings[-1].meeting_meta if meetings else None

    return {
        "id": handle.project_id,
        "name": project_meta.name,
        "path": str(handle.path),
        "is_default": handle.is_default,
        "description": project_meta.description or "",
        "meeting_count": status.total_meetings,
        "processed_meetings": status.processed_meetings,
        "pending_asr": status.pending_asr,
        "pending_minutes": status.pending_minutes,
        "actions_total": action_stats["total"],
        "actions_overdue": action_stats["overdue"],
        "glossary_confirmed": len([entry for entry in glossary.entries if entry.confirmed_at]),
        "glossary_pending": len(pending_terms.suggestions),
        "glossary_rejected": len(rejected_terms.rejected),
        "background_exists": bool(background_context.strip()),
        "last_meeting": latest_meta.title if latest_meta else "",
        "last_meeting_date": str(latest_meta.date) if latest_meta else "",
    }


def _build_meeting_card(task) -> dict:
    """构建会议卡片数据。"""
    meta = task.meeting_meta
    summary = _build_meeting_summary(task.meeting_dir, meta)
    audio_files = [_build_audio_file_item(path) for path in task.audio_files]
    files = _list_meeting_files(task.meeting_dir)
    pending_items = []
    if task.needs_asr:
        pending_items.append("ASR")
    if task.needs_minutes:
        pending_items.append("纪要")

    return {
        **summary,
        "date_sort": summary["date"],
        "audio_count": len(audio_files),
        "audio_files": audio_files,
        "files": files,
        "file_count": len(files),
        "has_transcript": task.has_transcript,
        "has_minutes": task.has_minutes,
        "needs_asr": task.needs_asr,
        "needs_minutes": task.needs_minutes,
        "pending_label": " / ".join(pending_items) if pending_items else "已完成",
        "status_tone": "pending" if pending_items else "ready",
    }


def _build_meeting_summary(meeting_dir: Path, meta: Optional[MeetingMeta]) -> dict:
    """构建会议摘要。"""
    return {
        "dir_name": meeting_dir.name,
        "title": meta.title if meta else meeting_dir.name,
        "date": str(meta.date) if meta else "-",
        "type": meta.type.value if meta else "other",
        "notes": meta.notes if meta and meta.notes else "",
        "language_profile": meta.language_profile_label() if meta else "未设置",
        "language_mode": meta.language_mode.value if meta else "single_primary",
        "primary_language": meta.effective_primary_language if meta else "zh-CN",
        "secondary_language": meta.effective_secondary_language if meta else "",
    }


def _list_meeting_files(meeting_dir: Path) -> list[dict]:
    """列出会议中的非音频相关文件。"""
    files = []
    for item in meeting_dir.iterdir():
        if (not item.is_file() or item.suffix.lower() in AUDIO_EXTENSIONS
                or item.name.startswith(".")
                or item.suffix.lower() not in EDITABLE_TEXT_EXTENSIONS):
            continue

        files.append({
            "name": item.name,
            "label": _build_file_label(item.name),
            "size_label": _format_file_size(item.stat().st_size),
            "updated_at": _format_timestamp(item.stat().st_mtime),
            "editable": item.suffix.lower() in EDITABLE_TEXT_EXTENSIONS,
            "is_text": item.suffix.lower() in EDITABLE_TEXT_EXTENSIONS,
        })

    files.sort(key=lambda item: (_file_priority(item["name"]), item["name"].lower()))
    return files


def _build_audio_file_item(path: Path) -> dict:
    """构建音频文件展示数据。"""
    return {
        "name": path.name,
        "size_label": _format_file_size(path.stat().st_size),
        "updated_at": _format_timestamp(path.stat().st_mtime),
    }


def _load_or_build_project_meta(handle: ProjectHandle,
                                scanner: MeetingScanner) -> ProjectConfig:
    """加载项目元信息，缺省时回退到目录信息。"""
    project_meta = scanner.load_project_config()
    if project_meta:
        return project_meta

    return ProjectConfig(
        name=handle.name,
        description="",
        team=[],
    )


def _save_project_config(project_dir: Path, project_config: ProjectConfig) -> None:
    """保存 _project.json。"""
    config_file = project_dir / PROJECT_CONFIG_FILE
    with open(config_file, "w", encoding="utf-8") as file_obj:
        json.dump(project_config.model_dump(mode="json"),
                  file_obj,
                  ensure_ascii=False,
                  indent=2,
                  default=str)


def _parse_team_members(team: Optional[str]) -> list[str]:
    """将逗号/换行分隔的成员文本转为列表。"""
    if not team:
        return []
    members = []
    for raw in re.split(r"[\n,，]+", team):
        member = raw.strip()
        if member and member not in members:
            members.append(member)
    return members


def _normalize_language_mode(language_mode: Optional[str],
                             secondary_language: Optional[str]) -> str:
    value = (language_mode or "").strip().lower()
    if value in {"bilingual", "single_primary"}:
        return value
    if (secondary_language or "").strip():
        return "bilingual"
    return "single_primary"


def _sanitize_meeting_slug(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", (title or "").strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip(" .-_")
    return cleaned or "meeting"


def _sanitize_project_slug(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", (name or "").strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip(" .-_")
    return cleaned or "project"


def _truncate_text(value: Optional[str], limit: int = 240) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _format_timestamp(timestamp: float) -> str:
    """格式化文件时间。"""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _format_file_size(size: int) -> str:
    """格式化文件体积。"""
    value = float(size)
    units = ["B", "KB", "MB", "GB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _file_priority(name: str) -> int:
    """核心文件优先显示。"""
    order = {
        MEETING_META_FILE: 0,
        MINUTES_FILE: 1,
        TRANSCRIPT_FILE: 2,
        PRE_HINT_FILE: 3,
    }
    return order.get(name, 10)


def _build_file_label(name: str) -> str:
    """生成友好的文件标签。"""
    return DEFAULT_FILE_LABELS.get(name, name)


def _dedupe_aliases(aliases: list[str], canonical: str) -> list[str]:
    """去重别名并排除与标准词重复项。"""
    canonical_key = canonical.lower()
    deduped = []
    seen = set()
    for alias in aliases:
        key = alias.lower()
        if not alias or key == canonical_key or key in seen:
            continue
        deduped.append(alias)
        seen.add(key)
    return deduped
