import logging

import requests
from flask import Blueprint, current_app, jsonify, request

from ..auth import get_request_access_token
from ..config import GENAI_IMAGE_STATIC_URL, GENAI_URL, build_genai_headers
from . import SETTINGS_CONFIG_KEY

logger = logging.getLogger("genai-proxy")

image_gen_bp = Blueprint("image_gen", __name__)

# 对外模型名 → 上游 aiType（上游命名与页面展示名不一致，在此归一）。
IMAGE_MODEL_MAP = {
    "gpt-image-2.5": "GPT-Image-2",
    "gpt-image-2.5-sunburst": "GPT-Image-2",
    "gpt-image-1.5": "gpt-image-1.5",
    "gpt-image-2.5-flare": "gpt-image-1.5",
}


def to_absolute_image_url(url):
    """上游返回相对路径，补全为 genaipic 静态服务绝对地址。"""
    if not isinstance(url, str):
        return url
    if url.startswith(("http://", "https://")):
        return url
    return f"{GENAI_IMAGE_STATIC_URL}{url.lstrip('/')}"


@image_gen_bp.route('/v1/images/generations', methods=['POST'])
def images_generations():
    """OpenAI Images API 兼容的图像生成接口。

    上游就是 /htk/chat/start/chat（aiType 为图像模型、同步返回 JSON），
    参数平铺：size/quality/count/style/outputFormat/imageBase64（垫图）。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    try:
        req_data = request.get_json(silent=True)
        if not req_data or not req_data.get("prompt"):
            return jsonify({"error": "Missing or invalid JSON body / missing prompt field"}), 400

        prompt = req_data["prompt"]
        model = req_data.get("model", "gpt-image-2.5")
        ai_type = IMAGE_MODEL_MAP.get(str(model).lower())
        if ai_type is None:
            return jsonify({
                "error": f"Unknown image model: {model}. Available: {sorted(IMAGE_MODEL_MAP)}"
            }), 400

        # 参考图（垫图）：支持 base64 原文或 data URL，上游要求纯 base64 字符串。
        image_base64 = req_data.get("image_base64") or req_data.get("image")
        if isinstance(image_base64, str) and image_base64.startswith("data:"):
            image_base64 = image_base64.split(",", 1)[1]

        payload = {
            "chatInfo": prompt,
            "type": "3",
            "aiType": ai_type,
            "aiSecType": "1",
            "size": req_data.get("size", "1024x1024"),
            "quality": req_data.get("quality", "low"),
            "count": str(req_data.get("n", 1)),
            "style": req_data.get("style", "vivid"),
            "outputFormat": req_data.get("output_format", "png"),
            "outputCompression": None,
            "imageBase64": image_base64 or None,
            "rootAiType": "azure",
        }
        access_token = get_request_access_token(settings)
        logger.debug("image generation request: model=%s size=%s quality=%s n=%s has_ref=%s",
                     model, payload["size"], payload["quality"], payload["count"], bool(payload["imageBase64"]))

        response = requests.post(
            GENAI_URL,
            headers=build_genai_headers(settings, access_token),
            json=payload,
            timeout=300,
        )
        try:
            result = response.json()
        except ValueError:
            return jsonify({"error": f"GenAI upstream error: HTTP {response.status_code}"}), 502

        # 上游错误包：{"code":500,"errMsg":"..."}
        if isinstance(result, dict) and result.get("code") not in (None, 0, 200, "200"):
            err_msg = result.get("errMsg") or result.get("message") or result
            return jsonify({"error": f"GenAI upstream error {result.get('code')}: {err_msg}"}), 502
        if response.status_code != 200:
            return jsonify({"error": f"GenAI upstream error: HTTP {response.status_code}", "detail": result}), 502

        data = []
        for item in result.get("data", []):
            if isinstance(item, dict) and item.get("url"):
                data.append({"url": to_absolute_image_url(item["url"])})

        body = {
            "created": result.get("created"),
            "data": data,
        }
        # 上游给了真实 usage（文生图 token 明细），原样透出。
        if result.get("usage"):
            body["usage"] = result["usage"]
        return jsonify(body)

    except Exception as e:
        logger.exception("images_generations failed")
        return jsonify({"error": str(e)}), 500
