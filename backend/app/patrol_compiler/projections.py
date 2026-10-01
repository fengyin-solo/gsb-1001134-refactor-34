"""读模型投影：把裁决事件回写到巡查台账、病害清单、车队待办。

所有写回都按稳定业务键 upsert，而不是追加：
- 巡查台账：patrol 行就地追加裁决字段（不覆盖既有中文键）；
- 病害清单：按「来源巡查 = 巡查记录 id」幂等 upsert；
- 车队待办：同样按「来源巡查」幂等 upsert。

rebuild_projections 先清掉编译器管理的行/字段，再按事件顺序重放，
因此事件重放任意次结果一致（幂等）。
"""
from __future__ import annotations

from typing import Any

from app.patrol_compiler.events import Event
from app.patrol_compiler.types import FactSet, Verdict

PROBLEM_LABELS = {
    "CRACK": "裂缝",
    "POTHOLE": "坑槽",
    "RUT": "车辙",
    "SUBSIDENCE": "沉陷",
    "WATER": "积水",
    "BLOCKAGE": "堵塞",
    "DEFECT": "设施损坏",
    "NORMAL": "无异常",
    "UNKNOWN": "待识别",
}

# 编译器写进 patrol 行的裁决字段，重放时按这份清单精确清理
PATROL_VERDICT_KEYS = (
    "处置建议", "处置建议码", "处置时限小时", "安全预警",
    "转病害清单", "转车队待办", "处置班组",
    "约定版本", "约定指纹", "裁决引擎", "命中规则",
    "裁决模式", "裁决时间", "问题码",
    "_约定版本", "_约定指纹", "_裁决引擎", "_裁决模式",
)


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class ProjectionState:
    def __init__(self, store: Any) -> None:
        self.store = store
        self.facts: dict[int, dict[str, Any]] = {}
        self.drifts: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.rollout: dict[str, Any] | None = None
        self.adjudications: dict[int, dict[str, Any]] = {}
        # 已按「当时约定」留档冻结的记录：之后任何事件都不再改写其台账裁决字段
        self.archived_ids: set[int] = set()

    def reset(self) -> None:
        """删除编译器管理的全部投影，准备从事件流重建。"""
        # 巡查台账：只抹掉编译器追加的字段，保留台账原有内容
        for row in self.store.rows("patrol"):
            for key in PATROL_VERDICT_KEYS:
                row.pop(key, None)
            row.pop("abnormal", None)  # 由裁决结论重新决定
        # 病害清单 / 车队待办：只删编译器生成或接管的行
        for module in ("pavement", "fleet_todo"):
            table = self.store.rows(module)
            kept = [row for row in table if not row.get("_compiler_managed")]
            table[:] = kept
        self.facts.clear()
        self.drifts.clear()
        self.reports.clear()
        self.rollout = None
        self.adjudications.clear()
        self.archived_ids.clear()


def _apply_adjudicated(state: ProjectionState, event: Event) -> None:
    payload = event.payload
    patrol_id = int(payload["patrol_id"])
    verdict_raw = payload["verdict"]
    verdict = Verdict(**verdict_raw)
    facts = FactSet(**payload["facts"])
    legacy_fields: dict[str, Any] = payload.get("legacy_fields", {})
    archived = bool(payload.get("archived"))
    patrol_row = state.store.find("patrol", patrol_id)

    # 存量留档结论按当时约定冻结：一旦该记录出现 archived 事件，
    # 此后（含重放顺序变化时）任何事件都不再改写它的裁决字段/下游单
    frozen = patrol_id in state.archived_ids
    if archived:
        state.archived_ids.add(patrol_id)

    state.adjudications[patrol_id] = payload
    if patrol_row is not None and not frozen:
        # 既有取值协议：平铺中文键；同时带下划线副本，避免和台账原字段撞名时被误改
        for key, value in legacy_fields.items():
            if isinstance(key, str) and key.isascii():
                continue  # 英文驼峰键不写进中文台账
            patrol_row[key] = value
        patrol_row["问题码"] = facts.problem_code
        patrol_row["裁决模式"] = payload.get("mode", "live")
        patrol_row["裁决时间"] = payload.get("decided_at")
        patrol_row["_约定版本"] = verdict.convention_version
        patrol_row["_约定指纹"] = verdict.convention_fingerprint
        patrol_row["_裁决引擎"] = verdict.engine
        patrol_row["_裁决模式"] = payload.get("mode", "live")
        patrol_row["abnormal"] = verdict.create_defect or facts.is_safety_risk

    # 存量留档只冻结结论，不再新增病害单和车队待办
    if archived or frozen or patrol_row is None:
        return

    _upsert_pavement(state, patrol_row, verdict, facts, payload)
    _upsert_todo(state, patrol_row, verdict, facts, payload)


