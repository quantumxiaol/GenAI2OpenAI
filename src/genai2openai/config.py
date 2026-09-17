import os
from dataclasses import dataclass

# 使用系统信任库做 TLS 校验（替代 certifi）：
# 1) 上游服务器证书链不完整（只发叶子证书），certifi 只有根证书、不会补中间链；
# 2) 本机跑 Clash 类代理时，其 MITM 根证书在系统钥匙串中但不在 certifi 中。
# 系统库（macOS 钥匙串等）对这两种情况都能正确处理，与浏览器和 curl 行为一致。
# 需在任何上游请求发生前注入；config 模块会被所有上游调用方导入。
import truststore

truststore.inject_into_ssl()

GENAI_BASE_URL = "https://genai.shanghaitech.edu.cn"
IDS_BASE_URL = "https://ids.shanghaitech.edu.cn"
GENAI_URL = f"{GENAI_BASE_URL}/htk/chat/start/chat"
GENAI_MODELS_URL = f"{GENAI_BASE_URL}/htk/ai/aiModel/list"
GENAI_UPLOAD_URL = "https://genaipic.shanghaitech.edu.cn//sys/common/upload"
GENAI_IMAGE_STATIC_URL = "https://genaipic.shanghaitech.edu.cn//sys/common/static/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
)

# 上游网页端请求头模板（不含 X-Access-Token，按请求填充）。
BASE_GENAI_HEADERS = {
    "Accept": "*/*, text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": GENAI_BASE_URL,
    "Referer": f"{GENAI_BASE_URL}/dashboard/analysis",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": USER_AGENT,
    "sec-ch-ua": '"Microsoft Edge";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


DEFAULT_UPLOAD_TOKEN = "2ea38f293adb4abca21132feba61eaa3"

# 默认端口：Ollama 默认端口 11434 的后一位（避开 macOS AirPlay 占用的 5000）。
DEFAULT_PORT = 11435


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
    port: int = DEFAULT_PORT
    log_level: str = "INFO"
    # 固定上游会话分组 ID：所有 API 请求归入网页版同一条会话；
    # 为空则不发送，每次请求在网页版各自建一条会话。
    chat_group_id: str | None = None


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
        "Referer": f"{GENAI_BASE_URL}/dashboard/analysis",
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
