import hmac

from flask import Flask, jsonify, request
from flask_cors import CORS

from ..config import Settings

SETTINGS_CONFIG_KEY = "GENAI2OPENAI_SETTINGS"

from .chat import chat_bp  # noqa: E402
from .image_gen import image_gen_bp  # noqa: E402
from .meta import meta_bp  # noqa: E402
from .responses import responses_bp  # noqa: E402


def create_app(settings: Settings) -> Flask:
    """Flask 应用工厂：挂载全部 OpenAI 兼容路由。"""
    app = Flask(__name__)
    CORS(app)
    app.config[SETTINGS_CONFIG_KEY] = settings

    if settings.api_key:
        @app.before_request
        def _require_api_key():
            """API 鉴权：/health 与 CORS 预检放行，其余请求校验 Bearer/api-key 头。"""
            if request.path == "/health" or request.method == "OPTIONS":
                return None

            candidates = []
            authorization = request.headers.get("Authorization", "")
            if authorization.lower().startswith("bearer "):
                candidates.append(authorization[7:].strip())
            for header_name in ("api-key", "X-API-Key"):
                value = request.headers.get(header_name)
                if value:
                    candidates.append(value.strip())

            for candidate in candidates:
                if candidate and hmac.compare_digest(candidate, settings.api_key):
                    return None

            return jsonify({
                "error": {
                    "message": "Invalid or missing API key",
                    "type": "authentication_error",
                    "code": 401,
                }
            }), 401

    app.register_blueprint(chat_bp)
    app.register_blueprint(responses_bp)
    app.register_blueprint(meta_bp)
    app.register_blueprint(image_gen_bp)
    return app
