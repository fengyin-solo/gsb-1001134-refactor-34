"""巡查约定编译器（Patrol Convention Compiler）。

把原来散在手持终端、地图图层、后台页面三处的巡查问题裁决收束成一条流水线：

    三端取值（旧协议兼容）
        -> 归一化事实 FactSet
        -> 约定文档（带版本/生效日期）编译成裁决计划
        -> 裁决结论 Verdict
        -> 事件（append-only、幂等、可重放）
        -> 投影回写：巡查台账 / 病害清单 / 车队待办 / 影子偏离台账

发布期通过 RolloutService 在「旧三端实现」与「编译器」之间切换：
shadow（影子双跑）-> canary（逐班组）-> full（全量），force_legacy 一键切回。
"""
from __future__ import annotations

from app.patrol_compiler.types import (
    CompiledPlan,
    ConventionDoc,
    FactSet,
    RuleDoc,
    Verdict,
)
from app.patrol_compiler.conventions import ConventionRegistry, default_registry
from app.patrol_compiler.compiler import ConventionCompileError, compile_convention, evaluate
from app.patrol_compiler import legacy
from app.patrol_compiler.protocol import normalize_facts, verdict_to_legacy_fields
from app.patrol_compiler.events import Event, EventStore
from app.patrol_compiler.rollout import RolloutService
from app.patrol_compiler.projections import ProjectionState, rebuild_projections
from app.patrol_compiler.service import PatrolConventionService

__all__ = [
    "FactSet",
    "Verdict",
    "RuleDoc",
    "ConventionDoc",
    "CompiledPlan",
    "ConventionRegistry",
    "default_registry",
    "ConventionCompileError",
    "compile_convention",
    "evaluate",
    "legacy",
    "normalize_facts",
    "verdict_to_legacy_fields",
    "Event",
    "EventStore",
    "RolloutService",
    "ProjectionState",
    "rebuild_projections",
    "PatrolConventionService",
]
