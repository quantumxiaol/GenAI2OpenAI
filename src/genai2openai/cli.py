import argparse
import logging
import os

from rich.console import Console
from rich.logging import RichHandler

from .auth import auto_login_with_account, load_cached_token, validate_cached_token
from .config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_UPLOAD_TOKEN, Settings, apply_proxy_policy, load_dotenv
from .registry import log_new_remote_models
from .routes import create_app


def parse_args():
    parser = argparse.ArgumentParser(description='GenAI Flask API Server')
    parser.add_argument('--token', type=str, default=None,
                        help='GenAI API Access Token (也可在 .env 中配置 GENAI_TOKEN)')
    parser.add_argument('--account', type=str, default=None,
                        help='ShanghaiTech account in the format student_id@password, used to auto-login and get token (也可在 .env 中配置 GENAI_ACCOUNT)')
    parser.add_argument('--upload-token', type=str, default=None,
                        help='GenAI image upload API token header value (也可在 .env 中配置 GENAI_UPLOAD_TOKEN)')
    parser.add_argument('--port', type=int, default=None,
                        help=f'Flask server port (default: {DEFAULT_PORT}；也可在 .env 中配置 GENAI_PORT)')
    parser.add_argument('--host', type=str, default=None,
                        help=f'监听地址 (default: {DEFAULT_HOST}；局域网/反向代理场景用 0.0.0.0；也可在 .env 中配置 GENAI_HOST)')
    parser.add_argument('--api-key', type=str, default=None,
                        help='代理自身的 API 鉴权密钥，设置后 /v1/* 需携带 Authorization: Bearer (也可在 .env 中配置 GENAI_API_KEY)')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Console log level (default: INFO)')
    parser.add_argument('--chat-group-id', type=str, default=None,
                        help='固定上游会话分组 ID：所有 API 请求归入网页版同一条会话，避免刷屏 (也可在 .env 中配置 GENAI_CHAT_GROUP_ID；默认不发送)')
    return parser.parse_args()


def setup_logging(log_level):
    console = Console()
    level = getattr(logging, log_level.upper(), logging.INFO)
    # 控制台 + 文件双写：终端滚走的内容可以在 genai2openai.log 里翻。
    file_handler = logging.FileHandler("genai2openai.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(
        level=level,
        format='%(message)s',
        datefmt='[%X]',
        handlers=[RichHandler(console=console, rich_tracebacks=True), file_handler],
    )


def resolve_port(cli_port):
    """端口优先级：--port 命令行 > GENAI_PORT 环境变量 > 默认值。"""
    if cli_port is not None:
        return cli_port
    env_port = os.environ.get("GENAI_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            raise SystemExit(f"GENAI_PORT must be an integer, got {env_port!r}")
    return DEFAULT_PORT


def env_flag(name, default=False):
    """解析布尔型环境变量（1/true/yes/on 为真，缺省用 default）。"""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def main():
    load_dotenv()
    apply_proxy_policy()
    args = parse_args()
    settings = Settings(
        token=args.token or os.environ.get("GENAI_TOKEN"),
        account=args.account or os.environ.get("GENAI_ACCOUNT"),
        upload_token=args.upload_token or os.environ.get("GENAI_UPLOAD_TOKEN") or DEFAULT_UPLOAD_TOKEN,
        host=args.host or os.environ.get("GENAI_HOST") or DEFAULT_HOST,
        port=resolve_port(args.port),
        log_level=args.log_level,
        chat_group_id=args.chat_group_id or os.environ.get("GENAI_CHAT_GROUP_ID"),
        api_key=args.api_key or os.environ.get("GENAI_API_KEY"),
        disable_azure=env_flag("GENAI_DISABLE_AZURE", False),
    )
    setup_logging(settings.log_level)

    if not settings.token:
        cached_token = load_cached_token()
        if cached_token:
            logging.getLogger("genai-proxy").info("Found cached token, validating with Kimi-k3")
            if validate_cached_token(cached_token):
                settings.token = cached_token
                logging.getLogger("genai-proxy").info("Cached token is valid")
            else:
                logging.getLogger("genai-proxy").info("Cached token is invalid or expired")

    if not settings.token and settings.account:
        # 上游不可用（平台维护/网络隔离）时也照常启动：请求级 token 仍可用，
        # 平台恢复后再重启即可获得启动 token。
        try:
            settings.token = auto_login_with_account(settings.account)
        except SystemExit as exc:
            logging.getLogger("genai-proxy").warning(
                "Auto login failed (%s); starting without a startup token", exc
            )

    app = create_app(settings)
    log_new_remote_models(settings)
    # threaded=True：每个请求独立线程。SSE 流式是 I/O 密集（等上游时 GIL 已释放），
    # 几个人并发互不阻塞；不开启时 Werkzeug 默认单线程串行。
    app.run(host=settings.host, port=settings.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
