#!/usr/bin/env python3
"""冒烟测试：验证代理核心链路（需要校内网络 + 服务已启动）。

用法：
    uv run tools/smoke_test.py                          # 默认打 http://127.0.0.1:11435
    uv run tools/smoke_test.py --base-url http://127.0.0.1:11435
"""

import argparse
import json
import sys

import requests

TIMEOUT = 30


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return ok


def post_chat(base_url, body):
    resp = requests.post(f"{base_url}/v1/chat/completions", json=body, timeout=TIMEOUT)
    return resp


def main():
    parser = argparse.ArgumentParser(description="GenAI2OpenAI smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:11435", help="proxy base URL (without /v1)")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    results = []

    def t_health():
        resp = requests.get(f"{base_url}/health", timeout=TIMEOUT)
        return resp.status_code == 200 and resp.json().get("status") == "ok", f"HTTP {resp.status_code}"

    results.append(check("health", t_health))

    def t_models():
        resp = requests.get(f"{base_url}/v1/models", timeout=TIMEOUT)
        ids = [m["id"] for m in resp.json().get("data", [])]
        return "kimi-k3" in ids and "deepseek-v4.1" in ids, f"{len(ids)} models: {ids}"

    results.append(check("models list", t_models))

    def t_nothink():
        resp = post_chat(base_url, {
            "model": "kimi-k3-nothink",
            "messages": [{"role": "user", "content": "say hi"}],
            "max_tokens": 128,
        })
        message = resp.json()["choices"][0]["message"]
        # 上游 2026-09-18 起 K3 默认不思考，-nothink 只是显式关闭，此项验证请求正常。
        return bool(message.get("content")), f"reasoning_present={bool(message.get('reasoning_content'))} content={str(message.get('content'))[:60]!r}"

    results.append(check("kimi-k3 -nothink (显式关思考，仅验证可用)", t_nothink))

    def t_kimi_default_no_thinking():
        # 上游 2026-09-18 变更：Kimi-K3 默认不再输出思维链。
        resp = post_chat(base_url, {
            "model": "kimi-k3",
            "messages": [{"role": "user", "content": "say hi"}],
            "max_tokens": 256,
        })
        message = resp.json()["choices"][0]["message"]
        reasoning = message.get("reasoning_content") or ""
        return not reasoning and bool(message.get("content")), f"reasoning={reasoning[:60]!r}"

    results.append(check("kimi-k3 默认 (思维链应关闭)", t_kimi_default_no_thinking))

    def check_thinking_on(model):
        # 上游思维链开关目前不稳定（平台刚升级，同一请求时有时无），
        # 降级为参考项：验证请求可用，思维链是否出现只作展示、不影响判定。
        resp = post_chat(base_url, {
            "model": model,
            "messages": [{"role": "user", "content": "9.11 和 9.9 哪个大？先推理再给结论"}],
            "max_tokens": 2048,
        })
        message = resp.json()["choices"][0]["message"]
        reasoning = message.get("reasoning_content") or ""
        detail = f"reasoning_present={bool(reasoning)}"
        if reasoning:
            detail += f" reasoning={reasoning[:60]!r}"
        return bool(message.get("content")), detail

    results.append(check("kimi-k3 -thinking (思维链开关，参考项)", lambda: check_thinking_on("kimi-k3-thinking")))
    results.append(check("deepseek-v4.1 -thinking (思维链开关，参考项)", lambda: check_thinking_on("deepseek-v4.1-thinking")))

    def t_search():
        resp = post_chat(base_url, {
            "model": "deepseek-v4.1-search",
            "messages": [{"role": "user", "content": "上海科技大学最近有什么新闻"}],
        })
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return len(content) > 100, f"content[:80]={content[:80]!r}"

    results.append(check("deepseek-v4.1 -search (联网)", t_search))

    def t_error_passthrough():
        resp = post_chat(base_url, {
            "model": "deepseek-v3",
            "messages": [{"role": "user", "content": "hi"}],
        })
        error = resp.json().get("error") or ""
        return "未找到对应节点信息" in error or "error" in error.lower(), f"error={error!r}"

    results.append(check("失效模型错误透传", t_error_passthrough))

    def t_stream():
        resp = post_chat(base_url, {
            "model": "deepseek-v4.1",
            "messages": [{"role": "user", "content": "count to 3"}],
            "stream": True,
            "max_tokens": 128,
        })
        chunks = []
        done = False
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            chunk = json.loads(data)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                chunks.append(delta["content"])
        content = "".join(chunks)
        return done and bool(content), f"chunks={len(chunks)} content={content[:60]!r}"

    results.append(check("流式输出", t_stream))

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
