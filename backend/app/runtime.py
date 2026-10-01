"""巡查约定编译器运行时单例。

事件日志默认落在 backend/data/patrol_events.jsonl（.gitignore 已排除 data 类
本地产物的同级约定，这里仍显式追加忽略）。可用环境变量覆盖：
- PATROL_EVENT_LOG=/path/to.jsonl  指定日志位置
- PATROL_EVENT_LOG=memory          纯内存（测试用）
"""
from __future__ import annotations

import os

from app.config import settings
from app.patrol_compiler.service import PatrolConventionService
from app.store import store

_service: PatrolConventionService | None = None


def _resolve_log_path() -> str | None:
    override = os.environ.get("PATROL_EVENT_LOG")
    if override == "memory":
        return None
    if override:
        return override
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(backend_root, settings.patrol_event_log)


def get_service() -> PatrolConventionService:
    global _service
    if _service is None:
        _service = PatrolConventionService(store, event_log_path=_resolve_log_path())
    return _service


def reset_service() -> PatrolConventionService:
    """测试钩子：强制重建（内存日志或切换日志位置时用）。"""
    global _service
    _service = PatrolConventionService(store, event_log_path=_resolve_log_path())
    return _service
