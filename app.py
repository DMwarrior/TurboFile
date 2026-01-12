#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web file transfer system - entrypoint.
"""

import os
import sys

from turbofile import create_app
from turbofile.extensions import socketio
from turbofile.core import TURBOFILE_HOST_IP, BASE_DIR

app = create_app()

if __name__ == '__main__':
    # Ensure the templates directory exists.
    os.makedirs(os.path.join(BASE_DIR, 'templates'), exist_ok=True)

    is_production = len(sys.argv) > 1 and sys.argv[1] == '--production'

    print("🚀 启动Web文件传输系统...")
    print(f"📱 访问地址: http://{TURBOFILE_HOST_IP}:5000")
    print("🔧 确保所有服务器SSH密钥已配置")

    if is_production:
        print("🏭 生产模式启动")
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    else:
        print("🛠️  开发模式启动")
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
