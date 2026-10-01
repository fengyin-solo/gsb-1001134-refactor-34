"""约定编译器：把声明式约定文档编译成可执行的裁决计划。

编译期把所有错误一次报全（未知字段、非法枚举、操作符配错、then 缺键、
同名规则、优先级重复），运行期只做「取第一条命中规则」这一件事，
保证三端拿到的结论只取决于事实和约定版本。
"""
from __future__ import annotations

from typing import Any

from app.patrol_compiler.conventions import _fingerprint
from app.patrol_compiler.types import (
    DISPOSITIONS,
    FIELD_SPEC,
    OPERATORS,
    SAFETY_LEVELS,
    SEVERITY_LEVELS,
    SCOPES,
    TERMINALS,
    CompiledPlan,
    CompiledRule,
    Condition,
    ConventionDoc,
    FactSet,
    RuleDoc,
    Verdict,
)

THEN_KEYS = {
    "disposition": str,
    "disposition_code": str,
    "sla_hours": int,
    "safety_level": str,
    "create_defect": bool,
    "create_todo": bool,
    "target_crew": (str, type(None)),
}


class ConventionCompileError(ValueError):
    """约定文档编译失败：errors 里带全部问题，而不是撞到第一个就返回。"""

    def __init__(self, version: str, errors: list[str]) -> None:
        self.version = version
        self.errors = errors
        super().__init__(f"约定 {version} 编译失败：{'; '.join(errors)}")


def _parse_condition(
    raw: dict[str, Any],
    *,
    errors: list[str],
    path: str,
) -> Condition | None:
    if not isinstance(raw, dict):
        errors.append(f"{path}：条件必须是对象")
        return None

    if "all_of" in raw or "any_of" in raw:
        group = raw.get("all_of") if "all_of" in raw else raw.get("any_of")
        if not isinstance(group, list) or not group:
            errors.append(f"{path}：all_of/any_of 必须是非空数组")
            return None
        children = [
            child
            for index, item in enumerate(group)
            if (child := _parse_condition(item, errors=errors, path=f"{path}[{index}]")) is not None
        ]
        if "all_of" in raw:
            return Condition(field_name="", op="all_of", value=None, all_of=tuple(children))
        return Condition(field_name="", op="any_of", value=None, any_of=tuple(children))

    field_name = raw.get("field")
    op = raw.get("op")
    value = raw.get("value")
    if field_name not in FIELD_SPEC:
        errors.append(f"{path}：未知字段 {field_name!r}，允许 {sorted(FIELD_SPEC)}")
        return None
    if op not in OPERATORS:
        errors.append(f"{path}：未知操作符 {op!r}，允许 {list(OPERATORS)}")
        return None

    expected = FIELD_SPEC[field_name]
    allowed_values: tuple[Any, ...] = ()
    if field_name == "severity":
        allowed_values = SEVERITY_LEVELS
    elif field_name == "safety_level" or field_name == "problem_code":
        allowed_values = tuple()  # 问题码集合在协议层归一化，不在这里限制
    elif field_name == "scope":
        allowed_values = SCOPES
    elif field_name == "source":
        allowed_values = TERMINALS

    candidates = value if op in ("in", "nin") else [value]
    if not isinstance(value, list) and op in ("in", "nin"):
        errors.append(f"{path}：操作符 {op} 的 value 必须是数组")
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, expected):
            errors.append(f"{path}：值 {candidate!r} 类型应为 {expected.__name__}")
        elif allowed_values and candidate not in allowed_values:
            errors.append(f"{path}：值 {candidate!r} 不在允许集合 {list(allowed_values)}")
    return Condition(field_name=field_name, op=op, value=value)


