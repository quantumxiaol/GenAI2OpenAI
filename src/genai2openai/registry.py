import logging
from datetime import datetime

import requests

from .config import GENAI_MODELS_URL, Settings, build_genai_headers

logger = logging.getLogger("genai-proxy")

MODEL_SPECS = [
    {
        "public_id": "deepseek-r1",
        "request_id": "deepseek-r1:671b",
        "actual_id": "deepseek-r1:671b",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "deepseek-v3",
        "request_id": "deepseek-v3:671b",
        "actual_id": "deepseek-v3:671b",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "glm-5.1",
        "request_id": "chatglm",
        "actual_id": "glm-chat",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "minimax-m1",
        "request_id": "MiniMax-M1",
        "actual_id": "minimax",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "qwen3.5-397b-a17b",
        "request_id": "qwen-instruct",
        "actual_id": "qwen-instruct",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "gpt-5.5",
        "request_id": "GPT-5.5",
        "actual_id": "gpt-5.5-2026-04-24",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5.4",
        "request_id": "GPT-5.4",
        "actual_id": "gpt-5.4-2026-03-05",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5.2",
        "request_id": "GPT-5.2",
        "actual_id": "gpt-5.2-2025-12-11",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5",
        "request_id": "GPT-5",
        "actual_id": "gpt-5-2025-08-07",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-4.1",
        "request_id": "GPT-4.1",
        "actual_id": "gpt-4.1-2025-04-14",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-4.1-mini",
        "request_id": "GPT-4.1-mini",
        "actual_id": "gpt-4.1-mini-2025-04-14",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-o4-mini",
        "request_id": "o4-mini",
        "actual_id": "o4-mini-2025-04-16",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-o3",
        "request_id": "o3",
        "actual_id": "o3-2025-04-16",
        "root_ai_type": "azure",
    },
    {
        "public_id": "deepseek-pro",
        "request_id": "deepseek-pro",
        "actual_id": "deepseek-v4-pro",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "deepseek-chat",
        "request_id": "deepseek-chat",
        "actual_id": "deepseek-v4-flash",
        "root_ai_type": "xinference",
    },
]


def build_model_alias_lookup():
    """构建模型别名查找表。

    将对外公开名称、上游请求名称和上游实际模型名称统一映射到同一份
    模型规格上，便于后续按任意别名解析。

    Returns:
        dict[str, dict]: 以小写别名为键、模型规格字典为值的查找表。
    """
    alias_lookup = {}
    for spec in MODEL_SPECS:
        for alias in {spec["public_id"], spec["request_id"], spec["actual_id"]}:
            alias_lookup[alias.lower()] = spec
    return alias_lookup


MODEL_ALIAS_LOOKUP = build_model_alias_lookup()


def infer_root_ai_type(model_name):
    """为未知模型推断上游路由类型。

    Args:
        model_name (Any): 调用方传入的模型名。

    Returns:
        str: 推断得到的 `rootAiType`，当前仅返回 `azure` 或 `xinference`。
    """
    if not isinstance(model_name, str):
        return "xinference"

    normalized = model_name.lower()
    # OpenAI / Azure 系列模型目前统一走 azure 路由。
    azure_markers = (
        "gpt-",
        "gpt",
        "o3",
        "o4-mini",
    )
    return "azure" if normalized.startswith(azure_markers) else "xinference"


def resolve_model(model_name):
    """解析模型名称到上游请求参数。

    Args:
        model_name (Any): 调用方传入的模型名，可能是 public id、request id
            或 actual id。

    Returns:
        tuple[Any, str]: 第一个元素为实际发给上游的 `aiType`，第二个元素为
        `rootAiType`。
    """
    if not isinstance(model_name, str):
        return model_name, infer_root_ai_type(model_name)

    spec = MODEL_ALIAS_LOOKUP.get(model_name.lower())
    if spec is None:
        return model_name, infer_root_ai_type(model_name)
    return spec["request_id"], spec["root_ai_type"]


def is_gpt_model(model_name):
    """判断模型是否为 GPT/Azure 系列（图片能力仅对其开放）。"""
    _, root_ai_type = resolve_model(model_name)
    return root_ai_type == "azure"


def fetch_remote_models(settings: Settings, access_token: str | None = None):
    """拉取 GenAI 平台当前可用模型列表。"""
    response = requests.get(
        GENAI_MODELS_URL,
        headers=build_genai_headers(settings, access_token),
        params={
            "_t": int(datetime.now().timestamp() * 1000),
            "pageNo": 1,
            "pageSize": 999,
            "showStatusList": "2,3",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"Failed to fetch remote models: {payload}")
    return payload.get("result", {}).get("records", [])


def log_new_remote_models(settings: Settings):
    """启动时检查远端模型列表，提示本地未登记的新模型。"""
    if not settings.token:
        logger.debug("Skipping remote model discovery because no startup token is available")
        return

    try:
        remote_records = fetch_remote_models(settings, settings.token)
    except Exception:
        logger.exception("Failed to fetch remote model list at startup")
        return

    local_aliases = {
        alias.lower()
        for spec in MODEL_SPECS
        for alias in (spec["public_id"], spec["request_id"], spec["actual_id"])
        if isinstance(alias, str)
    }

    discovered = []
    for record in remote_records:
        ai_type = record.get("aiType")
        simple_name = record.get("simpleName")
        ai_name = record.get("aiName")
        candidates = [value for value in (ai_type, simple_name, ai_name) if isinstance(value, str) and value]
        if any(candidate.lower() in local_aliases for candidate in candidates):
            continue
        discovered.append({
            "aiType": ai_type,
            "simpleName": simple_name,
            "aiName": ai_name,
            "rootAiType": record.get("rootAiType"),
        })

    if not discovered:
        logger.info("Remote model discovery: no new models compared with local MODEL_SPECS")
        return

    logger.warning("Remote model discovery found %d new model(s) not in local MODEL_SPECS:", len(discovered))
    for model in discovered:
        logger.warning(
            "  - aiType=%s simpleName=%s aiName=%s rootAiType=%s",
            model.get("aiType"),
            model.get("simpleName"),
            model.get("aiName"),
            model.get("rootAiType"),
        )
