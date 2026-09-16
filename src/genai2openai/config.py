import os
from dataclasses import dataclass

GENAI_BASE_URL = "https://genai.shanghaitech.edu.cn"
IDS_BASE_URL = "https://ids.shanghaitech.edu.cn"
GENAI_URL = f"{GENAI_BASE_URL}/htk/chat/start/chat"
GENAI_MODELS_URL = f"{GENAI_BASE_URL}/htk/ai/aiModel/list"
GENAI_UPLOAD_URL = "https://genaipic.shanghaitech.edu.cn//sys/common/upload"
GENAI_IMAGE_STATIC_URL = "https://genaipic.shanghaitech.edu.cn//sys/common/static/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

# 上游网页端请求头模板（不含 X-Access-Token，按请求填充）。
BASE_GENAI_HEADERS = {
    "Accept": "*/*, text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": GENAI_BASE_URL,
    "Referer": f"{GENAI_BASE_URL}/dialogue",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": USER_AGENT,
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


DEFAULT_UPLOAD_TOKEN = "2ea38f293adb4abca21132feba61eaa3"


def load_dotenv(path=".env"):
    """极简 .env 加载：KEY=VALUE 逐行读入环境变量，不覆盖已存在的变量。"""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


@dataclass
class Settings:
    """运行时配置，由 CLI 参数构建并随 Flask app 分发。"""

    token: str | None = None
    account: str | None = None
    upload_token: str = DEFAULT_UPLOAD_TOKEN
    port: int = 5000
    log_level: str = "INFO"


def build_genai_headers(settings: Settings, access_token: str | None = None) -> dict:
    """构建上游请求头，请求级 token 优先于启动参数 token。"""
    headers = BASE_GENAI_HEADERS.copy()
    headers["X-Access-Token"] = access_token or settings.token or ""
    return headers


def build_startup_genai_headers(token: str) -> dict:
    """构建启动阶段校验 token 所需的最小上游请求头。"""
    return {
        "Accept": "*/*, text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": GENAI_BASE_URL,
        "Referer": f"{GENAI_BASE_URL}/dialogue",
        "User-Agent": USER_AGENT,
        "X-Access-Token": token,
    }


def build_genai_upload_headers(settings: Settings, access_token: str | None = None) -> dict:
    """构建图片上传请求头。"""
    return {
        "Accept": "*/*",
        "Origin": GENAI_BASE_URL,
        "Referer": f"{GENAI_BASE_URL}/",
        "User-Agent": USER_AGENT,
        # 上传接口要求独立 token 头；同时附带 X-Access-Token 保持兼容。
        "token": settings.upload_token,
        "X-Access-Token": access_token or settings.token,
    }
