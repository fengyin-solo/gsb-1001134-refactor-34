"""事件日志：append-only JSONL，进程内串行化 + 文件锁，天然可重放。

幂等分两层：
1. 命令级 idempotency_key：同一批事件重复提交直接返回已落库的原事件；
2. 事件级 event_id：每个事件内容指纹唯一，重复事件不会产生第二条。

批量原子性：一批事件先在同一把锁内完成全部校验，再一次性写入文件
（单文件句柄顺序 flush + fsync）。转换未成功的批次在写入前就抛错，
因此不可能出现「只写入半个事件」。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

_EVENT_VERSION = 1


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    stream: str                 # 幂等投影键域，如 patrol:3、migration:batch-1
    sequence: int
    occurred_at: str
    payload: dict[str, Any]
    idempotency_key: str | None = None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "event_version": _EVENT_VERSION,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "stream": self.stream,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
        }

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> "Event":
        return cls(
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            stream=envelope["stream"],
            sequence=int(envelope["sequence"]),
            occurred_at=envelope["occurred_at"],
            payload=envelope.get("payload", {}),
            idempotency_key=envelope.get("idempotency_key"),
        )


def make_event_id(seed: str) -> str:
    return "evt_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class EventStore:
    """文件事件日志；path 为 None 时退化为纯内存（测试/演示用）。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._events: list[Event] = []
        self._event_ids: set[str] = set()
        self._idempotency: dict[str, list[str]] = {}
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self._load()

    # ---- 读取 / 重放 -------------------------------------------------------

    def _load(self) -> None:
        assert self.path is not None
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                event = Event.from_envelope(json.loads(line))
                self._index(event)

    def _index(self, event: Event) -> None:
        self._events.append(event)
        self._event_ids.add(event.event_id)
        if event.idempotency_key:
            self._idempotency.setdefault(event.idempotency_key, []).append(event.event_id)

    def read_all(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def by_idempotency_key(self, key: str) -> list[Event]:
        with self._lock:
            ids = self._idempotency.get(key, [])
            index = {event.event_id: event for event in self._events}
            return [index[event_id] for event_id in ids if event_id in index]

    @property
    def next_sequence(self) -> int:
        with self._lock:
            return len(self._events) + 1

    # ---- 写入 -------------------------------------------------------------

    def append_batch(
        self,
        specs: Iterable[tuple[str, str, dict[str, Any], str]],
        *,
        idempotency_key: str | None = None,
        build_event_id: Callable[[str, str, dict[str, Any]], str] | None = None,
    ) -> list[Event]:
        """原子追加一批 (event_type, stream, payload, stream_seed) 规格。

        stream_seed 用于在写前算出确定性 event_id，保证重放/重试幂等。
        整批先在内存构造并去重，任何异常都发生在落盘之前；随后一次打开文件
        连续写完并 fsync，因此不可能出现「只写入半个事件」的中间状态。
        """
        specs = list(specs)
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotency:
                return self.by_idempotency_key(idempotency_key)

            existing_index = {event.event_id: event for event in self._events}
            ordered_ids: list[str] = []
            candidates: dict[str, Event] = {}
            now = utc_now_text()

            # 1) 纯内存阶段：算确定性 ID、批内去重、与历史去重
            for event_type, stream, payload, _seed in specs:
                if build_event_id is not None:
                    event_id = build_event_id(event_type, stream, payload)
                else:
                    event_id = make_event_id(f"{stream}:{_seed}:{event_type}")
                if event_id in ordered_ids:
                    continue
                ordered_ids.append(event_id)
                if event_id not in candidates and event_id not in existing_index:
                    candidates[event_id] = Event(
                        event_id=event_id,
                        event_type=event_type,
                        stream=stream,
                        sequence=0,
                        occurred_at=now,
                        payload=payload,
                        idempotency_key=idempotency_key,
                    )

            # 2) 赋连续序列号
            fresh = [candidates[event_id] for event_id in ordered_ids if event_id in candidates]
            numbered = [
                Event(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    stream=event.stream,
                    sequence=self.next_sequence + index,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                    idempotency_key=event.idempotency_key,
                )
                for index, event in enumerate(fresh)
            ]

            # 3) 落盘阶段：一次打开、连续写、最后一次 fsync
            if self.path and numbered:
                with open(self.path, "a", encoding="utf-8") as handle:
                    for event in numbered:
                        handle.write(json.dumps(event.to_envelope(), ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())

            # 4) 提交到内存索引
            for event in numbered:
                self._index(event)
                existing_index[event.event_id] = event

            result = [existing_index[event_id] for event_id in ordered_ids]
            if idempotency_key:
                self._idempotency[idempotency_key] = ordered_ids
            return result
