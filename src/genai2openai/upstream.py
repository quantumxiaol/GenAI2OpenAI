import json
import logging

import requests

from .config import GENAI_URL, Settings, build_genai_headers
from .messages import split_chat_info
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


def _parse_total_tokens(other):
    """从上游 other 元数据（字符串化 JSON）中解析真实 totalTokens。"""
    try:
        payload = json.loads(other) if isinstance(other, str) else other
        total = payload.get("totalTokens") if isinstance(payload, dict) else None
        return total if isinstance(total, int) else None
    except (json.JSONDecodeError, TypeError):
        return None


def stream_genai_events(messages, model, max_tokens, settings: Settings, access_token=None, image_payload=None,
                        net_go=False, thinking=None, chat_group_id=None):
    """调用 GenAI 流式接口并产出统一事件流。

    该函数是整个协议转换的底层入口，负责：
    1. 解析模型别名
    2. 调用上游 GenAI SSE 接口
    3. 将上游原始事件规范化为内部事件类型

    Args:
        messages (list[dict]): 发送给上游的消息列表。
        model (str): 调用方指定的模型名（已剥离功能后缀）。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置（上游 token、请求头等）。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。
        net_go (bool): 联网搜索开关（上游字段 netGo）。
        thinking (bool | None): 深度思考开关；None 表示不发送该字段，跟随上游默认。
        chat_group_id (str | None): 请求级会话分组 ID，优先于启动配置
            （传入空字符串可显式关闭本次归组）。

    Yields:
        dict: 统一事件对象，`type` 可能为 `delta`、`done`、`meta` 或 `error`。
    """
    upstream_model, root_ai_type = resolve_model(model)

    # 这里保持与网页端接近的请求体结构，避免上游校验差异。
    # 上游语义为 chatInfo（本轮提问）+ messages（历史消息）拼接，需拆分，
    # 否则模型会看到两遍最后一条用户消息。
    chat_info, history_messages = split_chat_info(messages)
    genai_data = {
        "chatInfo": chat_info,
        "messages": history_messages,
        "type": "3",
        "stream": True,
        "aiType": upstream_model,
        "aiSecType": "1",
        "promptTokens": 0,
        "rootAiType": root_ai_type,
        "maxToken": max_tokens or 30000
    }
    if net_go:
        genai_data["netGo"] = True
    if thinking is not None:
        genai_data["thinking"] = thinking
    # 上游对 chatGroupId + 图片的组合会报 vLLM 图片加载错误（实测稳定复现），
    # 带图请求不发分组 ID。请求级 chat_group_id 优先于启动配置（空字符串显式关闭）。
    effective_group_id = chat_group_id if chat_group_id is not None else settings.chat_group_id
    if effective_group_id and not image_payload:
        genai_data["chatGroupId"] = effective_group_id
    elif effective_group_id and image_payload:
        logger.debug("chatGroupId skipped for image request (upstream incompatible)")
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

        logged_first_chunk = False
        saw_finish = False
        final_upstream_model = None
        usage_total = None
        for line in response.iter_lines():
            if not line:
                continue

            try:
                line_str = line.decode('utf-8') if isinstance(line, bytes) else line

                # 兼容标准 SSE 的 `data:` 前缀。
                if line_str.startswith('data:'):
                    line_str = line_str[5:].strip()

                # 上游在 `[DONE]` 之后还会发 other 元数据（含真实 token 数），
                # 不要 break，继续读到流结束。
                if line_str == "[DONE]":
                    continue

                if line_str:
                    genai_json = json.loads(line_str)
                    # 每个请求只记录首个 chunk 的 keys，避免刷屏。
                    if not logged_first_chunk:
                        logger.debug("Upstream first SSE chunk keys: %s", list(genai_json.keys()))
                        logged_first_chunk = True

                    # 上游错误以 {"code":500,"errMsg":"..."} 数据行返回，必须显式透出，
                    # 否则会被静默吞掉表现为空响应。
                    if "choices" not in genai_json and genai_json.get("code") not in (None, 0, 200, "200"):
                        err_msg = genai_json.get("errMsg") or genai_json.get("message") or genai_json
                        yield {
                            "type": "error",
                            "error": f"GenAI upstream error {genai_json.get('code')}: {err_msg}",
                        }
                        return

                    # 补充元数据（字符串化 JSON，含 totalTokens）。
                    if genai_json.get("other"):
                        usage_total = _parse_total_tokens(genai_json["other"]) or usage_total
                        yield {
                            "type": "meta",
                            "other": genai_json.get("other"),
                        }
                        continue

                    # 上游给出 finish_reason 即本轮内容结束；记下后继续读末尾元数据。
                    choices = genai_json.get("choices")
                    if choices and choices[0].get("finish_reason") is not None:
                        saw_finish = True
                        final_upstream_model = genai_json.get("model")
                        continue

                    if saw_finish:
                        continue

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
            "upstream_model": final_upstream_model,
            "usage": usage_total,
        }

    except Exception as e:
        logger.exception("stream_genai_events failed")
        # 流式链路统一转成 error 事件，交由上层协议各自包装。
        yield {
            "type": "error",
            "error": str(e),
        }


def collect_genai_response(messages, model, max_tokens, settings: Settings, access_token=None, image_payload=None,
                           net_go=False, thinking=None, chat_group_id=None):
    """收集完整响应并聚合为非流式结果。

    Args:
        messages (list[dict]): OpenAI 风格消息列表。
        model (str): 调用方传入的模型名。
        max_tokens (int | None): 最大输出 token 数。
        settings (Settings): 运行时配置。
        access_token (str | None): 请求级 GenAI token，未提供时使用启动参数。
        net_go (bool): 联网搜索开关。
        thinking (bool | None): 深度思考开关；None 跟随上游默认。

    Returns:
        dict: 聚合后的正文、思维链、上游模型名和真实 token 数（`usage_total`，
        上游未提供时为 None）。

    Raises:
        RuntimeError: 当上游事件流返回错误事件时抛出。
    """
    content_parts = []
    reasoning_parts = []
    upstream_model = None
    usage_total = None

    for event in stream_genai_events(messages, model, max_tokens, settings, access_token, image_payload,
                                     net_go, thinking, chat_group_id):
        if event["type"] == "error":
            raise RuntimeError(event["error"])
        if event["type"] == "delta":
            upstream_model = event.get("upstream_model") or upstream_model
            if event.get("reasoning"):
                reasoning_parts.append(event["reasoning"])
            if event.get("content"):
                content_parts.append(event["content"])
        if event["type"] == "done":
            upstream_model = event.get("upstream_model") or upstream_model
            usage_total = event.get("usage")
            break

    return {
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
        "upstream_model": upstream_model,
        "usage_total": usage_total,
    }
