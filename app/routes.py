"""
MeetingEZ 路由模块
提供静态文件服务、Realtime session 签发、翻译代理
所有页面和 API 受 ACCESS_CODE 保护
"""
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import requests
from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from app.workspace_service import (DEFAULT_PROJECT_ID, NO_PROJECT_ID,
                                   _normalize_language_mode,
                                   _parse_team_members,
                                   _save_project_config,
                                   build_audio_manager_view_model,
                                   build_background_editor_view_model,
                                   clone_config_for_dir,
                                   build_context_pack,
                                   build_glossary_editor_view_model,
                                   build_meeting_file_editor_view_model,
                                   build_project_detail_view_model,
                                   build_workspace_view_model,
                                   create_meeting_workspace,
                                   create_project_workspace,
                                   list_project_handles,
                                   resolve_meeting_audio_file,
                                   resolve_meeting_dir,
                                   resolve_meeting_file,
                                   resolve_project_handle,
                                   update_project_background,
                                   update_project_glossary)
from meeting_agent.models import LanguageMode, MeetingMeta, MeetingType, ProjectConfig
from meeting_agent.config import AUDIO_EXTENSIONS, MEETING_META_FILE, Config
from meeting_agent.glossary import GlossaryManager
from meeting_agent.scanner import MeetingScanner

