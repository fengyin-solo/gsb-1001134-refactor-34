"""新旧结果按抽样批次比对。

抽样是确定性的（记录 id + 批次盐做稳定散列），同一批多次重算抽到同一批记录，
便于复核；比对逐字段展开，旧三套口径与编译器的偏离都列出来。
"""
from __future__ import annotations

import hashlib
import itertools
import threading
from typing import Any

from app.patrol_convention import legacy as legacy_mod
from app.patrol_convention.compiler import compile_record
from app.patrol_convention.conventions import CURRENT_VERSION

COMPARE_FIELDS = ("处置建议", "优先级", "时限", "回写目标")

_batch_seq = itertools.count(1)


def _hash_token(token: str) -> int:
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)


def deterministic_sample(records: list[dict[str, Any]], *, ratio: float, salt: str) -> list[dict[str, Any]]:
    """按稳定散列抽样：ratio∈(0,1]，同盐同数据永远抽同一批。"""
    ratio = min(max(ratio, 0.0), 1.0)
    if ratio >= 1.0:
        return list(records)
    bucket = int(ratio * 1000)
    return [
        row for row in records
        if _hash_token(f"{salt}:{row.get('id')}") % 1000 < bucket
    ]


def _diff_fields(new: dict[str, Any], old: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for field in COMPARE_FIELDS:
        if new.get(field) != old.get(field):
            diff[field] = {"compiler": new.get(field), "legacy": old.get(field)}
    return diff


class ComparisonRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.batches: list[dict[str, Any]] = []

    def open_batch(self, records: list[dict[str, Any]], *, ratio: float, salt: str,
                   version: str = CURRENT_VERSION, scope: str = "manual",
                   at: str = "") -> dict[str, Any]:
        sample = deterministic_sample(records, ratio=ratio, salt=salt)
        items: list[dict[str, Any]] = []
        drift_count = 0
        for row in sample:
            _, verdict = compile_record(row, version=version)
            legacy_set = legacy_mod.all_legacy_rulings(row)
            per_source: dict[str, dict[str, Any]] = {}
            row_drift = False
            for source, old in legacy_set.items():
                diff = _diff_fields(verdict, old)
                if diff:
                    row_drift = True
                per_source[source] = {
                    "label": legacy_mod.SOURCE_LABELS[source],
                    "legacy": {field: old.get(field) for field in COMPARE_FIELDS},
                    "diff": diff,
                }
            drift_count += 1 if row_drift else 0
            items.append({
                "patrol_id": row.get("id"),
                "巡查编号": row.get("巡查编号"),
                "管养班组": row.get("管养班组") or "未排班",
                "compiler": {field: verdict.get(field) for field in COMPARE_FIELDS},
                "约定版本": verdict["约定版本"],
                "sources": per_source,
                "drift": row_drift,
            })

        batch = {
            "batch_id": f"CMP-{next(_batch_seq):04d}",
            "opened_at": at,
            "scope": scope,
            "salt": salt,
            "ratio": ratio,
            "version": version,
            "population": len(records),
            "sampled": len(sample),
            "drift": drift_count,
            "drift_ratio": round(drift_count / len(sample), 4) if sample else 0.0,
            "items": items,
        }
        with self._lock:
            self.batches.append(batch)
        return batch

    def list_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{key: value for key, value in batch.items() if key != "items"}
                    for batch in self.batches]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            for batch in self.batches:
                if batch["batch_id"] == batch_id:
                    return dict(batch)
        return None


comparisons = ComparisonRegistry()


def diff_pair(verdict: dict[str, Any], old: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _diff_fields(verdict, old)
