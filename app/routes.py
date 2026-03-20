"""
MeetingEZ 路由模块
提供静态文件服务、Realtime session 签发、翻译代理
所有页面和 API 受 ACCESS_CODE 保护
"""
import json
import os
import time

import requests
from flask import (Blueprint, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

main_bp = Blueprint('main', __name__)
TRANSCRIPTION_MODEL = 'gpt-4o-transcribe'
TRANSLATION_MODEL = os.getenv('TRANSLATION_MODEL', 'gpt-5.4-mini-2026-03-17')
TRANSLATION_REASONING_EFFORT = os.getenv('TRANSLATION_REASONING_EFFORT', 'low').strip()


def _normalize_language_code(value):
    """归一化语言代码，便于 zh / zh-TW 这类比较"""
    return (value or '').strip().lower().split('-')[0]


def _is_same_language(left, right):
    """宽松比较语言代码"""
    left_normalized = _normalize_language_code(left)
    right_normalized = _normalize_language_code(right)
    return bool(left_normalized and right_normalized and left_normalized == right_normalized)


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
        entries.append({
            'canonical': parts[0],
            'aliases': parts[1:]
        })
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


# ---- 认证 ----

@main_bp.before_request
def require_auth():
    """所有请求前检查登录状态，未登录则拦截"""
    access_code = os.getenv('ACCESS_CODE', '').strip()
    if not access_code:
        return

    exempt = ('main.login', 'main.health', 'main.favicon')
    if request.endpoint in exempt or (request.endpoint and request.endpoint.startswith('static')):
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
    """主页面"""
    model_info = {
        'transcription': {
            'purpose': '实时转写',
            'api': 'Realtime API',
            'model': TRANSCRIPTION_MODEL
        },
        'translation': {
            'purpose': '后置翻译',
            'api': 'Responses API',
            'model': TRANSLATION_MODEL,
            'reasoning_effort': _build_translation_reasoning(
                TRANSLATION_MODEL,
                TRANSLATION_REASONING_EFFORT
            )
        }
    }
    return render_template('index.html', model_info=model_info)


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


# ---- API ----

@main_bp.route('/api/test-connection', methods=['POST'])
def test_connection():
    """测试 OpenAI API 连接"""
    try:
        api_key = _get_api_key()
        resp = requests.get(
            'https://api.openai.com/v1/models',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10
        )
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
        resp = requests.post(
            'https://api.openai.com/v1/realtime/client_secrets',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={'session': session_config},
            timeout=15
        )

        if not resp.ok:
            print(f'[realtime-session] OpenAI error: {resp.status_code} {resp.text}')
            return jsonify({'error': resp.text}), resp.status_code

        session_data = resp.json()
        client_secret = session_data.get('value')
        expires_at = session_data.get('expires_at')

        if not client_secret:
            client_secret = session_data.get('client_secret', {}).get('value')
            expires_at = expires_at or session_data.get('client_secret', {}).get('expires_at')

        print(f'[realtime-session] Session created: {json.dumps(session_data, indent=2)[:500]}')
        _log_timing(
            'realtime_session_created',
            elapsed_ms=round((time.perf_counter() - session_started_at) * 1000, 1),
            language=language,
            has_prompt=bool(prompt),
            turn_detection=session_config['audio']['input']['turn_detection']
        )
        return jsonify({
            'clientSecret': client_secret,
            'expiresAt': expires_at,
            'session': session_data.get('session', {})
        })

    except Exception as e:
        _log_timing(
            'realtime_session_failed',
            elapsed_ms=round((time.perf_counter() - session_started_at) * 1000, 1),
            error=str(e)
        )
        return jsonify({'error': str(e)}), 500


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
    original_language_hint = data.get('originalLanguageHint', primary_language)
    context = data.get('context', '')
    enable_correction = _coerce_bool(data.get('enableCorrection'), default=False)
    enable_glossary = _coerce_bool(data.get('enableGlossary'), default=False)
    glossary_entries = _parse_glossary(data.get('glossary', '')) if enable_glossary else []

    system_prompt = (
        '你是实时会议字幕的后置语言处理助手，负责两件事：\n'
        '1. 在允许时，对 ASR 原始转写做轻量智能修正\n'
        '2. 输出双向翻译\n\n'
        '## 输入\n'
        '- primary_language: 第一语言（如 zh）\n'
        '- secondary_language: 第二语言（如 ja）\n'
        '- current_text: 当前需要处理的 ASR 原始文本\n'
        '- enable_correction: 是否启用智能修正\n'
        '- enable_glossary: 是否启用专有名词表\n'
        '- glossary_entries: 术语表，包含标准写法和别名\n'
        '- recent_context: 最近上下文\n\n'
        '## 智能修正规则\n'
        '- 仅在 enable_correction=true 时输出 correctedTranscript\n'
        '- 只允许修正明显 ASR 错误、术语误识别、轻度断句和标点\n'
        '- 不允许改写说话意图，不允许补充未说出的信息，不允许总结替代原句\n'
        '- 如果没有明确证据，不要擅自修改\n'
        '- 如果 enable_glossary=true，优先将术语修正为 glossary_entries 中的标准写法\n\n'
        '## 规则\n'
        '- 翻译应基于 correctedTranscript（如果启用了智能修正），否则基于 current_text\n'
        '- 情况A: 文本是第一语言 => primaryTranslation = null, secondaryTranslation = 翻译成第二语言\n'
        '- 情况B: 文本是第二语言 => primaryTranslation = 翻译成第一语言, secondaryTranslation = null\n'
        '- 情况C: 文本是其他语言 => primaryTranslation = 翻译成第一语言, secondaryTranslation = null\n'
        '绝对不要输出与原文同语种的“翻译”。若目标语言与原文同语种，对应字段必须是 null。\n'
        'originalLanguage 必须是你判定的原文语言，而不是目标语言。\n\n'
        '输出严格的 JSON，不要添加任何解释。'
    )

    user_content = json.dumps({
        'task': 'bidirectional_translation',
        'primary_language': primary_language,
        'secondary_language': secondary_language,
        'original_language_hint': original_language_hint,
        'enable_correction': enable_correction,
        'enable_glossary': enable_glossary,
        'glossary_entries': glossary_entries,
        'recent_context': context,
        'current_text': text
    })

    json_schema = {
        'name': 'BidirectionalTranslation',
        'schema': {
            '$schema': 'http://json-schema.org/draft-07/schema#',
            'type': 'object',
            'additionalProperties': False,
            'required': [
                'originalLanguage',
                'correctedTranscript',
                'correctionApplied',
                'primaryTranslation',
                'secondaryTranslation'
            ],
            'properties': {
                'originalLanguage': {
                    'type': 'string',
                    'description': '判定的文本语言 ISO 代码'
                },
                'correctedTranscript': {
                    'description': '智能修正后的文本；若未启用修正则为 null',
                    'anyOf': [{'type': 'string'}, {'type': 'null'}]
                },
                'correctionApplied': {
                    'type': 'boolean',
                    'description': '是否真的修改了原始转写'
                },
                'primaryTranslation': {
                    'description': '翻译成第一语言，或 null',
                    'anyOf': [{'type': 'string'}, {'type': 'null'}]
                },
                'secondaryTranslation': {
                    'description': '翻译成第二语言，或 null',
                    'anyOf': [{'type': 'string'}, {'type': 'null'}]
                }
            }
        }
    }

    model = data.get('model', TRANSLATION_MODEL)
    reasoning_effort = data.get('reasoningEffort', TRANSLATION_REASONING_EFFORT)
    reasoning = _build_translation_reasoning(model, reasoning_effort)
    translate_started_at = time.perf_counter()
    _log_timing(
        'translate_request_received',
        model=model,
        reasoning_effort=reasoning.get('effort') if reasoning else None,
        text_chars=len(text),
        enable_correction=enable_correction,
        enable_glossary=enable_glossary,
        glossary_entries=len(glossary_entries),
        primary_language=primary_language,
        secondary_language=secondary_language,
        original_language_hint=original_language_hint,
        context_chars=len(context)
    )

    try:
        payload = {
            'model': model,
            'input': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content}
            ],
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

        resp = requests.post(
            'https://api.openai.com/v1/responses',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=30
        )
        _log_timing(
            'translate_openai_response',
            model=model,
            reasoning_effort=reasoning.get('effort') if reasoning else None,
            elapsed_ms=round((time.perf_counter() - translate_started_at) * 1000, 1),
            status_code=resp.status_code,
            request_id=resp.headers.get('x-request-id')
        )

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
        correction_applied = _coerce_bool(structured.get('correctionApplied'), default=False)
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

        if primary_translation and primary_translation.strip() == effective_source_text.strip():
            primary_translation = None
        if secondary_translation and secondary_translation.strip() == effective_source_text.strip():
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
        _log_timing(
            'translate_request_completed',
            model=model,
            reasoning_effort=reasoning.get('effort') if reasoning else None,
            elapsed_ms=round((time.perf_counter() - translate_started_at) * 1000, 1),
            original_language=structured['originalLanguage'],
            correction_applied=structured['correctionApplied'],
            has_primary_translation=bool(structured['primaryTranslation']),
            has_secondary_translation=bool(structured['secondaryTranslation'])
        )

        return jsonify(structured)

    except Exception as e:
        _log_timing(
            'translate_request_failed',
            model=model,
            reasoning_effort=reasoning.get('effort') if reasoning else None,
            elapsed_ms=round((time.perf_counter() - translate_started_at) * 1000, 1),
            error=str(e)
        )
        return jsonify({'error': str(e)}), 500
