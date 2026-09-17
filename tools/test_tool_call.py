#!/usr/bin/env python3
"""工具调用兼容性测试：向代理发送带 tools 的请求并打印解析结果与原始文本。

用法：
    uv run python tools/test_tool_call.py                      # 默认 deepseek-v4.1
    uv run python tools/test_tool_call.py --model kimi-k3
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


def main():
    parser = argparse.ArgumentParser(description="Test tool-calling compatibility through the proxy")
    parser.add_argument("--model", default="deepseek-v4.1")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    args = parser.parse_args()

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "上海今天适合带伞吗？请调用工具查询天气后再回答"}],
        "tools": [TOOL],
        "tool_choice": "auto",
        "stream": False,
        "max_tokens": 2048,
    }
    resp = requests.post(f"{args.base_url.rstrip('/')}/v1/chat/completions", json=payload, timeout=300)
    data = resp.json()
    message = data.get("choices", [{}])[0].get("message", {})

    print(json.dumps(data, ensure_ascii=False, indent=1)[:2000])
    print("---")

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        for call in tool_calls:
            print(f"TOOL_CALL_OK: {call['function']['name']}({call['function']['arguments']})")
        return

    print("NO_TOOL_CALLS — 原始 content（供格式校准，请把这段发回来）：")
    print(repr(message.get("content")))
    sys.exit(1)


if __name__ == "__main__":
    main()
