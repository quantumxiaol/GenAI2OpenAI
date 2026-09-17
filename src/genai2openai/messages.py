import json


def convert_messages_to_genai_format(messages):
    """从消息列表中提取 GenAI 所需的 `chatInfo`。

    当前上游实际请求中 `chatInfo` 只使用最后一条用户消息内容，因此这里
    仅做最小提取。

    Args:
        messages (list[dict]): OpenAI 风格的消息列表。

    Returns:
        str: 最后一条用户消息的文本内容；若不存在则返回空字符串。
    """
    # 上游会单独接收一份 chatInfo，这里取最后一条 user 消息与网页行为对齐。
    chat_info = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            chat_info = msg.get("content", "")
            break

    return chat_info


def split_chat_info(messages):
    """按上游语义拆分：chatInfo 为本轮提问，messages 为之前的历史消息。

    网页端请求体中 messages 不含本轮提问（本轮在 chatInfo 中），上游会将二者
    拼接；若不拆分，模型会看到两遍最后一条用户消息。

    Args:
        messages (list[dict]): 归一化后的 OpenAI 风格消息列表。

    Returns:
        tuple[str, list[dict]]: (chatInfo, 去掉最后一条 user 消息的消息列表)。
    """
    chat_info = ""
    trimmed = list(messages)
    for index in range(len(trimmed) - 1, -1, -1):
        if trimmed[index].get("role") == "user":
            content = trimmed[index].get("content", "")
            chat_info = content if isinstance(content, str) else str(content)
            del trimmed[index]
            break
    return chat_info, trimmed


def normalize_content_for_genai(content):
    """将 OpenAI 消息 content 归一化为上游可读文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    text_parts.append(text)
            elif isinstance(part, str):
                text_parts.append(part)
        return "\n".join(text_parts)
    return str(content)


def normalize_messages_for_genai(messages):
    """把 OpenAI tool messages 降级为普通文本，避免上游无法理解原生工具结构。"""
    normalized_messages = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role", "user")
        content = normalize_content_for_genai(message.get("content"))

        if role == "tool":
            tool_name = message.get("name") or message.get("tool_call_id") or "tool"
            normalized_messages.append({
                "role": "user",
                "content": f"工具 {tool_name} 返回结果：\n{content}",
            })
            continue

        tool_calls = message.get("tool_calls")
        if role == "assistant" and tool_calls and not content:
            # 历史中的工具调用按提示词约定的 JSON 格式回放（不放自定义前缀）：
            # 模型若在后续轮次模仿该格式，解析器能直接识别为真实调用，不会被当成普通文本。
            compact_calls = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = function.get("name")
                if not name:
                    continue
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                compact_calls.append({"name": name, "arguments": arguments})
            normalized_messages.append({
                "role": "assistant",
                "content": json.dumps({"tool_calls": compact_calls}, ensure_ascii=False),
            })
            continue

        normalized_messages.append({
            "role": role,
            "content": content,
        })

    return normalized_messages
