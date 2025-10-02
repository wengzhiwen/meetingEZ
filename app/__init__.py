"""
MeetingEZ Flask 应用初始化模块
"""
import os

from dotenv import load_dotenv
from flask import Flask

# 加载环境变量
load_dotenv()


def create_app():
    """创建并配置 Flask 应用"""
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

    return app
