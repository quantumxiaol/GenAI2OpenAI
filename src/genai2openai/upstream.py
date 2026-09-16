import json
import logging

import requests

from .config import GENAI_URL, Settings, build_genai_headers
from .messages import convert_messages_to_genai_format
from .registry import resolve_model

logger = logging.getLogger("genai-proxy")


def extract_delta_from_genai(response_data):
    """从 GenAI 增量响应中提取正文和思维链字段。

    Args:
        response_data (dict): 单条 GenAI SSE 数据解析后的 JSON 对象。

    Returns:
        dict[str, str | None]: 包含 `reasoning` 与 `content` 两个字段；若缺失则
        返回 `None`。
    """
    try:
        if "choices" in response_data and len(response_data["choices"]) > 0:
            delta = response_data["choices"][0].get("delta", {})
            return {
                # 新版上游字段名为 reasoning_content，兼容旧版 reasoning。
                "reasoning": delta.get("reasoning_content") or delta.get("reasoning"),
                "content": delta.get("content"),
            }
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return {"reasoning": None, "content": None}


def stream_genai_events(messages, model, max_tokens, settings: Settings, access_token=None, image_payload=None):
    """调用 GenAI 流式接口并产出统一事件流。

    该函数是整个协议转换的底层入口，负责：
    1. 解析模型别名
    2. 调用上游 GenAI SSE 接口
    3. 将上游原始事件规范化为内部事件类型

    Args:
        messages (list[dict]): 发送给上游的消息列表。
        model (str): 调用方指定的模型名。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置（上游 token、请求头等）。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。

    Yields:
        dict: 统一事件对象，`type` 可能为 `delta`、`done`、`meta` 或 `error`。
    """
    upstream_model, root_ai_type = resolve_model(model)

    # 这里保持与网页端接近的请求体结构，避免上游校验差异。
    # chatInfo 取最后一条 user 消息，与网页端行为对齐（上游对空 chatInfo 可能不生成内容）。
    genai_data = {
        "chatInfo": convert_messages_to_genai_format(messages),
        "messages": messages,
        "type": "3",
        "stream": True,
        "aiType": upstream_model,
        "aiSecType": "1",
        "promptTokens": 0,
        "rootAiType": root_ai_type,
        "maxToken": max_tokens or 30000
    }
    if image_payload:
        genai_data.update(image_payload)

    logger.debug(
        "Upstream request prepared: model=%s rootAiType=%s stream=%s maxToken=%s has_image=%s message_count=%s",
        upstream_model,
        root_ai_type,
        genai_data.get("stream"),
        genai_data.get("maxToken"),
        bool(image_payload),
        len(messages) if isinstance(messages, list) else 0,
    )

    try:
        response = requests.post(
            GENAI_URL,
            headers=build_genai_headers(settings, access_token),
            json=genai_data,
            stream=True,
            timeout=60
        )

        if response.status_code != 200:
            logger.error("GenAI upstream HTTP error: %s", response.status_code)
            yield {
                "type": "error",
                "error": f"GenAI API error: {response.status_code}",
            }
            return

        finished = False
        for line in response.iter_lines():
            if finished:
                break

            if line:
                try:
                    line_str = line.decode('utf-8') if isinstance(line, bytes) else line

                    # 兼容标准 SSE 的 `data:` 前缀。
                    if line_str.startswith('data:'):
                        line_str = line_str[5:].strip()

                    # 新版上游在正常流末尾会追加 `data: [DONE]`。
                    if line_str == "[DONE]":
                        break

                    if line_str:
                        genai_json = json.loads(line_str)
                        logger.debug("Upstream SSE chunk keys: %s", list(genai_json.keys()))

                        # 上游错误以 {"code":500,"errMsg":"..."} 数据行返回，必须显式透出，
                        # 否则会被静默吞掉表现为空响应。
                        if "choices" not in genai_json and genai_json.get("code") not in (None, 0, 200, "200"):
                            err_msg = genai_json.get("errMsg") or genai_json.get("message") or genai_json
                            yield {
                                "type": "error",
                                "error": f"GenAI upstream error {genai_json.get('code')}: {err_msg}",
                            }
                            return

                        # 上游偶尔会返回补充元数据，先保留为内部 meta 事件。
                        if genai_json.get("other"):
                            yield {
                                "type": "meta",
                                "other": genai_json.get("other"),
                            }

                        # 只要上游给出 finish_reason，就视为本轮流式输出结束。
                        if "choices" in genai_json and len(genai_json["choices"]) > 0:
                            choice = genai_json["choices"][0]
                            if choice.get("finish_reason") is not None:
                                finished = True

                        if finished:
                            yield {
                                "type": "done",
                                "upstream_model": genai_json.get("model"),
                            }
                            break

                        delta = extract_delta_from_genai(genai_json)
                        reasoning = delta.get("reasoning")
                        content = delta.get("content")
                        # 内部统一拆成 reasoning 和 content，便于上层复用。
                        if reasoning is not None or content is not None:
                            yield {
                                "type": "delta",
                                "upstream_model": genai_json.get("model"),
                                "reasoning": reasoning,
                                "content": content,
                            }

                except json.JSONDecodeError:
                    pass

        yield {
            "type": "done",
            "upstream_model": None,
        }

    except Exception as e:
        logger.exception("stream_genai_events failed")
        # 流式链路统一转成 error 事件，交由上层协议各自包装。
        yield {
            "type": "error",
            "error": str(e),
        }


def collect_genai_response(messages, model, max_tokens, settings: Settings, access_token=None, image_payload=None):
    """收集完整响应并聚合为非流式结果。

    Args:
        messages (list[dict]): OpenAI 风格消息列表。
        model (str): 调用方传入的模型名。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。

    Returns:
        dict[str, str | None]: 聚合后的正文、思维链和上游模型名。

    Raises:
        RuntimeError: 当上游事件流返回错误事件时抛出。
    """
    content_parts = []
    reasoning_parts = []
    upstream_model = None

    for event in stream_genai_events(messages, model, max_tokens, settings, access_token, image_payload):
        if event["type"] == "error":
            raise RuntimeError(event["error"])
        if event["type"] == "delta":
            upstream_model = event.get("upstream_model") or upstream_model
            if event.get("reasoning"):
                reasoning_parts.append(event["reasoning"])
            if event.get("content"):
                content_parts.append(event["content"])
        if event["type"] == "done":
            break

    return {
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
        "upstream_model": upstream_model,
    }
