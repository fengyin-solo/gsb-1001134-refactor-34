"""端到端演示（不依赖 FastAPI，纯内存仓库）：

    1. 三端对同一问题各裁各的（收编前现状）
    2. 影子运行：权威仍走旧实现，编译器陪跑并记录偏离
    3. 逐班组切流：canary 名单内班组走编译器
    4. 全量切换 + 一键切回旧实现
    5. 存量按当时约定迁移留档（带约定水位）
    6. 抽样批次比对新旧结果
    7. 转换失败整批中止，不会只写半个事件
    8. 事件重放幂等，投影一致
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.patrol_compiler import legacy, normalize_facts  # noqa: E402
from app.patrol_compiler.service import PatrolConventionService  # noqa: E402
from app.store import Store  # noqa: E402


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def main() -> None:
    svc = PatrolConventionService(Store(), event_log_path=None)

    banner("1. 收编前：同一条严重裂缝，换端结论就变")
    facts = normalize_facts({"发现问题": "行车道严重裂缝", "source": "office"})
    for name, engine in (("手持终端", legacy.legacy_terminal),
                         ("地图图层", legacy.legacy_map),
                         ("后台页面", legacy.legacy_office)):
        v = engine(facts)
        print(f"  {name}: {v.disposition} / {v.sla_hours}h / 转病害={v.create_defect}")

    banner("2. 影子运行（shadow）：权威=旧终端，编译器陪跑记偏离")
    result = svc.adjudicate(4, terminal="terminal", idempotency_key="demo-shadow")
    print(f"  权威引擎={result['verdict']['engine']} 建议={result['verdict']['disposition']} "
          f"{result['verdict']['sla_hours']}h")
    print(f"  编译器建议={result['compiler_verdict']['disposition']} "
          f"{result['compiler_verdict']['sla_hours']}h")
    if result["legacy_verdict"]["disposition"] != result["compiler_verdict"]["disposition"] \
            or result["legacy_verdict"]["sla_hours"] != result["compiler_verdict"]["sla_hours"]:
        print("  → 新旧在处置建议/时限上存在偏离，已记入影子偏离台账")
    print(f"  影子偏离台账累计：{len(svc.list_drifts())} 条")

    banner("3. canary：只让交安班走编译器")
    svc.update_rollout({"mode": "canary", "canary_crews": ["交安班"], "reason": "先切一个班组"})
    r1 = svc.adjudicate(4, terminal="terminal")  # 路面一班
    r2 = svc.adjudicate(6, terminal="terminal")  # 交安班
    print(f"  路面一班 -> {r1['verdict']['engine']}")
    print(f"  交安班   -> {r2['verdict']['engine']}（建议 {r2['verdict']['disposition']}）")

    banner("4. 全量切换后一键切回旧实现")
    svc.update_rollout({"mode": "full"})
    r_full = svc.adjudicate(5, terminal="map")
    print(f"  full 模式：{r_full['verdict']['engine']}")
    svc.fallback_to_legacy("抽样偏离超阈值，先回退")
    r_back = svc.adjudicate(5, terminal="map", idempotency_key="after-fallback")
    print(f"  切回后：{r_back['verdict']['engine']}，force_legacy={svc.get_rollout()['force_legacy']}")

    # 为演示迁移，重置一个干净服务（不带上面的裁决事件）
    svc = PatrolConventionService(Store(), event_log_path=None)

    banner("5. 存量迁移：按巡查日期选用当时约定，事件带水位")
    migrated = svc.migrate_batch([4, 5, 6], batch_label="seed-2026-09")
    print(f"  ok={migrated['ok']} 迁移={migrated['migrated']} 事件={migrated['event_count']}")
    print(f"  使用约定：{migrated['convention_versions']}")
    for pid in (4, 5, 6):
        row = svc.store.find("patrol", pid)
        print(f"  PATR #{pid}（{row['巡查日期']}）冻结约定 {row['_约定版本']} "
              f"建议={row['处置建议']} {row['处置时限小时']}h 模式={row['_裁决模式']}")
    print(f"  留档不新增车队待办：fleet_todo={len(svc.fleet_todos())} 条")

    banner("6. 抽样批次比对新旧结果")
    report = svc.reconcile([
        {"source": "terminal", "终端问题码": "T_CRACK", "终端严重程度": "L3"},
        {"source": "map", "图层类型": "面层坑槽图层", "告警等级": "橙色"},
        {"source": "office", "发现问题": "护栏缺损一块，一般"},
    ], batch_label="sample-A")
    print(f"  批次 {report['batch_label']}：总 {report['total']}，"
          f"一致 {report['matched']}，偏离 {report['diverged']}，未识别 {report['unrecognized']}")
    for item in report["items"]:
        print(f"    #{item['index']} [{item['source']}] {item['facts']['problem_code']}: "
              f"新={item['compiler']['disposition']}/{item['compiler']['sla_hours']}h "
              f"旧={item['legacy']['disposition']}/{item['legacy']['sla_hours']}h "
              f"偏离={list(item['diff'])}")

    banner("7. 转换失败：脏批次整批中止，零事件落库")
    dirty = svc.migrate_batch([1, 2, 3], batch_label="dirty")
    print(f"  ok={dirty['ok']} 失败={len(dirty['failures'])} 事件落库={dirty['event_count']}")

    banner("8. 事件重放幂等")
    snapshot_todos = [(r["待办编号"], r["来源巡查"]) for r in svc.fleet_todos()]
    svc.replay()
    svc.replay()
    replayed_todos = [(r["待办编号"], r["来源巡查"]) for r in svc.fleet_todos()]
    print(f"  重放前后车队待办一致：{snapshot_todos == replayed_todos}")
    print(f"  事件总数：{len(svc.list_events(limit=10000))}")

    banner("水位快照")
    print(json.dumps(svc.watermark(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
