from flask import Blueprint, current_app, jsonify

from ..registry import MODEL_SPECS
from . import SETTINGS_CONFIG_KEY

meta_bp = Blueprint("meta", __name__)


@meta_bp.route('/v1/models', methods=['GET'])
def list_models():
    """返回当前对外暴露的模型列表。

    Returns:
        Response: OpenAI `/v1/models` 兼容 JSON 响应。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    models = []
    for spec in MODEL_SPECS:
        # GENAI_DISABLE_AZURE 模式下不列出 Azure/GPT 模型。
        if settings.disable_azure and spec["root_ai_type"] == "azure":
            continue
        models.append({
            "id": spec["public_id"],
            "object": "model",
            "owned_by": "genai",
            "permission": []
        })

    return jsonify({"object": "list", "data": models})


@meta_bp.route('/health', methods=['GET'])
def health_check():
    """返回服务健康状态。

    Returns:
        tuple[Response, int]: 健康检查 JSON 响应与状态码。
    """
    return jsonify({"status": "ok"}), 200
