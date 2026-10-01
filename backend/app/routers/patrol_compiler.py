"""巡查约定编译器接口：约定管理、裁决、切流、迁移、抽样比对与事件重放。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.runtime import get_service

router = APIRouter(prefix="/api/patrol-compiler", tags=["巡查约定编译器"])


class ValuesBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class AdjudicateBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    terminal: str | None = None
    convention_version: str | None = None
    idempotency_key: str | None = None


class MigrateBody(BaseModel):
    patrol_ids: list[int]
    batch_label: str
    idempotency_key: str | None = None
    strict: bool = True


class ReconcileBody(BaseModel):
    samples: list[dict[str, Any]]
    batch_label: str
    convention_version: str | None = None


def _svc():
    return get_service()


@router.get("/conventions")
def list_conventions() -> dict[str, Any]:
    """列出注册表内全部约定版本、指纹与规则顺序。"""
    return {"items": _svc().list_conventions(), "watermark": _svc().watermark()}


@router.post("/compile")
def dry_compile(payload: ValuesBody) -> dict[str, Any]:
    """试编译一份约定草案：不注册、不生效，只返回错误清单或指纹。"""
    return _svc().dry_run_compile(payload.values)


@router.get("/watermark")
def watermark() -> dict[str, Any]:
    """当前约定水位：版本清单、生效日期与指纹。"""
    return _svc().watermark()


@router.get("/rollout")
def get_rollout() -> dict[str, Any]:
    return _svc().get_rollout()


@router.post("/rollout")
def update_rollout(payload: ValuesBody) -> dict[str, Any]:
    """调整发布状态：shadow / canary（带 canary_crews）/ full。"""
    return _svc().update_rollout(payload.values)


@router.post("/rollout/fallback")
def fallback(payload: ValuesBody) -> dict[str, Any]:
    """偏离时一键切回旧三端实现。"""
    return _svc().fallback_to_legacy(str(payload.values.get("reason") or ""))


@router.post("/adjudicate/{patrol_id}")
def adjudicate(patrol_id: int, payload: AdjudicateBody) -> dict[str, Any]:
    """对一条巡查记录执行裁决；影子期权威结论仍来自旧实现并记录偏离。"""
    return _svc().adjudicate(
        patrol_id,
        values=payload.values,
        terminal=payload.terminal,
        convention_version=payload.convention_version,
        idempotency_key=payload.idempotency_key,
    )


@router.post("/migrate")
def migrate(payload: MigrateBody) -> dict[str, Any]:
    """存量巡查按当时约定整批迁移留档；任一条转换失败则整批不落事件。"""
    return _svc().migrate_batch(
        payload.patrol_ids,
        batch_label=payload.batch_label,
        idempotency_key=payload.idempotency_key,
        strict=payload.strict,
    )


@router.post("/reconcile")
def reconcile(payload: ReconcileBody) -> dict[str, Any]:
    """按抽样批次比对新旧裁决，返回逐字段偏离报告。"""
    return _svc().reconcile(
        payload.samples,
        batch_label=payload.batch_label,
        convention_version=payload.convention_version,
    )


@router.get("/drifts")
def list_drifts(limit: int = 100) -> dict[str, Any]:
    """影子运行期记录到的新旧偏离台账。"""
    return {"items": _svc().list_drifts(limit=limit), "total": len(_svc().list_drifts())}


@router.get("/reports")
def list_reports() -> dict[str, Any]:
    return {"items": _svc().list_reports()}


@router.get("/events")
def list_events(limit: int = 100) -> dict[str, Any]:
    """事件日志（排查与审计用）。"""
    events = _svc().list_events(limit=limit)
    return {"items": events, "total": len(events)}


@router.post("/replay")
def replay() -> dict[str, Any]:
    """从事件日志幂等重建全部投影。"""
    return _svc().replay()


@router.get("/fleet-todos")
def fleet_todos() -> dict[str, Any]:
    """车队待办（裁决结论回写产生）。"""
    items = _svc().fleet_todos()
    return {"items": items, "total": len(items)}
