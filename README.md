# 市政道路桥梁养护管理平台

覆盖道路巡查、桥隧定检、路面病害、交安设施、绿化管养、除雪防汛及养护工程管理的市政道桥全要素养护后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 路段管理 | `road_section` | 管养路段 | 路段编号、路段名称、起止桩号 |
| 日常巡查 | `patrol` | 巡查记录 | 巡查编号、巡查路段、巡查日期 |
| 路面病害 | `pavement` | 病害记录 | 病害编号、所属路段、病害类型 |
| 桥梁定检 | `bridge` | 检测记录 | 检测编号、桥梁名称、检测类型 |
| 桥梁档案 | `bridge_info` | 桥梁 | 桥梁编号、桥梁名称、桥型结构 |
| 隧道管养 | `tunnel` | 隧道 | 隧道编号、隧道名称、隧道长度 |
| 交安设施 | `traffic_facility` | 交安设施 | 设施编号、设施类型、所属路段 |
| 排水设施 | `drainage` | 排水设施 | 设施编号、设施类型、所属路段 |
| 绿化管养 | `green` | 绿化区域 | 区域编号、区域名称、植物品种 |
| 路灯照明 | `lighting` | 路灯设施 | 灯具编号、灯具类型、功率 |
| 除雪防滑 | `winter` | 除雪作业 | 作业编号、作业路段、作业日期 |
| 防汛应急 | `flood` | 防汛记录 | 记录编号、预警级别、影响路段 |
| 边坡防护 | `slope` | 边坡 | 边坡编号、所属路段、边坡类型 |
| 伸缩缝管理 | `expansion` | 伸缩缝 | 缝编号、所属桥梁、缝类型 |
| 支座维护 | `bearing` | 桥梁支座 | 支座编号、所属桥梁、支座类型 |
| 养护工程 | `project` | 养护工程 | 工程编号、工程名称、工程类型 |
| 养护车辆 | `vehicle` | 养护车辆 | 车辆编号、车辆类型、车牌号 |
| 养护材料 | `material` | 养护材料 | 材料编号、材料名称、材料类别 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 巡查约定编译器

同一条巡查问题，历史上在手持终端、地图端、后台页面各有一套裁决，换端后处置建议会变。
`backend/app/patrol_convention/` 把三套旧裁决收束成一份**版本化约定**，编译器只做
确定性查表，同事实、同约定版本在任意端结论一致。

```text
patrol_convention/
├── protocol.py      既有取值协议归一：P01/P06 问题编码、1/2/3 与 S1/S2/S3 程度编码、
│                    中文字段/英文别名/自由文本关键词，归一依据全程留痕
├── conventions.py   约定注册表与约定水位（v2026-09 基线 / v2026-10 现行）
├── compiler.py      纯函数编译器：归一事实 + 约定版本 → 唯一裁决（建议/优先级/时限/回写目标）
├── legacy.py        终端、地图、后台三套旧裁决（只读固化，供影子比对与一键切回）
├── events.py        只增事件日志：幂等去重 + 原子提交（派生写入失败不留半个事件）
├── writeback.py     结论原子回写：巡查台账 patrol / 病害清单 pavement / 车队待办 fleet_todo
├── rollout.py       发布策略：shadow 影子 → canary 逐班组切流 → compiler 全量；legacy 一键回退
├── sampling.py      新旧结果确定性抽样批次比对（同盐同数据抽同一批）
└── facade.py        唯一编排入口（裁决、切流、迁移、重放）
```

### 发布纪律（对应控制台 `/patrol_convention`）

1. **先影子运行**：默认 `shadow`，对外仍返回旧裁决，编译器同步算结论只落影子记录，
   不碰台账；`GET /api/patrol/convention/shadow?drift_only=true` 看偏离。
2. **抽样批次比对**：`POST /comparisons` 按稳定散列抽样，逐字段列出编译器与三套旧裁决差异。
3. **逐班组切流**：`POST /rollout {"mode":"canary","canary_crews":["南片二班"]}`，
   按管养班组路由，命中走编译器、其余继续旧裁决，可逐班扩大。
4. **一键切回**：`POST /rollback` 立即恢复三套旧裁决并停止影子计算，切换本身也是事件。
5. **存量留档**：`POST /migration` 只补约定水位、不重算历史结论——约定生效日前为
   `legacy`、之后为基线 `v2026-09`；已带水位的记录跳过，可重复增量执行。
6. **事件重放幂等**：`POST /replay` 回基线快照后顺序重演全部事件，回写全部按自然键
   upsert，并校验派生台账与重放前是否收敛一致。
7. **原子提交**：一次裁决的台账/病害/车队待办三处写入先暂存后落笔，任一处失败整体回滚，
   转换未成功不会只写入半个事件（见 `tests/test_patrol_convention.py`）。

兼容既有取值协议：回写时同时维护旧字段「处置措施」与新字段「处置建议 / 约定版本 /
约定水位 / 裁决指纹」，旧页面与既有报表不受影响。

后端测试：`cd backend && python3 -m unittest tests.test_patrol_convention -v`

