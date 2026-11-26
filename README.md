# 发版闸（Shipgate）

骨架：发版申请入闸 → 环境晋升裁决 → 放行/回滚窗 → 发布结果对账

- 前端 http://127.0.0.1:6800
- API http://127.0.0.1:11800
- 数据库环境变量 `SHIPGATE_DB`（容器内默认 `/data/shipgate.db`）

```bash
docker compose up -d --build
```

## 环境链与晋升路线（整站唯一路线）

环境链按 `environments.sort_order` 编码：**测 → 预 → 产**。整站只保留一条互斥路线：

> **允许跨环落产** —— 制品摘要非空且流水线绿时，可以跨环写入目标环境；
> 跨环事实落在晋升事件（`跨环`/`跳过环境`）与发版单（`落地环境`/`最近跨环`）上，
> 名册与总览露出真实落地环境。

裁决只有一处实现：`backend/app/services/chain.py::evaluate_promotion`。写入侧
（`POST /api/发版单/{id}/晋升`）与读取侧（`GET /api/发版单/{id}/晋升候选`、
`POST /api/发版单/{id}/晋升研判`）共用同一函数，页面上的可点目标由候选接口重放得出，
不存在第二套按钮判断。

### 拒因码

| 拒因码 | 含义 |
| --- | --- |
| `E_RELEASE_STATUS` | 当前状态不可晋升 |
| `E_APP_DISABLED` | 应用已停用 |
| `E_ENV_UNKNOWN` | 目标环境不存在 |
| `E_NOT_ADMITTED` | 未落地时只能先入测 |
| `E_SAME_ENV` | 目标环境即当前环境 |
| `E_BACKWARD` | 只能向更高环境晋升 |
| `E_CROSS_COND` | 跨环条件不足：制品摘要缺失或流水线未绿 |
| `E_WINDOW_CLOSED` | 目标环境放行窗已关 |
| `E_CONCURRENT` | 发版单被并发推进，当前环境已变更 |

### 同一笔收口

晋升在单个 `BEGIN IMMEDIATE` 事务内完成：名册版本 → 实例 → 晋升事件 → 单状态/当前环境 →
审计。任一步失败整笔回滚到点晋升前快照（夹具用故障注入验证“名册已切、事件未落”半截场景）。
拒绝也会落一条 `拒绝` 事件并带拒因码，但不改名册、不改单状态、不被总览计为已放行。
并发双晋升由数据库层串行化：只有一笔能改当前环境，另一笔读到新状态后返回 `E_RELEASE_STATUS`
或 `E_SAME_ENV`。

## 夹具

```bash
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
../.venv/bin/python -m pytest -q
```

- `tests/test_promote_chain.py`：下一环编码表、逐级合法晋升、逐级到产、跨环落产、
  跨环条件不足拒绝、同环境/未知环境拒绝、候选与写入侧一致性、双晋升对撞、
  半截名册已切事件未落的整笔回滚、回滚后名册不被拧花、审计过滤条件对读。
- `tests/test_migration.py`：旧库补 `seq/cross_env/skipped_envs/reason_code/env_code` 列。
