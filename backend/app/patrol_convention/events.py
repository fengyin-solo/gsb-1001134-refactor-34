"""只增事件日志：裁决域所有状态变化先落事件、再派生台账。

关键纪律：
- 幂等：相同 event_key 的事件重复提交直接返回已存在事件，绝不重复派生；
- 原子：一次裁决要回写三处台账（巡查台账/病害清单/车队待办），三处全部成功
  才把事件追加进日志；任一处失败整体回滚，不能只写入半个事件；
- 可重放：派生表可以回到基线快照后从事件日志重放，重放结果必须与现态一致。
"""
from __future__ import annotations

import copy
import itertools
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class EventError(RuntimeError):
    """事件提交失败：调用方应据此中止整次操作（回写不会留下半个事件）。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLog:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._index: dict[str, dict[str, Any]] = {}
        self._seq = itertools.count(1)
        self._lock = threading.RLock()
        # 派生状态基线快照：重放前先 restore 到这里
        self._snapshots: dict[str, Callable[[], None]] = {}
        self._clock: Callable[[], str] = utc_now

    def set_clock(self, clock: Callable[[], str]) -> None:
        """注入时钟，仅用于可重复的测试。"""
        self._clock = clock

    def register_snapshot(self, name: str, restore: Callable[[], None]) -> None:
        """登记一个派生状态的基线恢复函数。"""
        self._snapshots[name] = restore

    def reset(self) -> None:
        """清空日志与索引（仅供测试与本地演练回到基线使用）。"""
        with self._lock:
            self._events = []
            self._index = {}
            self._seq = itertools.count(1)
            self._clock = utc_now

    def list_events(self, *, event_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = [dict(event) for event in self._events]
        if event_type:
            events = [event for event in events if event["event_type"] == event_type]
        return events

    def get(self, event_key: str) -> dict[str, Any] | None:
        with self._lock:
            event = self._index.get(event_key)
            return dict(event) if event else None

    def exists(self, event_key: str) -> bool:
        with self._lock:
            return event_key in self._index

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_key: str,
        batch_id: str | None = None,
        operator: str = "system",
    ) -> tuple[dict[str, Any], bool]:
        """追加事件；返回 (事件, 是否新建)。重复键返回旧事件且不产生副作用。"""
        with self._lock:
            existing = self._index.get(event_key)
            if existing is not None:
                return dict(existing), False
            event = {
                "event_id": next(self._seq),
                "event_type": event_type,
                "event_key": event_key,
                "batch_id": batch_id,
                "operator": operator,
                "occurred_at": self._clock(),
                "payload": copy.deepcopy(payload),
            }
            self._events.append(event)
            self._index[event_key] = event
            return dict(event), True

    def commit_atomically(
        self,
        *,
        prepare: Callable[[], list[tuple[str, dict[str, Any], str]]],
        mutate: Callable[[list[dict[str, Any]]], None],
    ) -> tuple[list[dict[str, Any]], list[bool]]:
        """事务式提交：

        1. prepare() 给出待提交事件列表 (type, payload, key)，并据此做幂等裁决；
        2. mutate(events) 执行全部派生写入，内部可抛异常；
        3. 全部成功才 append 事件；异常则一个事件都不进日志，并向上抛出。

        已存在的事件键会被跳过（不重复 mutate），保证重入安全。
        """
        with self._lock:
            pending: list[tuple[str, dict[str, Any], str]] = []
            events: list[dict[str, Any]] = []
            created_flags: list[bool] = []
            for event_type, payload, key in prepare():
                existing = self._index.get(key)
                if existing is not None:
                    events.append(dict(existing))
                    created_flags.append(False)
                    continue
                pending.append((event_type, payload, key))

            if pending:
                # 先给挂起事件分配序号与外壳，但暂不入索引——mutate 失败即整体丢弃
                staged: list[dict[str, Any]] = []
                for event_type, payload, key in pending:
                    staged.append({
                        "event_id": next(self._seq),
                        "event_type": event_type,
                        "event_key": key,
                        "batch_id": payload.get("_batch_id"),
                        "operator": str(payload.get("_operator") or "system"),
                        "occurred_at": self._clock(),
                        "payload": copy.deepcopy(
                            {k: v for k, v in payload.items() if not k.startswith("_")}
                        ),
                    })
                try:
                    mutate(staged)
                except Exception as exc:  # 派生写入失败：半个事件也不留
                    raise EventError(f"事件派生写入失败，已整体回滚：{exc}") from exc
                for event in staged:
                    self._events.append(event)
                    self._index[event["event_key"]] = event
                events.extend(dict(event) for event in staged)
                created_flags.extend([True] * len(staged))

            return events, created_flags

    def reset_to_baseline(self) -> list[str]:
        """把全部派生状态恢复到基线快照（重放前调用）。"""
        restored = []
        for name, restore in self._snapshots.items():
            restore()
            restored.append(name)
        return restored

    def replay(self, apply: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """按事件发生顺序重放全部事件，逐事件幂等应用。

        apply 内部抛异常会中断重放——调用方据此知道第几个事件无法重演。
        """
        with self._lock:
            events = [dict(event) for event in self._events]
        applied = 0
        skipped = 0
        for event in events:
            result = apply(event)
            if result is False:
                skipped += 1
            else:
                applied += 1
        return {"events": len(events), "applied": applied, "skipped": skipped}


event_log = EventLog()
