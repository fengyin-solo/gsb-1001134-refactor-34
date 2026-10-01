"""版本化巡查约定注册表。

每个版本是一张“事实 → 处置建议”的规则表（DecisionTable），编译器只做确定性查表，
不夹带端上的历史经验。约定版本号即“约定水位”，裁决与留档都带这个水位：
- v2026-09：第一版统一约定（收编旧逻辑的基线）；
- v2026-10：现行约定（阈值收紧、积水新增立即上报、交安设施按一级路处置）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CURRENT_VERSION = "v2026-10"
BASELINE_VERSION = "v2026-09"
# 旧实现时代没有约定，用这个水位标记“按当时约定留档”的存量记录
LEGACY_WATERLINE = "legacy"

SEVERITY_ORDER = {"轻微": 1, "一般": 2, "严重": 3}

# 回写目标开关由约定给出，而不是由各端自己决定
DEFAULT_OUTPUTS = {"patrol_ledger": True, "defect_list": True, "fleet_todo": True}


@dataclass(frozen=True)
class Convention:
    version: str
    effective_from: str
    title: str
    rules: dict[tuple[str, str], dict[str, Any]]
    notes: str = ""

    def lookup(self, problem: str, severity: str) -> dict[str, Any]:
        return self.rules.get((problem, severity)) or self.rules[("其他", severity)]


# 第一版约定：三套旧裁决第一次收束，阈值与旧端大致对齐
_BASELINE_RULES: dict[tuple[str, str], dict[str, Any]] = {}
for _sev, _priority, _sla in (
    ("轻微", "P3", "7个工作日内"),
    ("一般", "P2", "3个工作日内"),
    ("严重", "P1", "24小时内"),
):
    _BASELINE_RULES.update({
        ("坑槽", _sev): {
            "处置建议": f"派单修复坑槽，{_sla}完成",
            "优先级": _priority, "时限": _sla,
            "是否立病害": True, "是否派车": True, "通知班组": True,
        },
        ("裂缝", _sev): {
            "处置建议": f"灌缝保养，{_sla}安排" if _sev != "严重" else f"专项处治裂缝，{_sla}完成",
            "优先级": _priority, "时限": _sla,
            "是否立病害": True, "是否派车": _sev == "严重", "通知班组": True,
        },
        ("沉陷", _sev): {
            "处置建议": f"隐患观测并上报，{_sla}复检",
            "优先级": _priority, "时限": _sla,
            "是否立病害": True, "是否派车": _sev == "严重", "通知班组": True,
        },
        ("积水", _sev): {
            "处置建议": f"抽排积水并疏通，{_sla}处理",
            "优先级": _priority, "时限": _sla,
            "是否立病害": _sev != "轻微", "是否派车": True, "通知班组": True,
        },
        ("拥包", _sev): {
            "处置建议": f"铣刨处治拥包，{_sla}完成",
            "优先级": _priority, "时限": _sla,
            "是否立病害": True, "是否派车": _sev != "轻微", "通知班组": True,
        },
        ("交安设施", _sev): {
            "处置建议": f"通知交安班组处置，{_sla}跟进",
            "优先级": _priority, "时限": _sla,
            "是否立病害": False, "是否派车": False, "通知班组": True,
        },
        ("其他", _sev): {
            "处置建议": f"人工研判后派单，{_sla}反馈",
            "优先级": _priority, "时限": _sla,
            "是否立病害": _sev == "严重", "是否派车": _sev == "严重", "通知班组": True,
        },
    })


# 现行约定：统一口径收紧（这就是“换端后处置建议不再变”的权威版本）
_CURRENT_RULES: dict[tuple[str, str], dict[str, Any]] = {}
for _sev, _priority, _sla, _escalate in (
    ("轻微", "P3", "7个工作日内", False),
    ("一般", "P2", "2个工作日内", False),
    ("严重", "P1", "12小时内", True),
):
    _CURRENT_RULES.update({
        ("坑槽", _sev): {
            "处置建议": (
                f"派单修复坑槽，{_sla}完成" if not _escalate
                else f"立即围挡并修复坑槽，{_sla}完成，同步上报路网中心"
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": True, "是否派车": True, "通知班组": True,
        },
        ("裂缝", _sev): {
            "处置建议": (
                f"灌缝保养，{_sla}安排" if _sev == "轻微"
                else (f"专项处治裂缝，{_sla}完成" if _sev == "一般"
                      else f"立即专项处治裂缝，{_sla}完成，同步上报路网中心")
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": True,
            "是否派车": _sev in ("一般", "严重"),
            "通知班组": True,
        },
        ("沉陷", _sev): {
            "处置建议": (
                f"列入观测台账，{_sla}复检" if _sev == "轻微"
                else (f"隐患观测并上报，{_sla}复检" if _sev == "一般"
                      else f"立即封路复检沉陷，{_sla}处置，同步上报路网中心")
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": True,
            "是否派车": _escalate,
            "通知班组": True,
        },
        ("积水", _sev): {
            # 现行约定：积水一律先抽排；一般即立即上报，不再等严重
            "处置建议": (
                f"抽排积水并疏通，{_sla}处理" if _sev == "轻微"
                else f"立即抽排积水并上报，{_sla}恢复交通"
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": True,
            "是否派车": True,
            "通知班组": True,
        },
        ("拥包", _sev): {
            "处置建议": f"铣刨处治拥包，{_sla}完成",
            "优先级": _priority, "时限": _sla,
            "是否立病害": True,
            "是否派车": _sev != "轻微",
            "通知班组": True,
        },
        ("交安设施", _sev): {
            # 现行约定：交安设施按一级路口径，一般即派车
            "处置建议": (
                f"通知交安班组处置，{_sla}跟进" if _sev == "轻微"
                else f"立即通知交安班组出车处置，{_sla}修复"
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": False,
            "是否派车": _sev in ("一般", "严重"),
            "通知班组": True,
        },
        ("其他", _sev): {
            "处置建议": (
                f"人工研判后派单，{_sla}反馈" if not _escalate
                else f"立即人工研判并处置，{_sla}反馈，同步上报路网中心"
            ),
            "优先级": _priority, "时限": _sla,
            "是否立病害": _sev in ("一般", "严重"),
            "是否派车": _escalate,
            "通知班组": True,
        },
    })


# 一级路加严：严重时限再压一档，通知路网中心
_FIRST_GRADE_OVERRIDES = {"严重": "8小时内", "一般": "1个工作日内"}


CONVENTIONS: dict[str, Convention] = {
    BASELINE_VERSION: Convention(
        version=BASELINE_VERSION,
        effective_from="2026-09-01",
        title="第一版巡查约定（收编基线）",
        rules=_BASELINE_RULES,
        notes="终端/地图/后台三套旧裁决首次收束的基线版本。",
    ),
    CURRENT_VERSION: Convention(
        version=CURRENT_VERSION,
        effective_from="2026-10-01",
        title="2026年10月巡查约定（现行）",
        rules=_CURRENT_RULES,
        notes="积水一般即立即上报；交安设施按一级路口径派车；一级路时限加严。",
    ),
}


def get_convention(version: str) -> Convention:
    if version not in CONVENTIONS:
        raise KeyError(f"约定版本 {version} 未注册，可用：{sorted(CONVENTIONS)}")
    return CONVENTIONS[version]


def list_versions() -> list[dict[str, str]]:
    return [
        {"version": c.version, "effective_from": c.effective_from, "title": c.title, "notes": c.notes}
        for c in sorted(CONVENTIONS.values(), key=lambda item: item.effective_from)
    ]