def _validate_then(raw: dict[str, Any], *, errors: list[str], path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        errors.append(f"{path}：then 必须是对象")
        return {}
    for key, expected in THEN_KEYS.items():
        if key == "target_crew":
            continue  # 可空，缺省时走班组路由表
        if key not in raw:
            errors.append(f"{path}：缺少结论键 {key}")
    unknown = set(raw) - set(THEN_KEYS)
    if unknown:
        errors.append(f"{path}：未知结论键 {sorted(unknown)}")
    if "disposition" in raw and raw["disposition"] not in DISPOSITIONS:
        errors.append(f"{path}：disposition {raw['disposition']!r} 非法")
    if "safety_level" in raw and raw["safety_level"] not in SAFETY_LEVELS:
        errors.append(f"{path}：safety_level {raw['safety_level']!r} 非法")
    for key in ("sla_hours",):
        if key in raw and (not isinstance(raw[key], int) or raw[key] < 0):
            errors.append(f"{path}：{key} 必须是非负整数")
    for key in ("create_defect", "create_todo"):
        if key in raw and not isinstance(raw[key], bool):
            errors.append(f"{path}：{key} 必须是布尔值")
    return dict(raw)


def compile_convention(doc: ConventionDoc) -> CompiledPlan:
    """编译约定文档；失败时 ConventionCompileError 带全部问题。"""
    errors: list[str] = []

    if not doc.version or not isinstance(doc.version, str):
        errors.append("version 不能为空")
    if not doc.effective_from or len(doc.effective_from) != 10:
        errors.append("effective_from 必须是 YYYY-MM-DD")
    if not doc.rules:
        errors.append("rules 不能为空")

    seen_names: set[str] = set()
    seen_priorities: set[int] = set()
    compiled: list[tuple[int, int, CompiledRule]] = []
    for index, rule in enumerate(doc.rules):
        if not isinstance(rule, RuleDoc):
            errors.append(f"rules[{index}]：必须是 RuleDoc")
            continue
        prefix = f"rules[{index}]「{rule.name}」"
        if rule.name in seen_names:
            errors.append(f"{prefix}：规则名重复")
        seen_names.add(rule.name)
        if rule.priority in seen_priorities:
            errors.append(f"{prefix}：优先级 {rule.priority} 与其他规则重复")
        seen_priorities.add(rule.priority)
        condition = _parse_rule_condition(rule.when, errors, f"{prefix}.when")
        conclusion = _validate_then(rule.then, errors=errors, path=f"{prefix}.then")
        if condition is not None and conclusion:
            compiled.append((
                rule.priority,
                index,
                CompiledRule(
                    name=rule.name,
                    condition=condition,
                    conclusion=conclusion,
                    priority=rule.priority,
                    order=index,
                ),
            ))

    default = _validate_then(dict(doc.default), errors=errors, path="default")

    if errors:
        raise ConventionCompileError(doc.version or "?", errors)

    compiled.sort(key=lambda item: (item[0], item[1]))
    return CompiledPlan(
        version=doc.version,
        effective_from=doc.effective_from,
        fingerprint=_fingerprint(doc),
        rules=tuple(rule for _, _, rule in compiled),
        default=default,
    )


def _parse_rule_condition(raw: dict[str, Any], errors: list[str], path: str) -> Condition | None:
    if not isinstance(raw, dict) or not raw:
        errors.append(f"{path}：when 必须是非空条件对象")
        return None
    return _parse_condition(raw, errors=errors, path=path)


def _match(condition: Condition, facts: FactSet) -> bool:
    if condition.op == "all_of":
        return all(_match(item, facts) for item in condition.all_of)
    if condition.op == "any_of":
        return any(_match(item, facts) for item in condition.any_of)

    actual = getattr(facts, condition.field_name)
    if condition.op == "eq":
        return actual == condition.value
    if condition.op == "ne":
        return actual != condition.value
    if condition.op == "in":
        return actual in condition.value
    if condition.op == "nin":
        return actual not in condition.value
    raise ValueError(f"未知操作符 {condition.op}")  # 编译期已拦截，理论不可达


def evaluate(plan: CompiledPlan, facts: FactSet, *, crew_routing: dict[str, str]) -> Verdict:
    """按优先级取第一条命中规则；都不中用默认结论。"""
    for rule in plan.rules:
        if _match(rule.condition, facts):
            conclusion = dict(rule.conclusion)
            matched = rule.name
            break
    else:
        conclusion = dict(plan.default)
        matched = "默认结论"

    target_crew = conclusion.get("target_crew") or crew_routing.get(facts.problem_code)
    return Verdict(
        disposition=str(conclusion["disposition"]),
        disposition_code=str(conclusion["disposition_code"]),
        sla_hours=int(conclusion["sla_hours"]),
        safety_level=str(conclusion["safety_level"]),
        create_defect=bool(conclusion["create_defect"]),
        create_todo=bool(conclusion["create_todo"]),
        target_crew=target_crew,
        problem_code=facts.problem_code,
        severity=facts.severity,
        matched_rule=matched,
        convention_version=plan.version,
        convention_fingerprint=plan.fingerprint,
        engine="compiler",
    )
