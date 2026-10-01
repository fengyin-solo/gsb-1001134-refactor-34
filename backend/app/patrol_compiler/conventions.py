"""约定文档库：版本化、带生效日期的巡查约定。

- v2026.01（2026-01-01 生效）：旧版约定，存量巡查按当时日期用它留档。
- v2026.09（2026-09-15 生效）：收编三端分歧后的现行约定。

两个版本在处置时限与「严重」档的处置建议上刻意不同，用来验证
「既有巡查按当时约定留档，存量迁移带约定水位」。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.patrol_compiler.types import ConventionDoc, RuleDoc


def _fingerprint(doc: ConventionDoc) -> str:
    """约定内容指纹：规则、默认结论、班组路由任一变化都会变。"""
    payload = {
        "version": doc.version,
        "effective_from": doc.effective_from,
        "rules": [
            {"name": r.name, "priority": r.priority, "when": r.when, "then": r.then}
            for r in doc.rules
        ],
        "default": dict(doc.default),
        "crew_routing": dict(doc.crew_routing),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class VersionedConvention:
    doc: ConventionDoc
    fingerprint: str


# ---- v2026.09 现行约定 -------------------------------------------------------

V2026_09 = ConventionDoc(
    version="v2026.09",
    title="日常巡查问题裁决约定（三端收编版）",
    effective_from="2026-09-15",
    description="收编手持终端、地图图层、后台页面三处分歧后的统一约定。",
    rules=(
        RuleDoc(
            name="安全隐患立即处置",
            priority=10,
            when={"field": "is_safety_risk", "op": "eq", "value": True},
            then={
                "disposition": "立即处置",
                "disposition_code": "EMERGENCY",
                "sla_hours": 4,
                "safety_level": "预警",
                "create_defect": True,
                "create_todo": True,
                "target_crew": "应急班",
            },
        ),
        RuleDoc(
            name="严重病害限期修复",
            priority=20,
            when={"field": "severity", "op": "eq", "value": "严重"},
            then={
                "disposition": "限期修复",
                "disposition_code": "REPAIR",
                "sla_hours": 24,
                "safety_level": "关注",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="较重病害限期修复",
            priority=30,
            when={"field": "severity", "op": "eq", "value": "较重"},
            then={
                "disposition": "限期修复",
                "disposition_code": "REPAIR",
                "sla_hours": 72,
                "safety_level": "关注",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="一般病害日常养护",
            priority=40,
            when={"all_of": [
                {"field": "severity", "op": "eq", "value": "一般"},
                {"field": "problem_code", "op": "nin", "value": ["NORMAL", "UNKNOWN"]},
            ]},
            then={
                "disposition": "日常养护",
                "disposition_code": "ROUTINE",
                "sla_hours": 168,
                "safety_level": "常规",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="未识别问题观察记录",
            priority=50,
            when={"field": "problem_code", "op": "eq", "value": "UNKNOWN"},
            then={
                "disposition": "观察记录",
                "disposition_code": "WATCH",
                "sla_hours": 48,
                "safety_level": "关注",
                "create_defect": False,
                "create_todo": False,
            },
        ),
        RuleDoc(
            name="无异常观察结案",
            priority=90,
            when={"field": "problem_code", "op": "eq", "value": "NORMAL"},
            then={
                "disposition": "无需处置",
                "disposition_code": "NONE",
                "sla_hours": 0,
                "safety_level": "常规",
                "create_defect": False,
                "create_todo": False,
            },
        ),
    ),
    default={
        "disposition": "观察记录",
        "disposition_code": "WATCH",
        "sla_hours": 48,
        "safety_level": "关注",
        "create_defect": False,
        "create_todo": False,
    },
    crew_routing={
        "CRACK": "路面一班",
        "POTHOLE": "路面一班",
        "RUT": "路面二班",
        "SUBSIDENCE": "路面二班",
        "WATER": "应急班",
        "BLOCKAGE": "应急班",
        "DEFECT": "交安班",
    },
)


# ---- v2026.01 旧版约定（存量留档用）-----------------------------------------

V2026_01 = ConventionDoc(
    version="v2026.01",
    title="日常巡查问题裁决约定（旧版）",
    effective_from="2026-01-01",
    description="三端收编前执行的旧版约定，仅用于存量记录按当时日期留档。",
    rules=(
        RuleDoc(
            name="旧版-安全隐患立即处置",
            priority=10,
            when={"field": "is_safety_risk", "op": "eq", "value": True},
            then={
                "disposition": "立即处置",
                "disposition_code": "EMERGENCY",
                "sla_hours": 8,
                "safety_level": "预警",
                "create_defect": True,
                "create_todo": True,
                "target_crew": "应急班",
            },
        ),
        RuleDoc(
            name="旧版-严重问题立即处置",
            priority=20,
            when={"field": "severity", "op": "eq", "value": "严重"},
            then={
                "disposition": "立即处置",
                "disposition_code": "EMERGENCY",
                "sla_hours": 48,
                "safety_level": "关注",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="旧版-较重问题限期修复",
            priority=30,
            when={"field": "severity", "op": "eq", "value": "较重"},
            then={
                "disposition": "限期修复",
                "disposition_code": "REPAIR",
                "sla_hours": 96,
                "safety_level": "关注",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="旧版-一般问题日常养护",
            priority=40,
            when={"field": "severity", "op": "eq", "value": "一般"},
            then={
                "disposition": "日常养护",
                "disposition_code": "ROUTINE",
                "sla_hours": 240,
                "safety_level": "常规",
                "create_defect": True,
                "create_todo": True,
            },
        ),
        RuleDoc(
            name="旧版-无异常结案",
            priority=90,
            when={"field": "problem_code", "op": "eq", "value": "NORMAL"},
            then={
                "disposition": "无需处置",
                "disposition_code": "NONE",
                "sla_hours": 0,
                "safety_level": "常规",
                "create_defect": False,
                "create_todo": False,
            },
        ),
    ),
    default={
        "disposition": "观察记录",
        "disposition_code": "WATCH",
        "sla_hours": 72,
        "safety_level": "关注",
        "create_defect": False,
        "create_todo": False,
    },
    crew_routing={
        "CRACK": "路面二班",
        "POTHOLE": "路面二班",
        "RUT": "路面二班",
        "SUBSIDENCE": "路面二班",
        "WATER": "应急班",
        "BLOCKAGE": "应急班",
        "DEFECT": "交安班",
    },
)


class ConventionRegistry:
    """约定注册表：登记版本、按日期选当时版本、给出约定水位。"""

    def __init__(self, docs: list[ConventionDoc]) -> None:
        ordered = sorted(docs, key=lambda d: d.effective_from)
        self._docs: dict[str, VersionedConvention] = {}
        for doc in ordered:
            if doc.version in self._docs:
                raise ValueError(f"约定版本重复：{doc.version}")
            self._docs[doc.version] = VersionedConvention(doc=doc, fingerprint=_fingerprint(doc))

    @property
    def versions(self) -> list[str]:
        return [v.doc.version for v in sorted(self._docs.values(), key=lambda v: v.doc.effective_from)]

    def get(self, version: str) -> VersionedConvention:
        if version not in self._docs:
            raise KeyError(f"约定版本不存在：{version}")
        return self._docs[version]

    def latest(self) -> VersionedConvention:
        return self.get(self.versions[-1])

    def effective_for(self, date_text: str | None) -> VersionedConvention:
        """按日期选「当时生效」的约定；日期缺失或早于全部版本时取最早版本。"""
        chosen: VersionedConvention | None = None
        for version in self.versions:
            item = self.get(version)
            if date_text is None or item.doc.effective_from <= date_text:
                chosen = item
        return chosen or self.latest()

    def watermark(self) -> dict[str, object]:
        """约定水位：迁移/裁决事件都带上它，声明当时注册表里有哪些版本与指纹。"""
        return {
            "latest_version": self.latest().doc.version,
            "versions": [
                {
                    "version": item.doc.version,
                    "effective_from": item.doc.effective_from,
                    "fingerprint": item.fingerprint,
                }
                for item in (self.get(v) for v in self.versions)
            ],
        }


def default_registry() -> ConventionRegistry:
    return ConventionRegistry([V2026_01, V2026_09])