def _upsert_pavement(
    state: ProjectionState,
    patrol_row: dict[str, Any],
    verdict: Verdict,
    facts: FactSet,
    payload: dict[str, Any],
) -> None:
    if not verdict.create_defect:
        return
    table = state.store.rows("pavement")
    source_key = str(patrol_row["id"])
    existing = next((row for row in table if str(row.get("来源巡查")) == source_key), None)
    common = {
        "所属路段": patrol_row.get("巡查路段", ""),
        "病害类型": PROBLEM_LABELS.get(facts.problem_code, facts.problem_code),
        "严重程度": facts.severity,
        "发现日期": patrol_row.get("巡查日期", payload.get("decided_at", "")[:10]),
        "处置建议": verdict.disposition,
        "处置时限小时": verdict.sla_hours,
        "安全预警": verdict.safety_level,
        "约定版本": verdict.convention_version,
        "约定指纹": verdict.convention_fingerprint,
        "_compiler_managed": True,
    }
    if existing is None:
        new_id = _next_id(table)
        row = {
            "id": new_id,
            "status": "待修复",
            "pending": True,
            "abnormal": verdict.safety_level != "常规",
            "病害编号": f"PAVE-AUTO-{new_id:04d}",
            "来源巡查": source_key,
            "病害状态": "待修复",
            **common,
        }
        table.append(row)
    else:
        existing.update(common)
        existing["_compiler_managed"] = True


def _upsert_todo(
    state: ProjectionState,
    patrol_row: dict[str, Any],
    verdict: Verdict,
    facts: FactSet,
    payload: dict[str, Any],
) -> None:
    if not verdict.create_todo:
        return
    table = state.store.rows("fleet_todo")
    source_key = str(patrol_row["id"])
    existing = next((row for row in table if str(row.get("来源巡查")) == source_key), None)
    common = {
        "巡查路段": patrol_row.get("巡查路段", ""),
        "问题码": facts.problem_code,
        "问题类型": PROBLEM_LABELS.get(facts.problem_code, facts.problem_code),
        "严重程度": facts.severity,
        "处置建议": verdict.disposition,
        "处置时限小时": verdict.sla_hours,
        "安全预警": verdict.safety_level,
        "处置班组": verdict.target_crew or "",
        "约定版本": verdict.convention_version,
        "约定指纹": verdict.convention_fingerprint,
        "来源端": payload.get("terminal") or facts.source,
        "_compiler_managed": True,
    }
    if existing is None:
        new_id = _next_id(table)
        table.append({
            "id": new_id,
            "status": "待派发",
            "pending": True,
            "abnormal": verdict.safety_level != "常规",
            "待办编号": f"TODO-{new_id:04d}",
            "来源巡查": source_key,
            "巡查编号": patrol_row.get("巡查编号", ""),
            "巡查日期": patrol_row.get("巡查日期", ""),
            **common,
        })
    else:
        existing.update(common)
        existing["_compiler_managed"] = True


_HANDLERS = {
    "PatrolFactsConverted": lambda state, event: state.facts.update(
        {int(event.payload["patrol_id"]): event.payload["facts"]}
    ),
    "PatrolAdjudicated": _apply_adjudicated,
    "ShadowDriftObserved": lambda state, event: state.drifts.append(event.payload),
    "RolloutChanged": lambda state, event: setattr_for_rollout(state, event.payload),
    "ReconciliationReported": lambda state, event: state.reports.append(event.payload),
}


def setattr_for_rollout(state: ProjectionState, payload: dict[str, Any]) -> None:
    state.rollout = payload


def apply_event(state: ProjectionState, event: Event) -> None:
    handler = _HANDLERS.get(event.event_type)
    if handler is not None:
        handler(state, event)


def rebuild_projections(store: Any, events: list[Event]) -> ProjectionState:
    """清空编译器投影后按事件顺序重放；任意次调用结果相同。"""
    state = ProjectionState(store)
    state.reset()
    for event in events:
        apply_event(state, event)
    return state
