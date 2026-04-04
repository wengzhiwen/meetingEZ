"""
MeetingEZ Flask 应用初始化模块
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

# 加载环境变量
load_dotenv()

logger = logging.getLogger(__name__)


def _recover_orphaned_processing():
    """清理服务器重启后残留的 _processing.lock，让会议恢复到可手动处理的状态。"""
    from meeting_agent.config import Config, ASR_STATE_FILE, PROCESSING_LOCK_FILE

    config = Config()
    config.ensure_dirs()

    # 收集所有项目目录
    project_dirs = []
    if config.projects_dir:
        for d in config.projects_dir.iterdir():
            if d.is_dir() and not d.name.startswith('.'):
                project_dirs.append(d)
    else:
        project_dirs.append(config.meetings_dir)

    recovered = 0
    for project_dir in project_dirs:
        for meeting_dir in project_dir.iterdir():
            if not meeting_dir.is_dir() or meeting_dir.name.startswith('.'):
                continue
            lock_file = meeting_dir / PROCESSING_LOCK_FILE
            if not lock_file.exists():
                continue

            # 清理锁文件
            try:
                lock_file.unlink()
            except OSError:
                continue

            # 写入中断错误，让前端显示"处理失败"并允许用户重试
            error_file = meeting_dir / '_processing.error'
            try:
                error_file.write_text('服务器重启，处理被中断，请重新处理', encoding='utf-8')
            except OSError:
                pass

            # 如果 ASR 处于 running 状态，回退到 failed 以便重试
            asr_state_file = meeting_dir / ASR_STATE_FILE
            if asr_state_file.exists():
                try:
                    state = json.loads(asr_state_file.read_text(encoding='utf-8'))
                    if state.get('status') == 'running':
                        state['status'] = 'failed'
                        state['last_error'] = '服务器重启，处理被中断'
                        asr_state_file.write_text(
                            json.dumps(state, ensure_ascii=False, indent=2),
                            encoding='utf-8')
                except (OSError, json.JSONDecodeError):
                    pass

            recovered += 1

    if recovered:
        logger.info('已恢复 %d 个因服务器重启而中断的会议', recovered)


def create_app():
    """创建并配置 Flask 应用"""
    # 配置 meeting_agent 日志输出到 stderr（Gunicorn 会捕获）
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _agent_logger = logging.getLogger("meeting_agent")
    _agent_logger.setLevel(logging.INFO)
    _agent_logger.addHandler(_handler)

    app = Flask(__name__,
                template_folder='../templates',
                static_folder='../app/static')

    # 基础配置
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY',
                                         'dev-secret-key-change-in-production')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH',
                                                     16777216))  # 16MB

    # 注册路由
    from app.routes import main_bp
    app.register_blueprint(main_bp)

    # 清理服务器重启后残留的锁文件
    _recover_orphaned_processing()

    return app
