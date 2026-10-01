"""巡查约定编译器应用服务：三端裁决的唯一入口。

流水线（每次裁决）：
    台账/请求取值 -> normalize_facts -> 约定编译器 + 旧端引擎双跑
    -> 偏离记账 -> 按发布状态选权威结论 -> 原子事件批次 -> 投影回写三表

存量迁移与抽样比对复用同一套事件与投影，区别仅在 archived 标记和
「转换失败整批中止」的严格模式。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.patrol_compiler.compiler import (
    ConventionCompileError,
    compile_convention,
    evaluate,
)
from app.patrol_compiler.events import Event, EventStore, utc_now_text
from app.patrol_compiler import legacy as legacy_engines
from app.patrol_compiler.projections import ProjectionState, apply_event, rebuild_projections
from app.patrol_compiler.protocol import normalize_facts, verdict_to_legacy_fields
from app.patrol_compiler.rollout import RolloutService
from app.patrol_compiler.types import (
    CompiledPlan,
    ConventionDoc,
    FactSet,
    RuleDoc,
    Verdict,
)

DIFF_FIELDS = (
    "disposition", "sla_hours", "safety_level",
    "create_defect", "create_todo", "target_crew",
)


def stable_event_id(event_type: str, stream: str, payload: dict[str, Any]) -> str:
    """事件内容指纹：去掉时间戳类易变字段后取哈希，重放/重试不产生重复事件。"""
    stable = {
        key: value
        for key, value in payload.items()
        if key not in ("decided_at", "observed_at", "converted_at")
    }
    blob = f"{event_type}|{stream}|" + json.dumps(
        stable, ensure_ascii=False, sort_keys=True, default=str
    )
    return "evt_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class PatrolConventionService:
    def __init__(self, store: Any, *, event_log_path: str | None = None,
                 registry: Any | None = None) -> None:
        self.store = store
        self.registry = registry or _build_default_registry()
        self.event_store = EventStore(event_log_path)
        self._plans: dict[str, CompiledPlan] = {
            version: compile_convention(self.registry.get(version).doc)
            for version in self.registry.versions
        }
        self.state: ProjectionState = rebuild_projections(store, self.event_store.read_all())
        self.rollout = RolloutService(
            self.event_store.read_all(),
            default_convention_version=self.registry.latest().doc.version,
        )

    # ---- 约定管理 ----------------------------------------------------------

    def list_conventions(self) -> list[dict[str, Any]]:
        result = []
        for version in self.registry.versions:
            item = self.registry.get(version)
            result.append({
                **item.doc.to_dict(),
                "fingerprint": item.fingerprint,
                "rule_names": [rule.name for rule in item.doc.rules],
            })
        return result

    def watermark(self) -> dict[str, Any]:
        return self.registry.watermark()

    def dry_run_compile(self, doc_payload: dict[str, Any]) -> dict[str, Any]:
        """只编译不落库：让约定发布人先看错误清单或指纹。"""
        doc = _doc_from_payload(doc_payload)
        try:
            plan = compile_convention(doc)
        except ConventionCompileError as exc:
            return {"ok": False, "version": doc.version, "errors": exc.errors}
        return {
            "ok": True,
            "version": plan.version,
            "fingerprint": plan.fingerprint,
            "rule_order": [
                {"priority": rule.priority, "name": rule.name} for rule in plan.rules
            ],
        }

    # ---- 裁决 --------------------------------------------------------------

    def adjudicate(
        self,
        patrol_id: int,
        *,
        values: dict[str, Any] | None = None,
        terminal: str | None = None,
        convention_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """对一条巡查记录裁决并回写；重复提交（同幂等键）返回原结论。"""
        command_key = idempotency_key or f"adjudicate:{patrol_id}:{_payload_hash(values, terminal)}"
        existing = self.event_store.by_idempotency_key(command_key)
        if existing:
            adjudicated = next(e for e in existing if e.event_type == "PatrolAdjudicated")
            return {"ok": True, "idempotent": True, **adjudicated.payload}

        patrol_row = self.store.find("patrol", patrol_id)
        if patrol_row is None:
            return {"ok": False, "error": f"巡查记录 {patrol_id} 不存在或已归档"}

        merged = _merge_row_values(patrol_row, values or {}, terminal=terminal)
        facts = normalize_facts(merged)
        terminal_name = terminal or facts.source

        plan = self._plan_for(convention_version)
        doc = self.registry.get(plan.version).doc
        compiler_verdict = evaluate(plan, facts, crew_routing=doc.crew_routing)
        legacy_verdict = legacy_engines.legacy_for(terminal_name)(facts)

        engine = self.rollout.resolve_engine(crew=facts.crew, terminal=terminal_name)
        authoritative = compiler_verdict if engine == "compiler" else legacy_verdict
        in_shadow = self.rollout.state.mode == "shadow" and not self.rollout.state.force_legacy
        mode = "shadow" if in_shadow else "live"

        events: list[tuple[str, str, dict[str, Any], str]] = []
        drift = self._diff_verdicts(compiler_verdict, legacy_verdict)
        if drift:
            events.append(("ShadowDriftObserved", f"patrol:{patrol_id}", {
                "patrol_id": patrol_id,
                "terminal": terminal_name,
                "crew": facts.crew,
                "facts": facts.to_dict(),
                "compiler": compiler_verdict.to_dict(),
                "legacy": legacy_verdict.to_dict(),
                "diff": drift,
                "observed_at": utc_now_text(),
            }, f"drift:{terminal_name}"))

        now = utc_now_text()
        events.append(("PatrolFactsConverted", f"patrol:{patrol_id}", {
            "patrol_id": patrol_id,
            "facts": facts.to_dict(),
            "terminal": terminal_name,
            "converted_at": now,
            "watermark": self.watermark(),
        }, f"facts:{command_key}"))
        events.append(("PatrolAdjudicated", f"patrol:{patrol_id}", {
            "patrol_id": patrol_id,
            "facts": facts.to_dict(),
            "verdict": authoritative.to_dict(),
            "compiler_verdict": compiler_verdict.to_dict(),
            "legacy_verdict": legacy_verdict.to_dict(),
            "legacy_fields": verdict_to_legacy_fields(authoritative),
            "mode": mode,
            "terminal": terminal_name,
            "crew": facts.crew,
            "decided_at": now,
            "archived": False,
            "watermark": self.watermark(),
        }, f"verdict:{command_key}"))

        stored = self.event_store.append_batch(
            events, idempotency_key=command_key, build_event_id=stable_event_id
        )
        for event in stored:
            apply_event(self.state, event)

        return {
            "ok": True,
            "idempotent": False,
            **next(e.payload for e in stored if e.event_type == "PatrolAdjudicated"),
        }

    # ---- 存量迁移（带约定水位）---------------------------------------------

    def migrate_batch(
        self,
        patrol_ids: list[int],
        *,
        batch_label: str,
        idempotency_key: str | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """把存量巡查记录按「当时生效的约定」补裁决并留档。

        严格模式下只要有一条无法识别，整批中止、不落任何事件，
        杜绝「转换未成功只写入半个事件」。
        """
        command_key = idempotency_key or f"migration:{batch_label}"
        existing = self.event_store.by_idempotency_key(command_key)
        if existing:
            return {"ok": True, "idempotent": True, "batch_label": batch_label,
                    "event_count": len(existing)}

        rows: list[tuple[int, dict[str, Any], FactSet, CompiledPlan]] = []
        failures: list[dict[str, Any]] = []
        for patrol_id in patrol_ids:
            row = self.store.find("patrol", patrol_id)
            if row is None:
                failures.append({"patrol_id": patrol_id, "reason": "记录不存在或已归档"})
                continue
            facts = normalize_facts(_merge_row_values(row, {}, terminal=None))
            if strict and not facts.recognized:
                failures.append({
                    "patrol_id": patrol_id,
                    "巡查编号": row.get("巡查编号"),
                    "reason": "无法识别问题码，未通过取值协议转换",
                    "raw_text": facts.raw_text,
                })
            plan = self._plan_for_date(str(row.get("巡查日期") or ""))
            rows.append((patrol_id, row, facts, plan))

        if failures:
            return {"ok": False, "batch_label": batch_label, "failures": failures,
                    "event_count": 0}

        now = utc_now_text()
        specs: list[tuple[str, str, dict[str, Any], str]] = []
        for patrol_id, row, facts, plan in rows:
            doc = self.registry.get(plan.version).doc
            compiler_verdict = evaluate(plan, facts, crew_routing=doc.crew_routing)
            legacy_verdict = legacy_engines.legacy_for(facts.source)(facts)
            specs.append(("PatrolFactsConverted", f"patrol:{patrol_id}", {
                "patrol_id": patrol_id,
                "facts": facts.to_dict(),
                "terminal": facts.source,
                "converted_at": now,
                "batch_label": batch_label,
                "watermark": self.watermark(),
            }, f"facts:{batch_label}"))
            specs.append(("PatrolAdjudicated", f"patrol:{patrol_id}", {
                "patrol_id": patrol_id,
                "facts": facts.to_dict(),
                "verdict": compiler_verdict.to_dict(),
                "compiler_verdict": compiler_verdict.to_dict(),
                "legacy_verdict": legacy_verdict.to_dict(),
                "legacy_fields": verdict_to_legacy_fields(compiler_verdict),
                "mode": "archived",
                "terminal": facts.source,
                "crew": facts.crew,
                "decided_at": now,
                "archived": True,
                "batch_label": batch_label,
                "watermark": self.watermark(),
            }, f"verdict:{batch_label}"))

        stored = self.event_store.append_batch(
            specs, idempotency_key=command_key, build_event_id=stable_event_id
        )
        for event in stored:
            apply_event(self.state, event)
        return {
            "ok": True,
            "idempotent": False,
            "batch_label": batch_label,
            "migrated": len(rows),
            "event_count": len(stored),
            "convention_versions": sorted({plan.version for _, _, _, plan in rows}),
            "watermark": self.watermark(),
        }

    # ---- 新旧抽样比对 -------------------------------------------------------

    def reconcile(
        self,
        samples: list[dict[str, Any]],
        *,
        batch_label: str,
        convention_version: str | None = None,
    ) -> dict[str, Any]:
        """按抽样批次跑新旧两套裁决并出比对报告；不落业务投影、不回写台账。"""
        plan = self._plan_for(convention_version)
        doc = self.registry.get(plan.version).doc
        command_key = f"reconcile:{batch_label}"
        existing = self.event_store.by_idempotency_key(command_key)
        if existing:
            report_event = next(e for e in reversed(existing)
                                if e.event_type == "ReconciliationReported")
            return report_event.payload
        items: list[dict[str, Any]] = []
        unrecognized = 0
        diverged = 0
        for index, sample in enumerate(samples):
            facts = normalize_facts(sample)
            if not facts.recognized:
                unrecognized += 1
            compiler_verdict = evaluate(plan, facts, crew_routing=doc.crew_routing)
            legacy_verdict = legacy_engines.legacy_for(sample.get("source", facts.source))(facts)
            diff = self._diff_verdicts(compiler_verdict, legacy_verdict)
            if diff:
                diverged += 1
            items.append({
                "index": index,
                "source": sample.get("source", facts.source),
                "facts": facts.to_dict(),
                "compiler": compiler_verdict.to_dict(),
                "legacy": legacy_verdict.to_dict(),
                "diff": diff,
            })

        report = {
            "batch_label": batch_label,
            "convention_version": plan.version,
            "convention_fingerprint": plan.fingerprint,
            "total": len(samples),
            "matched": len(samples) - diverged,
            "diverged": diverged,
            "unrecognized": unrecognized,
            "sampled_at": utc_now_text(),
            "watermark": self.watermark(),
            "items": items,
        }
        stored = self.event_store.append_batch(
            [("ReconciliationReported", f"reconcile:{batch_label}", report, batch_label)],
            idempotency_key=command_key,
            build_event_id=stable_event_id,
        )
        apply_event(self.state, stored[-1])
        return report

    # ---- 发布控制 ----------------------------------------------------------

    def get_rollout(self) -> dict[str, Any]:
        return self.rollout.state.to_dict()

    def update_rollout(self, payload: dict[str, Any]) -> dict[str, Any]:
        new_state = self.rollout.apply(payload)
        stored = self.event_store.append_batch(
            [("RolloutChanged", "rollout", new_state.to_dict(),
              f"{new_state.mode}:{new_state.force_legacy}")],
            build_event_id=stable_event_id,
        )
        apply_event(self.state, stored[-1])
        return new_state.to_dict()

    def fallback_to_legacy(self, reason: str = "") -> dict[str, Any]:
        """偏离时一键切回旧实现。"""
        new_state = self.rollout.fallback_all(reason)
        stored = self.event_store.append_batch(
            [("RolloutChanged", "rollout", new_state.to_dict(),
              f"fallback:{new_state.updated_at}")],
            build_event_id=stable_event_id,
        )
        apply_event(self.state, stored[-1])
        return new_state.to_dict()

    # ---- 重放 / 查询 -------------------------------------------------------

    def replay(self) -> dict[str, Any]:
        """从事件日志重建全部投影：幂等，调用前后投影一致。"""
        events = self.event_store.read_all()
        self.state = rebuild_projections(self.store, events)
        return {"ok": True, "events_replayed": len(events)}

    def list_drifts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.state.drifts[-limit:]

    def list_reports(self) -> list[dict[str, Any]]:
        return self.state.reports

    def list_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [event.to_envelope() for event in self.event_store.read_all()[-limit:]]

    def fleet_todos(self) -> list[dict[str, Any]]:
        return self.store.rows("fleet_todo")

    # ---- 内部 --------------------------------------------------------------

    def _plan_for(self, version: str | None) -> CompiledPlan:
        target = version or self.rollout.state.active_convention_version \
            or self.registry.latest().doc.version
        if target not in self._plans:
            raise KeyError(f"约定版本不存在：{target}")
        return self._plans[target]

    def _plan_for_date(self, date_text: str) -> CompiledPlan:
        versioned = self.registry.effective_for(date_text or None)
        return self._plans[versioned.doc.version]

    @staticmethod
    def _diff_verdicts(compiler: Verdict, old: Verdict) -> dict[str, Any]:
        diff: dict[str, Any] = {}
        new_d = compiler.to_dict()
        old_d = old.to_dict()
        for field_name in DIFF_FIELDS:
            if new_d[field_name] != old_d[field_name]:
                diff[field_name] = {"compiler": new_d[field_name], "legacy": old_d[field_name]}
        return diff


# ---- 模块级辅助 -------------------------------------------------------------

def _payload_hash(values: dict[str, Any] | None, terminal: str | None) -> str:
    blob = json.dumps({"v": values or {}, "t": terminal}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _merge_row_values(row: dict[str, Any], values: dict[str, Any], *,
                      terminal: str | None) -> dict[str, Any]:
    """把台账中文行翻译成协议输入，再让请求显式取值覆盖。"""
    merged: dict[str, Any] = {
        "发现问题": row.get("发现问题", ""),
        "严重程度": row.get("严重程度"),
        "所属班组": row.get("巡查班组") or row.get("所属班组"),
        "source": terminal or row.get("来源端") or "office",
    }
    merged.update({key: value for key, value in values.items() if value is not None})
    if terminal:
        merged["source"] = terminal
    return merged


def _build_default_registry():
    from app.patrol_compiler.conventions import default_registry
    return default_registry()


def _doc_from_payload(payload: dict[str, Any]) -> ConventionDoc:
    rules = tuple(
        RuleDoc(
            name=str(item["name"]),
            when=dict(item["when"]),
            then=dict(item["then"]),
            priority=int(item.get("priority", 100)),
        )
        for item in payload.get("rules", [])
    )
    return ConventionDoc(
        version=str(payload["version"]),
        title=str(payload.get("title", payload["version"])),
        effective_from=str(payload["effective_from"]),
        rules=rules,
        description=str(payload.get("description", "")),
        default=dict(payload.get("default", {})),
        crew_routing=dict(payload.get("crew_routing", {})),
    )
