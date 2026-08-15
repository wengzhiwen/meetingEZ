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
import threading
import time

import requests
from flask import (Blueprint, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from app.workspace_service import (
    DEFAULT_PROJECT_ID, NO_PROJECT_ID, _normalize_language_mode, _parse_team_members,
    _save_project_config, build_audio_manager_view_model,
    build_background_editor_view_model, clone_config_for_dir, build_context_pack,
    build_glossary_editor_view_model, build_meeting_file_editor_view_model,
    build_project_detail_view_model, build_workspace_view_model,
    create_meeting_workspace, create_project_workspace, list_project_handles,
    resolve_meeting_audio_file, resolve_meeting_dir, resolve_meeting_file,
    resolve_project_handle, update_project_glossary)
from meeting_agent.models import LanguageMode, MeetingMeta, MeetingType, ProjectConfig
from meeting_agent.config import AUDIO_EXTENSIONS, ASR_STATE_FILE, MEETING_META_FILE, PROCESSING_LOCK_FILE, PROCESSING_PROGRESS_FILE, Config
from meeting_agent.glossary import GlossaryManager
from meeting_agent.glossary.context_manager import ContextManager as BackgroundContextManager
from meeting_agent.scanner import MeetingScanner

main_bp = Blueprint('main', __name__)
TRANSCRIPTION_MODEL = os.getenv('TRANSCRIPTION_MODEL', 'gpt-live-transcribe')
TRANSLATION_MODEL = os.getenv('TRANSLATION_MODEL', 'gpt-5.6-luna')
TRANSLATION_REASONING_EFFORT = os.getenv('TRANSLATION_REASONING_EFFORT', 'high').strip()
# 实时转写模型的延迟/准确率档位：minimal/low/medium/high/xhigh。
# 值越低首个 delta 越快，但准确率略降；默认 low 适合实时字幕。
_TRANSCRIPTION_DELAY_OPTIONS = ('minimal', 'low', 'medium', 'high', 'xhigh')
TRANSCRIPTION_DELAY = os.getenv('TRANSCRIPTION_DELAY', 'low').strip().lower()
if TRANSCRIPTION_DELAY not in _TRANSCRIPTION_DELAY_OPTIONS:
    print(f'[config] TRANSCRIPTION_DELAY={TRANSCRIPTION_DELAY!r} 不在 '
          f'{_TRANSCRIPTION_DELAY_OPTIONS}，回退为 low')
    TRANSCRIPTION_DELAY = 'low'
# 单次 session 下发的 keywords 上限，避免把整张术语表塞进去拖慢识别。
REALTIME_KEYWORDS_LIMIT = 100
# 术语校正后置处理：realtime session 注入不了术语表，定格后的字幕交给便宜的文本
# 模型按术语表纠错。默认 effort=high 质量优先；字幕延迟敏感时可降 low
# （gpt-5.6-luna + effort=low 实测约 4 秒 / 6 句，$0.20/$1.20 每百万 token）。
REFINE_MODEL = os.getenv('REFINE_MODEL', 'gpt-5.6-luna')
REFINE_REASONING_EFFORT = os.getenv('REFINE_REASONING_EFFORT', 'high').strip()
REFINE_MAX_SEGMENTS = 12
REFINE_MAX_CHARS = 4000
REALTIME_TRANSLATION_MODEL = os.getenv('REALTIME_TRANSLATION_MODEL',
                                       'gpt-realtime-translate')
REALTIME_TRANSLATION_INPUT_MODEL = os.getenv('REALTIME_TRANSLATION_INPUT_MODEL',
                                             'gpt-live-transcribe')
# 本地 ASR（Qwen3-ASR）服务地址。浏览器直连该地址进行实时转写，不经后端中转。
# 注入到前端作为「转写引擎」本地端点输入框的默认值，用户可在设置面板覆盖；
# 留空则输入框初始为空（引擎选项仍显示）。
LOCAL_ASR_BASE_URL = os.getenv('LOCAL_ASR_BASE_URL', '').strip()
REALTIME_TRANSLATION_LANGUAGES = {
    'es', 'pt', 'fr', 'ja', 'ru', 'zh', 'de', 'ko', 'hi', 'id', 'vi', 'it', 'en'
}
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
    """只在明确支持 reasoning 的模型上发送 reasoning 参数。"""
    return (model or '').strip().lower().startswith('gpt-5')


def _build_translation_reasoning(model, effort):
    normalized_effort = (effort or '').strip().lower()
    if not normalized_effort or not _supports_translation_reasoning(model):
        return None
    return {'effort': normalized_effort}


def _supports_transcription_context(model):
    """判断模型是否接受 prompt / keywords / languages 上下文字段。

    gpt-live-transcribe 支持三者；上一代 gpt-realtime-whisper 全部不支持，
    发送会被 Realtime client_secrets 端点以 unknown_parameter 拒绝。
    """
    return (model or '').strip().lower().startswith('gpt-live-transcribe')


def _supports_transcription_delay(model):
    """判断模型是否接受 delay 档位字段。"""
    normalized_model = (model or '').strip().lower()
    return normalized_model.startswith(('gpt-live-transcribe', 'gpt-realtime-whisper'))


def _normalize_keyword_list(values, limit=REALTIME_KEYWORDS_LIMIT):
    """清洗 keywords：去空白、去重（大小写不敏感）、保序、限量。"""
    if not values:
        return []
    if isinstance(values, str):
        values = values.replace('\n', ',').split(',')

    keywords = []
    seen = set()
    for raw in values:
        keyword = str(raw or '').strip()
        if not keyword:
            continue
        dedupe_key = keyword.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return keywords


def _normalize_language_hints(values, limit=4):
    """清洗 languages：转 ISO-639-1 短码、去重、保序、限量。"""
    if not values:
        return []
    if isinstance(values, str):
        values = values.replace('\n', ',').split(',')

    languages = []
    seen = set()
    for raw in values:
        code = _normalize_language_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        languages.append(code)
        if len(languages) >= limit:
            break
    return languages


def _build_realtime_transcription_config(model,
                                         language=None,
                                         languages=None,
                                         prompt='',
                                         keywords=None):
    """构造 Realtime transcription 配置，避免发送模型不支持的字段。

    gpt-live-transcribe 接受 prompt（自由文本背景）、keywords（人名/产品名等
    字面词）、languages（期望输入语言，支持多语言混说）和 delay
    （minimal/low/medium/high/xhigh，值越低首个 delta 越快）。
    上一代模型只接受 model / language / delay。
    """
    transcription_config = {'model': model}

    if _supports_transcription_context(model):
        normalized_languages = _normalize_language_hints(languages or language)
        if normalized_languages:
            transcription_config['languages'] = normalized_languages
        normalized_prompt = (prompt or '').strip()
        if normalized_prompt:
            transcription_config['prompt'] = normalized_prompt
        normalized_keywords = _normalize_keyword_list(keywords)
        if normalized_keywords:
            transcription_config['keywords'] = normalized_keywords
    elif language:
        transcription_config['language'] = language

    if _supports_transcription_delay(model):
        transcription_config['delay'] = TRANSCRIPTION_DELAY

    return transcription_config


def _normalize_realtime_translation_language(language):
    """归一化 Realtime Translation 支持的紧凑目标语言代码。"""
    normalized = _normalize_language_code(language)
    if normalized not in REALTIME_TRANSLATION_LANGUAGES:
        raise ValueError(
            'Unsupported realtime translation target language: '
            f'{language}. Supported: {", ".join(sorted(REALTIME_TRANSLATION_LANGUAGES))}'
        )
    return normalized


def _proxy_realtime_sdp_call(openai_url):
    """代理浏览器 WebRTC SDP offer 到 OpenAI，避免客户端网络直连失败。"""
    client_secret = (request.headers.get('X-OpenAI-Client-Secret') or '').strip()
    if not client_secret:
        return jsonify({'error': 'Missing X-OpenAI-Client-Secret'}), 400

    offer_sdp = request.get_data(as_text=True) or ''
    if not offer_sdp.strip():
        return jsonify({'error': 'Missing SDP offer'}), 400

    started_at = time.perf_counter()
    try:
        resp = requests.post(openai_url,
                             headers={
                                 'Authorization': f'Bearer {client_secret}',
                                 'Content-Type': 'application/sdp'
                             },
                             data=offer_sdp.encode('utf-8'),
                             timeout=30)
        if not resp.ok:
            print(f'[realtime-call-proxy] OpenAI error: {resp.status_code} {resp.text}')
            return resp.text, resp.status_code, {
                'Content-Type': 'text/plain; charset=utf-8'
            }

        _log_timing('realtime_call_proxy_succeeded',
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    endpoint=openai_url.rsplit('/', 1)[-1],
                    answer_bytes=len(resp.text))
        return resp.text, 200, {'Content-Type': 'application/sdp'}
    except Exception as e:
        _log_timing('realtime_call_proxy_failed',
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    endpoint=openai_url.rsplit('/', 1)[-1],
                    error=str(e))
        return jsonify({'error': str(e)}), 500


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _parse_glossary(text):
    entries = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split('|') if part.strip()]
        if parts:
            entries.append({'canonical': parts[0], 'aliases': parts[1:]})
    return entries