main_bp = Blueprint('main', __name__)
TRANSCRIPTION_MODEL = 'gpt-4o-transcribe'
TRANSLATION_MODEL = os.getenv('TRANSLATION_MODEL', 'gpt-5.4-mini-2026-03-17')
TRANSLATION_REASONING_EFFORT = os.getenv('TRANSLATION_REASONING_EFFORT', 'low').strip()
MEETING_TYPE_OPTIONS = [
    ('review', '评审会'),
    ('weekly', '周会'),
    ('brainstorm', '头脑风暴'),
    ('retro', '复盘会'),
    ('kickoff', '启动会'),
    ('other', '其他'),
]
LANGUAGE_OPTIONS = [
    ('zh', '中文 (简体)'),
    ('zh-TW', '中文 (繁体)'),
    ('en', 'English'),
    ('ja', '日本语'),
    ('ko', '한국어'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('de', 'Deutsch'),
    ('ru', 'Русский'),
    ('pt', 'Português'),
]


def _normalize_language_code(value):
    """归一化语言代码，便于 zh / zh-TW 这类比较"""
    return (value or '').strip().lower().split('-')[0]


def _is_same_language(left, right):
    """宽松比较语言代码"""
    left_normalized = _normalize_language_code(left)
    right_normalized = _normalize_language_code(right)
    return bool(left_normalized and right_normalized
                and left_normalized == right_normalized)


def _supports_translation_reasoning(model):
    """只在明确支持 reasoning 的模型上发送 reasoning 参数"""
    normalized_model = (model or '').strip().lower()
    return normalized_model.startswith('gpt-5')


def _build_translation_reasoning(model, effort):
    """根据模型能力构造 reasoning 配置"""
    normalized_effort = (effort or '').strip().lower()
    if not normalized_effort or not _supports_translation_reasoning(model):
        return None
    return {'effort': normalized_effort}


def _coerce_bool(value, default=False):
    """兼容 bool / 字符串形式的布尔值"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _parse_glossary(text):
    """将多行术语表解析为标准词 + 别名列表"""
    entries = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split('|') if part.strip()]
        if not parts:
            continue
        entries.append({'canonical': parts[0], 'aliases': parts[1:]})
    return entries


def _get_api_key():
    """从环境变量读取 OpenAI API Key"""
    key = os.getenv('OPENAI_API_KEY', '')
    if not key:
        raise ValueError('OPENAI_API_KEY 未配置')
    return key


def _log_timing(stage, **fields):
    """统一输出性能日志，便于 grep 和比对"""
    payload = {'stage': stage, **fields}
    print(f'[perf] {json.dumps(payload, ensure_ascii=False)}')


def _build_workspace_project_options(include_no_project=False):
    try:
        handles = list_project_handles()
    except OSError:
        handles = []
    options = []
    if include_no_project:
        options.append({
            'id': NO_PROJECT_ID,
            'name': '不关联项目',
            'label': '不关联项目（快速模式）',
        })
    options.extend([{
        'id': handle.project_id,
        'name': handle.name,
        'label': '当前工作区' if handle.is_default else handle.name,
    } for handle in handles])
    return handles, options


def _render_workspace_page(error_message=None):
    view_model = build_workspace_view_model()
    _, project_options = _build_workspace_project_options()
    view_model.update({
        'access_protected': bool(os.getenv('ACCESS_CODE', '').strip()),
        'project_options': project_options,
        'meeting_type_options': MEETING_TYPE_OPTIONS,
        'language_options': LANGUAGE_OPTIONS,
        'default_meeting_date': date.today().isoformat(),
        'workspace_error': error_message,
        'quick_project_id': NO_PROJECT_ID,
    })
    return render_template('workspace.html', **view_model)


def _render_project_detail_page(project_id):
    view_model = build_project_detail_view_model(project_id)
    view_model.update({
        'access_protected': bool(os.getenv('ACCESS_CODE', '').strip()),
        'meeting_type_options': MEETING_TYPE_OPTIONS,
        'language_options': LANGUAGE_OPTIONS,
        'default_meeting_date': date.today().isoformat(),
    })
    return render_template('workspace_project.html', **view_model)


def _build_agent_run_command(project_handle, meeting_dir_name, action):
    """构造会议处理命令。"""
    cmd = [sys.executable, '-m', 'meeting_agent', 'run']
    config = Config()
    if config.projects_dir:
        cmd.extend(['--project', project_handle.project_id])
    cmd.extend(['--meeting', meeting_dir_name])

    if action == 'minutes':
        cmd.append('--force-minutes')

    return cmd


def _workspace_root_dir():
    """返回仓库根目录。"""
    return Path(__file__).resolve().parents[1]


def _safe_error(exc: Exception) -> str:
    """将异常转为不含服务端路径的错误消息。
    OSError/FileNotFoundError 的 str() 会暴露绝对路径，统一替换为业务描述。
    其他业务异常（ValueError、RuntimeError 等）均为自定义消息，可直接使用。
    """
    if isinstance(exc, FileNotFoundError):
        return '资源不存在'
    if isinstance(exc, OSError):
        return '服务器文件系统错误'
    return str(exc)


def _sanitize_audio_filename(filename):
    """清洗上传或重命名的音频文件名。"""
    name = Path(filename or '').name.strip()
    if not name:
        raise ValueError('文件名不能为空')

    suffix = Path(name).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError('仅支持常见音频格式上传')

    stem = Path(name).stem.strip().replace('/', '-').replace('\\', '-')
    stem = stem or 'audio'
    return f'{stem}{suffix}'


def _allocate_available_path(directory, filename):
    """若文件名已存在，则自动追加序号。"""
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        next_candidate = directory / f'{stem}-{index}{suffix}'
        if not next_candidate.exists():
            return next_candidate
        index += 1


# ---- 认证 ----


@main_bp.before_request
def require_auth():
    """所有请求前检查登录状态，未登录则拦截"""
    access_code = os.getenv('ACCESS_CODE', '').strip()
    if not access_code:
        return

    exempt = ('main.login', 'main.health', 'main.favicon')
    if request.endpoint in exempt or (request.endpoint
                                      and request.endpoint.startswith('static')):
        return
    if not session.get('authenticated'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect(url_for('main.login'))


@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    expected = os.getenv('ACCESS_CODE', '').strip()
    if not expected:
        session['authenticated'] = True
        return redirect(url_for('main.index'))

    if session.get('authenticated'):
        return redirect(url_for('main.index'))

    error = None
    if request.method == 'POST':
        code = request.form.get('access_code', '')
        if code == expected:
            session['authenticated'] = True
            return redirect(url_for('main.index'))
        error = '访问码错误'

    return render_template('login.html', error=error)


@main_bp.route('/logout')
def logout():
    """登出"""
    session.clear()
    return redirect(url_for('main.login'))


# ---- 页面 ----


@main_bp.route('/')
def index():
    """控制台首页 — SPA 工作台。"""
    access_protected = bool(os.getenv('ACCESS_CODE', '').strip())
    return render_template('workspace_spa.html', access_protected=access_protected)


@main_bp.route('/realtime')
def realtime():
    """实时转写页面。"""
    workspace_handles, workspace_project_options = _build_workspace_project_options(
        include_no_project=True)
    entry_mode = (request.args.get('mode') or 'quick').strip().lower()
    requested_project_id = (request.args.get('project') or '').strip()
    access_protected = bool(os.getenv('ACCESS_CODE', '').strip())

    if entry_mode == 'project':
        default_project_id = requested_project_id or (workspace_handles[0].project_id
                                                      if workspace_handles else
                                                      DEFAULT_PROJECT_ID)
    else:
        default_project_id = NO_PROJECT_ID

    session_meeting_title = (request.args.get('meetingTitle') or '').strip()
    session_meeting_dir = (request.args.get('meeting') or '').strip()
    session_primary_language = (request.args.get('primaryLanguage') or '').strip()
    session_secondary_language = (request.args.get('secondaryLanguage') or '').strip()
    session_language_mode = (request.args.get('languageMode') or '').strip()

    if entry_mode == 'project':
        page_heading = '项目会议实时页'
        session_mode_label = '项目模式'
        session_summary = session_meeting_title or '已关联项目会议，可直接开始实时转写。'
    else:
        page_heading = '快速转写'
        session_mode_label = '快速模式'
        session_summary = '未关联项目，可直接开始实时转写。'

    model_info = {
        'transcription': {
            'purpose': '实时转写',
            'api': 'Realtime API',
            'model': TRANSCRIPTION_MODEL
        },
        'translation': {
            'purpose':
            '后置翻译',
            'api':
            'Responses API',
            'model':
            TRANSLATION_MODEL,
            'reasoning_effort':
            _build_translation_reasoning(TRANSLATION_MODEL,
                                         TRANSLATION_REASONING_EFFORT)
        }
    }
    return render_template(
        'index.html',
        model_info=model_info,
        access_protected=access_protected,
        workspace_projects=workspace_project_options,
        default_project_id=default_project_id,
        entry_mode=entry_mode,
        page_heading=page_heading,
        session_mode_label=session_mode_label,
        session_summary=session_summary,
        session_project_id=default_project_id,
        session_meeting_title=session_meeting_title,
        session_meeting_dir=session_meeting_dir,
        initial_language_mode=session_language_mode,
        initial_primary_language=session_primary_language,
        initial_secondary_language=session_secondary_language,
    )


@main_bp.route('/workspace')
def workspace():
    """控制台别名路由 — 重定向到 SPA。"""
    return redirect(url_for('main.index'))


@main_bp.route('/workspace/project/create', methods=['POST'])
def workspace_project_create():
    """创建新项目并进入项目详情页。"""
    try:
        created = create_project_workspace(
            name=request.form.get('project_name'),
            description=request.form.get('project_description'),
            team=request.form.get('project_team'),
            start_date=request.form.get('project_start_date'),
        )
        flash(f"项目已创建：{created['project_name']}", 'success')
        return redirect(url_for('main.workspace_project_detail',
                                project_id=created['project_id']))
    except Exception as exc:  # pragma: no cover - surface validation message
        flash(str(exc), 'error')
        return redirect(url_for('main.workspace'))


@main_bp.route('/workspace/project/<project_id>')
def workspace_project_detail(project_id):
    """项目详情页 — 重定向到 SPA。"""
    return redirect(f'/#project/{project_id}')


@main_bp.route('/workspace/project/<project_id>/meeting/create', methods=['POST'])
def workspace_project_create_meeting(project_id):
    """在项目详情页内创建会议。"""
    try:
        created = create_meeting_workspace(
            project_id=project_id,
            title=request.form.get('meeting_title'),
            meeting_date=request.form.get('meeting_date'),
            meeting_type=request.form.get('meeting_type'),
            primary_language=request.form.get('primary_language'),
            secondary_language=request.form.get('secondary_language'),
            language_mode=request.form.get('language_mode'),
            notes=request.form.get('notes'),
        )
        flash(f"会议已创建：{created['meeting_title']}", 'success')
    except Exception as exc:  # pragma: no cover - surfacing form error
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_project_detail', project_id=project_id))


@main_bp.route('/workspace/project/<project_id>/glossary', methods=['GET', 'POST'])
def workspace_project_glossary(project_id):
    """术语页 — 重定向到 SPA。"""
    return redirect(f'/#project/{project_id}/glossary')


@main_bp.route('/workspace/project/<project_id>/glossary/approve', methods=['POST'])
def workspace_project_glossary_approve(project_id):
    """确认待审核术语。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        canonical = (request.form.get('canonical') or '').strip()
        if canonical and glossary_mgr.approve_suggestion(canonical):
            flash(f'已确认术语：{canonical}', 'success')
        else:
            flash('未找到待审核术语', 'error')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_project_glossary', project_id=project_id))


@main_bp.route('/workspace/project/<project_id>/glossary/reject', methods=['POST'])
def workspace_project_glossary_reject(project_id):
    """拒绝待审核术语。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        canonical = (request.form.get('canonical') or '').strip()
        reason = (request.form.get('reason') or '').strip() or None
        if canonical and glossary_mgr.reject_suggestion(canonical, reason):
            flash(f'已拒绝术语：{canonical}', 'success')
        else:
            flash('未找到待审核术语', 'error')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_project_glossary', project_id=project_id))


@main_bp.route('/workspace/project/<project_id>/background', methods=['GET', 'POST'])
def workspace_project_background(project_id):
    """背景页 — 重定向到 SPA。"""
    return redirect(f'/#project/{project_id}/background')


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/audio')
def workspace_meeting_audio(project_id, meeting_dir):
    """音频管理页 — 重定向到 SPA 会议列表。"""
    return redirect(f'/#project/{project_id}/meetings')


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/audio/upload',
               methods=['POST'])
def workspace_meeting_audio_upload(project_id, meeting_dir):
    """上传会议音频。"""
    try:
        _, _, resolved_meeting_dir = resolve_meeting_dir(project_id, meeting_dir)
        uploaded_files = [item for item in request.files.getlist('audio_files')
                          if item and item.filename]
        if not uploaded_files:
            raise ValueError('请选择至少一个音频文件')

        saved_files = []
        for uploaded in uploaded_files:
            target_name = _sanitize_audio_filename(uploaded.filename)
            target_path = _allocate_available_path(resolved_meeting_dir, target_name)
            uploaded.save(target_path)
            saved_files.append(target_path.name)

        flash(f'已上传 {len(saved_files)} 个音频文件', 'success')
    except Exception as exc:  # pragma: no cover - upload failure path
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_meeting_audio',
                            project_id=project_id,
                            meeting_dir=meeting_dir))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>')
def workspace_meeting_audio_file(project_id, meeting_dir, filename):
    """返回音频文件，用于试听或下载。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(project_id,
                                                                            meeting_dir,
                                                                            filename)
        return send_from_directory(str(resolved_meeting_dir),
                                   audio_path.name,
                                   as_attachment=request.args.get('download') == '1')
    except FileNotFoundError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('main.workspace_meeting_audio',
                                project_id=project_id,
                                meeting_dir=meeting_dir))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/rename',
               methods=['POST'])
