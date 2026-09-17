#!/usr/bin/env python3
"""工具调用兼容性测试：向代理发送带 tools 的请求并打印解析结果与原始文本。

用法：
    uv run python tools/test_tool_call.py                      # 默认 deepseek-v4.1，单轮
    uv run python tools/test_tool_call.py --model kimi-k3
    uv run python tools/test_tool_call.py --loop               # 多轮工具循环模拟（接近 agent 场景）
"""

import argparse
import json
import sys

import requests

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}


def post_chat(base_url, payload):
    resp = requests.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, timeout=300)
    return resp.json()


def message_of(data):
    return data.get("choices", [{}])[0].get("message", {})


def main():
    parser = argparse.ArgumentParser(description="Test tool-calling compatibility through the proxy")
    parser.add_argument("--model", default="deepseek-v4.1")
    parser.add_argument("--base-url", default="http://127.0.0.1:11435")
    parser.add_argument("--loop", action="store_true", help="simulate a multi-turn tool loop")
    args = parser.parse_args()

    messages = [{"role": "user", "content": "上海今天适合带伞吗？请调用工具查询天气后再回答"}]

    # 第一轮：期望模型发起工具调用
    data = post_chat(args.base_url, {
        "model": args.model, "messages": messages, "tools": [TOOL],
        "tool_choice": "auto", "stream": False, "max_tokens": 2048,
    })
    message = message_of(data)
    print(json.dumps(data, ensure_ascii=False, indent=1)[:2000])
    print("---")

    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        print("NO_TOOL_CALLS — 原始 content（供格式校准，请把这段发回来）：")
        print(repr(message.get("content")))
        sys.exit(1)

    for call in tool_calls:
        print(f"TOOL_CALL_OK: {call['function']['name']}({call['function']['arguments']})")

    if not args.loop:
        return

    # 第二轮：回填 assistant tool_calls + 伪造工具结果，期望模型给出最终文字回答
    messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
    for call in tool_calls:
        messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": "上海，今日多云转小雨，气温 22-26℃，建议携带雨伞。",
        })

    data2 = post_chat(args.base_url, {
        "model": args.model, "messages": messages, "tools": [TOOL],
        "tool_choice": "auto", "stream": False, "max_tokens": 2048,
    })
    message2 = message_of(data2)
    print("--- 第二轮（工具结果回填后）---")
    if message2.get("tool_calls"):
        print("又发起了工具调用:", json.dumps(message2["tool_calls"], ensure_ascii=False)[:500])
    else:
        print("最终回答:", (message2.get("content") or "")[:300])
    print("LOOP_OK")


if __name__ == "__main__":
    main()
