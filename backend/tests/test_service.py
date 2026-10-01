"""应用服务级测试：影子/切流/回退、留档水位、迁移原子性、重放幂等、抽样比对。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from app.patrol_compiler.service import PatrolConventionService
from app.seed import SEED_ROWS


def _fresh_store():
    """每次测试拿一份全新的内存仓库，避免事件投影互相串。"""
    from app.store import Store
    return Store()


class AdjudicationRolloutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = PatrolConventionService(_fresh_store(), event_log_path=None)

    def test_shadow_uses_legacy_authority_and_records_drift(self) -> None:
        result = self.svc.adjudicate(4, terminal="map")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "shadow")
        self.assertTrue(result["verdict"]["engine"].startswith("legacy"))
        self.assertEqual(result["verdict"]["engine"], "legacy_map")
        # 影子下编译器结论只是陪跑，台账里的引擎标记必须是旧实现
        row = self.svc.store.find("patrol", 4)
        self.assertEqual(row["_裁决引擎"], "legacy_map")
        drifts = self.svc.list_drifts()
        self.assertGreaterEqual(len(drifts), 1)

    def test_canary_only_switches_listed_crew(self) -> None:
        self.svc.update_rollout({"mode": "canary", "canary_crews": ["交安班"]})
        off_crew = self.svc.adjudicate(4, terminal="terminal")  # 路面一班，不在名单
        on_crew = self.svc.adjudicate(6, terminal="terminal")   # 交安班，在名单
        self.assertTrue(off_crew["verdict"]["engine"].startswith("legacy"))
        self.assertEqual(on_crew["verdict"]["engine"], "compiler")

    def test_full_mode_compiler_authoritative(self) -> None:
        self.svc.update_rollout({"mode": "full"})
        result = self.svc.adjudicate(5, terminal="map")
        self.assertEqual(result["verdict"]["engine"], "compiler")

    def test_force_legacy_one_click_fallback(self) -> None:
        self.svc.update_rollout({"mode": "full"})
        self.svc.fallback_to_legacy("抽样偏离超阈值")
        result = self.svc.adjudicate(5, terminal="map")
        self.assertEqual(result["verdict"]["engine"], "legacy_map")
        self.assertTrue(self.svc.get_rollout()["force_legacy"])

    def test_adjudication_idempotent(self) -> None:
        first = self.svc.adjudicate(4, terminal="map", idempotency_key="k-1")
        second = self.svc.adjudicate(4, terminal="map", idempotency_key="k-1")
        self.assertFalse(first.get("idempotent"))
        self.assertTrue(second["idempotent"])
        adjudicated = [e for e in self.svc.list_events(limit=1000)
                       if e["event_type"] == "PatrolAdjudicated"]
        self.assertEqual(len(adjudicated), 1)


class WriteBackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = PatrolConventionService(_fresh_store(), event_log_path=None)
        self.svc.update_rollout({"mode": "full"})

    def test_writeback_three_places_and_upsert_idempotent(self) -> None:
        self.svc.adjudicate(5, terminal="map")
        patrol = self.svc.store.find("patrol", 5)
        self.assertEqual(patrol["约定版本"], "v2026.09")
        self.assertEqual(patrol["处置班组"], "应急班")

        pavements = [r for r in self.svc.store.rows("pavement")
                     if str(r.get("来源巡查")) == "5"]
        todos = [r for r in self.svc.store.rows("fleet_todo")
                 if str(r.get("来源巡查")) == "5"]
        self.assertEqual(len(pavements), 1)
        self.assertEqual(len(todos), 1)

        # 再裁决一次（不同幂等键模拟改派），仍只有一张病害单、一条待办
        self.svc.adjudicate(5, terminal="map", idempotency_key="re-decide-1")
        pavements = [r for r in self.svc.store.rows("pavement")
                     if str(r.get("来源巡查")) == "5"]
        todos = [r for r in self.svc.store.rows("fleet_todo")
                 if str(r.get("来源巡查")) == "5"]
        self.assertEqual(len(pavements), 1)
        self.assertEqual(len(todos), 1)

    def test_no_defect_no_todo_for_normal(self) -> None:
        result = self.svc.adjudicate(
            1, values={"发现问题": "未见异常，路况完好", "source": "office"}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["verdict"]["create_defect"])
        self.assertFalse(result["verdict"]["create_todo"])
        self.assertEqual(self.svc.store.rows("fleet_todo"), [])


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = PatrolConventionService(_fresh_store(), event_log_path=None)

    def test_migration_uses_convention_effective_at_record_time(self) -> None:
        # 记录4 巡查日期 2026-09-05（v2026.01 期），记录5 为 2026-09-20（v2026.09 期）
        result = self.svc.migrate_batch([4, 5], batch_label="seed-2026-09")
        self.assertTrue(result["ok"])
        self.assertEqual(sorted(result["convention_versions"]), ["v2026.01", "v2026.09"])
        row4 = self.svc.store.find("patrol", 4)
        row5 = self.svc.store.find("patrol", 5)
        self.assertEqual(row4["_约定版本"], "v2026.01")
        self.assertEqual(row5["_约定版本"], "v2026.09")
        self.assertEqual(row4["_裁决模式"], "archived")
        # 留档不新增病害单/待办
        self.assertEqual(self.svc.store.rows("fleet_todo"), [])
        # 事件带约定水位
        events = self.svc.list_events(limit=1000)
        verdict_events = [e for e in events if e["event_type"] == "PatrolAdjudicated"]
        self.assertTrue(all("watermark" in e["payload"] for e in verdict_events))

    def test_migration_failure_aborts_whole_batch_no_half_events(self) -> None:
        before = len(self.svc.list_events(limit=1000))
        # 样例行 1-3 的「发现问题」是占位文本，无法识别，整批应中止
        result = self.svc.migrate_batch([1, 2, 3], batch_label="dirty-batch")
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["failures"]), 3)
        after = len(self.svc.list_events(limit=1000))
        self.assertEqual(before, after)
        # 台账不得被污染
        self.assertNotIn("约定版本", self.svc.store.find("patrol", 1))

    def test_migration_idempotent_batch(self) -> None:
        first = self.svc.migrate_batch([4], batch_label="dup")
        second = self.svc.migrate_batch([4], batch_label="dup")
        self.assertFalse(first.get("idempotent"))
        self.assertTrue(second["idempotent"])
        count = len([e for e in self.svc.list_events(limit=1000)
                     if e["event_type"] == "PatrolAdjudicated"])
        self.assertEqual(count, 1)

    def test_archived_verdict_is_frozen_against_later_adjunction(self) -> None:
        self.svc.migrate_batch([4], batch_label="freeze-case")
        frozen_version = self.svc.store.find("patrol", 4)["_约定版本"]
        frozen_disp = self.svc.store.find("patrol", 4)["处置建议"]
        # 迁移后即便切到全量再裁决，留档结论也不得被改写
        self.svc.update_rollout({"mode": "full"})
        self.svc.adjudicate(4, terminal="map", idempotency_key="post-archive")
        row = self.svc.store.find("patrol", 4)
        self.assertEqual(row["_约定版本"], frozen_version)
        self.assertEqual(row["处置建议"], frozen_disp)
        self.assertEqual(row["_裁决模式"], "archived")
        self.assertEqual(self.svc.store.rows("fleet_todo"), [])
        # 重放后冻结依旧成立（与事件到达顺序无关）
        self.svc.replay()
        row = self.svc.store.find("patrol", 4)
        self.assertEqual(row["_约定版本"], frozen_version)
        self.assertEqual(row["处置建议"], frozen_disp)


class ReconcileReplayTest(unittest.TestCase):
    def test_reconcile_reports_diff_per_batch(self) -> None:
        svc = PatrolConventionService(_fresh_store(), event_log_path=None)
        report = svc.reconcile([
            {"source": "terminal", "终端问题码": "T_CRACK", "终端严重程度": "L3"},
            {"source": "office", "问题码": "NORMAL", "严重程度": "一般"},
        ], batch_label="B-001")
        self.assertEqual(report["total"], 2)
        # 严重裂缝新旧不一；无异常在编译器与各端口径下结论一致
        self.assertEqual(report["diverged"], 1)
        self.assertEqual(report["matched"], 1)
        self.assertIn("sla_hours", report["items"][0]["diff"])
        # 报告本身也事件化
        again = svc.reconcile([
            {"source": "terminal", "终端问题码": "T_CRACK", "终端严重程度": "L3"},
        ], batch_label="B-001")
        self.assertEqual(again["total"], 2)  # 幂等返回原批次

    def test_replay_rebuilds_projections_identically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "events.jsonl")
            svc = PatrolConventionService(_fresh_store(), event_log_path=log_path)
            svc.update_rollout({"mode": "full"})
            svc.adjudicate(5, terminal="map", idempotency_key="a-1")
            svc.adjudicate(6, terminal="terminal", idempotency_key="a-2")
            svc.migrate_batch([4], batch_label="m-1")
            snapshot = {
                "patrol": {
                    row["id"]: {k: v for k, v in row.items() if k != "id"}
                    for row in svc.store.rows("patrol")
                },
                "pavement": sorted(
                    (r.get("病害编号"), r.get("来源巡查"), r.get("处置建议"))
                    for r in svc.store.rows("pavement")
                ),
                "fleet_todo": sorted(
                    (r.get("待办编号"), r.get("来源巡查"), r.get("处置班组"))
                    for r in svc.store.rows("fleet_todo")
                ),
            }
            svc.adjudicate(5, terminal="map", idempotency_key="a-1")  # 重放前先验幂等

            # 用全新 Store 重放：投影必须一致（含确定性病害/待办编号）
            replayed = PatrolConventionService(_fresh_store(), event_log_path=log_path)
            rebuilt = {
                "patrol": {
                    row["id"]: {k: v for k, v in row.items() if k != "id"}
                    for row in replayed.store.rows("patrol")
                },
                "pavement": sorted(
                    (r.get("病害编号"), r.get("来源巡查"), r.get("处置建议"))
                    for r in replayed.store.rows("pavement")
                ),
                "fleet_todo": sorted(
                    (r.get("待办编号"), r.get("来源巡查"), r.get("处置班组"))
                    for r in replayed.store.rows("fleet_todo")
                ),
            }
            self.maxDiff = None
            self.assertEqual(snapshot["pavement"], rebuilt["pavement"])
            self.assertEqual(snapshot["fleet_todo"], rebuilt["fleet_todo"])
            for patrol_id, fields in snapshot["patrol"].items():
                for key, value in fields.items():
                    if key.startswith("_compiler"):
                        continue
                    self.assertEqual(
                        rebuilt["patrol"][patrol_id].get(key), value,
                        msg=f"patrol {patrol_id} 字段 {key} 重放后不一致",
                    )

            # 事件日志每行都是合法 JSON，且没有同一事件的重复行
            with open(log_path, "r", encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line]
            ids = [json.loads(line)["event_id"] for line in lines]
            self.assertEqual(len(ids), len(set(ids)))

    def test_in_memory_replay_is_idempotent(self) -> None:
        svc = PatrolConventionService(_fresh_store(), event_log_path=None)
        svc.update_rollout({"mode": "full"})
        svc.adjudicate(5, terminal="map")
        before = len(svc.store.rows("fleet_todo"))
        svc.replay()
        svc.replay()
        self.assertEqual(len(svc.store.rows("fleet_todo")), before)
        self.assertGreaterEqual(len(svc.list_drifts()), 1)

    def test_event_batch_atomic_on_disk(self) -> None:
        """迁移批次在磁盘上要么整批出现，要么不出现（这里校验成批连续落盘）。"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "events.jsonl")
            svc = PatrolConventionService(_fresh_store(), event_log_path=log_path)
            result = svc.migrate_batch([1, 2, 3], batch_label="dirty")
            self.assertFalse(result["ok"])
            self.assertFalse(os.path.exists(log_path))


if __name__ == "__main__":
    unittest.main()
