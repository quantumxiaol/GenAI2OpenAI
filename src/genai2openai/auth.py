import json
import logging
import os
import threading

import requests
from flask import request

from .cas import LoginError, login_genai
from .config import GENAI_URL, Settings, build_startup_genai_headers

logger = logging.getLogger("genai-proxy")

# token 缓存文件位于当前工作目录（通常即项目根目录）。
TOKEN_CACHE_PATH = os.path.join(os.getcwd(), ".genai_token_cache")

# CAS 重登录是慢操作（数秒），串行化避免并发请求同时触发多次登录。
_refresh_lock = threading.Lock()


def refresh_token(settings: Settings, failed_token: str | None = None) -> bool:
    """用已配置账号重新 CAS 登录刷新服务端 token（线程安全）。

    Args:
        settings: 运行时配置（需含 account）。
        failed_token: 本次失败请求所用的 token；若其他线程已抢先刷新则跳过重复登录。

    Returns:
        bool: 是否拿到可用的新 token。
    """
    if not settings.account:
        return False
    with _refresh_lock:
        if failed_token and settings.token and settings.token != failed_token:
            logger.debug("token already refreshed by another thread")
            return True
        try:
            student_id, password = settings.account.split("@", 1)
            token = login_genai(student_id, password)
            settings.token = token
            save_cached_token(token)
            logger.info("token refreshed via CAS auto-login")
            return True
        except ValueError:
            logger.error("invalid --account format for token refresh, expected student_id@password")
            return False
        except LoginError as exc:
            logger.error("token refresh failed: %s", exc)
            return False


def load_cached_token():
    """从工作目录读取缓存 token。"""
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None

    try:
        with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as token_file:
            token = token_file.read().strip()
    except OSError:
        logger.exception("Failed to read token cache")
        return None

    return token or None


def save_cached_token(token):
    """将自动登录获得的 token 写入工作目录缓存。"""
    try:
        with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as token_file:
            token_file.write(token.strip())
    except OSError:
        logger.exception("Failed to write token cache")


def validate_cached_token(token):
    """用 Kimi-k3 发起最小对话，返回非空内容则认为 token 有效。"""
    if not token:
        return False

    payload = {
        "chatInfo": "你好",
        "messages": [{"role": "user", "content": "你好"}],
        "type": "3",
        "stream": True,
        "aiType": "Kimi-k3",
        "aiSecType": "1",
        "promptTokens": 0,
        "rootAiType": "xinference",
        "maxToken": 16,
    }

    try:
        response = requests.post(
            GENAI_URL,
            headers=build_startup_genai_headers(token),
            json=payload,
            stream=True,
            timeout=30,
        )
        if response.status_code != 200:
            logger.info("Cached token validation failed with HTTP %s", response.status_code)
            return False

        for line in response.iter_lines():
            if not line:
                continue

            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            if line_str.startswith("data:"):
                line_str = line_str[5:].strip()
            if not line_str:
                continue

            try:
                chunk = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices") or []
            if not choices:
                continue

            delta = choices[0].get("delta") or {}
            if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"):
                return True
    except Exception:
        logger.exception("Cached token validation failed")

    return False


def auto_login_with_account(account):
    try:
        account_student_id, account_password = account.split("@", 1)
        logger.info("Attempting auto login with account: %s", account_student_id)
        token = login_genai(account_student_id, account_password)
        logger.info("Auto login succeeded for account: %s", account_student_id)
        save_cached_token(token)
        return token
    except ValueError:
        logger.error("Invalid --account format, expected student_id@password")
        raise SystemExit("--account must be in the format student_id@password")
    except LoginError as exc:
        logger.exception("Auto login failed")
        raise SystemExit(f"Auto login failed: {exc}")


def get_request_access_token(settings: Settings):
    """从 OpenAI 常用认证头中提取请求级 GenAI token。"""
    if settings.account:
        logger.debug("Ignoring request access token because --account is enabled")
        return None
    if settings.api_key:
        # 鉴权模式下 Authorization 头是客户端对代理的凭据，不能透传给上游。
        logger.debug("Ignoring request access token because API key auth is enabled")
        return None

    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token

    for header_name in ("X-Access-Token", "api-key", "X-API-Key"):
        token = request.headers.get(header_name)
        if token:
            return token.strip()

    return None
