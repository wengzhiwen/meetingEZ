"""
MeetingEZ 路由模块
提供静态文件服务和基础页面
"""
import os

from flask import Blueprint, jsonify, render_template, send_from_directory

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """主页面"""
    return render_template('index.html')


@main_bp.route('/health')
def health():
    """健康检查端点"""
    return jsonify({'status': 'healthy', 'service': 'MeetingEZ', 'version': '0.1.0'})


@main_bp.route('/favicon.ico')
def favicon():
    """网站图标"""
    return send_from_directory(os.path.join(main_bp.root_path, 'static'),
                               'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')
