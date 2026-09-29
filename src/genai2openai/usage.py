"""Token 用量统计：每次上游调用追加一行 JSONL 记账，按需聚合查询。

数据文件为启动目录下的 `.genai_usage.jsonl`（与 token 缓存同位置约定）。
上游只返回 totalTokens 总数（含 prompt 与思维链），无法拆分 prompt/completion；
上游未返回 token 数的请求记 `estimated: true`（消耗存在但无法计量）。
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta

logger = logging.getLogger("genai-proxy")

USAGE_LOG_PATH = os.path.join(os.getcwd(), ".genai_usage.jsonl")

_lock = threading.Lock()


def record_usage(model, upstream_model=None, total_tokens=None, kind="chat"):
    """追加一条用量记录；记录失败只记日志，绝不打断请求链路。

    Args:
        model (str): 客户端请求的模型名（含 -search/-thinking 等后缀）。
        upstream_model (str | None): 上游真实模型名（区分本地/Azure 路由）。
        total_tokens (int | None): 上游返回的真实 totalTokens；None 表示上游未提供。
        kind (str): 请求类型（chat / image）。
    """
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "model": model,
        "upstream_model": upstream_model,
        "total_tokens": total_tokens,
        "estimated": total_tokens is None,
    }
    try:
        line = json.dumps(record, ensure_ascii=False)
        with _lock:
            with open(USAGE_LOG_PATH, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
    except OSError:
        logger.warning("failed to write usage record", exc_info=True)


def summarize_usage(days=30):
    """聚合最近 days 天（含今天）的用量记录。

    Args:
        days (int): 统计窗口天数。

    Returns:
        dict: 总量、按模型、按天 × 模型三级聚合。文件不存在或读失败时返回
        全零统计；坏行跳过。
    """
    since = (datetime.now() - timedelta(days=days - 1)).date().isoformat()
    summary = {
        "since": since,
        "days": days,
        "total_requests": 0,
        "total_tokens": 0,
        "estimated_requests": 0,
        "by_model": {},
        "by_day": {},
    }
    if not os.path.exists(USAGE_LOG_PATH):
        return summary

    try:
        with open(USAGE_LOG_PATH, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                day = str(record.get("ts", ""))[:10]
                if not day or day < since:
                    continue
                _accumulate(summary, day, record)
    except OSError:
        logger.warning("failed to read usage log", exc_info=True)

    return summary


def _accumulate(summary, day, record):
    """把一条记录累加进总量、by_model 与 by_day 三层桶。"""
    tokens = record.get("total_tokens") or 0
    estimated = bool(record.get("estimated"))
    model = record.get("model") or "unknown"

    summary["total_requests"] += 1
    summary["total_tokens"] += tokens
    if estimated:
        summary["estimated_requests"] += 1

    bucket = summary["by_model"].setdefault(model, {"requests": 0, "tokens": 0, "estimated": 0})
    bucket["requests"] += 1
    bucket["tokens"] += tokens
    bucket["estimated"] += int(estimated)

    day_bucket = summary["by_day"].setdefault(day, {"requests": 0, "tokens": 0, "by_model": {}})
    day_bucket["requests"] += 1
    day_bucket["tokens"] += tokens
    day_model = day_bucket["by_model"].setdefault(model, {"requests": 0, "tokens": 0})
    day_model["requests"] += 1
    day_model["tokens"] += tokens
