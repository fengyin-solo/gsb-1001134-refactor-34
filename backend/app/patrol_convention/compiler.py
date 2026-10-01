"""约定编译器：归一事实 + 指定约定版本 → 唯一裁决。

编译是纯函数、确定性的：同一事实、同一约定版本，在终端、地图、后台编译出的
处置建议、优先级、时限、回写目标必须逐字节一致。
"""
from __future__ import annotations

import copy
from typing import Any

from app.patrol_convention.conventions import (
    CURRENT_VERSION,
    DEFAULT_OUTPUTS,
    Convention,
    get_convention,
)
from app.patrol_convention.protocol import facts_fingerprint, normalize


def compile_facts(
    facts: dict[str, Any],
    *,
    version: str = CURRENT_VERSION,
) -> dict[str, Any]:
    convention: Convention = get_convention(version)
    rule = convention.lookup(facts["问题类型"], facts["严重程度"])
    decision = copy.deepcopy(rule)

    # 一级路加严（写在约定层的统一口径，端上无权覆盖）
    overrides: dict[str, list[str]] = {}
    if facts["道路等级"] == "一级":
        from app.patrol_convention.conventions import _FIRST_GRADE_OVERRIDES
        tighter = _FIRST_GRADE_OVERRIDES.get(facts["严重程度"])
        if tighter:
            decision["时限"] = tighter
            decision["处置建议"] = f"{decision['处置建议'].split('，')[0]}，{tighter}完成（一级路加严）"
            overrides["时限"] = ["一级路口径加严"]

    return {
        "约定版本": convention.version,
        "约定水位": convention.version,
        "事实指纹": facts_fingerprint(facts),
        "问题类型": facts["问题类型"],
        "严重程度": facts["严重程度"],
        "道路等级": facts["道路等级"],
        "处置建议": decision["处置建议"],
        "优先级": decision["优先级"],
        "时限": decision["时限"],
        "通知班组": decision["通知班组"],
        "回写目标": {
            "巡查台账": DEFAULT_OUTPUTS["patrol_ledger"],
            "病害清单": decision["是否立病害"],
            "车队待办": decision["是否派车"],
        },
        "编译依据": [
            f"约定 {convention.version}：({facts['问题类型']}, {facts['严重程度']}) 命中规则行",
            *facts.get("归一依据", []),
            *overrides.get("时限", []),
        ],
    }


def compile_record(
    record: dict[str, Any],
    *,
    version: str = CURRENT_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """给一条巡查台账记录出裁决；返回 (归一事实, 裁决)。"""
    facts = normalize(record)
    if not facts["巡查编号"]:
        facts["巡查编号"] = str(record.get("id") or "")
    return facts, compile_facts(facts, version=version)
