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


def download_and_save_image(url, save=True):
    """下载图片字节；save=True 时同时保存到本地留存目录。

    Args:
        url (str): 图片地址。
        save (bool): 是否保存到 generated_images/（垫图参考传 False，避免混入生成留存）。

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
        if not save:
            return None, image_bytes
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


def _run_image_generation(prompt, ai_type, model, ref_images, params, settings):
    """共用生图主流程：上游 payload 构造、调用、token 刷新重试、响应组装。

    generations（JSON）与 edits（multipart 文件上传）两个 OpenAI 端点共用。
    params 提供 size/quality/n/style/output_format/response_format（dict 或
    multipart form 均可，均有 .get(key, default)）。
    """
    # 与网页端一致：参考图最多 4 张；多张时提示词可用「图1」「图2」指代。
    if len(ref_images) > 4:
        return jsonify({"error": f"Too many reference images ({len(ref_images)}), max 4 (same as web UI)"}), 400

    payload = {
        "chatInfo": prompt,
        "type": "3",
        "aiType": ai_type,
        "aiSecType": "1",
        "size": params.get("size", "1024x1024"),
        "quality": params.get("quality", "low"),
        "count": str(params.get("n", 1)),
        "style": params.get("style", "vivid"),
        "outputFormat": params.get("output_format", "png"),
        "outputCompression": None,
        # 上游形态（网页端抓包）：单张 imageBase64=字符串，多张 imageBase64=null + imageBase64List。
        "imageBase64": ref_images[0] if len(ref_images) == 1 else None,
        "imageBase64List": ref_images if len(ref_images) > 1 else None,
        "rootAiType": "azure",
    }
    access_token = get_request_access_token(settings)
    logger.debug("image generation request: model=%s size=%s quality=%s n=%s ref_images=%d",
                 model, payload["size"], payload["quality"], payload["count"], len(ref_images))

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
    response_format = params.get("response_format", "url")
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


@image_gen_bp.route('/v1/images/generations', methods=['POST'])
def images_generations():
    """OpenAI Images API 兼容的图像生成接口（JSON 请求体）。

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

        # 参考图（垫图）：单张 image_base64/image_url，多张 image_base64_list/image_url_list。
        # base64 支持 data URL 前缀；URL 由代理下载转 base64（不写入生成留存）。
        ref_images = []
        single_b64 = req_data.get("image_base64") or req_data.get("image")
        if isinstance(single_b64, str):
            ref_images.append(single_b64)
        for item in req_data.get("image_base64_list") or []:
            if isinstance(item, str):
                ref_images.append(item)
        ref_images = [s.split(",", 1)[1] if s.startswith("data:") else s for s in ref_images]

        ref_urls = []
        if isinstance(req_data.get("image_url"), str):
            ref_urls.append(req_data["image_url"])
        for item in req_data.get("image_url_list") or []:
            if isinstance(item, str):
                ref_urls.append(item)
        for url in ref_urls:
            if not url.startswith(("http://", "https://")):
                return jsonify({"error": f"Invalid image_url (http/https required): {url}"}), 400
            _, image_bytes = download_and_save_image(url, save=False)
            if image_bytes is None:
                return jsonify({"error": f"Failed to download reference image from image_url: {url}"}), 400
            ref_images.append(base64.b64encode(image_bytes).decode("ascii"))

        return _run_image_generation(prompt, ai_type, model, ref_images, req_data, settings)

    except Exception as e:
        logger.exception("images_generations failed")
        return jsonify({"error": str(e)}), 500


@image_gen_bp.route('/v1/images/edits', methods=['POST'])
def images_edits():
    """OpenAI Images Edits 兼容端点：multipart/form-data 文件上传转垫图通道。

    `image` 字段上传参考图文件（可重复多个，兼容 `image[]` 写法），其余参数
    （prompt/model/n/size/quality/style/output_format/response_format）走表单字段。
    与 OpenAI 的差异：上游只有参考图生图能力，没有 mask 局部重绘——mask 文件
    会被忽略并记警告，语义为参考图重绘而非 inpainting。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    try:
        if settings.disable_image_gen:
            return jsonify({"error": "Image generation is disabled on this server (GENAI_DISABLE_IMAGE_GEN)."}), 400
        prompt = request.form.get("prompt")
        if not prompt:
            return jsonify({"error": "Missing prompt field"}), 400
        model = request.form.get("model", "gpt-image-2.5")
        ai_type = IMAGE_MODEL_MAP.get(str(model).lower())
        if ai_type is None:
            return jsonify({
                "error": f"Unknown image model: {model}. Available: {sorted(IMAGE_MODEL_MAP)}"
            }), 400

        ref_images = []
        for fp in request.files.getlist("image") + request.files.getlist("image[]"):
            image_bytes = fp.read()
            if image_bytes:
                ref_images.append(base64.b64encode(image_bytes).decode("ascii"))
        if not ref_images:
            return jsonify({
                "error": "Missing image file(s): upload at least one file in the 'image' field (multipart/form-data)"
            }), 400
        if request.files.get("mask"):
            logger.warning("edits mask is not supported by upstream (reference-image redraw only); ignoring mask")

        return _run_image_generation(prompt, ai_type, model, ref_images, request.form, settings)

    except Exception as e:
        logger.exception("images_edits failed")
        return jsonify({"error": str(e)}), 500
