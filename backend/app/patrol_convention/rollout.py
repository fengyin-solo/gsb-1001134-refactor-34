"""发布策略：影子运行 → 逐班组切流 → 一键切回旧实现。

模式：
- shadow（默认）：对外仍走旧裁决（按请求 source），新编译器同步计算但只留影子记录，
  绝不回写台账；偏离进入影子比对流；
- canary：按管养班组白名单切流，命中走编译器，其余班组仍走旧裁决；
- legacy：一键切回旧实现，所有端恢复各自旧口径（紧急止损位）；
- compiler：全量编译器。
"""
from __future__ import annotations

import threading
from typing import Any

MODES = ("shadow", "canary", "legacy", "compiler")


class RolloutConfig:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.mode = "shadow"
        self.canary_crews: list[str] = []
        self.history: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "canary_crews": list(self.canary_crews),
                "history": list(self.history),
            }

    def apply(self, mode: str, canary_crews: list[str] | None = None,
              *, reason: str = "", operator: str = "system", at: str = "") -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"发布模式 {mode} 不合法，可选 {MODES}")
        crews = sorted({crew.strip() for crew in (canary_crews or []) if crew.strip()})
        with self._lock:
            change = {
                "from_mode": self.mode,
                "to_mode": mode,
                "canary_crews": crews,
                "reason": reason,
                "operator": operator,
                "at": at,
            }
            self.mode = mode
            self.canary_crews = crews
            self.history.append(change)
            return change

    def route(self, crew: str) -> str:
        """决定一条事实由谁裁决：compiler 或 legacy。"""
        with self._lock:
            if self.mode == "compiler":
                return "compiler"
            if self.mode == "legacy":
                return "legacy"
            if self.mode == "canary" and crew in self.canary_crews:
                return "compiler"
            # shadow 与 canary 下未命中的班组都继续旧实现
            return "legacy"

    def shadow_enabled(self) -> bool:
        with self._lock:
            return self.mode in ("shadow", "canary")

    def rollback(self, *, reason: str, operator: str, at: str) -> dict[str, Any]:
        """一键切回旧实现。"""
        return self.apply("legacy", [], reason=reason or "紧急切回旧裁决", operator=operator, at=at)

    def reset(self) -> None:
        """复位到发布初始状态（影子运行），仅供测试与本地演练使用。"""
        with self._lock:
            self.mode = "shadow"
            self.canary_crews = []
            self.history = []


rollout = RolloutConfig()