def _extract_structured_output(result):
    """从 Responses API 结果里取出 json_schema 结构化输出。

    新版返回顶层 `output_parsed`，旧版只有 output[].content[].text 里的 JSON 串，
    两种都要兼容。
    """
    structured = result.get('output_parsed')
    if structured:
        return structured

    for output in result.get('output') or []:
        if output.get('type') != 'message':
            continue
        for content in output.get('content') or []:
            text_out = content.get('text')
            if not text_out:
                continue
            try:
                return json.loads(text_out)
            except json.JSONDecodeError:
                return None
    return None


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
    elif action == 'reprocess':
        cmd.append('--force-minutes')  # 仅强制重生成纪要；ASR 由 agent 自行判断（新音频才跑）

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
        'realtime_translation': {
            'purpose': '实时翻译',
            'api': 'Realtime Translation API',
            'model': REALTIME_TRANSLATION_MODEL,
            'input_model': REALTIME_TRANSLATION_INPUT_MODEL
        },
        'local_asr': {
            'purpose': '本地转写',
            'api': 'Qwen3-ASR Streaming HTTP',
            'base_url': LOCAL_ASR_BASE_URL
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
        return redirect(
            url_for('main.workspace_project_detail', project_id=created['project_id']))
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
        uploaded_files = [
            item for item in request.files.getlist('audio_files')
            if item and item.filename
        ]
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
    return redirect(
        url_for('main.workspace_meeting_audio',
                project_id=project_id,
                meeting_dir=meeting_dir))


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>')
def workspace_meeting_audio_file(project_id, meeting_dir, filename):
    """返回音频文件，用于试听或下载。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(
            project_id, meeting_dir, filename)
        return send_from_directory(str(resolved_meeting_dir),
                                   audio_path.name,
                                   as_attachment=request.args.get('download') == '1')
    except FileNotFoundError as exc:
        flash(str(exc), 'error')
        return redirect(
            url_for('main.workspace_meeting_audio',
                    project_id=project_id,
                    meeting_dir=meeting_dir))


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/rename',
    methods=['POST'])
def workspace_meeting_audio_rename(project_id, meeting_dir, filename):
    """重命名会议音频。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(
            project_id, meeting_dir, filename)
        new_name = _sanitize_audio_filename(request.form.get('new_name'))
        target_path = resolved_meeting_dir / new_name
        if target_path.exists() and target_path.name != audio_path.name:
            raise ValueError('目标文件名已存在')
        audio_path.rename(target_path)
        flash('音频文件已重命名', 'success')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(
        url_for('main.workspace_meeting_audio',
                project_id=project_id,
                meeting_dir=meeting_dir))


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/delete',
    methods=['POST'])
