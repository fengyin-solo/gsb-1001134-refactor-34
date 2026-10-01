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

## 巡查约定编译器

日常巡查问题在手持终端（`legacy_terminal`）、地图图层（`legacy_map`）、后台页面
（`legacy_office`）原有三套裁决，同一记录换端结论会变。现已收束为「巡查约定编译器」
（后端代码在 `backend/app/patrol_compiler/`）：

```text
三端取值信封（中文键/驼峰键/旧信封）
  -> protocol.normalize_facts 归一化（UNKNOWN 会带 recognized=False）
  -> conventions 版本化约定（v2026.01 旧版 / v2026.09 现行，带生效日期与 SHA256 指纹）
  -> compiler 编译（错误一次报全）+ 首条命中规则求值
  -> 与对应旧端引擎双跑比对偏离
  -> events append-only JSONL（事件 ID 内容指纹，幂等；批次原子写入，不写半个事件）
  -> projections 回写：巡查台账（追加裁决字段）、病害清单、车队待办（按来源巡查 upsert）
```

关键约定：

- **取值协议兼容**：入参接受 `终端问题码/终端严重程度/终端班组`、
  `图层类型/告警等级`、`发现问题/所属班组` 与驼峰英文键；出参平铺
  `处置建议/处置时限小时/安全预警/转病害清单/转车队待办/处置班组/约定版本` 等旧协议键。
- **发布**：`shadow`（影子双跑，权威仍是旧实现，偏离记台账）→ `canary`
  （`canary_crews` 班组走编译器）→ `full`；`POST /api/patrol-compiler/rollout/fallback`
  一键切回旧三端实现。
- **留档与迁移**：存量迁移按 `巡查日期` 选当时生效的约定裁决并冻结（`archived`），
  事件携带完整约定水位（registry 版本 + 指纹）；冻结记录后续裁决不改写。
  严格模式下任一条无法识别，整批中止、零事件落库。
- **重放与比对**：`POST /api/patrol-compiler/replay` 从事件日志幂等重建投影；
  `POST /api/patrol-compiler/reconcile` 按抽样批次输出逐字段新旧偏离报告。
- 事件日志默认在 `backend/data/patrol_events.jsonl`，可用环境变量
  `PATROL_EVENT_LOG` 覆盖，设为 `memory` 时纯内存运行。

前端「巡查裁决控制台」（侧边栏入口 `/patrol/compiler`）可切流、单条裁决、
整批迁移、跑抽样比对、查看影子偏离与三表回写、触发重放。

后端纯标准库测试（无需安装依赖）：

```bash
cd backend
PATROL_EVENT_LOG=memory python3 -m unittest discover -s tests
PATROL_EVENT_LOG=memory python3 demo_patrol_compiler.py
```

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
