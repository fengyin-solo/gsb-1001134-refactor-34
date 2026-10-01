"""三套旧裁决：巡查问题历史上在终端、地图、后台各有一套口径。

这里把旧口径固化成只读代码，仅用于：
1. 影子运行期间对外的现行返回（新编译器只对比、不落账）；
2. 逐班组切流期的新旧结果比对；
3. 一键切回旧实现。

旧裁决故意保留彼此的差异——这正是要被编译器收束掉的问题。
"""
from __future__ import annotations

from typing import Any

from app.patrol_convention.conventions import LEGACY_WATERLINE
from app.patrol_convention.protocol import normalize

SOURCES = ("terminal", "map", "backend")
SOURCE_LABELS = {"terminal": "手持终端", "map": "地图端", "backend": "后台页面"}


def _shape(facts: dict[str, Any], advice: str, priority: str, sla: str,
           defect: bool, vehicle: bool) -> dict[str, Any]:
    return {
        "约定版本": LEGACY_WATERLINE,
        "约定水位": LEGACY_WATERLINE,
        "事实指纹": facts["事实指纹"] if "事实指纹" in facts else "",
        "问题类型": facts["问题类型"],
        "严重程度": facts["严重程度"],
        "道路等级": facts["道路等级"],
        "处置建议": advice,
        "优先级": priority,
        "时限": sla,
        "通知班组": True,
        "回写目标": {"巡查台账": True, "病害清单": defect, "车队待办": vehicle},
        "旧裁决来源": SOURCE_LABELS.get("", ""),
    }


def terminal_ruling(facts: dict[str, Any]) -> dict[str, Any]:
    """手持终端旧口径：现场图快，基本都派维修，时限偏宽。"""
    sev, problem = facts["严重程度"], facts["问题类型"]
    matrix = {
        "轻微": ("现场记录并自行小修，7个工作日内补录", "P3", "7个工作日内"),
        "一般": ("派单维修，3个工作日内完成", "P2", "3个工作日内"),
        "严重": ("立即上报班长并派单抢修，24小时内完成", "P1", "24小时内"),
    }
    advice, priority, sla = matrix[sev]
    if problem == "交安设施":
        advice = "现场登记，交安设施问题统一转后台派单"
        verdict = _shape(facts, advice, priority, sla, False, False)
    else:
        # 终端历史上不管立不立病害，直接默认全部立病害、不主动派车（走维修单）
        verdict = _shape(facts, advice, priority, sla, True, sev == "严重")
    verdict["旧裁决来源"] = SOURCE_LABELS["terminal"]
    return verdict


def map_ruling(facts: dict[str, Any]) -> dict[str, Any]:
    """地图端旧口径：按地图图层与位置可见性决策，倾向派车跑一趟。"""
    sev, problem = facts["严重程度"], facts["问题类型"]
    if sev == "严重":
        advice, priority, sla = "在地图上标红并立即派车现场处置，24小时内消除", "P1", "24小时内"
    elif sev == "一般":
        advice, priority, sla = "地图标注隐患点，派车巡检顺路处置，3个工作日内", "P2", "3个工作日内"
    else:
        advice, priority, sla = "地图打点留痕，下个巡检周期路过复查", "P3", "7个工作日内"
    if problem == "积水":
        # 地图端历史上把积水一律当应急抽水，轻微也派车
        advice, priority, sla = "地图标蓝积水点，立即派排涝车抽排", "P1", "24小时内"
    if problem == "交安设施":
        # 地图端没有交安图层，历史上全部不立病害
        defect = False
    else:
        defect = sev in ("一般", "严重")
    verdict = _shape(facts, advice, priority, sla, defect, True)
    verdict["旧裁决来源"] = SOURCE_LABELS["map"]
    return verdict


def backend_ruling(facts: dict[str, Any]) -> dict[str, Any]:
    """后台页面旧口径：保守，所有问题先转人工研判，很少自动派车。"""
    sev, problem = facts["严重程度"], facts["问题类型"]
    if sev == "严重":
        advice, priority, sla = "转人工研判并立项，2个工作日内安排专项处治", "P2", "2个工作日内"
    elif sev == "一般":
        advice, priority, sla = "转人工研判，3个工作日内反馈处置意见", "P3", "3个工作日内"
    else:
        advice, priority, sla = "暂存待批量复核，7个工作日内统一研判", "P3", "7个工作日内"
    # 后台历史上只立病害、不直接给车队派待办（派车要再走一轮审批）
    verdict = _shape(facts, advice, priority, sla, problem not in ("交安设施",) or sev == "严重", False)
    verdict["旧裁决来源"] = SOURCE_LABELS["backend"]
    return verdict


_LEGACY = {
    "terminal": terminal_ruling,
    "map": map_ruling,
    "backend": backend_ruling,
}


def legacy_ruling(record: dict[str, Any], source: str) -> dict[str, Any]:
    if source not in _LEGACY:
        raise KeyError(f"未知旧裁决来源 {source}，仅支持 {SOURCES}")
    facts = normalize(record)
    return _LEGACY[source](facts)


def all_legacy_rulings(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    facts = normalize(record)
    return {name: fn(facts) for name, fn in _LEGACY.items()}
