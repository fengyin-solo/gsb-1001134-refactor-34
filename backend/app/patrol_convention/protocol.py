"""既有取值协议：终端、地图、后台历史上对同一字段有不同叫法与编码，这里统一归一。

编译器只认归一后的事实（Facts）；任何端上传的旧编码、中文字段、英文别名都能进来，
归一失败不抛异常，落到可解释的默认值上，并在 reasons 中留痕。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# 问题类型：关键词按特异性排序，先命中先得
PROBLEM_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("交安设施", ("标线", "标志", "护栏", "轮廓标", "交安", "信号灯")),
    ("坑槽", ("坑槽", "坑洞", "坑洼", "pothole")),
    ("裂缝", ("裂缝", "龟裂", "网裂", "开裂")),
    ("沉陷", ("沉陷", "沉降", "塌陷")),
    ("积水", ("积水", "淹水", "内涝")),
    ("拥包", ("拥包", "壅包", "油包")),
]

# 既有问题编码协议（旧终端仍在传 P01 这类码）
PROBLEM_CODES: dict[str, str] = {
    "P01": "坑槽",
    "P02": "裂缝",
    "P03": "沉陷",
    "P04": "积水",
    "P05": "拥包",
    "P06": "交安设施",
    "P99": "其他",
}

SEVERITY_BY_WORD: dict[str, str] = {
    "严重": "严重", "危急": "严重", "紧急": "严重", "重大": "严重", "大面积": "严重",
    "一般": "一般", "明显": "一般", "中等": "一般", "中度": "一般",
    "轻微": "轻微", "轻度": "轻微", "局部": "轻微", "小面积": "轻微",
}
# 既有程度编码：1/2/3 与 S1/S2/S3 两套都要认
SEVERITY_CODES: dict[str, str] = {
    "1": "轻微", "S1": "轻微", "L": "轻微",
    "2": "一般", "S2": "一般", "M": "一般",
    "3": "严重", "S3": "严重", "H": "严重",
}

ROAD_GRADE_CODES: dict[str, str] = {
    "G1": "一级", "G2": "二级", "G3": "三级", "G4": "四级",
    "1": "一级", "2": "二级", "3": "三级", "4": "四级",
}

# 字段别名：旧端可能用结构化字段，也可能直接塞自由文本
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "发现问题": ("发现问题", "问题描述", "问题类型", "problem", "problem_desc"),
    "严重程度": ("严重程度", "程度", "severity", "level"),
    "问题编码": ("问题编码", "问题代码", "problem_code"),
    "管养班组": ("管养班组", "班组", "巡查班组", "crew", "crew_name"),
    "道路等级": ("道路等级", "road_grade", "grade"),
    "巡查路段": ("巡查路段", "路段", "位置", "桩号", "location"),
}


def _pick(row: dict[str, Any], canonical: str) -> tuple[Any, str] | tuple[None, None]:
    for name in FIELD_ALIASES.get(canonical, (canonical,)):
        if name in row and str(row[name] or "").strip() != "":
            return row[name], name
    return None, None


def classify_problem(text: str) -> tuple[str, bool]:
    """自由文本 → 标准问题类型；返回 (类型, 是否命中关键词)。"""
    blob = str(text or "").lower()
    for category, keywords in PROBLEM_KEYWORDS:
        if any(keyword.lower() in blob for keyword in keywords):
            return category, True
    return "其他", False


def normalize_severity(value: Any, text: str = "") -> str:
    raw = str(value or "").strip()
    if raw.upper() in SEVERITY_CODES:
        return SEVERITY_CODES[raw.upper()]
    if raw in SEVERITY_BY_WORD:
        return SEVERITY_BY_WORD[raw]
    blob = f"{raw} {text}"
    for word, level in SEVERITY_BY_WORD.items():
        if word in blob:
            return level
    return "一般"


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """把一条巡查记录（含任意旧协议字段）归一为编译器事实。"""
    reasons: list[str] = []

    code, code_field = _pick(row, "问题编码")
    problem_text, problem_field = _pick(row, "发现问题")
    category = "其他"
    if code is not None and str(code).strip().upper() in PROBLEM_CODES:
        category = PROBLEM_CODES[str(code).strip().upper()]
        reasons.append(f"问题类型取自既有编码 {code}（字段 {code_field}）")
    elif problem_text is not None:
        category, hit = classify_problem(problem_text)
        if hit:
            reasons.append(f"问题类型按关键词归类为「{category}」（字段 {problem_field}）")
        else:
            reasons.append(f"未识别问题关键词，兜底为「其他」（字段 {problem_field}）")

    severity_raw, severity_field = _pick(row, "严重程度")
    severity = normalize_severity(severity_raw, str(problem_text or ""))
    if severity_raw is not None:
        reasons.append(f"严重程度按取值协议归一为「{severity}」（字段 {severity_field}）")

    grade_raw, grade_field = _pick(row, "道路等级")
    grade = "二级"
    if grade_raw is not None:
        token = str(grade_raw).strip()
        grade = ROAD_GRADE_CODES.get(token.upper(), ROAD_GRADE_CODES.get(token, token if token.endswith("级") else "二级"))
        reasons.append(f"道路等级归一为「{grade}」（字段 {grade_field}）")

    crew, crew_field = _pick(row, "管养班组")
    crew_name = str(crew or "").strip() or "未排班"
    if not crew:
        reasons.append("未携带管养班组，按「未排班」走默认发布策略")

    section, _ = _pick(row, "巡查路段")

    return {
        "巡查编号": str(row.get("巡查编号") or ""),
        "巡查路段": str(section or row.get("巡查路段") or ""),
        "巡查日期": str(row.get("巡查日期") or ""),
        "问题原文": str(problem_text or ""),
        "问题类型": category,
        "严重程度": severity,
        "道路等级": grade,
        "管养班组": crew_name,
        "归一依据": reasons,
    }


def facts_fingerprint(facts: dict[str, Any]) -> str:
    """事实指纹：同一批事实走任意端、任意次编译都应一致。"""
    basis = {key: facts.get(key) for key in (
        "问题类型", "严重程度", "道路等级", "巡查路段", "巡查日期", "管养班组",
    )}
    digest = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:12]