def workspace_meeting_audio_delete(project_id, meeting_dir, filename):
    """删除会议音频。"""
    try:
        _, _, _, audio_path = resolve_meeting_audio_file(project_id, meeting_dir,
                                                         filename)
        audio_path.unlink()
        flash('音频文件已删除', 'success')
    except Exception as exc:  # pragma: no cover - defensive branch
        flash(str(exc), 'error')
    return redirect(
        url_for('main.workspace_meeting_audio',
                project_id=project_id,
                meeting_dir=meeting_dir))


@main_bp.route('/workspace/project/<project_id>/meeting/<meeting_dir>/process',
               methods=['POST'])
def workspace_meeting_process(project_id, meeting_dir):
    """对单个会议执行完整处理或仅纪要处理。"""
    action = (request.form.get('action') or 'full').strip()
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(
            project_id, meeting_dir)
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


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
    methods=['GET', 'POST'])
def workspace_meeting_file(project_id, meeting_dir, filename):
    """文件页 — 重定向到 SPA 会议列表。"""
    return redirect(f'/#project/{project_id}/meetings')


@main_bp.route(
    '/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>/download'
)
def workspace_meeting_file_download(project_id, meeting_dir, filename):
    """下载会议文件。"""
    try:
        _, _, resolved_meeting_dir, file_path = resolve_meeting_file(
            project_id, meeting_dir, filename)
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
                               'logo.svg',
                               mimetype='image/svg+xml')


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
            'workspace_summary': {
                'project_count': 0,
                'meeting_count': 0,
                'pending_count': 0
            },
            'quick_entry': {
                'project_id': NO_PROJECT_ID,
                'title': '快速模式',
                'description': ''
            },
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
                'id': project_id,
                'name': project_id,
                'description': '',
                'team': [],
                'start_date': '-',
                'meeting_count': 0,
                'pending_asr': 0,
                'pending_minutes': 0,
                'glossary_confirmed': 0,
                'glossary_pending': 0,
                'background_exists': False,
                'pending_term_count': 0,
            },
            'meetings': [],
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
    """术语编辑器数据 (JSON) — 返回统一的 terms 列表。"""
    try:
        view_model = build_glossary_editor_view_model(project_id)
        terms = []
        for t in view_model['confirmed_terms']:
            terms.append({
                'state': 'confirmed',
                'canonical': t.canonical,
                'aliases': t.aliases,
                'type': t.type.value,
                'context': t.context or t.description or '',
                'source_meeting': t.source_meeting or '',
            })
        for t in view_model['pending_terms']:
            terms.append({
                'state': 'pending',
                'canonical': t.canonical,
                'aliases': t.aliases,
                'type': t.type.value,
                'context': t.context or '',
                'source_meeting': t.source_meeting or '',
                'frequency': t.frequency,
            })
        for t in view_model['rejected_terms']:
            terms.append({
                'state':
                'rejected',
                'canonical':
                t.canonical,
                'aliases':
                getattr(t, 'aliases', []),
                'type':
                getattr(t, 'type', 'other')
                if not hasattr(t.type, 'value') else t.type.value,
                'context':
                t.context or '',
                'source_meeting':
                getattr(t, 'source_meeting', '') or '',
                'reason':
                t.reason or '',
            })
        terms.reverse()
        return jsonify({'project': view_model['project'], 'terms': terms})
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/glossary/entries', methods=['POST'])
def api_workspace_glossary_add_entry(project_id):
    """手动添加术语到术语表 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        data = request.get_json() or {}
        canonical = (data.get('canonical') or '').strip()
        if not canonical:
            return jsonify({'error': '术语名称不能为空'}), 400
        aliases_raw = data.get('aliases', [])
        if isinstance(aliases_raw, str):
            aliases_raw = [a.strip() for a in aliases_raw.split(',') if a.strip()]
        from meeting_agent.models_glossary import TermType as _TermType
        try:
            term_type = _TermType(data.get('type', 'other'))
        except ValueError:
            term_type = _TermType.OTHER
        entry = glossary_mgr.add_term(canonical=canonical,
                                      aliases=aliases_raw,
                                      type=term_type)
        return jsonify({'ok': True, 'canonical': entry.canonical})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/glossary/entries/<path:canonical>',
               methods=['PUT'])
def api_workspace_glossary_update_entry(project_id, canonical):
    """更新术语表中的术语 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        data = request.get_json() or {}
        aliases_raw = data.get('aliases', None)
        if isinstance(aliases_raw, str):
            aliases_raw = [a.strip() for a in aliases_raw.split(',') if a.strip()]
        from meeting_agent.models_glossary import TermType as _TermType
        term_type = None
        if 'type' in data:
            try:
                term_type = _TermType(data['type'])
            except ValueError:
                term_type = _TermType.OTHER
        entry = glossary_mgr.update_entry(
            canonical=canonical,
            new_canonical=data.get('canonical') or None,
            aliases=aliases_raw,
            type=term_type,
            context=data.get('context') or None,
        )
        if not entry:
            return jsonify({'error': '未找到术语'}), 404
        return jsonify({'ok': True, 'canonical': entry.canonical})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/glossary/entries/<path:canonical>',
               methods=['DELETE'])
