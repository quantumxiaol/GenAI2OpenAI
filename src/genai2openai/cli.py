import argparse
import logging

from rich.console import Console
from rich.logging import RichHandler

from .auth import auto_login_with_account, load_cached_token, validate_cached_token
from .config import Settings
from .registry import log_new_remote_models
from .routes import create_app


def parse_args():
    parser = argparse.ArgumentParser(description='GenAI Flask API Server')
    parser.add_argument('--token', type=str, default=None,
                        help='GenAI API Access Token')
    parser.add_argument('--account', type=str, default=None,
                        help='ShanghaiTech account in the format student_id@password, used to auto-login and get token')
    parser.add_argument('--upload-token', type=str, default=Settings.upload_token,
                        help='GenAI image upload API token header value')
    parser.add_argument('--port', type=int, default=5000,
                        help='Flask server port (default: 5000)')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Console log level (default: INFO)')
    return parser.parse_args()


def setup_logging(log_level):
    console = Console()
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(message)s',
        datefmt='[%X]',
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def main():
    args = parse_args()
    settings = Settings(
        token=args.token,
        account=args.account,
        upload_token=args.upload_token,
        port=args.port,
        log_level=args.log_level,
    )
    setup_logging(settings.log_level)

    if not settings.token:
        cached_token = load_cached_token()
        if cached_token:
            logging.getLogger("genai-proxy").info("Found cached token, validating with deepseek-v3")
            if validate_cached_token(cached_token):
                settings.token = cached_token
                logging.getLogger("genai-proxy").info("Cached token is valid")
            else:
                logging.getLogger("genai-proxy").info("Cached token is invalid or expired")

    if not settings.token and settings.account:
        settings.token = auto_login_with_account(settings.account)

    app = create_app(settings)
    log_new_remote_models(settings)
    app.run(host='0.0.0.0', port=settings.port, debug=False)


if __name__ == '__main__':
    main()
