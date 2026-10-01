"""领域类型：归一化事实、约定文档、编译产物与裁决结论。

约定文档（ConventionDoc）是纯数据：任何一方（终端、地图、后台）都不允许
再私藏规则，规则只能以版本化文档的形式登记进 registry，由编译器统一裁决。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# 归一化问题码：不管三端原来用「裂缝/crack/T_CRACK/面层裂纹图层」什么叫法
CANON_CODES = (
    "CRACK",       # 裂缝
    "POTHOLE",     # 坑槽
    "RUT",         # 车辙
    "SUBSIDENCE",  # 沉陷
    "WATER",       # 积水
    "BLOCKAGE",    # 堵塞
    "DEFECT",      # 设施损坏（交安/照明等）
    "NORMAL",      # 无异常
    "UNKNOWN",     # 无法识别
)

# 取值协议里长期沿用的字符串枚举，编译器与旧代码都只能取这些值
SEVERITY_LEVELS = ("一般", "较重", "严重")
SAFETY_LEVELS = ("常规", "关注", "预警")
DISPOSITIONS = ("日常养护", "限期修复", "立即处置", "观察记录", "无需处置")
SCOPES = ("point", "line", "area")
TERMINALS = ("terminal", "map", "office")
CREWS = ("路面一班", "路面二班", "桥隧班", "交安班", "应急班")

# 条件里允许出现的字段名 -> 取值类型；归一化之后全是 FactSet 上的属性
FIELD_SPEC: dict[str, type] = {
    "problem_code": str,
    "severity": str,
    "is_safety_risk": bool,
    "scope": str,
    "source": str,
    "crew": str,
}
OPERATORS = ("eq", "ne", "in", "nin")


@dataclass(frozen=True)
class FactSet:
    """归一化后的巡查事实：三端信封最终都变成它。"""

    problem_code: str = "UNKNOWN"
    severity: str = "一般"
    is_safety_risk: bool = False
    scope: str = "point"
    source: str = "office"
    crew: str | None = None
    raw_text: str = ""
    # 转换置信度：自由文本靠关键词猜，猜得准不准要告诉调用方（迁移期抽样用）
    confidence: float = 1.0
    recognized: bool = True

    def with_defaults(self, **changes: Any) -> "FactSet":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_code": self.problem_code,
            "severity": self.severity,
            "is_safety_risk": self.is_safety_risk,
            "scope": self.scope,
            "source": self.source,
            "crew": self.crew,
            "raw_text": self.raw_text,
            "confidence": round(self.confidence, 3),
            "recognized": self.recognized,
        }


@dataclass(frozen=True)
class Condition:
    """单条条件，如 {field: 'severity', op: 'eq', value: '严重'}。"""

    field_name: str
    op: str
    value: Any
    all_of: tuple["Condition", ...] = ()
    any_of: tuple["Condition", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        if self.all_of or self.any_of:
            data: dict[str, Any] = {}
            if self.all_of:
                data["all_of"] = [item.to_dict() for item in self.all_of]
            if self.any_of:
                data["any_of"] = [item.to_dict() for item in self.any_of]
            return data
        return {"field": self.field_name, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class RuleDoc:
    """约定里的一条规则：when 命中则给出 then 结论。"""

    name: str
    when: dict[str, Any]
    then: dict[str, Any]
    priority: int = 100


@dataclass(frozen=True)
class ConventionDoc:
    """版本化的巡查约定文档。

    effective_from 决定「既有巡查按当时约定留档」：迁移存量记录时按巡查日期
    选当时生效的版本，而不是一律用最新版。
    """

    version: str
    title: str
    effective_from: str  # ISO 日期，含当天
    rules: tuple[RuleDoc, ...]
    description: str = ""
    default: dict[str, Any] = field(default_factory=dict)
    # 问题码 -> 处置班组的路由表，同样属于约定内容，改动即变更指纹
    crew_routing: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "title": self.title,
            "effective_from": self.effective_from,
            "description": self.description,
            "rule_count": len(self.rules),
            "default": dict(self.default),
        }


@dataclass(frozen=True)
class CompiledRule:
    name: str
    condition: Condition
    conclusion: dict[str, Any]
    priority: int
    order: int


@dataclass(frozen=True)
class CompiledPlan:
    """编译产物：校验通过、按优先级排好的裁决计划，带内容指纹。"""

    version: str
    effective_from: str
    fingerprint: str
    rules: tuple[CompiledRule, ...]
    default: dict[str, Any]


@dataclass(frozen=True)
class Verdict:
    """一次裁决的结论。compiler 引擎与三套旧引擎都产出同一形状。"""

    disposition: str            # 处置建议（旧协议中文枚举）
    disposition_code: str       # 机器可读码
    sla_hours: int              # 处置时限小时
    safety_level: str           # 安全预警：常规/关注/预警
    create_defect: bool         # 是否回写病害清单
    create_todo: bool           # 是否回写车队待办
    target_crew: str | None     # 派给哪个班组
    problem_code: str
    severity: str
    matched_rule: str
    convention_version: str
    convention_fingerprint: str
    engine: str                 # compiler / legacy_terminal / legacy_map / legacy_office

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "disposition_code": self.disposition_code,
            "sla_hours": self.sla_hours,
            "safety_level": self.safety_level,
            "create_defect": self.create_defect,
            "create_todo": self.create_todo,
            "target_crew": self.target_crew,
            "problem_code": self.problem_code,
            "severity": self.severity,
            "matched_rule": self.matched_rule,
            "convention_version": self.convention_version,
            "convention_fingerprint": self.convention_fingerprint,
            "engine": self.engine,
        }