def api_workspace_glossary_delete_entry(project_id, canonical):
    """从术语表中删除术语 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        if glossary_mgr.remove_entry(canonical):
            return jsonify({'ok': True})
        return jsonify({'error': '未找到术语'}), 404
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


@main_bp.route('/api/workspace/project/<project_id>/glossary/revert', methods=['POST'])
def api_workspace_glossary_revert(project_id):
    """将术语回退到待审核状态 (JSON)。from_state: confirmed | rejected"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        glossary_mgr = GlossaryManager(project_config)
        data = request.get_json() or {}
        canonical = (data.get('canonical') or '').strip()
        from_state = (data.get('from_state') or '').strip()
        if not canonical:
            return jsonify({'error': '缺少 canonical'}), 400
        if from_state == 'confirmed':
            ok = glossary_mgr.revert_confirmed_to_pending(canonical)
        elif from_state == 'rejected':
            ok = glossary_mgr.revert_rejected_to_pending(canonical)
        else:
            return jsonify({'error': '未知的 from_state'}), 400
        if ok:
            return jsonify({'ok': True, 'canonical': canonical})
        return jsonify({'error': '未找到术语'}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/background')
def api_workspace_project_background(project_id):
    """背景说明数据 (JSON)。"""
    try:
        view_model = build_background_editor_view_model(project_id)
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route('/api/workspace/project/<project_id>/background/entries',
               methods=['POST'])
def api_workspace_background_add_entry(project_id):
    """新增背景说明条目 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        mgr = BackgroundContextManager(project_config)
        data = request.get_json() or {}
        topic = (data.get('topic') or '').strip()
        question = (data.get('question') or '').strip()
        if not topic:
            return jsonify({'error': '标题不能为空'}), 400
        entry = mgr.add_entry(
            topic=topic,
            question=question,
            answer=(data.get('answer') or '').strip() or None,
            source_meeting=data.get('source_meeting') or None,
        )
        return jsonify({'ok': True, 'id': entry.id})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/background/entries/<entry_id>',
               methods=['PUT'])
def api_workspace_background_update_entry(project_id, entry_id):
    """更新背景说明条目 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        mgr = BackgroundContextManager(project_config)
        data = request.get_json() or {}
        entry = mgr.update_entry(
            entry_id=entry_id,
            topic=data.get('topic') or None,
            question=data.get('question') or None,
            answer=data.get('answer'),  # allow empty string to clear answer
        )
        if not entry:
            return jsonify({'error': '未找到条目'}), 404
        return jsonify({'ok': True, 'id': entry.id})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/background/entries/<entry_id>',
               methods=['DELETE'])
def api_workspace_background_delete_entry(project_id, entry_id):
    """删除背景说明条目 (JSON)。"""
    try:
        handle = resolve_project_handle(project_id)
        project_config = clone_config_for_dir(Config(), handle.path)
        mgr = BackgroundContextManager(project_config)
        if mgr.delete_entry(entry_id):
            return jsonify({'ok': True})
        return jsonify({'error': '未找到条目'}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>',
               methods=['PUT'])
def api_workspace_meeting_update(project_id, meeting_dir):
    """更新会议基本信息 (JSON)。"""
    try:
        data = request.get_json() or {}
        handle, project_config, meeting_path = resolve_meeting_dir(
            project_id, meeting_dir)
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
            meta.primary_language = data['primary_language'].strip(
            ) or meta.primary_language
        if 'secondary_language' in data:
            meta.secondary_language = data['secondary_language'].strip() or None
        if 'language_mode' in data:
            mode_str = _normalize_language_mode(data['language_mode'],
                                                meta.secondary_language or '')
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
        uploaded_files = [
            item for item in request.files.getlist('audio_files')
            if item and item.filename
        ]
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


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>/rename',
    methods=['POST'])