def workspace_meeting_audio_rename(project_id, meeting_dir, filename):
    """重命名会议音频。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(project_id,
                                                                            meeting_dir,
                                                                            filename)
        new_name = _sanitize_audio_filename(request.form.get('new_name'))
        target_path = resolved_meeting_dir / new_name
        if target_path.exists() and target_path.name != audio_path.name:
            raise ValueError('目标文件名已存在')
        audio_path.rename(target_path)
        flash('音频文件已重命名', 'success')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_meeting_audio',
                            project_id=project_id,
                            meeting_dir=meeting_dir))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/delete',
               methods=['POST'])
def workspace_meeting_audio_delete(project_id, meeting_dir, filename):
    """删除会议音频。"""
    try:
        _, _, _, audio_path = resolve_meeting_audio_file(project_id, meeting_dir, filename)
        audio_path.unlink()
        flash('音频文件已删除', 'success')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_meeting_audio',
                            project_id=project_id,
                            meeting_dir=meeting_dir))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/process',
               methods=['POST'])
def workspace_meeting_process(project_id, meeting_dir):
    """对单个会议执行完整处理或仅纪要处理。"""
    action = (request.form.get('action') or 'full').strip()
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(project_id,
                                                                           meeting_dir)
        scanner = MeetingScanner(project_config)
        task = next((item for item in scanner.scan_meetings()
                     if item.dir_name == resolved_meeting_dir.name), None)
        if not task:
            raise FileNotFoundError(f'未找到会议: {meeting_dir}')

        if action == 'minutes' and not task.has_transcript:
            raise ValueError('当前会议还没有正式转写，无法只生成会议纪要')

        cmd = _build_agent_run_command(handle, resolved_meeting_dir.name, action)
        result = subprocess.run(cmd,
                                cwd=_workspace_root_dir(),
                                capture_output=True,
                                text=True,
                                check=False,
                                timeout=1800)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or '处理失败').strip().splitlines()
            raise RuntimeError(message[-1] if message else '处理失败')

        flash('会议处理完成', 'success')
    except Exception as exc:  # pragma: no cover - process error surfacing
        flash(str(exc), 'error')
    return redirect(url_for('main.workspace_project_detail', project_id=project_id))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
               methods=['GET', 'POST'])
def workspace_meeting_file(project_id, meeting_dir, filename):
    """文件页 — 重定向到 SPA 会议列表。"""
    return redirect(f'/#project/{project_id}/meetings')


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>/download')
def workspace_meeting_file_download(project_id, meeting_dir, filename):
    """下载会议文件。"""
    try:
        _, _, resolved_meeting_dir, file_path = resolve_meeting_file(project_id,
                                                                     meeting_dir,
                                                                     filename)
        return send_from_directory(str(resolved_meeting_dir),
                                   file_path.name,
                                   as_attachment=True)
    except FileNotFoundError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('main.workspace_project_detail', project_id=project_id))


@main_bp.route('/workspace/launch-project-meeting', methods=['POST'])
def launch_project_meeting():
    """从控制台创建会议目录并进入实时页。"""
    form = request.form
    title = (form.get('meeting_title') or '').strip()
    if not title:
        return redirect(url_for('main.index', error='会议标题不能为空'))

    try:
        created = create_meeting_workspace(
            project_id=form.get('project_id'),
            title=title,
            meeting_date=form.get('meeting_date'),
            meeting_type=form.get('meeting_type'),
            primary_language=form.get('primary_language'),
            secondary_language=form.get('secondary_language'),
            language_mode=form.get('language_mode'),
            notes=form.get('notes'),
        )
    except Exception as exc:  # pragma: no cover - form validation fallback
        return redirect(url_for('main.index', error=str(exc)))

    return redirect(
        url_for(
            'main.realtime',
            mode='project',
            project=created['project_id'],
            meeting=created['meeting_dir_name'],
            meetingTitle=created['meeting_title'],
            primaryLanguage=created['primary_language'],
            secondaryLanguage=created['secondary_language'],
            languageMode=created['language_mode'],
        ))


@main_bp.route('/health')
def health():
    """健康检查端点（免认证）"""
    return jsonify({'status': 'healthy', 'service': 'MeetingEZ', 'version': '0.2.0'})


@main_bp.route('/favicon.ico')
def favicon():
    """网站图标"""
    return send_from_directory(os.path.join(main_bp.root_path, 'static'),
                               'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')


# ---- SPA JSON API ----


def _strip_project_paths(projects: list) -> list:
    """从项目列表中移除服务端文件路径字段。"""
    for p in projects:
        p.pop('path', None)
    return projects


@main_bp.route('/api/workspace/dashboard')
def api_workspace_dashboard():
    """SPA 仪表盘数据。"""
    try:
        view_model = build_workspace_view_model()
        view_model['can_create_project'] = bool(Config().projects_dir)
        _strip_project_paths(view_model.get('projects', []))
        return jsonify(view_model)
    except OSError:
        # 项目目录尚不存在，返回空工作区
        cfg = Config()
        return jsonify({
            'projects': [],
            'can_create_project': bool(cfg.projects_dir),
            'workspace_summary': {'project_count': 0, 'meeting_count': 0, 'pending_count': 0},
            'quick_entry': {'project_id': NO_PROJECT_ID, 'title': '快速模式', 'description': ''},
        })
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/create', methods=['POST'])
def api_workspace_project_create():
    """创建新项目 (JSON)。"""
    try:
        data = request.get_json() or {}
        created = create_project_workspace(
            name=data.get('name'),
            description=data.get('description'),
            team=data.get('team'),
            start_date=data.get('start_date'),
        )
        created.pop('project_path', None)
        return jsonify(created)
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>')
def api_workspace_project_detail(project_id):
    """项目详情 (JSON)。"""
    try:
        view_model = build_project_detail_view_model(project_id)
        view_model['meeting_type_options'] = MEETING_TYPE_OPTIONS
        view_model['language_options'] = LANGUAGE_OPTIONS
        view_model.get('project', {}).pop('path', None)
        return jsonify(view_model)
    except OSError:
        # 目录不存在（如 PROJECTS_DIR 已配置但尚未初始化），返回空项目
        return jsonify({
            'project': {
                'id': project_id, 'name': project_id, 'description': '',
                'team': [], 'start_date': '-', 'meeting_count': 0,
                'pending_asr': 0, 'pending_minutes': 0,
                'glossary_confirmed': 0, 'glossary_pending': 0,
                'actions_total': 0, 'actions_overdue': 0,
                'background_exists': False, 'pending_term_count': 0,
            },
            'meetings': [],
            'recent_actions': [],
            'meeting_type_options': MEETING_TYPE_OPTIONS,
            'language_options': LANGUAGE_OPTIONS,
        })
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>', methods=['PUT'])
def api_workspace_project_update(project_id):
    """更新项目基本信息 (JSON)。"""
    try:
        data = request.get_json() or {}
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        scanner = MeetingScanner(project_config)
        meta = scanner.load_project_config() or ProjectConfig(name=handle.name)
        if 'name' in data and data['name'].strip():
            meta.name = data['name'].strip()
        if 'description' in data:
            meta.description = data['description'].strip() or None
        if 'team' in data:
            meta.team = _parse_team_members(data['team'])
        if 'start_date' in data:
            meta.start_date = data['start_date'].strip() or None
        _save_project_config(handle.path, meta)
        return jsonify({'ok': True})
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/create', methods=['POST'])
def api_workspace_project_create_meeting(project_id):
    """创建会议 (JSON)。"""
    try:
        data = request.get_json() or {}
        created = create_meeting_workspace(
            project_id=project_id,
            title=data.get('title'),
            meeting_date=data.get('meeting_date'),
            meeting_type=data.get('meeting_type'),
            primary_language=data.get('primary_language'),
            secondary_language=data.get('secondary_language'),
            language_mode=data.get('language_mode'),
            notes=data.get('notes'),
        )
        return jsonify(created)
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/glossary')
def api_workspace_project_glossary(project_id):
    """术语编辑器数据 (JSON)。"""
    try:
        view_model = build_glossary_editor_view_model(project_id)
        # 将 Pydantic 对象转为可序列化 dict
        view_model['confirmed_terms'] = [
            {'canonical': t.canonical, 'aliases': t.aliases, 'type': t.type.value}
            for t in view_model['confirmed_terms']
        ]
        view_model['pending_terms'] = [
            {'canonical': t.canonical, 'aliases': t.aliases, 'frequency': t.frequency,
             'source_meeting': t.source_meeting or ''}
            for t in view_model['pending_terms']
        ]
        view_model['rejected_terms'] = [
            {'canonical': t.canonical, 'reason': t.reason or ''}
            for t in view_model['rejected_terms']
        ]
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/glossary', methods=['PUT'])
def api_workspace_project_glossary_save(project_id):
    """保存术语表 (JSON)。"""
    try:
        data = request.get_json() or {}
        update_project_glossary(project_id, data.get('editor_text', ''))
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/glossary/approve', methods=['POST'])
def api_workspace_glossary_approve(project_id):
    """确认待审核术语 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        data = request.get_json() or {}
        canonical = (data.get('canonical') or '').strip()
        if canonical and glossary_mgr.approve_suggestion(canonical):
            return jsonify({'ok': True, 'canonical': canonical})
        return jsonify({'error': '未找到待审核术语'}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/glossary/reject', methods=['POST'])
def api_workspace_glossary_reject(project_id):
    """拒绝待审核术语 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        data = request.get_json() or {}
        canonical = (data.get('canonical') or '').strip()
        reason = (data.get('reason') or '').strip() or None
        if canonical and glossary_mgr.reject_suggestion(canonical, reason):
            return jsonify({'ok': True, 'canonical': canonical})
        return jsonify({'error': '未找到待审核术语'}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/background')
def api_workspace_project_background(project_id):
    """背景说明数据 (JSON)。"""
    try:
        view_model = build_background_editor_view_model(project_id)
        view_model.pop('file_path', None)
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/background', methods=['PUT'])
def api_workspace_project_background_save(project_id):
    """保存背景说明 (JSON)。"""
    try:
        data = request.get_json() or {}
        update_project_background(project_id, data.get('content', ''))
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>', methods=['PUT'])
def api_workspace_meeting_update(project_id, meeting_dir):
    """更新会议基本信息 (JSON)。"""
    try:
        data = request.get_json() or {}
        handle, project_config, meeting_path = resolve_meeting_dir(project_id, meeting_dir)
        scanner = MeetingScanner(project_config)
        meta = scanner.load_meeting_meta(meeting_path) or MeetingMeta(
            date=meeting_dir[:10], title=meeting_dir)
        if 'title' in data and data['title'].strip():
            meta.title = data['title'].strip()
        if 'date' in data and data['date'].strip():
            meta.date = data['date'].strip()
        if 'type' in data:
            try:
                meta.type = MeetingType(data['type'])
            except ValueError:
                meta.type = MeetingType.OTHER
        if 'notes' in data:
            meta.notes = data['notes'].strip() or None
        if 'primary_language' in data:
            meta.primary_language = data['primary_language'].strip() or meta.primary_language
        if 'secondary_language' in data:
            meta.secondary_language = data['secondary_language'].strip() or None
        if 'language_mode' in data:
            mode_str = _normalize_language_mode(
                data['language_mode'], meta.secondary_language or '')
            meta.language_mode = LanguageMode(mode_str)
        meta_file = meeting_path / MEETING_META_FILE
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta.model_dump(), f, ensure_ascii=False, indent=2, default=str)
        return jsonify({'ok': True})
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio')
def api_workspace_meeting_audio(project_id, meeting_dir):
    """音频文件列表 (JSON)。"""
    try:
        view_model = build_audio_manager_view_model(project_id, meeting_dir)
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio/upload',
               methods=['POST'])
def api_workspace_meeting_audio_upload(project_id, meeting_dir):
    """上传音频 (multipart)。"""
    try:
        _, _, resolved_meeting_dir = resolve_meeting_dir(project_id, meeting_dir)
        uploaded_files = [item for item in request.files.getlist('audio_files')
                          if item and item.filename]
        if not uploaded_files:
            raise ValueError('请选择至少一个音频文件')

        saved_files = []
        for uploaded in uploaded_files:
            target_name = _sanitize_audio_filename(uploaded.filename)
            target_path = _allocate_available_path(resolved_meeting_dir, target_name)
            uploaded.save(target_path)
            saved_files.append(target_path.name)

        return jsonify({'ok': True, 'saved': saved_files})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/rename',
               methods=['POST'])
def api_workspace_meeting_audio_rename(project_id, meeting_dir, filename):
    """重命名音频 (JSON)。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(project_id,
                                                                            meeting_dir,
                                                                            filename)
        data = request.get_json() or {}
        new_name = _sanitize_audio_filename(data.get('new_name'))
        target_path = resolved_meeting_dir / new_name
        if target_path.exists() and target_path.name != audio_path.name:
            raise ValueError('目标文件名已存在')
        audio_path.rename(target_path)
        return jsonify({'ok': True, 'new_name': target_path.name})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>',
               methods=['DELETE'])
