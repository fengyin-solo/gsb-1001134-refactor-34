"""裁决回写：把结论写到巡查台账、病害清单、车队待办三处。

纪律：
- 全部 upsert 走自然键（来源巡查编号），事件重放不产生重复行；
- 一批事件先“暂存”全部改动并校验，任一结论不合法则整体不落笔，
  配合事件日志的原子提交，杜绝“转换未成功只写入半个事件”；
- 字段同时写既有中文名（处置措施/病害状态/…）与新水位字段，兼容既有取值协议。
"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store

PATROL_TABLE = "patrol"
DEFECT_TABLE = "pavement"
FLEET_TABLE = "fleet_todo"

DEFECT_SEED_FIELDS = ["病害编号", "所属路段", "病害类型", "严重程度", "起止桩号", "面积", "发现日期", "病害状态"]


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _find_by_natural(table: str, key: str, value: str) -> dict[str, Any] | None:
    for row in store.rows(table):
        if str(row.get(key) or "") == value:
            return row
    return None


def stage_patrol_write(row: dict[str, Any], verdict: dict[str, Any], facts: dict[str, Any],
                       source_label: str) -> dict[str, Any]:
    """构造巡查台账字段覆盖集（不直接落笔）。"""
    return {
        "处置建议": verdict["处置建议"],
        # 既有协议字段：旧页面读的是「处置措施」，继续同步它
        "处置措施": verdict["处置建议"],
        "处置版本": verdict["约定版本"],
        "约定版本": verdict["约定版本"],
        "约定水位": verdict["约定水位"],
        "裁决来源": source_label,
        "裁决优先级": verdict["优先级"],
        "裁决时限": verdict["时限"],
        "裁决指纹": verdict.get("事实指纹", ""),
        "严重程度": facts["严重程度"],
        "问题类型": facts["问题类型"],
        "管养班组": facts["管养班组"] if row.get("管养班组") in (None, "") else row.get("管养班组"),
        "pending": row.get("pending", True),
    }


def stage_defect_upsert(verdict: dict[str, Any], facts: dict[str, Any], patrol_code: str) -> tuple[str, dict[str, Any]] | None:
    if not verdict["回写目标"].get("病害清单"):
        return None
    values = {
        "病害编号": f"DEF-{patrol_code}",
        "所属路段": facts["巡查路段"],
        "病害类型": facts["问题类型"],
        "严重程度": facts["严重程度"],
        "起止桩号": facts["巡查路段"],
        "面积": "",
        "发现日期": facts["巡查日期"],
        "病害状态": "待修复",
        "来源巡查编号": patrol_code,
        "约定版本": verdict["约定版本"],
        "约定水位": verdict["约定水位"],
        "处置建议": verdict["处置建议"],
        "status": "待修复",
        "pending": True,
        "abnormal": facts["严重程度"] == "严重",
    }
    return ("来源巡查编号", values)


def stage_fleet_upsert(verdict: dict[str, Any], facts: dict[str, Any], patrol_code: str) -> tuple[str, dict[str, Any]] | None:
    if not verdict["回写目标"].get("车队待办"):
        return None
    values = {
        "待办编号": f"TODO-{patrol_code}",
        "来源巡查编号": patrol_code,
        "巡查路段": facts["巡查路段"],
        "巡查日期": facts["巡查日期"],
        "管养班组": facts["管养班组"],
        "待办事项": verdict["处置建议"],
        "优先级": verdict["优先级"],
        "时限": verdict["时限"],
        "车辆类型": "综合养护车",
        "待办状态": "待派车",
        "约定版本": verdict["约定版本"],
        "约定水位": verdict["约定水位"],
        "status": "待派车",
        "pending": True,
        "abnormal": verdict["优先级"] == "P1",
    }
    return ("来源巡查编号", values)


def _validate_staged(verdict: dict[str, Any]) -> None:
    for field in ("处置建议", "优先级", "时限", "约定版本", "回写目标"):
        if field not in verdict or verdict[field] in (None, ""):
            raise ValueError(f"裁决缺少必要字段「{field}」，拒绝回写")
    if not isinstance(verdict.get("回写目标"), dict):
        raise ValueError("裁决的回写目标不是协议约定的结构，拒绝回写")


def _upsert(table: str, natural_key: str, values: dict[str, Any]) -> None:
    row = _find_by_natural(table, natural_key, str(values.get(natural_key) or ""))
    if row is None:
        entry = {"id": _next_id(table)}
        entry.update(values)
        store.rows(table).append(entry)
    else:
        row.update(values)


def apply_events(events: list[dict[str, Any]]) -> None:
    """暂存并提交一批事件的全部台账改动（all-or-nothing）。"""
    operations: list[tuple[str, str, dict[str, Any]]] = []
    patrol_overrides: list[tuple[int, dict[str, Any]]] = []

    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]

        if event_type == "VerdictApplied":
            verdict, facts = payload["verdict"], payload["facts"]
            patrol_code = str(payload["巡查编号"])
            _validate_staged(verdict)
            row = store.find(PATROL_TABLE, int(payload["patrol_id"]))
            if row is not None:
                patrol_overrides.append((
                    int(payload["patrol_id"]),
                    stage_patrol_write(row, verdict, facts, source_label="巡查约定编译器"),
                ))
            for stage in (stage_defect_upsert, stage_fleet_upsert):
                staged = stage(verdict, facts, patrol_code)
                if staged:
                    table = DEFECT_TABLE if stage is stage_defect_upsert else FLEET_TABLE
                    natural_key, values = staged
                    operations.append((table, natural_key, values))

        elif event_type == "LegacyRulingApplied":
            verdict, facts = payload["verdict"], payload["facts"]
            row = store.find(PATROL_TABLE, int(payload["patrol_id"]))
            if row is not None:
                patrol_overrides.append((
                    int(payload["patrol_id"]),
                    stage_patrol_write(row, verdict, facts, source_label=verdict.get("旧裁决来源", "旧实现")),
                ))

        elif event_type == "WaterlineBackfilled":
            row = store.find(PATROL_TABLE, int(payload["patrol_id"]))
            waterline = payload["waterline"]
            if row is not None:
                patrol_overrides.append((
                    int(payload["patrol_id"]),
                    {"约定版本": waterline, "约定水位": waterline, "处置版本": waterline},
                ))

        elif event_type in ("ShadowVerdictComputed", "RolloutSwitched", "SamplingBatchOpened"):
            continue  # 非台账事件，由各自模块消费

        else:
            raise ValueError(f"未知事件类型 {event_type}，拒绝盲目回写")

    # 暂存阶段全部成功后一次性落笔
    for patrol_id, overrides in patrol_overrides:
        row = store.find(PATROL_TABLE, patrol_id)
        if row is not None:
            row.update(overrides)
    for table, natural_key, values in operations:
        _upsert(table, natural_key, values)


def projected_snapshot() -> dict[str, Any]:
    """台账派生状态快照（用于重放前后一致性比对）。"""
    def slim(table: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [{field: row.get(field) for field in fields} for row in store.rows(table)]

    return {
        "patrol": slim(PATROL_TABLE, (
            "id", "巡查编号", "处置建议", "处置措施", "约定版本", "约定水位",
            "裁决来源", "裁决优先级", "裁决时限", "裁决指纹", "严重程度", "问题类型",
        )),
        "pavement": slim(DEFECT_TABLE, (
            "id", "病害编号", "来源巡查编号", "病害类型", "严重程度", "病害状态", "约定版本",
        )),
        "fleet_todo": slim(FLEET_TABLE, (
            "id", "待办编号", "来源巡查编号", "待办事项", "优先级", "时限", "管养班组", "待办状态", "约定版本",
        )),
    }
