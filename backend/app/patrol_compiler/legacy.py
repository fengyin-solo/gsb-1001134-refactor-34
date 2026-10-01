"""三套旧裁决实现：固化收编前三端各自为政的口径。

它们存在的唯一目的是：
1. 影子运行期与编译器双跑、逐条比对偏离；
2. force_legacy 一键切回时，仍按原来的端给出原来的建议。

三套口径故意不一致（同一事实，换端结论会变）：

- legacy_terminal（手持终端 APP）：处置偏保守，严重问题 48h 才要求闭环，
  一般问题只在终端里「提醒」，不转病害清单；积水分归路面一班。
- legacy_map（地图图层告警）：按告警颜色拍板，橙色就立即处置、红色 2h，
  且一律生成车队待办（哪怕一般问题）；堵塞分归路面二班。
- legacy_office（后台页面）：偏宽松，严重才「限期修复」，一般问题直接
  「观察记录」，不转待办；严重问题时限给到 72h。
"""
from __future__ import annotations

from app.patrol_compiler.types import FactSet, Verdict

_LEGACY_FINGERPRINT = "legacy://2026.01-scattered"


def _verdict(
    facts: FactSet,
    *,
    engine: str,
    disposition: str,
    code: str,
    sla: int,
    safety: str,
    defect: bool,
    todo: bool,
    crew: str | None,
    matched: str,
) -> Verdict:
    return Verdict(
        disposition=disposition,
        disposition_code=code,
        sla_hours=sla,
        safety_level=safety,
        create_defect=defect,
        create_todo=todo,
        target_crew=crew,
        problem_code=facts.problem_code,
        severity=facts.severity,
        matched_rule=matched,
        convention_version="legacy",
        convention_fingerprint=_LEGACY_FINGERPRINT,
        engine=engine,
    )


def legacy_terminal(facts: FactSet) -> Verdict:
    """手持终端 APP 旧口径。"""
    if not facts.recognized:
        return _verdict(facts, engine="legacy_terminal", disposition="观察记录",
                        code="WATCH", sla=72, safety="关注", defect=False, todo=False,
                        crew=None, matched="终端-未识别提醒")
    if facts.is_safety_risk:
        return _verdict(facts, engine="legacy_terminal", disposition="立即处置",
                        code="EMERGENCY", sla=12, safety="预警", defect=True, todo=True,
                        crew="应急班", matched="终端-安全隐患")
    if facts.severity == "严重":
        return _verdict(facts, engine="legacy_terminal", disposition="限期修复",
                        code="REPAIR", sla=48, safety="关注", defect=True, todo=True,
                        crew=facts.crew or "路面一班", matched="终端-严重48小时")
    if facts.severity == "较重":
        return _verdict(facts, engine="legacy_terminal", disposition="限期修复",
                        code="REPAIR", sla=96, safety="关注", defect=True, todo=False,
                        crew=facts.crew or "路面一班", matched="终端-较重96小时")
    return _verdict(facts, engine="legacy_terminal", disposition="日常养护",
                    code="ROUTINE", sla=192, safety="常规", defect=False, todo=False,
                    crew=facts.crew or "路面一班", matched="终端-一般仅提醒")


def legacy_map(facts: FactSet) -> Verdict:
    """地图图层告警旧口径：颜色驱动、一律派车。"""
    if not facts.recognized:
        return _verdict(facts, engine="legacy_map", disposition="观察记录",
                        code="WATCH", sla=24, safety="关注", defect=False, todo=True,
                        crew="应急班", matched="地图-未知图层派车核查")
    if facts.is_safety_risk:
        return _verdict(facts, engine="legacy_map", disposition="立即处置",
                        code="EMERGENCY", sla=2, safety="预警", defect=True, todo=True,
                        crew="应急班", matched="地图-红色告警2小时")
    if facts.severity == "严重":
        return _verdict(facts, engine="legacy_map", disposition="立即处置",
                        code="EMERGENCY", sla=8, safety="预警", defect=True, todo=True,
                        crew="应急班", matched="地图-橙色即立即处置")
    if facts.severity == "较重":
        return _verdict(facts, engine="legacy_map", disposition="限期修复",
                        code="REPAIR", sla=48, safety="关注", defect=True, todo=True,
                        crew="路面二班", matched="地图-黄色派工")
    return _verdict(facts, engine="legacy_map", disposition="限期修复",
                    code="REPAIR", sla=72, safety="常规", defect=True, todo=True,
                    crew="路面二班", matched="地图-蓝色也派车")


def legacy_office(facts: FactSet) -> Verdict:
    """后台页面旧口径：最宽松，一般问题只观察。"""
    if not facts.recognized:
        return _verdict(facts, engine="legacy_office", disposition="观察记录",
                        code="WATCH", sla=96, safety="常规", defect=False, todo=False,
                        crew=None, matched="后台-未识别挂起")
    if facts.is_safety_risk:
        return _verdict(facts, engine="legacy_office", disposition="立即处置",
                        code="EMERGENCY", sla=24, safety="预警", defect=True, todo=True,
                        crew="应急班", matched="后台-安全隐患24小时")
    if facts.severity == "严重":
        return _verdict(facts, engine="legacy_office", disposition="限期修复",
                        code="REPAIR", sla=72, safety="关注", defect=True, todo=True,
                        crew="路面一班", matched="后台-严重72小时")
    if facts.severity == "较重":
        return _verdict(facts, engine="legacy_office", disposition="日常养护",
                        code="ROUTINE", sla=120, safety="常规", defect=True, todo=False,
                        crew="路面一班", matched="后台-较重日常养护")
    if facts.problem_code == "NORMAL":
        return _verdict(facts, engine="legacy_office", disposition="无需处置",
                        code="NONE", sla=0, safety="常规", defect=False, todo=False,
                        crew=None, matched="后台-无异常结案")
    return _verdict(facts, engine="legacy_office", disposition="观察记录",
                    code="WATCH", sla=168, safety="常规", defect=False, todo=False,
                    crew=None, matched="后台-一般观察")


_LEGACY_ENGINES = {
    "terminal": legacy_terminal,
    "map": legacy_map,
    "office": legacy_office,
}


def legacy_for(source: str):
    """按来源端取旧裁决函数；未知端默认后台口径。"""
    return _LEGACY_ENGINES.get(source, legacy_office)
