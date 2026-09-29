from flask import Blueprint, current_app, jsonify, request

from ..registry import MODEL_SPECS
from ..usage import summarize_usage
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


@meta_bp.route('/v1/usage', methods=['GET'])
def usage_summary():
    """返回最近 N 天的 token 用量聚合（总量、按模型、按天 × 模型）。

    Query Args:
        days (int): 统计窗口天数（含今天），默认 30，上限 3650。

    Returns:
        Response: 聚合 JSON；`estimated_requests` 为上游未返回 token 数的
        请求条数（消耗存在但无法计量）。
    """
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    days = max(1, min(days, 3650))
    return jsonify(summarize_usage(days))
