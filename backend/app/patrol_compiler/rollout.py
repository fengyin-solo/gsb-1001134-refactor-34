"""发布控制：影子运行、逐班组切流、一键切回旧实现。

状态机（force_legacy 是最高优先级的总闸，任何模式下都立即切回旧三端）：

    shadow  -> canary（canary_crews 白名单内的班组走编译器，其余仍走旧实现，
                         且任何时候编译器都会影子双跑并记录偏离）
    canary  -> full（全部走编译器，影子仍可用于观察新版本约定）
    *       -> force_legacy=True（一键切回）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.patrol_compiler.events import Event, utc_now_text

MODES = ("shadow", "canary", "full")


@dataclass
class RolloutState:
    mode: str = "shadow"
    canary_crews: list[str] = field(default_factory=list)
    force_legacy: bool = False
    active_convention_version: str | None = None
    updated_at: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "canary_crews": list(self.canary_crews),
            "force_legacy": self.force_legacy,
            "active_convention_version": self.active_convention_version,
            "updated_at": self.updated_at,
            "reason": self.reason,
        }


class RolloutService:
    """当前生效的发布状态；从事件流重放得到，不靠单独配置文件。"""

    def __init__(self, events: list[Event] | None = None, *,
                 default_convention_version: str) -> None:
        self._state = RolloutState(active_convention_version=default_convention_version)
        if events:
            for event in events:
                if event.event_type == "RolloutChanged":
                    self.apply(event.payload)

    @property
    def state(self) -> RolloutState:
        return self._state

    def apply(self, payload: dict[str, Any]) -> RolloutState:
        mode = str(payload.get("mode", self._state.mode))
        if mode not in MODES:
            raise ValueError(f"未知发布模式：{mode}，允许 {list(MODES)}")
        crews = list(payload.get("canary_crews", self._state.canary_crews))
        state = RolloutState(
            mode=mode,
            canary_crews=crews,
            force_legacy=bool(payload.get("force_legacy", False)),
            active_convention_version=payload.get("active_convention_version")
                or self._state.active_convention_version,
            updated_at=payload.get("updated_at") or utc_now_text(),
            reason=str(payload.get("reason", "")),
        )
        self._state = state
        return state

    def fallback_all(self, reason: str) -> RolloutState:
        """一键切回旧实现：保留当前切流进度，只抬总闸，便于排查后恢复。"""
        self._state = RolloutState(
            mode=self._state.mode,
            canary_crews=list(self._state.canary_crews),
            force_legacy=True,
            active_convention_version=self._state.active_convention_version,
            updated_at=utc_now_text(),
            reason=reason or "人工一键切回旧实现",
        )
        return self._state

    def resolve_engine(self, *, crew: str | None, terminal: str | None) -> str:
        """裁决本次请求由编译器还是旧实现出权威结论。

        返回 "compiler" 或 "legacy"；影子双跑由上层始终执行，不在这里决定。
        """
        if self._state.force_legacy:
            return "legacy"
        if self._state.mode == "shadow":
            return "legacy"
        if self._state.mode == "full":
            return "compiler"
        # canary：只按班组切（终端不再各自为政，这正是收编目的）
        if crew in self._state.canary_crews:
            return "compiler"
        return "legacy"
