#!/usr/bin/env python3
"""协议适配调试工具：直连上游 GenAI 接口，打印原始响应。

用法（仓库根目录下）：

    # 打印上游当前模型清单（aiType | simpleName | aiName | rootAiType）
    uv run tools/probe_upstream.py --list-models

    # 用指定模型发一条消息，原样打印上游返回的 SSE 报文
    uv run tools/probe_upstream.py --ai-type Kimi-k3 --root-ai-type xinference

    # 验证功能开关（联网搜索 / 深度思考 / 固定会话分组）
    uv run tools/probe_upstream.py --ai-type deepseek-pro --net-go --prompt "上科大最近新闻"
    uv run tools/probe_upstream.py --ai-type Kimi-k3 --nothink --prompt "say hi"
    uv run tools/probe_upstream.py --ai-type deepseek-pro --chat-group-id ApiTest001 --prompt "hi"

    # 默认从 .genai_token_cache 读 token，也可以用 --token 显式指定
    uv run tools/probe_upstream.py --token eyJ... --ai-type deepseek-pro --prompt "hello"
"""

import argparse
import json
import os

import requests

from genai2openai.auth import load_cached_token
from genai2openai.config import GENAI_URL, Settings, apply_proxy_policy, build_genai_headers, load_dotenv
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
    parser.add_argument("--net-go", action="store_true", help="enable web search (upstream netGo)")
    thinking_group = parser.add_mutually_exclusive_group()
    thinking_group.add_argument("--thinking", action="store_true", help="enable deep thinking (upstream thinking)")
    thinking_group.add_argument("--nothink", action="store_true", help="explicitly disable deep thinking")
    parser.add_argument("--chat-group-id", default=None, help="send a fixed chatGroupId")
    parser.add_argument("--image", default=None, help="attach an image (local path / URL / data URL), uploaded via the GenAI image service")
    parser.add_argument("--image-urls-array", action="store_true", help="put the image into imageUrls[] instead of imageUrl/width/height")
    return parser.parse_args()


def main():
    load_dotenv()
    apply_proxy_policy()
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
    if args.net_go:
        payload["netGo"] = True
    if args.thinking:
        payload["thinking"] = True
    elif args.nothink:
        payload["thinking"] = False
    if args.chat_group_id:
        payload["chatGroupId"] = args.chat_group_id
    if args.image:
        from genai2openai.images import (
            fetch_image_bytes,
            read_image_from_data_url,
            upload_image_to_genai,
        )

        if args.image.startswith(("http://", "https://")):
            image_bytes, mime_type, filename = fetch_image_bytes(args.image)
        elif args.image.startswith("data:"):
            image_bytes, mime_type, filename = read_image_from_data_url(args.image)
        else:
            with open(args.image, "rb") as image_file:
                image_bytes = image_file.read()
            mime_type = "image/png" if args.image.lower().endswith(".png") else "image/jpeg"
            filename = os.path.basename(args.image)
        image_payload = upload_image_to_genai(image_bytes, filename, mime_type, settings)
        print("uploaded image:", image_payload)
        if args.image_urls_array:
            # imageUrls 数组形式（新版网页端字段），元素结构按 {url,width,height} 猜测
            payload["imageUrls"] = [{
                "url": image_payload["imageUrl"],
                "width": image_payload.get("width"),
                "height": image_payload.get("height"),
            }]
        else:
            payload.update(image_payload)
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
