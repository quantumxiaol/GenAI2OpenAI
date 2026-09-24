#!/usr/bin/env python3
"""长上下文工具调用测试：模拟 12 轮工具循环历史后，检查模型是否仍能发起工具调用。

复现场景：opencode 长会话后期模型声称"消息限制不能调用工具"的问题。
用法：
    uv run python tools/test_tool_long_context.py                      # 默认 kimi-k3 + kimi-k3-thinking，各 3 次
    uv run python tools/test_tool_long_context.py --model deepseek-v4.1 --runs 5
"""

import argparse
import json
import os

import requests

TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "run shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}


def build_history(turns=12):
    """构造 opencode 真实形态的长工具循环历史。"""
    history = [{"role": "system", "content": "You are opencode, an AI coding assistant."}]
    for i in range(turns):
        history.append({"role": "user", "content": f"第{i + 1}步：列出目录"})
        history.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})}}]})
        history.append({"role": "tool", "tool_call_id": f"call_{i}", "content": "file1.py file2.py README.md"})
    history.append({"role": "user", "content": "现在再列一次当前目录文件，用工具"})
    return history


def main():
    parser = argparse.ArgumentParser(description="Long-context tool calling test")
    parser.add_argument("--base-url", default=os.environ.get("GENAI_API_BASE_URL", "http://127.0.0.1:11435"))
    parser.add_argument("--api-key", default=os.environ.get("GENAI_API_KEY"))
    parser.add_argument("--model", default=None, help="single model (default: kimi-k3 and kimi-k3-thinking)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--turns", type=int, default=12, help="tool-loop history length")
    args = parser.parse_args()

    models = [args.model] if args.model else ["kimi-k3", "kimi-k3-thinking"]
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    history = build_history(args.turns)
    url = f"{args.base_url.rstrip('/')}/v1/chat/completions"

    for model in models:
        ok = 0
        for i in range(args.runs):
            resp = requests.post(url, headers=headers, timeout=300, json={
                "model": model, "max_tokens": 1024, "messages": history, "tools": [TOOL]})
            message = resp.json().get("choices", [{}])[0].get("message", {})
            called = bool(message.get("tool_calls"))
            ok += called
            content = (message.get("content") or "")[:60].replace("\n", "\\n")
            print(f"{model} run{i + 1}: tool_call={called} content={content!r}")
        print(f"=> {model}: {ok}/{args.runs} 发起了工具调用\n")


if __name__ == "__main__":
    main()
