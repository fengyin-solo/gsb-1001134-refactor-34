"""巡查约定编译器接口。

- POST /api/patrol/convention/records/{id}/adjudicate：统一裁决（按发布策略自动走新/旧）；
- GET  发布策略、影子记录、抽样批次、事件日志；
- POST 切流、一键回退、开比对批次、存量迁移、事件重放。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.patrol_convention import convention_service
from app.patrol_convention.conventions import list_versions
from app.patrol_convention.writeback import FLEET_TABLE, projected_snapshot
from app.store import store

router = APIRouter(prefix="/api/patrol/convention", tags=["巡查约定编译器"])

VALID_SOURCES = ("terminal", "map", "backend")


class AdjudicatePayload(BaseModel):
    source: str = Field(default="backend", description="terminal/map/backend：旧实现时代按端出建议")
    version: str | None = Field(default=None, description="约定版本，缺省走现行版本")
    operator: str = "system"


class RolloutPayload(BaseModel):
    mode: str
    canary_crews: list[str] = Field(default_factory=list)
    reason: str = ""
    operator: str = "system"


class RollbackPayload(BaseModel):
    reason: str = ""
    operator: str = "system"


class BatchPayload(BaseModel):
    ratio: float = 0.2
    salt: str = ""
    version: str | None = None
    crew: str | None = None
    scope: str = "manual"


class MigratePayload(BaseModel):
    batch_id: str = "MIG-0001"
    operator: str = "system"


def _bootstrap() -> None:
    # 幂等初始化基线快照（重复调用会刷新为当前状态，仅启动时执行一次）
    convention_service.bootstrap()


@router.post("/bootstrap")
def bootstrap() -> dict[str, Any]:
    """登记基线快照（服务启动后首次操作前自动执行，也可手动重置）。"""
    _bootstrap()
    return {"ok": True, "snapshots": ["derived-ledgers"]}


@router.get("/versions")
def versions() -> dict[str, Any]:
    """已注册的约定版本（约定水位取值域）。"""
    return {"current": list_versions()[-1]["version"], "versions": list_versions()}


@router.post("/records/{entry_id}/adjudicate")
def adjudicate(entry_id: int, payload: AdjudicatePayload) -> dict[str, Any]:
    """对单条巡查记录裁决；实际生效的是编译器还是旧裁决取决于发布策略与班组。"""
    if payload.source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"source 仅支持 {VALID_SOURCES}")
    from app.patrol_convention.conventions import CURRENT_VERSION
    result = convention_service.adjudicate(
        entry_id,
        source=payload.source,
        version=payload.version or CURRENT_VERSION,
        operator=payload.operator,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/rollout")
def rollout_status() -> dict[str, Any]:
    """当前发布策略：模式 + 切流班组白名单 + 变更历史。"""
    return convention_service.rollout_status()


@router.post("/rollout")
def switch_rollout(payload: RolloutPayload) -> dict[str, Any]:
    """切换发布策略（shadow/canary/compiler/legacy）。"""
    try:
        return convention_service.switch_rollout(
            payload.mode, canary_crews=payload.canary_crews,
            reason=payload.reason, operator=payload.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rollback")
def rollback(payload: RollbackPayload) -> dict[str, Any]:
    """一键切回旧实现（紧急止损）。"""
    return convention_service.rollback(reason=payload.reason, operator=payload.operator)


@router.get("/shadow")
def shadow_records(
    drift_only: bool = Query(default=False, description="只看新旧有偏离的影子记录"),
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    rows = convention_service.shadow_records(drift_only=drift_only, limit=limit)
    return {"total": len(rows), "items": rows}


@router.delete("/shadow")
def clear_shadow() -> dict[str, Any]:
    return convention_service.clear_shadow()


@router.post("/comparisons")
def open_comparison_batch(payload: BatchPayload) -> dict[str, Any]:
    """开一个抽样比对批次：确定性抽样，新旧三套口径逐字段比对。"""
    from app.patrol_convention.conventions import CURRENT_VERSION
    return convention_service.open_comparison_batch(
        ratio=payload.ratio, salt=payload.salt,
        version=payload.version or CURRENT_VERSION,
        crew=payload.crew, scope=payload.scope,
    )


@router.get("/comparisons")
def list_comparison_batches() -> dict[str, Any]:
    return {"items": convention_service.comparison_batches()}


@router.get("/comparisons/{batch_id}")
def get_comparison_batch(batch_id: str) -> dict[str, Any]:
    batch = convention_service.comparison_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"比对批次 {batch_id} 不存在")
    return batch


@router.post("/migration")
def migrate(payload: MigratePayload) -> dict[str, Any]:
    """存量巡查记录补约定水位（按当时约定留档，不重算结论，可重复执行）。"""
    return convention_service.migrate_legacy(batch_id=payload.batch_id, operator=payload.operator)


@router.get("/events")
def list_events(event_type: str | None = Query(default=None)) -> dict[str, Any]:
    """事件日志（只增），可按事件类型过滤。"""
    return {"items": convention_service.list_events(event_type)}


@router.post("/replay")
def replay() -> dict[str, Any]:
    """事件重放：回基线后顺序重演，返回是否收敛到重放前状态（幂等校验）。"""
    return convention_service.replay()


@router.get("/fleet-todos")
def fleet_todos() -> dict[str, Any]:
    """车队待办（编译器回写目标之一）。"""
    return {"items": store.rows(FLEET_TABLE)}


@router.get("/state")
def derived_state() -> dict[str, Any]:
    """三处派生台账当前状态（巡查台账/病害清单/车队待办）。"""
    return projected_snapshot()