def api_workspace_meeting_audio_rename(project_id, meeting_dir, filename):
    """重命名音频 (JSON)。"""
    try:
        _, _, resolved_meeting_dir, audio_path = resolve_meeting_audio_file(
            project_id, meeting_dir, filename)
        data = request.get_json() or {}
        new_name = _sanitize_audio_filename(data.get('new_name'))
        target_path = resolved_meeting_dir / new_name
        if target_path.exists() and target_path.name != audio_path.name:
            raise ValueError('目标文件名已存在')
        audio_path.rename(target_path)
        return jsonify({'ok': True, 'new_name': target_path.name})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/audio/<path:filename>',
    methods=['DELETE'])
def api_workspace_meeting_audio_delete(project_id, meeting_dir, filename):
    """删除音频 (JSON)。"""
    try:
        _, _, _, audio_path = resolve_meeting_audio_file(project_id, meeting_dir,
                                                         filename)
        audio_path.unlink()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


def _run_meeting_process_async(lock_file: Path, cmd: list, cwd: str):
    """在后台线程中运行会议处理，完成后删除锁文件，并将日志写入会议目录。"""
    log_file = lock_file.parent / '_processing.log'
    error_file = lock_file.parent / '_processing.error'
    # 清理残留的 progress 文件
    progress_file = lock_file.parent / PROCESSING_PROGRESS_FILE
    if progress_file.exists():
        try:
            progress_file.unlink()
        except OSError:
            pass
    import logging as _logging
    _logger = _logging.getLogger('meeting_agent.process')
    try:
        _logger.info('开始处理: %s cmd=%s', lock_file.parent.name, ' '.join(cmd))
        with open(log_file, 'w', encoding='utf-8') as log_fh:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=1800,
            )
        if result.returncode != 0:
            # 读取日志末尾作为错误摘要
            output = log_file.read_text(encoding='utf-8')
            error_lines = output.strip().splitlines()
            last_line = error_lines[-1] if error_lines else '处理失败'
            error_file.write_text(last_line, encoding='utf-8')
            _logger.error('处理失败 (code=%d): %s\n%s', result.returncode, last_line,
                          output[-2000:])
        else:
            error_file.unlink(missing_ok=True)
            _logger.info('处理完成: %s', lock_file.parent.name)
    except subprocess.TimeoutExpired:
        error_file.write_text('处理超时（超过 30 分钟）', encoding='utf-8')
        _logger.error('处理超时: %s', lock_file.parent.name)
    except Exception as exc:
        error_file.write_text(str(exc), encoding='utf-8')
        _logger.error('处理异常: %s %s', lock_file.parent.name, exc)
    finally:
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/process',
               methods=['POST'])
def api_workspace_meeting_process(project_id, meeting_dir):
    """触发会议处理 (JSON) — 异步启动，立即返回。"""
    data = request.get_json() or {}
    action = (data.get('action') or 'full').strip()
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(
            project_id, meeting_dir)
        scanner = MeetingScanner(project_config)
        task = next((item for item in scanner.scan_meetings()
                     if item.dir_name == resolved_meeting_dir.name), None)
        if not task:
            raise FileNotFoundError(f'未找到会议: {meeting_dir}')

        if task.is_processing:
            return jsonify({'error': '该会议正在处理中，请稍候'}), 409

        if action == 'minutes' and not task.has_transcript:
            raise ValueError('当前会议还没有正式转写，无法只生成会议纪要')

        lock_file = resolved_meeting_dir / PROCESSING_LOCK_FILE
        lock_file.write_text(str(os.getpid()))

        cmd = _build_agent_run_command(handle, resolved_meeting_dir.name, action)
        t = threading.Thread(
            target=_run_meeting_process_async,
            args=(lock_file, cmd, _workspace_root_dir()),
            daemon=True,
        )
        t.start()

        return jsonify({'ok': True, 'status': 'started'})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/process/status',
    methods=['GET'])
def api_workspace_meeting_process_status(project_id, meeting_dir):
    """查询会议处理状态 (JSON)。"""
    try:
        _, _, resolved_meeting_dir = resolve_meeting_dir(project_id, meeting_dir)
        lock_file = resolved_meeting_dir / PROCESSING_LOCK_FILE
        error_file = resolved_meeting_dir / '_processing.error'
        is_processing = lock_file.exists()
        error_msg = None
        if not is_processing and error_file.exists():
            try:
                error_msg = error_file.read_text(encoding='utf-8').strip()
            except Exception:
                pass

        # ASR 重试/降级状态
        asr_state = None
        asr_state_file = resolved_meeting_dir / ASR_STATE_FILE
        if asr_state_file.exists():
            try:
                asr_state = json.loads(asr_state_file.read_text(encoding='utf-8'))
            except Exception:
                pass

        # 处理进度
        progress = None
        progress_file = resolved_meeting_dir / PROCESSING_PROGRESS_FILE
        if is_processing and progress_file.exists():
            try:
                progress = json.loads(progress_file.read_text(encoding='utf-8'))
            except Exception:
                pass

        return jsonify({
            'is_processing': is_processing,
            'error': error_msg,
            'asr_state': asr_state,
            'progress': progress,
        })
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/asr/retry',
               methods=['POST'])