def api_workspace_meeting_audio_delete(project_id, meeting_dir, filename):
    """删除音频 (JSON)。"""
    try:
        _, _, _, audio_path = resolve_meeting_audio_file(project_id, meeting_dir, filename)
        audio_path.unlink()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/process',
               methods=['POST'])
def api_workspace_meeting_process(project_id, meeting_dir):
    """触发会议处理 (JSON)。"""
    data = request.get_json() or {}
    action = (data.get('action') or 'full').strip()
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(project_id,
                                                                           meeting_dir)
        scanner = MeetingScanner(project_config)
        task = next((item for item in scanner.scan_meetings()
                     if item.dir_name == resolved_meeting_dir.name), None)
        if not task:
            raise FileNotFoundError(f'未找到会议: {meeting_dir}')

        if action == 'minutes' and not task.has_transcript:
            raise ValueError('当前会议还没有正式转写，无法只生成会议纪要')

        cmd = _build_agent_run_command(handle, resolved_meeting_dir.name, action)
        result = subprocess.run(cmd,
                                cwd=_workspace_root_dir(),
                                capture_output=True,
                                text=True,
                                check=False,
                                timeout=1800)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or '处理失败').strip().splitlines()
            raise RuntimeError(message[-1] if message else '处理失败')

        return jsonify({'ok': True, 'message': '会议处理完成'})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>')
