import os
from flask import Flask

from .extensions import socketio, SOCKETIO_INIT_OPTIONS
from .core import secret_key, BASE_DIR
from .web import bp as web_bp


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, 'templates'),
        static_folder=os.path.join(BASE_DIR, 'static')
    )
    app.config['SECRET_KEY'] = secret_key
    socketio.init_app(app, **SOCKETIO_INIT_OPTIONS)
    app.register_blueprint(web_bp)
    return app
