import logging
from datetime import datetime

import requests

from .config import GENAI_MODELS_URL, Settings, build_genai_headers

logger = logging.getLogger("genai-proxy")

MODEL_SPECS = [
    {
        "public_id": "kimi-k3",
        "request_id": "Kimi-k3",
        "actual_id": "Kimi-K3",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "deepseek-v4.1",
        "request_id": "deepseek-pro",
        "actual_id": "DeepSeek-V4.1",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "glm-5.3-flash",
        "request_id": "chatglm",
        "actual_id": "GLM-5.3-Flash",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "qwen-3.8",
        "request_id": "qwen-instruct",
        "actual_id": "Qwen-3.8",
        "root_ai_type": "xinference",
    },
    {
        "public_id": "gpt-6-astra",
        "request_id": "gpt-6-astra",
        "actual_id": "GPT-6-Astra",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5.6-sol",
        "request_id": "GPT-5.6-SOL",
        "actual_id": "GPT-5.6-Sol",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5.6-terra",
        "request_id": "GPT-5.6-Terra",
        "actual_id": "GPT-5.6-Terra",
        "root_ai_type": "azure",
    },
    {
        "public_id": "gpt-5.6-luna",
        "request_id": "GPT-5.6-Luna",
        "actual_id": "GPT-5.6-Luna",
        "root_ai_type": "azure",
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
