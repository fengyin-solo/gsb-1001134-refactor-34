"""巡查约定编译器对外门面：裁决、影子运行、切流、迁移、重放都从这里进。

路由规则（见 rollout）：
- shadow：旧裁决生效，编译器结果仅影子记录；
- canary：白名单班组走编译器，其余走旧裁决；任何请求仍生成影子比对；
- compiler：全部走编译器；
- legacy：一键切回，全部走旧裁决且停止影子计算。
"""
from __future__ import annotations

import copy
import itertools
from typing import Any

from app.patrol_convention.compiler import compile_record
from app.patrol_convention.conventions import (
    BASELINE_VERSION,
    CURRENT_VERSION,
    LEGACY_WATERLINE,
)
from app.patrol_convention.events import EventError, event_log, utc_now
from app.patrol_convention.legacy import SOURCE_LABELS, legacy_ruling
from app.patrol_convention.protocol import normalize
from app.patrol_convention.rollout import rollout
from app.patrol_convention.sampling import comparisons, diff_pair
from app.patrol_convention.writeback import apply_events, projected_snapshot
from app.store import store

_shadow_seq = itertools.count(1)

# 影子记录不参与台账重放，单独留一份（可清空、可导出）
_shadow_log: list[dict[str, Any]] = []


class ConventionService:
    # ---------- 初始化 ----------
    def bootstrap(self) -> None:
        """登记派生状态基线快照：重放时先回基线，再从事件日志重演。"""
        baseline = {
            table: copy.deepcopy(store.rows(table))
            for table in ("patrol", "pavement", "fleet_todo")
        }
        baseline.setdefault("fleet_todo", [])

        def restore() -> None:
            for table, rows in baseline.items():
                store._tables[table] = copy.deepcopy(rows)  # noqa: SLF001

        event_log.register_snapshot("derived-ledgers", restore)

    # ---------- 裁决 ----------
    def adjudicate(self, patrol_id: int, *, source: str = "backend",
                   version: str = CURRENT_VERSION, operator: str = "system") -> dict[str, Any]:
        row = store.find("patrol", patrol_id)
        if row is None:
            return {"ok": False, "message": f"巡查记录 {patrol_id} 不存在或已归档"}
        source = source if source in SOURCE_LABELS else "backend"
        facts, verdict = compile_record(row, version=version)
        crew = facts["管养班组"]
        route = rollout.route(crew)
        mode = rollout.snapshot()["mode"]
        patrol_code = str(row.get("巡查编号") or patrol_id)
        at = utc_now()

        # 影子运行：新编译器同步算出结果与偏离，但不碰台账
        shadow: dict[str, Any] | None = None
        old_view: dict[str, Any] | None = None
        if rollout.shadow_enabled():
            old_view = legacy_ruling(row, source)
            drift = diff_pair(verdict, old_view)
            shadow = {
                "shadow_id": f"SH-{next(_shadow_seq):05d}",
                "at": at,
                "patrol_id": patrol_id,
                "巡查编号": patrol_code,
                "管养班组": crew,
                "mode": mode,
                "source": source,
                "version": version,
                "compiler": verdict,
                "legacy": old_view,
                "drift": drift,
            }
            _shadow_log.append(shadow)
            # 影子事件也进事件日志，但事件类型标记为非派生，重放不落账
            event_log.append(
                "ShadowVerdictComputed",
                {
                    "patrol_id": patrol_id, "巡查编号": patrol_code,
                    "source": source, "version": version,
                    "drift_fields": sorted(drift),
                },
                event_key=f"shadow:{patrol_id}:{source}:{version}:{len(_shadow_log)}",
                operator=operator,
            )

        if route == "compiler":
            applied = self._apply_new(
                patrol_id=patrol_id, patrol_code=patrol_code,
                facts=facts, verdict=verdict, mode=mode,
                operator=operator, at=at,
            )
            return {
                "ok": True, "applied": "compiler", "mode": mode,
                "verdict": verdict, "facts": facts, "shadow": shadow,
                "events": [{"event_key": e["event_key"], "created": created}
                           for e, created in zip(*applied)],
            }

        old_view = old_view or legacy_ruling(row, source)
        applied = self._apply_legacy(
            patrol_id=patrol_id, patrol_code=patrol_code,
            facts=facts, verdict=old_view, source=source,
            operator=operator, at=at,
        )
        return {
            "ok": True, "applied": "legacy", "mode": mode, "source": source,
            "verdict": old_view, "facts": facts, "shadow": shadow,
            "events": [{"event_key": e["event_key"], "created": created}
                       for e, created in zip(*applied)],
        }

    def _apply_new(self, *, patrol_id: int, patrol_code: str, facts: dict[str, Any],
                   verdict: dict[str, Any], mode: str, operator: str, at: str):
        # 幂等键：同一记录同一约定版本同一事实指纹只裁决一次；事实变了指纹会变，允许重新裁决
        key = f"verdict:{patrol_id}:{verdict['约定版本']}:{verdict['事实指纹']}"

        def prepare():
            return [(
                "VerdictApplied",
                {
                    "_batch_id": None, "_operator": operator,
                    "patrol_id": patrol_id, "巡查编号": patrol_code,
                    "facts": facts, "verdict": verdict, "mode": mode,
                },
                key,
            )]

        return event_log.commit_atomically(prepare=prepare, mutate=apply_events)

    def _apply_legacy(self, *, patrol_id: int, patrol_code: str, facts: dict[str, Any],
                      verdict: dict[str, Any], source: str, operator: str, at: str):
        key = f"legacy:{patrol_id}:{source}:{verdict['事实指纹'] or patrol_code}"
        # 旧裁决没有事实指纹概念时按巡查编号幂等，保证切回旧实现重放不重复
        if not verdict.get("事实指纹"):
            key = f"legacy:{patrol_id}:{source}"

        def prepare():
            return [(
                "LegacyRulingApplied",
                {
                    "_batch_id": None, "_operator": operator,
                    "patrol_id": patrol_id, "巡查编号": patrol_code,
                    "facts": facts, "verdict": verdict, "source": source,
                },
                key,
            )]

        return event_log.commit_atomically(prepare=prepare, mutate=apply_events)

    # ---------- 发布控制 ----------
    def switch_rollout(self, mode: str, *, canary_crews: list[str] | None = None,
                       reason: str = "", operator: str = "system") -> dict[str, Any]:
        at = utc_now()
        change = rollout.apply(mode, canary_crews, reason=reason, operator=operator, at=at)
        key = f"rollout:{change['from_mode']}->{mode}:{at}"
        event_log.append(
            "RolloutSwitched",
            {**change, "_operator": operator},
            event_key=key, operator=operator,
        )
        return {"ok": True, "change": change, "config": rollout.snapshot()}

    def rollback(self, *, reason: str = "", operator: str = "system") -> dict[str, Any]:
        at = utc_now()
        change = rollout.rollback(reason=reason, operator=operator, at=at)
        event_log.append(
            "RolloutSwitched",
            {**change, "_operator": operator},
            event_key=f"rollout:{change['from_mode']}->legacy:{at}",
            operator=operator,
        )
        return {"ok": True, "change": change, "config": rollout.snapshot()}

    def rollout_status(self) -> dict[str, Any]:
        return rollout.snapshot()

    # ---------- 影子 ----------
    def shadow_records(self, *, drift_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        rows = list(reversed(_shadow_log))
        if drift_only:
            rows = [item for item in rows if item["drift"]]
        return rows[:limit]

    def clear_shadow(self) -> dict[str, int]:
        count = len(_shadow_log)
        _shadow_log.clear()
        return {"cleared": count}

    # ---------- 抽样批次比对 ----------
    def open_comparison_batch(self, *, ratio: float = 0.2, salt: str = "",
                              version: str = CURRENT_VERSION,
                              crew: str | None = None, scope: str = "manual") -> dict[str, Any]:
        records = store.rows("patrol")
        if crew:
            records = [row for row in records if (row.get("管养班组") or "未排班") == crew]
        salt = salt or f"batch-{len(comparisons.batches) + 1}"
        batch = comparisons.open_batch(
            records, ratio=ratio, salt=salt, version=version,
            scope=scope, at=utc_now(),
        )
        event_log.append(
            "SamplingBatchOpened",
            {"batch_id": batch["batch_id"], "ratio": ratio, "salt": salt,
             "sampled": batch["sampled"], "drift": batch["drift"], "version": version,
             "_operator": "system"},
            event_key=f"cmp-batch:{batch['batch_id']}",
            batch_id=batch["batch_id"],
        )
        return batch

    def comparison_batches(self) -> list[dict[str, Any]]:
        return comparisons.list_batches()

    def comparison_batch(self, batch_id: str) -> dict[str, Any] | None:
        return comparisons.get_batch(batch_id)

    # ---------- 存量迁移（带约定水位） ----------
    def migrate_legacy(self, *, operator: str = "system", batch_id: str = "MIG-0001") -> dict[str, Any]:
        """给存量巡查记录补约定水位，按当时约定留档，不重算结论。

        约定生效日之前的记录水位 legacy；之后到旧账统一前按基线版本 v2026-09。
        已带水位的记录跳过——迁移可重复执行、增量补漏。
        """
        to_backfill: list[dict[str, Any]] = []
        skipped = 0
        for row in store.rows("patrol"):
            if row.get("约定水位"):
                skipped += 1
                continue
            date_text = str(row.get("巡查日期") or "")
            waterline = BASELINE_VERSION if date_text >= "2026-09-01" else LEGACY_WATERLINE
            to_backfill.append({
                "patrol_id": int(row.get("id", 0)),
                "巡查编号": row.get("巡查编号"),
                "waterline": waterline,
                "当时处置": row.get("处置措施"),
            })

        def prepare():
            return [(
                "WaterlineBackfilled",
                {"_batch_id": batch_id, "_operator": operator, **item},
                f"waterline:{batch_id}:{item['patrol_id']}",
            ) for item in to_backfill]

        if to_backfill:
            events, flags = event_log.commit_atomically(prepare=prepare, mutate=apply_events)
        else:
            events, flags = [], []

        result = {
            "ok": True,
            "batch_id": batch_id,
            "backfilled": len(to_backfill),
            "skipped": skipped,
            "events": len(events),
            "events_created": sum(1 for created in flags if created),
            "waterlines": {
                LEGACY_WATERLINE: sum(1 for item in to_backfill if item["waterline"] == LEGACY_WATERLINE),
                BASELINE_VERSION: sum(1 for item in to_backfill if item["waterline"] == BASELINE_VERSION),
            },
        }
        return result

    # ---------- 事件与重放 ----------
    def list_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        return event_log.list_events(event_type=event_type)

    def replay(self) -> dict[str, Any]:
        """回基线 → 顺序重放事件日志；幂等键保证重放不产生重复派生。"""
        before = projected_snapshot()
        restored = event_log.reset_to_baseline()
        report = event_log.replay(apply_events_one)
        after = projected_snapshot()
        report["restored"] = restored
        report["converged"] = before == after
        return report

    def replay_convergence(self) -> dict[str, bool]:
        return {"converged": self.replay()["converged"]}


def apply_events_one(event: dict[str, Any]) -> bool:
    """重放单事件；同一自然键 upsert 天然幂等，始终返回 True。"""
    apply_events([event])
    return True


convention_service = ConventionService()
