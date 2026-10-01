"""取值协议：三端入参归一化、裁决结论平铺回旧协议。

兼容三类既有取值方式：

1. 手持终端（terminal）旧信封：
   {"终端问题码": "T_CRACK", "终端班组": "路面一班", "终端描述": "...", "source": "terminal"}
2. 地图（map）旧信封：
   {"图层类型": "面层裂纹图层", "告警等级": "橙色", "source": "map"}
3. 后台台账（office）/ 直传中文键 / 驼峰英文键：
   {"发现问题": "路面裂缝严重", "所属班组": "路面二班", "source": "office"}
"""
from __future__ import annotations

from typing import Any

from app.patrol_compiler.types import FactSet, Verdict

# 中文问题词 -> 标准问题码
_CODE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CRACK": ("裂缝", "龟裂", "裂纹", "开裂"),
    "POTHOLE": ("坑槽", "坑洞", "坑洼", "麻面"),
    "RUT": ("车辙", "拥包", "推移"),
    "SUBSIDENCE": ("沉陷", "沉降", "塌陷"),
    "WATER": ("积水", "水淹", "排水不畅"),
    "BLOCKAGE": ("堵塞", "淤堵", "淤积"),
    "DEFECT": ("标志", "标线", "护栏", "路灯", "信号灯", "设施损坏", "缺损"),
    "NORMAL": ("无异常", "正常", "未见异常", "完好"),
}

# 地图图层名 -> 标准问题码（旧地图取值协议）
_LAYER_MAP: dict[str, str] = {
    "面层裂纹图层": "CRACK",
    "面层坑槽图层": "POTHOLE",
    "车辙图层": "RUT",
    "路基沉陷图层": "SUBSIDENCE",
    "路面积水图层": "WATER",
    "排水淤积图层": "BLOCKAGE",
    "交安设施图层": "DEFECT",
}
# 地图告警等级 -> 严重程度 + 安全风险（旧地图把安全信息混在颜色里）
_ALARM_MAP: dict[str, tuple[str, bool]] = {
    "蓝色": ("一般", False),
    "黄色": ("较重", False),
    "橙色": ("严重", False),
    "红色": ("严重", True),
}

# 手持终端旧问题码（T_ 前缀）
_TERMINAL_CODE_MAP: dict[str, str] = {
    "T_CRACK": "CRACK",
    "T_POTHOLE": "POTHOLE",
    "T_RUT": "RUT",
    "T_SUBSIDENCE": "SUBSIDENCE",
    "T_WATER": "WATER",
    "T_BLOCKAGE": "BLOCKAGE",
    "T_DEFECT": "DEFECT",
    "T_NORMAL": "NORMAL",
}
# 终端旧严重程度值
_TERMINAL_LEVEL_MAP = {"L1": "一般", "L2": "较重", "L3": "严重"}

# 中/英文键别名 -> FactSet 字段
_ALIASES: dict[str, str] = {
    "发现问题": "raw_text",
    "问题描述": "raw_text",
    "终端描述": "raw_text",
    "台账描述": "raw_text",
    "description": "raw_text",
    "text": "raw_text",
    "严重程度": "severity",
    "severity": "severity",
    "安全隐患": "is_safety_risk",
    "is_safety_risk": "is_safety_risk",
    "safety": "is_safety_risk",
    "影响范围": "scope",
    "scope": "scope",
    "所属班组": "crew",
    "巡查班组": "crew",
    "终端班组": "crew",
    "crew": "crew",
    "team": "crew",
    "问题码": "problem_code",
    "problem_code": "problem_code",
    "code": "problem_code",
}
_SCOPE_ALIASES = {"点": "point", "point": "point", "线": "line", "line": "line",
                  "面": "area", "area": "area"}
_SOURCE_ALIASES = {"terminal": "terminal", "终端": "terminal", "手持终端": "terminal",
                   "map": "map", "地图": "map", "office": "office", "后台": "office",
                   "台账": "office"}
_BOOL_TRUE = {"是", "true", "True", "1", "有", "yes", "Y"}


