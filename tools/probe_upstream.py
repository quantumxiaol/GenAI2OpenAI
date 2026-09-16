#!/usr/bin/env python3
"""协议适配调试工具：直连上游 GenAI 接口，打印原始响应。

用法（仓库根目录下）：

    # 打印上游当前模型清单（aiType | simpleName | aiName | rootAiType）
    uv run tools/probe_upstream.py --list-models

    # 用指定模型发一条消息，原样打印上游返回的 SSE 报文
    uv run tools/probe_upstream.py --ai-type Kimi-k3 --root-ai-type xinference

    # 默认从 .genai_token_cache 读 token，也可以用 --token 显式指定
    uv run tools/probe_upstream.py --token eyJ... --ai-type deepseek-pro --prompt "hello"
"""

import argparse
import json

import requests

from genai2openai.auth import load_cached_token
from genai2openai.config import GENAI_URL, Settings, build_genai_headers
from genai2openai.registry import fetch_remote_models


def parse_args():
    parser = argparse.ArgumentParser(description="Probe the upstream GenAI chat API and print raw responses")
    parser.add_argument("--token", default=None, help="GenAI token (default: read .genai_token_cache)")
    parser.add_argument("--list-models", action="store_true", help="only dump the upstream model list")
    parser.add_argument("--ai-type", default="Kimi-k3", help="upstream aiType (default: Kimi-k3)")
    parser.add_argument("--root-ai-type", default="xinference", help="upstream rootAiType (default: xinference)")
    parser.add_argument("--prompt", default="say hello", help="test prompt")
    parser.add_argument("--max-token", type=int, default=256, help="maxToken sent upstream")
    parser.add_argument("--url", default=GENAI_URL, help="upstream chat URL (default: config.GENAI_URL)")
    return parser.parse_args()


def main():
    args = parse_args()
    token = args.token or load_cached_token()
    if not token:
        raise SystemExit("No token: pass --token or run the server with --account once to create .genai_token_cache")
    settings = Settings(token=token)

    if args.list_models:
        for record in fetch_remote_models(settings):
            print(record.get("aiType"), "|", record.get("simpleName"), "|", record.get("aiName"), "|", record.get("rootAiType"))
        return

    payload = {
        "chatInfo": args.prompt,
        "messages": [{"role": "user", "content": args.prompt}],
        "type": "3",
        "stream": True,
        "aiType": args.ai_type,
        "aiSecType": "1",
        "promptTokens": 0,
        "rootAiType": args.root_ai_type,
        "maxToken": args.max_token,
    }
    print("POST", args.url)
    print("payload:", json.dumps(payload, ensure_ascii=False))
    print("---")
    response = requests.post(
        args.url,
        headers=build_genai_headers(settings),
        json=payload,
        stream=True,
        timeout=60,
    )
    print("HTTP", response.status_code, "| content-type:", response.headers.get("Content-Type"))
    print("---")
    for line in response.iter_lines():
        if line:
            print(line.decode("utf-8", "replace"))


if __name__ == "__main__":
    main()
