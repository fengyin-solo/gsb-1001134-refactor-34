"""养护车辆接口：维护养护车辆，覆盖派车出车、收车归库、送修车辆等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.vehicle import VehicleService
from app.store import store

router = APIRouter(prefix="/api/vehicle", tags=["养护车辆"])

service = VehicleService()

LIST_FIELDS = ["车辆编号", "车辆类型", "车牌号", "所属单位", "年检日期", "驾驶员", "当前里程", "车辆状态"]
STATUSES = ["在库", "出车作业", "维修", "报废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按车辆编号检索"),
    status: str | None = Query(default=None, description="在库、出车作业、维修、报废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按车辆编号与状态过滤养护车辆列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/fleet-todos")
def fleet_todos() -> dict[str, Any]:
    """车队待办：巡查约定编译器裁决后回写生成的处置任务。"""
    rows = store.rows("fleet_todo")
    return {"items": rows, "total": len(rows)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出养护车辆清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "vehicle", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条养护车辆明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"养护车辆 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条养护车辆，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="养护车辆已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条养护车辆执行派车出车、收车归库、送修车辆；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