def _pick(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _guess_code(text: str) -> tuple[str, float]:
    """从自由文本猜问题码：命中标准词给 0.9，完全猜不出标记 UNKNOWN。"""
    for code, words in _CODE_KEYWORDS.items():
        if any(word in text for word in words):
            return code, 0.9
    return "UNKNOWN", 0.3


def _guess_severity(text: str) -> tuple[str, float]:
    if "严重" in text or "紧急" in text or "重大" in text:
        return "严重", 0.85
    if "较重" in text or "明显" in text or "较大" in text:
        return "较重", 0.85
    if "轻微" in text or "一般" in text:
        return "一般", 0.8
    return "一般", 0.5


# 自由文本里的安全风险信号（旧三端只有地图红色告警表达安全信息，统一后要能识别）
_SAFETY_KEYWORDS = ("影响通行安全", "危及安全", "安全隐患", "有安全", "险情", "失稳风险")


def _guess_safety(text: str) -> bool:
    return any(word in text for word in _SAFETY_KEYWORDS)


def _coerce_bool(value: Any) -> bool:
    return str(value).strip() in _BOOL_TRUE


def normalize_facts(payload: dict[str, Any]) -> FactSet:
    """把任意一端的取值信封归一成 FactSet；识别不出的记录 recognized=False。

    注意：本函数不抛异常用于「脏值」，无法识别时返回 UNKNOWN + recognized=False，
    由调用方决定是整批失败（存量迁移严格模式）还是走观察记录。
    """
    source_raw = str(_pick(payload, "source", "来源端", "端") or "office")
    source = _SOURCE_ALIASES.get(source_raw, "office")

    code: str | None = None
    severity: str | None = None
    is_safety: bool | None = None
    confidence = 1.0

    # 1) 地图旧信封：图层类型 + 告警等级
    layer = _pick(payload, "图层类型", "layer_type", "图层")
    alarm = _pick(payload, "告警等级", "alarm_level")
    if layer is not None:
        code = _LAYER_MAP.get(str(layer))
        if code is None:
            code, code_conf = _guess_code(str(layer))
            confidence = min(confidence, code_conf)
        if alarm is not None:
            mapped = _ALARM_MAP.get(str(alarm))
            if mapped is not None:
                severity, is_safety = mapped
            else:
                confidence = min(confidence, 0.4)

    # 2) 终端旧问题码（T_ 前缀）
    terminal_code = _pick(payload, "终端问题码", "terminal_code")
    if terminal_code is not None:
        mapped_code = _TERMINAL_CODE_MAP.get(str(terminal_code).strip().upper())
        code = mapped_code or code
        if mapped_code is None:
            confidence = min(confidence, 0.4)
    terminal_level = _pick(payload, "终端严重程度", "终端级别")
    if terminal_level is not None:
        severity = _TERMINAL_LEVEL_MAP.get(str(terminal_level), severity)

    # 3) 直传标准码/中文键/驼峰键
    direct_code = _pick(payload, "问题码", "problem_code", "code")
    if direct_code is not None and str(direct_code).strip().upper() in {
        *dict(_TERMINAL_CODE_MAP).values(),
    }:
        code = str(direct_code).strip().upper()
    elif direct_code is not None and not str(direct_code).startswith(("T_",)):
        guessed, guessed_conf = _guess_code(str(direct_code))
        code = code or guessed
        confidence = min(confidence, guessed_conf)

    raw_text = str(_pick(payload, "发现问题", "问题描述", "终端描述", "台账描述",
                         "description", "text") or "")
    if code is None and raw_text:
        code, code_conf = _guess_code(raw_text)
        confidence = min(confidence, code_conf)
    if severity is None and raw_text:
        severity, sev_conf = _guess_severity(raw_text)
        confidence = min(confidence, sev_conf)

    explicit_severity = _pick(payload, "严重程度", "severity")
    if explicit_severity is not None:
        severity = str(explicit_severity)
        confidence = min(confidence, 1.0)
    explicit_safety = _pick(payload, "安全隐患", "is_safety_risk", "safety")
    if explicit_safety is not None:
        is_safety = _coerce_bool(explicit_safety)
    elif is_safety is None and raw_text:
        is_safety = _guess_safety(raw_text)
        if is_safety:
            confidence = min(confidence, 0.8)

    scope_raw = _pick(payload, "影响范围", "scope")
    scope = _SCOPE_ALIASES.get(str(scope_raw), "point") if scope_raw is not None else "point"
    crew_value = _pick(payload, "所属班组", "巡查班组", "终端班组", "crew", "team")
    crew = str(crew_value) if crew_value not in (None, "") else None

    code = code or "UNKNOWN"
    recognized = code != "UNKNOWN"
    if code == "UNKNOWN":
        confidence = min(confidence, 0.3)

    return FactSet(
        problem_code=code,
        severity=severity or "一般",
        is_safety_risk=bool(is_safety),
        scope=scope,
        source=source,
        crew=crew,
        raw_text=raw_text,
        confidence=round(confidence, 3),
        recognized=recognized,
    )


def verdict_to_legacy_fields(verdict: Verdict) -> dict[str, Any]:
    """平铺成既有取值协议：老页面/老脚本只读中文键也能拿到新结论。"""
    return {
        "处置建议": verdict.disposition,
        "处置建议码": verdict.disposition_code,
        "处置时限小时": verdict.sla_hours,
        "安全预警": verdict.safety_level,
        "转病害清单": "是" if verdict.create_defect else "否",
        "转车队待办": "是" if verdict.create_todo else "否",
        "处置班组": verdict.target_crew or "",
        "约定版本": verdict.convention_version,
        "约定指纹": verdict.convention_fingerprint,
        "裁决引擎": verdict.engine,
        "命中规则": verdict.matched_rule,
        # 驼峰英文键同时给一份，方便新代码
        "disposition": verdict.disposition,
        "slaHours": verdict.sla_hours,
        "safetyLevel": verdict.safety_level,
        "createDefect": verdict.create_defect,
        "createTodo": verdict.create_todo,
        "targetCrew": verdict.target_crew,
        "conventionVersion": verdict.convention_version,
    }