def api_workspace_asr_retry(project_id, meeting_dir):
    """立即重试 ASR（复用已完成的分块进度）。"""
    import logging as _logging
    _logging.getLogger('meeting_agent.process').info('ASR 重试请求: project=%s, meeting=%s',
                                                     project_id, meeting_dir)
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(
            project_id, meeting_dir)

        lock_file = resolved_meeting_dir / PROCESSING_LOCK_FILE
        if lock_file.exists():
            return jsonify({'error': '该会议正在处理中，请稍候'}), 409

        from meeting_agent.asr.router import ASRRouter
        asr_router = ASRRouter(project_config)
        asr_router.retry_now(resolved_meeting_dir)

        # 触发后台处理
        lock_file.write_text(str(os.getpid()))
        cmd = _build_agent_run_command(handle, resolved_meeting_dir.name, 'full')
        t = threading.Thread(
            target=_run_meeting_process_async,
            args=(lock_file, cmd, _workspace_root_dir()),
            daemon=True,
        )
        t.start()

        return jsonify({'ok': True, 'status': 'started'})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route('/api/workspace/project/<project_id>/meeting/<meeting_dir>/asr/restart',
               methods=['POST'])
def api_workspace_asr_restart(project_id, meeting_dir):
    """丢弃分块进度，从头重新转写。"""
    import logging as _logging
    _logging.getLogger('meeting_agent.process').warning(
        'ASR 重头转写请求: project=%s, meeting=%s', project_id, meeting_dir)
    try:
        handle, project_config, resolved_meeting_dir = resolve_meeting_dir(
            project_id, meeting_dir)

        lock_file = resolved_meeting_dir / PROCESSING_LOCK_FILE
        if lock_file.exists():
            return jsonify({'error': '该会议正在处理中，请稍候'}), 409

        from meeting_agent.asr.router import ASRRouter
        asr_router = ASRRouter(project_config)
        asr_router.reset_progress(resolved_meeting_dir)
        asr_router.retry_now(resolved_meeting_dir)

        # 触发后台处理
        lock_file.write_text(str(os.getpid()))
        cmd = _build_agent_run_command(handle, resolved_meeting_dir.name, 'full')
        t = threading.Thread(
            target=_run_meeting_process_async,
            args=(lock_file, cmd, _workspace_root_dir()),
            daemon=True,
        )
        t.start()

        return jsonify({'ok': True, 'status': 'started'})
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 400


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>')
def api_workspace_meeting_file(project_id, meeting_dir, filename):
    """获取文件内容 (JSON)。"""
    try:
        view_model = build_meeting_file_editor_view_model(project_id, meeting_dir,
                                                          filename)
        return jsonify(view_model)
    except FileNotFoundError as exc:
        return jsonify({'error': _safe_error(exc)}), 404
    except Exception as exc:
        return jsonify({'error': _safe_error(exc)}), 500


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
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


