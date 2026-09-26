import base64
import hashlib
import logging
import mimetypes
import os
from urllib.parse import urlparse

import requests

from .config import (
    GENAI_IMAGE_STATIC_URL,
    GENAI_UPLOAD_URL,
    Settings,
    build_genai_upload_headers,
)

logger = logging.getLogger("genai-proxy")

# 进程内图片去重缓存：image_sha256 -> {imageUrl, width, height}
IMAGE_UPLOAD_CACHE = {}


def guess_filename_from_url(image_url):
    """从 URL 推断文件名。"""
    path = urlparse(image_url).path
    filename = os.path.basename(path) or "image"
    if "." not in filename:
        filename += ".jpg"
    return filename


def read_image_from_data_url(data_url):
    """解析 data URL，返回 (bytes, mime_type, filename)。"""
    header, encoded = data_url.split(",", 1)
    mime_type = "image/jpeg"
    if header.startswith("data:"):
        mime_type = header[5:].split(";")[0] or mime_type
    extension = mimetypes.guess_extension(mime_type) or ".jpg"
    image_bytes = base64.b64decode(encoded)
    return image_bytes, mime_type, f"image{extension}"


def fetch_image_bytes(image_url):
    """下载远端图片，返回 (bytes, mime_type, filename)。"""
    response = requests.get(image_url, timeout=60)
    response.raise_for_status()
    mime_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
    filename = guess_filename_from_url(image_url)
    return response.content, mime_type, filename


def upload_image_to_genai(image_bytes, filename, mime_type, settings: Settings, access_token=None):
    """上传图片到 GenAI 图片服务，返回上游需要的 URL 与尺寸信息。"""
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    cached_payload = IMAGE_UPLOAD_CACHE.get(image_hash)
    if cached_payload:
        logger.debug("Image cache hit: sha256=%s url=%s", image_hash, cached_payload.get("imageUrl"))
        return cached_payload

    logger.debug("Image cache miss: sha256=%s", image_hash)
    files = {
        "file": (filename, image_bytes, mime_type),
    }
    data = {
        "biz": "temp",
        "uploadType": "local",
    }
    logger.debug("Uploading image to GenAI: filename=%s mime=%s bytes=%s", filename, mime_type, len(image_bytes))
    response = requests.post(
        GENAI_UPLOAD_URL,
        headers=build_genai_upload_headers(settings, access_token),
        files=files,
        data=data,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    logger.debug("Upload response: %s", payload)
    if not payload.get("success") or not isinstance(payload.get("result"), dict):
        raise RuntimeError(f"Image upload failed: {payload}")

    result = payload["result"]
    relative_url = result.get("url")
    if not relative_url:
        raise RuntimeError("Image upload failed: missing result.url")
    image_url = relative_url
    if not image_url.startswith("http://") and not image_url.startswith("https://"):
        image_url = f"{GENAI_IMAGE_STATIC_URL}{relative_url}"

    payload = {
        "imageUrl": image_url,
        "width": result.get("width"),
        "height": result.get("height"),
    }
    IMAGE_UPLOAD_CACHE[image_hash] = payload
    logger.debug("Image cached: sha256=%s url=%s", image_hash, payload.get("imageUrl"))
    return payload


def parse_image_input_from_message(message):
    """从单条 OpenAI user message 中提取图片输入（URL 或 data URL）。

    兼容的 part 形态：
    - `image_url` / `input_image`（OpenAI 标准）
    - `file`（AI SDK v5：{"type":"file","file":{"data":<base64>,"mediaType":"image/png"}}）
    - `image`（{"type":"image","image"/"url"/"data": ...}）
    """
    if not isinstance(message, dict) or message.get("role") != "user":
        return None

    content = message.get("content")
    if not isinstance(content, list):
        return None

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")

        # AI SDK v5 的 file 部分。
        if part_type == "file" and isinstance(part.get("file"), dict):
            file_info = part["file"]
            data = file_info.get("data")
            media_type = file_info.get("mediaType") or file_info.get("mimeType") or "image/jpeg"
            if data:
                if data.startswith("data:"):
                    return data
                return f"data:{media_type};base64,{data}"
            if file_info.get("url"):
                return file_info["url"]

        # {"type": "image", ...} 变体。
        if part_type == "image":
            if isinstance(part.get("image"), str):
                return part["image"]
            if isinstance(part.get("url"), str):
                return part["url"]
            if isinstance(part.get("data"), str):
                media_type = part.get("mediaType") or part.get("mimeType") or "image/jpeg"
                data = part["data"]
                if data.startswith("data:"):
                    return data
                return f"data:{media_type};base64,{data}"

        if part_type not in {"image_url", "input_image"}:
            continue

        if isinstance(part.get("image_url"), dict):
            url_value = part["image_url"].get("url")
            if url_value:
                return url_value
        if isinstance(part.get("image_url"), str):
            return part.get("image_url")
        if isinstance(part.get("url"), str):
            return part.get("url")

    return None


def prepare_image_payload(messages, model, settings: Settings, access_token=None):
    """从请求消息中准备上游所需图片参数（本地与 Azure 路由模型均已验证可用）。"""
    image_input = None
    for message in reversed(messages):
        image_input = parse_image_input_from_message(message)
        if image_input:
            break

    if not image_input:
        # 顺带记录实际收到的 part 类型，便于定位"客户端发了图但格式不认识"。
        part_types = [
            part.get("type")
            for msg in messages if isinstance(msg, dict) and msg.get("role") == "user"
            for part in (msg.get("content") if isinstance(msg.get("content"), list) else [])
            if isinstance(part, dict)
        ]
        logger.debug("No image input found in messages (part types seen: %s)", part_types)
        return None

    if image_input.startswith("data:"):
        image_bytes, mime_type, filename = read_image_from_data_url(image_input)
    else:
        image_bytes, mime_type, filename = fetch_image_bytes(image_input)

    image_payload = upload_image_to_genai(image_bytes, filename, mime_type, settings, access_token)
    logger.debug("Prepared image payload: %s", image_payload)
    return image_payload
