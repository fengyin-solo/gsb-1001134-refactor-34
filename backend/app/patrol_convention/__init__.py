"""巡查约定编译器：把终端 / 地图 / 后台三套裁决收束为同一份版本化结论。

子模块职责：
- protocol：既有取值协议归一（字段别名、问题编码、程度编码）；
- conventions：版本化巡查约定注册表与“约定水位”；
- compiler：约定编译器，事实 + 约定 → 唯一裁决；
- legacy：三套旧裁决（终端、地图、后台），仅用于影子运行与切流比对；
- events：只增事件日志，幂等去重 + 原子提交（不允许半个事件）；
- writeback：裁决结论回写巡查台账、病害清单、车队待办；
- rollout：发布策略（影子 / 逐班组切流 / 一键切回旧实现）；
- sampling：新旧结果按抽样批次比对；
- migration：存量记录带约定水位迁移；
- facade：对外唯一编排入口。
"""
from app.patrol_convention.facade import convention_service

__all__ = ["convention_service"]
