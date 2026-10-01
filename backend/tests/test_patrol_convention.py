"""巡查约定编译器测试。

覆盖用户提出的每条纪律：
- 三套旧裁决互相打架，编译器三端一致；
- 影子运行不落账、逐班组切流、一键切回旧实现；
- 存量迁移带约定水位、按当时约定留档；
- 事件重放幂等且收敛；
- 提交失败时不允许留下半个事件；
- 新旧结果按抽样批次比对；
- 既有取值协议（问题编码/程度编码/字段别名）兼容。
"""
from __future__ import annotations

import unittest

from app.patrol_convention import convention_service as service
from app.patrol_convention.compiler import compile_record
from app.patrol_convention.conventions import CURRENT_VERSION
from app.patrol_convention.events import EventError, event_log
from app.patrol_convention.legacy import all_legacy_rulings, legacy_ruling
from app.patrol_convention.protocol import normalize
from app.patrol_convention.rollout import rollout
from app.patrol_convention.writeback import DEFECT_TABLE, FLEET_TABLE, projected_snapshot
from app.store import store


def reset_world() -> None:
    """回到进程启动时的种子基线：重建内存表、清空事件日志、复位发布策略。"""
    from app.seed import SEED_ROWS
    import copy
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}  # noqa: SLF001
    event_log.reset()
    rollout.reset()
    service.clear_shadow()
    service.bootstrap()


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_world()

    def test_protocol_normalizes_legacy_codes(self) -> None:
        # P06 旧问题编码、2/S1 旧程度编码、G2 道路等级编码、别名字段都要能归一
        facts = normalize({
            "id": 6, "问题编码": "P06", "发现问题": "局部标线磨损",
            "严重程度": "2", "道路等级": "G1", "crew": "东片一班",
            "位置": "黄埔大道 K1+800",
        })
        self.assertEqual(facts["问题类型"], "交安设施")
        self.assertEqual(facts["严重程度"], "一般")
        self.assertEqual(facts["道路等级"], "一级")
        self.assertEqual(facts["管养班组"], "东片一班")
        self.assertTrue(any("既有编码" in reason for reason in facts["归一依据"]))

    def test_legacy_rulings_diverge_but_compiler_is_uniform(self) -> None:
        row = store.find("patrol", 5)  # 积水·一般
        legacy = all_legacy_rulings(row)
        self.assertNotEqual(
            legacy["terminal"]["处置建议"], legacy["map"]["处置建议"],
            "旧终端与旧地图对同一积水记录的建议应当不同（这正是要收束的问题）",
        )
        self.assertNotEqual(legacy["map"]["优先级"], legacy["backend"]["优先级"])

        verdicts = {source: compile_record(row)[1] for source in ("terminal", "map", "backend")}
        self.assertEqual(
            {v["处置建议"] for v in verdicts.values()},
            {verdicts["terminal"]["处置建议"]},
            "编译器对终端、地图、后台必须给出完全一致的处置建议",
        )
        for verdict in verdicts.values():
            self.assertEqual(verdict["约定版本"], CURRENT_VERSION)
            self.assertEqual(verdict["约定水位"], CURRENT_VERSION)
            self.assertIn("立即抽排积水并上报", verdict["处置建议"], "现行约定：积水一般即立即上报")

    def test_deterministic_facts_fingerprint(self) -> None:
        row = store.find("patrol", 4)
        _, first = compile_record(row)
        _, second = compile_record(dict(row))
        self.assertEqual(first["事实指纹"], second["事实指纹"])

    def test_first_grade_tightens_sla(self) -> None:
        row = store.find("patrol", 4)  # 坑槽·严重·一级
        _, verdict = compile_record(row)
        self.assertEqual(verdict["时限"], "8小时内")
        self.assertTrue(verdict["回写目标"]["病害清单"])
        self.assertTrue(verdict["回写目标"]["车队待办"])


class RolloutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_world()

    def test_shadow_does_not_write_ledgers(self) -> None:
        before = projected_snapshot()
        result = service.adjudicate(5, source="map")  # 积水记录
        self.assertEqual(result["applied"], "legacy")
        self.assertIsNotNone(result["shadow"])
        self.assertTrue(result["shadow"]["drift"], "影子运行应记录到新旧偏离")
        # 影子只更新巡查台账的旧裁决字段，但绝不立病害、不派车队待办
        self.assertEqual(projected_snapshot()["fleet_todo"], before["fleet_todo"])
        pavement_after = projected_snapshot()["pavement"]
        self.assertFalse(any(row.get("来源巡查编号") == "PATR-20260915-02" for row in pavement_after))

    def test_canary_routes_by_crew(self) -> None:
        service.switch_rollout("canary", canary_crews=["东片一班"], reason="逐班组切流")
        # 东片一班的记录（id=4）走编译器
        hit = service.adjudicate(4, source="terminal")
        self.assertEqual(hit["applied"], "compiler")
        self.assertEqual(store.find("patrol", 4)["裁决来源"], "巡查约定编译器")
        self.assertTrue(any(row.get("来源巡查编号") == "PATR-20260912-01" for row in store.rows(FLEET_TABLE)))
        # 南片二班（id=5）仍走旧裁决
        miss = service.adjudicate(5, source="map")
        self.assertEqual(miss["applied"], "legacy")
        self.assertEqual(store.find("patrol", 5)["裁决来源"], "地图端")

    def test_one_click_rollback(self) -> None:
        service.switch_rollout("compiler", reason="全量")
        service.rollback(reason="发现异常止损")
        status = service.rollout_status()
        self.assertEqual(status["mode"], "legacy")
        result = service.adjudicate(4, source="terminal")
        self.assertEqual(result["applied"], "legacy")
        self.assertEqual(result["verdict"]["约定水位"], "legacy")
        switch_events = event_log.list_events(event_type="RolloutSwitched")
        self.assertTrue(any(e["payload"]["to_mode"] == "legacy" for e in switch_events))


class MigrationAndReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_world()

    def test_migration_backfills_waterlines_by_date(self) -> None:
        report = service.migrate_legacy(batch_id="MIG-TEST")
        self.assertGreaterEqual(report["backfilled"], 8)
        self.assertEqual(
            store.find("patrol", 1)["约定水位"], "legacy",
            "约定生效日前的存量记录按当时约定留档（legacy 水位）",
        )
        self.assertEqual(store.find("patrol", 5)["约定水位"], "v2026-09")

    def test_migration_is_incremental_and_idempotent(self) -> None:
        first = service.migrate_legacy(batch_id="MIG-TEST")
        second = service.migrate_legacy(batch_id="MIG-TEST")
        self.assertEqual(second["backfilled"], 0)
        self.assertEqual(second["skipped"], first["backfilled"])
        # 迁移事件按 记录+批次 幂等，不重复产生
        keys = [e["event_key"] for e in event_log.list_events(event_type="WaterlineBackfilled")]
        self.assertEqual(len(keys), len(set(keys)))

    def test_replay_converges_after_adjudication_and_migration(self) -> None:
        service.adjudicate(4, source="terminal")
        service.switch_rollout("canary", canary_crews=["南片二班"], reason="切流")
        service.adjudicate(5, source="map")
        service.adjudicate(7, source="backend")
        service.migrate_legacy(batch_id="MIG-TEST")

        report = service.replay()
        self.assertTrue(report["converged"], f"重放应收敛到现态：{report}")
        # 重放后车队待办、病害清单不产生重复自然键记录
        todos = [row["来源巡查编号"] for row in store.rows(FLEET_TABLE)]
        self.assertEqual(len(todos), len(set(todos)))

    def test_adjudication_idempotent(self) -> None:
        service.switch_rollout("compiler", reason="幂等性验证")
        first = service.adjudicate(4, source="terminal")
        second = service.adjudicate(4, source="terminal")
        self.assertEqual(first["verdict"]["处置建议"], second["verdict"]["处置建议"])
        verdicts = event_log.list_events(event_type="VerdictApplied")
        self.assertEqual(len(verdicts), 1, "同一事实重复裁决不得产生第二条事件")
        defects = [row for row in store.rows(DEFECT_TABLE)
                   if row.get("来源巡查编号") == "PATR-20260912-01"]
        self.assertEqual(len(defects), 1, "重复裁决不得在病害清单产生重复行")


class AtomicCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_world()

    def test_failed_conversion_leaves_no_half_event(self) -> None:
        bad_payload = {
            "patrol_id": 4,
            "巡查编号": "PATR-20260912-01",
            "facts": normalize(store.find("patrol", 4)),
            # 裁决转换失败：缺少编译器必须给出的全部结论字段
            "verdict": {"问题类型": "坑槽", "严重程度": "严重", "回写目标": {"病害清单": True}},
        }
        ledger_before = dict(store.find("patrol", 4))

        with self.assertRaises(EventError):
            event_log.commit_atomically(
                prepare=lambda: [("VerdictApplied", bad_payload, "verdict:4:bad")],
                mutate=service_apply,
            )

        self.assertIsNone(event_log.get("verdict:4:bad"), "转换失败的事件不得进入日志")
        self.assertEqual(store.find("patrol", 4), ledger_before, "巡查台账不得被半个事件改写")
        self.assertFalse(
            any(row.get("来源巡查编号") == "PATR-20260912-01" for row in store.rows(DEFECT_TABLE)),
            "转换失败时病害清单也不得落数据",
        )

    def test_atomic_batch_all_or_nothing(self) -> None:
        good = {
            "patrol_id": 7, "巡查编号": "PATR-20260920-04",
            "facts": normalize(store.find("patrol", 7)),
            "verdict": {
                "问题类型": "裂缝", "严重程度": "轻微", "道路等级": "二级",
                "处置建议": "灌缝保养，7个工作日内安排", "优先级": "P3", "时限": "7个工作日内",
                "约定版本": CURRENT_VERSION, "约定水位": CURRENT_VERSION, "事实指纹": "t1",
                "回写目标": {"巡查台账": True, "病害清单": True, "车队待办": False},
            },
        }
        bad = {
            "patrol_id": 8, "巡查编号": "PATR-20260922-05",
            "facts": normalize(store.find("patrol", 8)),
            "verdict": {"回写目标": {}},
        }
        from app.patrol_convention.writeback import apply_events
        with self.assertRaises(EventError):
            event_log.commit_atomically(
                prepare=lambda: [
                    ("VerdictApplied", good, "verdict:7:ok"),
                    ("VerdictApplied", bad, "verdict:8:bad"),
                ],
                mutate=apply_events,
            )
        self.assertIsNone(event_log.get("verdict:7:ok"), "批次内有坏事件时，好事件也不能留下")
        self.assertFalse(
            any(row.get("来源巡查编号") == "PATR-20260920-04" for row in store.rows(DEFECT_TABLE)),
        )


def service_apply(events):  # 与门面一致的派生入口
    from app.patrol_convention.writeback import apply_events
    apply_events(events)


class SamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_world()

    def test_deterministic_sample_and_batch_diff(self) -> None:
        first = service.open_comparison_batch(ratio=0.5, salt="s1")
        second = service.open_comparison_batch(ratio=0.5, salt="s1")
        ids_a = [item["patrol_id"] for item in first["items"]]
        ids_b = [item["patrol_id"] for item in second["items"]]
        self.assertEqual(ids_a, ids_b, "同盐同数据必须抽到同一批")
        self.assertLess(len(ids_a), first["population"], "半量抽样不应覆盖全量")

        # 批次中能看到旧终端/旧地图对同一记录的偏离
        drift_items = [item for item in first["items"] if item["drift"]]
        self.assertTrue(drift_items)
        sample = drift_items[0]
        self.assertTrue(
            any(view["diff"] for view in sample["sources"].values()),
            "偏离项要逐字段给出 compiler 与 legacy 的差异",
        )


if __name__ == "__main__":
    unittest.main()