def api_workspace_meeting_file(project_id, meeting_dir, filename):
    """获取文件内容 (JSON)。"""
    try:
        view_model = build_meeting_file_editor_view_model(project_id, meeting_dir, filename)
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
               methods=['PUT'])
def api_workspace_meeting_file_save(project_id, meeting_dir, filename):
    """保存文件内容 (JSON)。"""
    try:
        _, _, _, file_path = resolve_meeting_file(project_id, meeting_dir, filename)
        if file_path.suffix.lower() not in {'.json', '.md', '.txt', '.csv', '.log'}:
            raise ValueError('当前文件不支持在线编辑')
        data = request.get_json() or {}
        file_path.write_text(data.get('content', ''), encoding='utf-8')
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
               methods=['DELETE'])
def api_workspace_meeting_file_delete(project_id, meeting_dir, filename):
    """删除会议文件（转写结果、纪要等）。"""
    try:
        _, _, _, file_path = resolve_meeting_file(project_id, meeting_dir, filename)
        if file_path.name in {MEETING_META_FILE}:
            raise ValueError('该文件不允许删除')
        if file_path.exists():
            file_path.unlink()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


# ---- Realtime & Utility API ----


@main_bp.route('/api/test-connection', methods=['POST'])
def test_connection():
    """测试 OpenAI API 连接"""
    try:
        api_key = _get_api_key()
        resp = requests.get('https://api.openai.com/v1/models',
                            headers={'Authorization': f'Bearer {api_key}'},
                            timeout=10)
        if not resp.ok:
            return jsonify({'error': f'HTTP {resp.status_code}'}), resp.status_code
        return jsonify({'ok': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/realtime-session', methods=['POST'])
def create_realtime_session():
    """
    创建 OpenAI Realtime transcription session 的 client secret
    前端使用该 secret 通过 WebRTC 连接 OpenAI Realtime API
    """
    try:
        api_key = _get_api_key()
    except ValueError as e:
        return jsonify({'error': str(e)}), 500

    data = request.get_json() or {}
    language = data.get('language')
    prompt = data.get('prompt', '')
    transcription_config = {'model': TRANSCRIPTION_MODEL}
    if language:
        transcription_config['language'] = language
    if prompt:
        transcription_config['prompt'] = prompt

    session_config = {
        'type': 'transcription',
        'audio': {
            'input': {
                'format': {
                    'type': 'audio/pcm',
                    'rate': 24000
                },
                'noise_reduction': {
                    'type': 'near_field'
                },
                'transcription': transcription_config,
                'turn_detection': {
                    'type': 'semantic_vad',
                    'eagerness': 'high'
                }
            }
        },
        'include': ['item.input_audio_transcription.logprobs']
    }

    session_started_at = time.perf_counter()
    try:
        resp = requests.post('https://api.openai.com/v1/realtime/client_secrets',
                             headers={
                                 'Authorization': f'Bearer {api_key}',
                                 'Content-Type': 'application/json'
                             },
                             json={'session': session_config},
                             timeout=15)

        if not resp.ok:
            print(f'[realtime-session] OpenAI error: {resp.status_code} {resp.text}')
            return jsonify({'error': resp.text}), resp.status_code

        session_data = resp.json()
        client_secret = session_data.get('value')
        expires_at = session_data.get('expires_at')

        if not client_secret:
            client_secret = session_data.get('client_secret', {}).get('value')
            expires_at = expires_at or session_data.get('client_secret',
                                                        {}).get('expires_at')

        print(
            f'[realtime-session] Session created: {json.dumps(session_data, indent=2)[:500]}'
        )
        _log_timing('realtime_session_created',
                    elapsed_ms=round((time.perf_counter() - session_started_at) * 1000,
                                     1),
                    language=language,
                    has_prompt=bool(prompt),
                    turn_detection=session_config['audio']['input']['turn_detection'])
        return jsonify({
            'clientSecret': client_secret,
            'expiresAt': expires_at,
            'session': session_data.get('session', {})
        })

    except Exception as e:
        _log_timing('realtime_session_failed',
                    elapsed_ms=round((time.perf_counter() - session_started_at) * 1000,
                                     1),
                    error=str(e))
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/workspace/projects', methods=['GET'])
def workspace_projects():
    """返回工作区项目列表，供实时页选择协同来源。"""
    try:
        handles = list_project_handles()
    except OSError:
        handles = []
    return jsonify({
        'projects': [{
            'id': handle.project_id,
            'name': handle.name,
            'isDefault': handle.is_default,
        } for handle in handles]
    })


@main_bp.route('/api/workspace/context-pack', methods=['GET'])
def workspace_context_pack():
    """返回某个项目的 context pack，供实时会议页增强提示。"""
    try:
        pack = build_context_pack(
            project_id=request.args.get('project'),
            primary_language=request.args.get('primaryLanguage'),
            secondary_language=request.args.get('secondaryLanguage'),
            language_mode=request.args.get('languageMode'),
        )
        return jsonify(pack)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:  # pragma: no cover - defensive branch
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/translate', methods=['POST'])
def translate():
    """后置语言处理代理：智能修正 + 双向翻译"""
    try:
        api_key = _get_api_key()
    except ValueError as e:
        return jsonify({'error': str(e)}), 500

    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text'}), 400

    text = data['text']
    primary_language = data.get('primaryLanguage', 'zh')
    secondary_language = data.get('secondaryLanguage', 'ja')
    language_mode = data.get('languageMode', 'single_primary')
    original_language_hint = data.get('originalLanguageHint', primary_language)
    context = data.get('context', '')
    meeting_context = data.get('meetingContext', '')
    enable_correction = _coerce_bool(data.get('enableCorrection'), default=False)
    enable_glossary = _coerce_bool(data.get('enableGlossary'), default=False)
    glossary_entries = _parse_glossary(data.get('glossary',
                                                '')) if enable_glossary else []

    system_prompt = (
        '你是实时会议字幕的后置语言处理助手，负责两件事：\n'
        '1. 在允许时，对 ASR 原始转写做轻量智能修正\n'
        '2. 输出双向翻译\n\n'
        '## 输入\n'
        '- language_mode: 会议语言模式（single_primary / bilingual）\n'
        '- primary_language: 第一语言（如 zh）\n'
        '- secondary_language: 第二语言（如 ja）\n'
        '- current_text: 当前需要处理的 ASR 原始文本\n'
        '- enable_correction: 是否启用智能修正\n'
        '- enable_glossary: 是否启用术语表增强\n'
        '- glossary_entries: 术语表，包含标准写法和别名\n'
        '- meeting_context: 会议级上下文摘要（项目、术语、近期议题）\n'
        '- recent_context: 最近上下文\n\n'
        '## 智能修正规则\n'
        '- 仅在 enable_correction=true 时输出 correctedTranscript\n'
        '- 只允许修正明显 ASR 错误、术语误识别、轻度断句和标点\n'
        '- 不允许改写说话意图，不允许补充未说出的信息，不允许总结替代原句\n'
        '- 如果没有明确证据，不要擅自修改\n'
        '- 如果 enable_glossary=true，优先将术语修正为 glossary_entries 中的标准写法\n\n'
        '## 语言模式约束\n'
        '- 如果 language_mode=single_primary，说明会议主要使用第一语言，只会夹杂少量外语术语或缩写，应优先保留这些术语原样，不要过度判定成第二语言句子\n'
        '- 如果 language_mode=bilingual，说明两种语言都是真正的会议语言，应尽量保留原始语言边界，避免把中文和日语等混淆\n\n'
        '## 规则\n'
        '- 翻译应基于 correctedTranscript（如果启用了智能修正），否则基于 current_text\n'
        '- 情况A: 文本是第一语言 => primaryTranslation = null, secondaryTranslation = 翻译成第二语言\n'
        '- 情况B: 文本是第二语言 => primaryTranslation = 翻译成第一语言, secondaryTranslation = null\n'
        '- 情况C: 文本是其他语言 => primaryTranslation = 翻译成第一语言, secondaryTranslation = null\n'
        '绝对不要输出与原文同语种的“翻译”。若目标语言与原文同语种，对应字段必须是 null。\n'
        'originalLanguage 必须是你判定的原文语言，而不是目标语言。\n\n'
        '输出严格的 JSON，不要添加任何解释。')

    user_content = json.dumps({
        'task': 'bidirectional_translation',
        'language_mode': language_mode,
        'primary_language': primary_language,
        'secondary_language': secondary_language,
        'original_language_hint': original_language_hint,
        'enable_correction': enable_correction,
        'enable_glossary': enable_glossary,
        'glossary_entries': glossary_entries,
        'meeting_context': meeting_context,
        'recent_context': context,
        'current_text': text
    })

    json_schema = {
        'name': 'BidirectionalTranslation',
        'schema': {
            '$schema':
            'http://json-schema.org/draft-07/schema#',
            'type':
            'object',
            'additionalProperties':
            False,
            'required': [
                'originalLanguage', 'correctedTranscript', 'correctionApplied',
                'primaryTranslation', 'secondaryTranslation'
            ],
            'properties': {
                'originalLanguage': {
                    'type': 'string',
                    'description': '判定的文本语言 ISO 代码'
                },
                'correctedTranscript': {
                    'description': '智能修正后的文本；若未启用修正则为 null',
                    'anyOf': [{
                        'type': 'string'
                    }, {
                        'type': 'null'
                    }]
                },
                'correctionApplied': {
                    'type': 'boolean',
                    'description': '是否真的修改了原始转写'
                },
                'primaryTranslation': {
                    'description': '翻译成第一语言，或 null',
                    'anyOf': [{
                        'type': 'string'
                    }, {
                        'type': 'null'
                    }]
                },
                'secondaryTranslation': {
                    'description': '翻译成第二语言，或 null',
                    'anyOf': [{
                        'type': 'string'
                    }, {
                        'type': 'null'
                    }]
                }
            }
        }
    }

    model = data.get('model', TRANSLATION_MODEL)
    reasoning_effort = data.get('reasoningEffort', TRANSLATION_REASONING_EFFORT)
    reasoning = _build_translation_reasoning(model, reasoning_effort)
    translate_started_at = time.perf_counter()
    _log_timing('translate_request_received',
                model=model,
                reasoning_effort=reasoning.get('effort') if reasoning else None,
                text_chars=len(text),
                enable_correction=enable_correction,
                enable_glossary=enable_glossary,
                glossary_entries=len(glossary_entries),
                language_mode=language_mode,
                primary_language=primary_language,
                secondary_language=secondary_language,
                original_language_hint=original_language_hint,
                context_chars=len(context),
                meeting_context_chars=len(meeting_context))

    try:
        payload = {
            'model':
            model,
            'input': [{
                'role': 'system',
                'content': system_prompt
            }, {
                'role': 'user',
                'content': user_content
            }],
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': json_schema['name'],
                    'schema': json_schema['schema'],
                    'strict': True
                }
            }
        }
        if reasoning:
            payload['reasoning'] = reasoning

        resp = requests.post('https://api.openai.com/v1/responses',
                             headers={
                                 'Authorization': f'Bearer {api_key}',
                                 'Content-Type': 'application/json'
                             },
                             json=payload,
                             timeout=30)
        _log_timing('translate_openai_response',
                    model=model,
                    reasoning_effort=reasoning.get('effort') if reasoning else None,
                    elapsed_ms=round(
                        (time.perf_counter() - translate_started_at) * 1000, 1),
                    status_code=resp.status_code,
                    request_id=resp.headers.get('x-request-id'))

        if not resp.ok:
            return jsonify({'error': resp.text}), resp.status_code

        result = resp.json()

        structured = result.get('output_parsed')
        if not structured:
            text_out = ''
            for output in (result.get('output') or []):
                if output.get('type') == 'message':
                    content = output.get('content', [])
                    if content:
                        text_out = content[0].get('text', '')
                    break
            if text_out:
                try:
                    structured = json.loads(text_out)
                except json.JSONDecodeError:
                    structured = None

        if not structured or 'originalLanguage' not in structured:
            structured = {
                'originalLanguage': original_language_hint,
                'correctedTranscript': text if enable_correction else None,
                'correctionApplied': False,
                'primaryTranslation': None,
                'secondaryTranslation': None
            }

        original_language = structured.get('originalLanguage') or original_language_hint
        corrected_transcript = structured.get('correctedTranscript')
        correction_applied = _coerce_bool(structured.get('correctionApplied'),
                                          default=False)
        primary_translation = structured.get('primaryTranslation')
        secondary_translation = structured.get('secondaryTranslation')

        if enable_correction:
            corrected_transcript = (corrected_transcript or text).strip()
            if corrected_transcript == text.strip():
                correction_applied = False
        else:
            corrected_transcript = None
            correction_applied = False

        effective_source_text = corrected_transcript or text

        if primary_translation and primary_translation.strip(
        ) == effective_source_text.strip():
            primary_translation = None
        if secondary_translation and secondary_translation.strip(
        ) == effective_source_text.strip():
            secondary_translation = None

        if not secondary_language:
            secondary_translation = None

        if _is_same_language(original_language, primary_language):
            primary_translation = None
        if _is_same_language(original_language, secondary_language):
            secondary_translation = None
        elif not _is_same_language(original_language, primary_language):
            secondary_translation = None

        structured = {
            'originalLanguage': original_language,
            'rawTranscript': text,
            'correctedTranscript': corrected_transcript,
            'correctionApplied': correction_applied,
            'primaryTranslation': primary_translation,
            'secondaryTranslation': secondary_translation
        }
        _log_timing('translate_request_completed',
                    model=model,
                    reasoning_effort=reasoning.get('effort') if reasoning else None,
                    elapsed_ms=round(
                        (time.perf_counter() - translate_started_at) * 1000, 1),
                    original_language=structured['originalLanguage'],
                    correction_applied=structured['correctionApplied'],
                    has_primary_translation=bool(structured['primaryTranslation']),
                    has_secondary_translation=bool(structured['secondaryTranslation']))

        return jsonify(structured)

    except Exception as e:
        _log_timing('translate_request_failed',
                    model=model,
                    reasoning_effort=reasoning.get('effort') if reasoning else None,
                    elapsed_ms=round(
                        (time.perf_counter() - translate_started_at) * 1000, 1),
                    error=str(e))
        return jsonify({'error': str(e)}), 500