@main_bp.route(
    '/api/workspace/project/<project_id>/meeting/<meeting_dir>/files/<path:filename>',
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
    language = _normalize_language_code(data.get('language')) or None
    prompt = data.get('prompt', '')
    transcription_config = _build_realtime_transcription_config(
        TRANSCRIPTION_MODEL,
        language=language,
        languages=data.get('languages'),
        prompt=prompt,
        keywords=data.get('keywords'))

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
                'transcription': transcription_config
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
        _log_timing(
            'realtime_session_created',
            elapsed_ms=round((time.perf_counter() - session_started_at) * 1000, 1),
            model=TRANSCRIPTION_MODEL,
            languages=transcription_config.get('languages') or language,
            has_prompt=bool(transcription_config.get('prompt')),
            keyword_count=len(transcription_config.get('keywords') or []),
            turn_detection=session_config['audio']['input'].get('turn_detection'))
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


@main_bp.route('/api/realtime-translation-session', methods=['POST'])
def create_realtime_translation_session():
    """
    创建 OpenAI Realtime Translation session 的 client secret。
    该模式作为实验性旁路使用，不替代默认实时转写链路。
    """
    try:
        api_key = _get_api_key()
    except ValueError as e:
        return jsonify({'error': str(e)}), 500

    data = request.get_json() or {}
    try:
        target_language = _normalize_realtime_translation_language(
            data.get('targetLanguage'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    session_config = {
        'model': REALTIME_TRANSLATION_MODEL,
        'audio': {
            'input': {
                'transcription': {
                    'model': REALTIME_TRANSLATION_INPUT_MODEL
                },
                'noise_reduction': {
                    'type': 'near_field'
                }
            },
            'output': {
                'language': target_language
            }
        }
    }

    session_started_at = time.perf_counter()
    try:
        resp = requests.post(
            'https://api.openai.com/v1/realtime/translations/client_secrets',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={'session': session_config},
            timeout=15)

        if not resp.ok:
            print(
                f'[realtime-translation-session] OpenAI error: {resp.status_code} {resp.text}'
            )
            return jsonify({'error': resp.text}), resp.status_code

        session_data = resp.json()
        client_secret = session_data.get('value')
        expires_at = session_data.get('expires_at')

        if not client_secret:
            client_secret = session_data.get('client_secret', {}).get('value')
            expires_at = expires_at or session_data.get('client_secret',
                                                        {}).get('expires_at')

        print('[realtime-translation-session] Session created: '
              f'{json.dumps(session_data, indent=2)[:500]}')
        _log_timing('realtime_translation_session_created',
                    elapsed_ms=round((time.perf_counter() - session_started_at) * 1000,
                                     1),
                    target_language=target_language,
                    model=REALTIME_TRANSLATION_MODEL,
                    input_model=REALTIME_TRANSLATION_INPUT_MODEL)
        return jsonify({
            'clientSecret': client_secret,
            'expiresAt': expires_at,
            'session': session_data.get('session', {}),
            'targetLanguage': target_language,
            'model': REALTIME_TRANSLATION_MODEL
        })

    except Exception as e:
        _log_timing('realtime_translation_session_failed',
                    elapsed_ms=round((time.perf_counter() - session_started_at) * 1000,
                                     1),
                    target_language=target_language,
                    error=str(e))
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/realtime-call', methods=['POST'])
def proxy_realtime_call():
    """代理 Realtime transcription WebRTC SDP exchange。"""
    return _proxy_realtime_sdp_call('https://api.openai.com/v1/realtime/calls')


@main_bp.route('/api/realtime-translation-call', methods=['POST'])
def proxy_realtime_translation_call():
    """代理 Realtime Translation WebRTC SDP exchange。"""
    return _proxy_realtime_sdp_call(
        'https://api.openai.com/v1/realtime/translations/calls')


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
    """稳定后置处理：智能修正、术语增强和双向翻译。"""
    try:
        api_key = _get_api_key()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 500

    data = request.get_json() or {}
    text = str(data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'Missing text'}), 400

    primary_language = data.get('primaryLanguage', 'zh')
    secondary_language = data.get('secondaryLanguage', '')
    original_language_hint = data.get('originalLanguageHint', primary_language)
    enable_correction = _coerce_bool(data.get('enableCorrection'))
    enable_glossary = _coerce_bool(data.get('enableGlossary'))
    glossary_entries = (_parse_glossary(data.get('glossary', ''))
                        if enable_glossary else [])
    model = data.get('model', TRANSLATION_MODEL)
    reasoning = _build_translation_reasoning(
        model, data.get('reasoningEffort', TRANSLATION_REASONING_EFFORT))

    system_prompt = ('你是会议字幕后置处理器。只修正有明确依据的 ASR 错误，不得改写意图或补充内容。'
                     '识别原文语言，并按以下规则翻译：原文若是第一语言，只译为第二语言；原文若是第二语言，'
                     '只译为第一语言；其他语言只译为第一语言。目标语言与原文相同的字段必须为 null。'
                     '仅当 enable_correction=true 时返回 correctedTranscript，否则必须为 null。'
                     '输出必须严格符合给定 JSON schema。')
    user_content = json.dumps(
        {
            'language_mode': data.get('languageMode', 'single_primary'),
            'primary_language': primary_language,
            'secondary_language': secondary_language,
            'original_language_hint': original_language_hint,
            'enable_correction': enable_correction,
            'glossary_entries': glossary_entries,
            'meeting_context': data.get('meetingContext', ''),
            'recent_context': data.get('context', ''),
            'current_text': text,
        },
        ensure_ascii=False)

    schema = {
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
                'type': 'string'
            },
            'correctedTranscript': {
                'anyOf': [{
                    'type': 'string'
                }, {
                    'type': 'null'
                }]
            },
            'correctionApplied': {
                'type': 'boolean'
            },
            'primaryTranslation': {
                'anyOf': [{
                    'type': 'string'
                }, {
                    'type': 'null'
                }]
            },
            'secondaryTranslation': {
                'anyOf': [{
                    'type': 'string'
                }, {
                    'type': 'null'
                }]
            },
        },
    }
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
                'name': 'BidirectionalTranslation',
                'schema': schema,
                'strict': True,
            }
        },
    }
    if reasoning:
        payload['reasoning'] = reasoning

    started_at = time.perf_counter()
    try:
        resp = requests.post('https://api.openai.com/v1/responses',
                             headers={
                                 'Authorization': f'Bearer {api_key}',
                                 'Content-Type': 'application/json',
                             },
                             json=payload,
                             timeout=30)
        if not resp.ok:
            return jsonify({'error': resp.text}), resp.status_code

        structured = _extract_structured_output(resp.json())
        if not isinstance(structured, dict):
            raise ValueError('Responses API 未返回有效的结构化翻译结果')

        original_language = (structured.get('originalLanguage')
                             or original_language_hint)
        corrected = structured.get('correctedTranscript')
        correction_applied = _coerce_bool(structured.get('correctionApplied'))
        if enable_correction:
            corrected = (corrected or text).strip()
            correction_applied = correction_applied and corrected != text
        else:
            corrected = None
            correction_applied = False

        effective_source = corrected or text
        primary_translation = structured.get('primaryTranslation')
        secondary_translation = structured.get('secondaryTranslation')
        if primary_translation and primary_translation.strip() == effective_source:
            primary_translation = None
        if secondary_translation and secondary_translation.strip() == effective_source:
            secondary_translation = None
        if _is_same_language(original_language, primary_language):
            primary_translation = None
        if _is_same_language(original_language, secondary_language):
            secondary_translation = None
        elif not _is_same_language(original_language, primary_language):
            secondary_translation = None
        if not secondary_language:
            secondary_translation = None

        _log_timing('translate_request_completed',
                    model=model,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    correction_applied=correction_applied)
        return jsonify({
            'originalLanguage': original_language,
            'rawTranscript': text,
            'correctedTranscript': corrected,
            'correctionApplied': correction_applied,
            'primaryTranslation': primary_translation,
            'secondaryTranslation': secondary_translation,
        })
    except Exception as exc:
        _log_timing('translate_request_failed',
                    model=model,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    error=str(exc))
        return jsonify({'error': str(exc)}), 500


