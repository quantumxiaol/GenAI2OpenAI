import base64
import logging
import os

import requests
from flask import Blueprint, current_app, jsonify, request

from ..auth import get_request_access_token
from ..config import GENAI_IMAGE_STATIC_URL, GENAI_URL, build_genai_headers
from ..upstream import _is_token_expired_error
from ..usage import record_usage
from . import SETTINGS_CONFIG_KEY

logger = logging.getLogger("genai-proxy")

image_gen_bp = Blueprint("image_gen", __name__)

# 生成图片的本地留存目录（启动目录下，已 gitignore）：
# 学校静态服务的图片可能有时效，代理下载保存后长期可用。
GENERATED_IMAGE_DIR = os.path.join(os.getcwd(), "generated_images")

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


def download_and_save_image(url):
    """下载生成图片并保存到本地留存目录。

    Returns:
        tuple[str | None, bytes | None]: (本地保存路径, 图片字节)；下载或保存
        失败返回 (None, None)，不阻断 url 模式的正常返回。
    """
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            logger.warning("download generated image failed: HTTP %s", resp.status_code)
            return None, None
        image_bytes = resp.content
        os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
        # basename 防路径穿越；上游文件名本身是 hash 命名，直接复用。
        filename = os.path.basename(url.rstrip("/").rsplit("/", 1)[-1]) or "image.png"
        local_path = os.path.join(GENERATED_IMAGE_DIR, filename)
        with open(local_path, "wb") as fp:
            fp.write(image_bytes)
        return local_path, image_bytes
    except (requests.RequestException, OSError) as exc:
        logger.warning("download/save generated image failed: %s", exc)
        return None, None


def _post_image(payload, settings, access_token):
    """发生图请求到上游 GenAI（同步 JSON，非流式）。"""
    return requests.post(
        GENAI_URL,
        headers=build_genai_headers(settings, access_token),
        json=payload,
        timeout=300,
    )


@image_gen_bp.route('/v1/images/generations', methods=['POST'])
def images_generations():
    """OpenAI Images API 兼容的图像生成接口。

    上游就是 /htk/chat/start/chat（aiType 为图像模型、同步返回 JSON），
    参数平铺：size/quality/count/style/outputFormat/imageBase64（垫图）。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    try:
        # 文生图只受 GENAI_DISABLE_IMAGE_GEN 控制，与 GENAI_DISABLE_AZURE（聊天）解耦：
        # 禁 Azure 聊天模型的同时可单独保留生图。
        if settings.disable_image_gen:
            return jsonify({"error": "Image generation is disabled on this server (GENAI_DISABLE_IMAGE_GEN)."}), 400
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

        response = _post_image(payload, settings, access_token)
        try:
            result = response.json()
        except ValueError:
            return jsonify({"error": f"GenAI upstream error: HTTP {response.status_code}"}), 502

        # token 过期自动 CAS 重登录并重试一次（与 chat 链路一致；仅服务端 token 场景，
        # 请求级 access_token 过期时由客户端自己更新凭据，不代为刷新）。
        if isinstance(result, dict) and access_token is None and settings.account:
            err_text = str(result.get("errMsg") or result.get("message") or "")
            if response.status_code == 401 or _is_token_expired_error(err_text):
                from ..auth import refresh_token
                if refresh_token(settings, failed_token=settings.token):
                    logger.info("image gen token expired; refreshed via CAS login, retrying once")
                    response = _post_image(payload, settings, access_token)
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
        # OpenAI 风格 response_format：url（默认，直接给学校图片服务地址）/
        # b64_json（代理下载转 base64，客户端不依赖学校图片服务的可访问性）。
        response_format = req_data.get("response_format", "url")
        for item in result.get("data", []):
            if isinstance(item, dict) and item.get("url"):
                abs_url = to_absolute_image_url(item["url"])
                # 每次生图都下载留存到本地目录（学校静态图片可能有时效）；
                # 下载失败不阻断 url 模式，b64_json 模式则必须成功。
                local_path, image_bytes = download_and_save_image(abs_url)
                if local_path:
                    logger.info("generated image saved to %s", local_path)
                if response_format == "b64_json":
                    if image_bytes is None:
                        return jsonify({"error": "Failed to download generated image for b64_json encoding"}), 502
                    data.append({"b64_json": base64.b64encode(image_bytes).decode("ascii")})
                else:
                    data.append({"url": abs_url})

        # 用量记账：上游 usage 可能是 dict（取 total_tokens）或纯数字。
        usage = result.get("usage")
        total_tokens = None
        if isinstance(usage, dict):
            total_tokens = usage.get("total_tokens")
        elif isinstance(usage, (int, float)):
            total_tokens = int(usage)
        record_usage(model, total_tokens=total_tokens, kind="image")

        body = {
            "created": result.get("created"),
            "data": data,
        }
        # 上游给的元数据（usage 为真实 token 数）原样透出。
        for key in ("background", "output_format", "quality", "size", "usage"):
            if result.get(key) is not None:
                body[key] = result[key]
        return jsonify(body)

    except Exception as e:
        logger.exception("images_generations failed")
        return jsonify({"error": str(e)}), 500
