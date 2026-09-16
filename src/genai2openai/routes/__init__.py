from flask import Flask
from flask_cors import CORS

from ..config import Settings

SETTINGS_CONFIG_KEY = "GENAI2OPENAI_SETTINGS"

from .chat import chat_bp  # noqa: E402
from .meta import meta_bp  # noqa: E402
from .responses import responses_bp  # noqa: E402


def create_app(settings: Settings) -> Flask:
    """Flask 应用工厂：挂载全部 OpenAI 兼容路由。"""
    app = Flask(__name__)
    CORS(app)
    app.config[SETTINGS_CONFIG_KEY] = settings

    app.register_blueprint(chat_bp)
    app.register_blueprint(responses_bp)
    app.register_blueprint(meta_bp)
    return app
