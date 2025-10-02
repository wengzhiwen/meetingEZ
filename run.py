"""
MeetingEZ 应用启动文件
"""
import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5090))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'

    app.run(host=host, port=port, debug=debug)
