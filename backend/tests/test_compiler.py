"""编译器与取值协议的单测。"""
from __future__ import annotations

import unittest

from app.patrol_compiler.compiler import (
    ConventionCompileError,
    compile_convention,
    evaluate,
)
from app.patrol_compiler.conventions import default_registry
from app.patrol_compiler import legacy
from app.patrol_compiler.protocol import normalize_facts, verdict_to_legacy_fields
from app.patrol_compiler.types import ConventionDoc, RuleDoc


class CompilerValidationTest(unittest.TestCase):
    def test_collects_all_errors_at_once(self) -> None:
        doc = ConventionDoc(
            version="x",
            title="t",
            effective_from="bad-date",
            rules=(
                RuleDoc(name="r1", when={"field": "not_a_field", "op": "eq", "value": 1},
                        then={"disposition": "不是合法值"}, priority=10),
                RuleDoc(name="r1", when={"field": "severity", "op": "??", "value": "严重"},
                        then={"sla_hours": -1}, priority=10),
            ),
            default={},
        )
        with self.assertRaises(ConventionCompileError) as ctx:
            compile_convention(doc)
        errors = ctx.exception.errors
        joined = "\n".join(errors)
        # 一次报全：字段、枚举、操作符、缺键、类型、优先级、规则名、日期都要有
        self.assertTrue(any("未知字段" in e for e in errors))
        self.assertTrue(any("disposition" in e for e in errors))
        self.assertTrue(any("未知操作符" in e for e in errors))
        self.assertTrue(any("sla_hours" in e for e in errors))
        self.assertTrue(any("缺少结论键" in e for e in errors))
        self.assertTrue(any("优先级" in e for e in errors))
        self.assertTrue(any("规则名重复" in e for e in errors))
        self.assertTrue(any("effective_from" in e for e in errors))
        self.assertIn("未知字段", joined)

    def test_priority_first_match_wins(self) -> None:
        registry = default_registry()
        plan = compile_convention(registry.get("v2026.09").doc)
        facts = normalize_facts({"发现问题": "纵向裂缝贯通，存在明显安全隐患", "source": "office"})
        self.assertTrue(facts.is_safety_risk)
        verdict = evaluate(plan, facts, crew_routing=registry.get("v2026.09").doc.crew_routing)
        self.assertEqual(verdict.matched_rule, "安全隐患立即处置")
        self.assertEqual(verdict.disposition, "立即处置")
        self.assertEqual(verdict.sla_hours, 4)


class ProtocolTest(unittest.TestCase):
    def test_terminal_envelope(self) -> None:
        facts = normalize_facts({
            "source": "terminal",
            "终端问题码": "T_POTHOLE",
            "终端严重程度": "L2",
            "终端班组": "路面二班",
        })
        self.assertEqual(facts.problem_code, "POTHOLE")
        self.assertEqual(facts.severity, "较重")
        self.assertEqual(facts.crew, "路面二班")
        self.assertTrue(facts.recognized)

    def test_map_envelope_alarm_colors(self) -> None:
        red = normalize_facts({"source": "map", "图层类型": "面层裂纹图层", "告警等级": "红色"})
        self.assertEqual(red.problem_code, "CRACK")
        self.assertEqual(red.severity, "严重")
        self.assertTrue(red.is_safety_risk)
        blue = normalize_facts({"source": "map", "图层类型": "路面积水图层", "告警等级": "蓝色"})
        self.assertEqual(blue.problem_code, "WATER")
        self.assertEqual(blue.severity, "一般")

    def test_office_free_text(self) -> None:
        facts = normalize_facts({"发现问题": "K3+200 护栏缺损", "所属班组": "交安班"})
        self.assertEqual(facts.problem_code, "DEFECT")
        self.assertEqual(facts.crew, "交安班")
        self.assertLess(facts.confidence, 1.0)

    def test_unrecognized(self) -> None:
        facts = normalize_facts({"发现问题": "一切如常"})
        self.assertEqual(facts.problem_code, "UNKNOWN")
        self.assertFalse(facts.recognized)

    def test_legacy_fields_shape(self) -> None:
        registry = default_registry()
        plan = compile_convention(registry.get("v2026.09").doc)
        facts = normalize_facts({"发现问题": "严重坑槽", "source": "office"})
        verdict = evaluate(plan, facts, crew_routing=registry.get("v2026.09").doc.crew_routing)
        flat = verdict_to_legacy_fields(verdict)
        self.assertEqual(flat["处置建议"], "限期修复")
        self.assertEqual(flat["处置时限小时"], 24)
        self.assertIn(flat["转病害清单"], ("是", "否"))
        self.assertEqual(flat["约定版本"], "v2026.09")
        self.assertEqual(flat["conventionVersion"], "v2026.09")


class ThreeWayDivergenceTest(unittest.TestCase):
    """复现「换端后同一记录改变处置建议」，并验证编译器结论与端无关。"""

    def setUp(self) -> None:
        registry = default_registry()
        self.plan = compile_convention(registry.get("v2026.09").doc)
        self.routing = registry.get("v2026.09").doc.crew_routing

    def test_same_facts_legacy_engines_disagree(self) -> None:
        facts = normalize_facts({"发现问题": "严重裂缝", "source": "office"})
        t = legacy.legacy_terminal(facts)
        m = legacy.legacy_map(facts)
        o = legacy.legacy_office(facts)
        # 终端：限期修复48h；地图：立即处置8h；后台：限期修复72h
        self.assertEqual((t.disposition, t.sla_hours), ("限期修复", 48))
        self.assertEqual((m.disposition, m.sla_hours), ("立即处置", 8))
        self.assertEqual((o.disposition, o.sla_hours), ("限期修复", 72))

    def test_compiler_result_independent_of_source(self) -> None:
        v1 = evaluate(self.plan, normalize_facts({"发现问题": "严重裂缝", "source": "terminal"}),
                      crew_routing=self.routing)
        v2 = evaluate(self.plan, normalize_facts({"发现问题": "严重裂缝", "source": "map"}),
                      crew_routing=self.routing)
        v3 = evaluate(self.plan, normalize_facts({"发现问题": "严重裂缝", "source": "office"}),
                      crew_routing=self.routing)
        self.assertEqual(v1.disposition, v2.disposition)
        self.assertEqual(v2.disposition, v3.disposition)
        self.assertEqual(v1.sla_hours, v3.sla_hours)


if __name__ == "__main__":
    unittest.main()
