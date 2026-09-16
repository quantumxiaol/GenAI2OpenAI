import json
import logging
import uuid
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    stream_with_context,
)

from ..auth import get_request_access_token
from ..images import prepare_image_payload
from ..messages import convert_messages_to_genai_format, normalize_messages_for_genai
from ..tool_calling import (
    build_tool_calling_messages,
    get_request_tool_choice,
    get_request_tools,
    parse_tool_calls_from_content,
    should_enable_tools,
)
from ..upstream import collect_genai_response, stream_genai_events
from . import SETTINGS_CONFIG_KEY

logger = logging.getLogger("genai-proxy")

chat_bp = Blueprint("chat", __name__)


def build_chat_completion_payload(model, content, reasoning_content=None, tool_calls=None):
    """构建非流式 Chat Completions 响应。"""
    message = {
        "role": "assistant",
        "content": None if tool_calls else content,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    elif reasoning_content is not None:
        message["reasoning_content"] = reasoning_content

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content or ""),
            "total_tokens": len(content or "")
        }
    }


def stream_chat_completions_response(messages, model, max_tokens, settings, access_token=None, image_payload=None):
    """将内部事件流转换为 Chat Completions SSE。

    Args:
        messages (list[dict]): OpenAI 风格消息列表。
        model (str): 调用方传入的模型名。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。

    Yields:
        str: 符合 OpenAI Chat Completions SSE 格式的文本片段。
    """
    response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(datetime.now().timestamp())

    for event in stream_genai_events(messages, model, max_tokens, settings, access_token, image_payload):
        if event["type"] == "error":
            yield f"data: {json.dumps({'error': event['error']})}\n\n"
            return

        if event["type"] == "delta":
            delta_payload = {}
            # 对外沿用 DeepSeek 常见字段名 reasoning_content。
            if event.get("reasoning") is not None:
                delta_payload["reasoning_content"] = event["reasoning"]
            if event.get("content") is not None:
                delta_payload["content"] = event["content"]

            if delta_payload:
                openai_response = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta_payload,
                            "finish_reason": None
                        }
                    ]
                }
                yield f"data: {json.dumps(openai_response)}\n\n"

        if event["type"] == "done":
            final_response = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }
                ]
            }
            yield f"data: {json.dumps(final_response)}\n\n"
            yield "data: [DONE]\n\n"
            return


def stream_tool_calls_response(model, content, tool_calls):
    """将完整解析出的工具调用转换为 Chat Completions SSE。"""
    response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(datetime.now().timestamp())

    if tool_calls:
        delta_tool_calls = []
        for index, tool_call in enumerate(tool_calls):
            delta_tool_calls.append({
                "index": index,
                "id": tool_call["id"],
                "type": "function",
                "function": tool_call["function"],
            })

        tool_call_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": delta_tool_calls,
                    },
                    "finish_reason": None,
                }
            ]
        }
        yield f"data: {json.dumps(tool_call_chunk)}\n\n"
        finish_reason = "tool_calls"
    else:
        content_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": content,
                    },
                    "finish_reason": None,
                }
            ]
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"
        finish_reason = "stop"

    final_response = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ]
    }
    yield f"data: {json.dumps(final_response)}\n\n"
    yield "data: [DONE]\n\n"


SSE_HEADERS = {
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Content-Type': 'text/event-stream',
}


@chat_bp.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """处理 OpenAI Chat Completions 兼容请求。

    Returns:
        Response: Flask JSON 响应或 SSE 流式响应。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    try:
        # silent=True：畸形 JSON 返回 None，走下面的 400，而不是抛 BadRequest 变成 500。
        req_data = request.get_json(silent=True)
        logger.debug("/v1/chat/completions request received: stream=%s model=%s", (req_data or {}).get('stream'), (req_data or {}).get('model'))

        # Chat Completions 至少需要消息数组。
        if not req_data or 'messages' not in req_data:
            return jsonify({'error': 'Missing or invalid JSON body / missing messages field'}), 400

        messages = req_data.get('messages', [])
        model = req_data.get('model', 'kimi-k3')
        stream = req_data.get('stream', False)
        max_tokens = req_data.get('max_tokens', req_data.get('max_completion_tokens', 30000))
        tools = get_request_tools(req_data)
        tool_choice = get_request_tool_choice(req_data)
        access_token = get_request_access_token(settings)
        image_payload = prepare_image_payload(messages, model, settings, access_token)

        # 转换消息格式
        chat_info = convert_messages_to_genai_format(messages)

        if not chat_info:
            return jsonify({'error': 'No user message found'}), 400

        tools_enabled = should_enable_tools(tools, tool_choice)
        upstream_messages = normalize_messages_for_genai(messages)
        if tools_enabled:
            upstream_messages = build_tool_calling_messages(upstream_messages, tools, tool_choice)

        if stream:
            if tools_enabled:
                collected = collect_genai_response(upstream_messages, model, max_tokens, settings, access_token, image_payload)
                tool_calls = parse_tool_calls_from_content(collected["content"])
                return Response(
                    stream_with_context(stream_tool_calls_response(model, collected["content"], tool_calls)),
                    mimetype='text/event-stream',
                    headers=SSE_HEADERS,
                )

            return Response(
                stream_with_context(stream_chat_completions_response(upstream_messages, model, max_tokens, settings, access_token, image_payload)),
                mimetype='text/event-stream',
                headers=SSE_HEADERS,
            )

        # 非流式模式先完整收集，再一次性组装 OpenAI 响应体。
        collected = collect_genai_response(upstream_messages, model, max_tokens, settings, access_token, image_payload)
        tool_calls = parse_tool_calls_from_content(collected["content"]) if tools_enabled else []
        response = build_chat_completion_payload(
            model,
            collected["content"],
            collected["reasoning_content"],
            tool_calls,
        )
        return jsonify(response)

    except Exception as e:
        logger.exception("chat_completions failed")
        return jsonify({'error': str(e)}), 500
