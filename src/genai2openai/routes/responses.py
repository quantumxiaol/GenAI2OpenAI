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
from ..registry import parse_model_flags
from ..upstream import collect_genai_response, stream_genai_events
from . import SETTINGS_CONFIG_KEY

logger = logging.getLogger("genai-proxy")

responses_bp = Blueprint("responses", __name__)


def build_response_input_messages(input_value):
    """将 Responses API 输入归一化为消息列表。

    当前仅处理文本输入，兼容字符串输入以及包含文本片段的数组输入。

    Args:
        input_value (str | list | Any): `/v1/responses` 的 `input` 字段。

    Returns:
        list[dict]: 可直接发给上游的消息列表。
    """
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]

    if isinstance(input_value, list):
        messages = []
        for item in input_value:
            if not isinstance(item, dict):
                continue

            role = item.get("role", "user")
            content = item.get("content")

            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue

            if isinstance(content, list):
                # 仅提取文本片段，忽略当前版本尚未支持的其他 item 类型。
                text_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if part_type in {"input_text", "text", "output_text"}:
                        text = part.get("text")
                        if text:
                            text_parts.append(text)
                if text_parts:
                    messages.append({"role": role, "content": "\n".join(text_parts)})

        return messages

    return []


def stream_responses_api(messages, model, max_tokens, settings, access_token=None, net_go=False, thinking=None,
                         chat_group_id=None):
    """将内部事件流转换为最小 Responses API SSE。

    Args:
        messages (list[dict]): 发送给上游的消息列表。
        model (str): 调用方传入的模型名。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。
        net_go (bool): 联网搜索开关。
        thinking (bool | None): 深度思考开关；None 跟随上游默认。

    Yields:
        str: 符合最小 Responses API SSE 格式的文本片段。
    """
    response_id = f"resp_{uuid.uuid4().hex}"
    created = int(datetime.now().timestamp())
    reasoning_id = f"rs_{uuid.uuid4().hex[:12]}"
    output_index = 0

    created_event = {
        "type": "response.created",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "status": "in_progress",
            "model": model,
        }
    }
    yield f"data: {json.dumps(created_event)}\n\n"

    for event in stream_genai_events(messages, model, max_tokens, settings, access_token,
                                     net_go=net_go, thinking=thinking, chat_group_id=chat_group_id):
        if event["type"] == "error":
            error_event = {
                "type": "response.failed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "failed",
                    "model": model,
                },
                "error": {
                    "message": event["error"],
                }
            }
            yield f"data: {json.dumps(error_event)}\n\n"
            yield "data: [DONE]\n\n"
            return

        if event["type"] == "delta":
            # Responses 接口将 reasoning 和正文拆成不同事件类型。
            if event.get("reasoning") is not None:
                reasoning_event = {
                    "type": "response.reasoning.delta",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item_id": reasoning_id,
                    "delta": event["reasoning"],
                }
                yield f"data: {json.dumps(reasoning_event)}\n\n"

            if event.get("content") is not None:
                content_event = {
                    "type": "response.output_text.delta",
                    "response_id": response_id,
                    "output_index": output_index,
                    "delta": event["content"],
                }
                yield f"data: {json.dumps(content_event)}\n\n"

        if event["type"] == "done":
            completed_event = {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "completed",
                    "model": model,
                }
            }
            yield f"data: {json.dumps(completed_event)}\n\n"
            yield "data: [DONE]\n\n"
            return


SSE_HEADERS = {
    # waitress 按 PEP 3333 禁止逐跳头（Connection/Keep-Alive/Transfer-Encoding），
    # 不得出现在这里；Content-Type 由 Response 的 mimetype 设置。
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
}


@responses_bp.route('/v1/responses', methods=['POST'])
def responses():
    """处理最小 OpenAI Responses 兼容请求。

    Returns:
        Response: Flask JSON 响应或 SSE 流式响应。
    """
    settings = current_app.config[SETTINGS_CONFIG_KEY]
    try:
        # silent=True：畸形 JSON 返回 None，走下面的 400，而不是抛 BadRequest 变成 500。
        req_data = request.get_json(silent=True)
        logger.debug("/v1/responses request received: stream=%s model=%s", (req_data or {}).get('stream'), (req_data or {}).get('model'))
        if not req_data or 'input' not in req_data:
            return jsonify({'error': 'Missing or invalid JSON body / missing input field'}), 400

        model, model_flags = parse_model_flags(req_data.get('model', 'kimi-k3'))
        net_go = req_data.get('net_go', model_flags.get('net_go', False))
        thinking = req_data.get('thinking', model_flags.get('thinking'))
        # 请求级会话归组（None=跟随启动配置，空字符串=显式关闭本次归组）。
        # 请求体字段优先；不能自定义 body 的标准客户端（如 opencode）用 X-GenAI-Chat-Group 头。
        chat_group_id = req_data.get('chat_group_id')
        if chat_group_id is None:
            header_group_id = request.headers.get('X-GenAI-Chat-Group')
            if header_group_id:
                chat_group_id = header_group_id
        stream = req_data.get('stream', False)
        max_output_tokens = req_data.get('max_output_tokens', req_data.get('max_tokens', 30000))
        messages = build_response_input_messages(req_data.get('input'))
        access_token = get_request_access_token(settings)

        if not messages:
            return jsonify({'error': 'No input message found'}), 400

        if stream:
            return Response(
                stream_with_context(stream_responses_api(messages, model, max_output_tokens, settings, access_token,
                                                         net_go, thinking, chat_group_id)),
                mimetype='text/event-stream',
                headers=SSE_HEADERS,
            )

        # 非流式返回时，将 reasoning 和 message 组装到 output 数组中。
        collected = collect_genai_response(messages, model, max_output_tokens, settings, access_token,
                                           net_go=net_go, thinking=thinking, chat_group_id=chat_group_id)
        response_id = f"resp_{uuid.uuid4().hex}"
        output = []
        if collected["reasoning_content"]:
            output.append({
                "id": f"rs_{uuid.uuid4().hex[:12]}",
                "type": "reasoning",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": collected["reasoning_content"],
                    }
                ]
            })
        output.append({
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": collected["content"],
                }
            ]
        })

        response_body = {
            "id": response_id,
            "object": "response",
            "created_at": int(datetime.now().timestamp()),
            "status": "completed",
            "model": model,
            "output": output,
            "output_text": collected["content"],
        }
        # 上游提供真实 totalTokens 时附带 usage。
        if collected.get("usage_total") is not None:
            response_body["usage"] = {
                "input_tokens": 0,
                "output_tokens": len(collected["content"]) + len(collected["reasoning_content"]),
                "total_tokens": collected["usage_total"],
            }
        return jsonify(response_body)

    except Exception as e:
        logger.exception("responses failed")
        return jsonify({'error': str(e)}), 500