_REFINE_SYSTEM_PROMPT = ('你是会议字幕的术语校正器。输入是若干已经定格的字幕片段，可能来自原文转写，也可能'
                         '来自机器翻译。你的唯一职责是：按 keywords 给出的正确写法，修正片段里明显是同音、'
                         '音近、错误分词或错误音译造成的写法错误。'
                         '严格约束：不得改写语义，不得增删内容，不得调整语序，不得翻译，不得修改标点风格，'
                         '不得"顺便"润色。片段用什么语言写的就保持什么语言。'
                         '只有当某处确实对应 keywords 中的某个词、且当前写法明显是识别错误时才改；'
                         '拿不准就原样返回并把 changed 置为 false。'
                         '必须原样返回所有输入片段的 id，数量和顺序都不变。严格输出给定 JSON schema。')

_REFINE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['segments'],
    'properties': {
        'segments': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['id', 'text', 'changed'],
                'properties': {
                    'id': {
                        'type': 'string'
                    },
                    'text': {
                        'type': 'string'
                    },
                    'changed': {
                        'type': 'boolean'
                    },
                },
            },
        },
    },
}


def _parse_refine_segments(raw_segments):
    """清洗前端提交的待校正片段，超限的直接截断。"""
    segments = []
    total_chars = 0
    for raw in raw_segments or []:
        if not isinstance(raw, dict):
            continue
        segment_id = str(raw.get('id') or '').strip()
        text = str(raw.get('text') or '').strip()
        if not segment_id or not text:
            continue
        if total_chars + len(text) > REFINE_MAX_CHARS:
            break
        segments.append({
            'id': segment_id,
            'lang': _normalize_language_code(raw.get('lang')) or 'auto',
            'text': text,
        })
        total_chars += len(text)
        if len(segments) >= REFINE_MAX_SEGMENTS:
            break
    return segments


@main_bp.route('/api/refine-transcript', methods=['POST'])
def refine_transcript():
    """按术语表批量校正已定格的字幕片段（原文和译文都走这里）。

    Realtime transcription session 和 Realtime Translation session 都注入不了
    术语表（translation session 连 keywords 字段都不收），所以术语准确性靠这条
    后置文本链路补。前端先显示 realtime 原始结果，本接口返回后再原位替换。
    """
    try:
        api_key = _get_api_key()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 500

    data = request.get_json() or {}
    segments = _parse_refine_segments(data.get('segments'))
    if not segments:
        return jsonify({'segments': []})

    keywords = _normalize_keyword_list(data.get('keywords'))
    if not keywords:
        # 没有术语表就没有校正依据，直接原样返回，省掉一次调用。
        return jsonify({'segments': []})

    model = data.get('model') or REFINE_MODEL
    reasoning = _build_translation_reasoning(
        model, data.get('reasoningEffort', REFINE_REASONING_EFFORT))

    user_content = json.dumps(
        {
            'keywords': keywords,
            'meeting_context': str(data.get('context') or '')[:1000],
            'segments': segments,
        },
        ensure_ascii=False)

    payload = {
        'model':
        model,
        'input': [{
            'role': 'system',
            'content': _REFINE_SYSTEM_PROMPT
        }, {
            'role': 'user',
            'content': user_content
        }],
        'text': {
            'format': {
                'type': 'json_schema',
                'name': 'TranscriptTermFix',
                'schema': _REFINE_SCHEMA,
                'strict': True,
            }
        },
    }
    if reasoning:
        payload['reasoning'] = reasoning

    started_at = time.perf_counter()
    try:
        resp = requests.post('https://api.openai.com/v1/responses',
                             headers={
                                 'Authorization': f'Bearer {api_key}',
                                 'Content-Type': 'application/json',
                             },
                             json=payload,
                             timeout=60)
        if not resp.ok:
            print(
                f'[refine-transcript] OpenAI error: {resp.status_code} {resp.text[:300]}'
            )
            return jsonify({'error': resp.text}), resp.status_code

        structured = _extract_structured_output(resp.json())
        if not isinstance(structured, dict):
            raise ValueError('Responses API 未返回有效的结构化校正结果')

        original_by_id = {item['id']: item['text'] for item in segments}
        refined = []
        for item in structured.get('segments') or []:
            if not isinstance(item, dict):
                continue
            segment_id = str(item.get('id') or '')
            text = str(item.get('text') or '').strip()
            original = original_by_id.get(segment_id)
            # 只回传真正变了的片段：模型偶尔会把 changed 标反，以文本比对为准。
            if original is None or not text or text == original:
                continue
            refined.append({'id': segment_id, 'text': text})

        _log_timing('refine_request_completed',
                    model=model,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    segments=len(segments),
                    keyword_count=len(keywords),
                    changed=len(refined))
        return jsonify({'segments': refined})

    except Exception as exc:
        _log_timing('refine_request_failed',
                    model=model,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    error=str(exc))
        return jsonify({'error': str(exc)}), 500
