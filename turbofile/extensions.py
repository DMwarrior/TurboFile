from flask_socketio import SocketIO

SOCKETIO_INIT_OPTIONS = {
    "cors_allowed_origins": "*",
    "async_mode": "threading",
    "ping_interval": 20,
    "ping_timeout": 120,
}

socketio = SocketIO(**SOCKETIO_INIT_OPTIONS)
