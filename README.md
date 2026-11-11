# 点时间特征工程 · 时间泄漏核查样例

给定一组用户和预测时刻，构造训练样本：

- **特征**：近 7 天支付次数、支付金额、最近支付时间
- **标签**：未来 7 天是否下单
- 特征只能使用**预测那一刻已经可用**的数据；晚到数据、事后补录、事后撤销都不允许倒灌进早先的特征。

技术栈：DuckDB（批量计算 + Parquet 导出）+ FastAPI（接口）+ React/Vite（样本预览与下钻）。

## 时间语义（核心规则）

每条支付 / 订单记录都带四个关键字段：

| 字段 | 含义 |
| --- | --- |
| `event_time` | 业务发生时间 |
| `ingest_time` | 系统可用时间（该版本何时对下游可见） |
| `event_id` | 事件编号 |
| `version` | 修订版本号（同一事件可有多版，如 ACTIVE → REVOKED） |

计算顺序不能颠倒：

1. **先选版本**：对每个 `(样本, event_id)`，只在 `ingest_time ≤ 决策时刻` 的修订里取 `version` 最大的一版。
   - 特征的决策时刻 = `prediction_time`
   - 标签的决策时刻 = 声明的 `observation_cutoff`
2. **再判窗口**：用选中版本的 `event_time` 判断是否落在窗口内。
3. **特征窗口** `[t − 7d, t)`：**左包含、右不包含**，`event_time = t` 的事件排除。
4. **标签窗口** `[t, t + 7d)`：仅当 `observation_cutoff ≥ t + 7d` 时标签成熟；观察范围不足时标签置 `NULL` 并标记 `label_mature = false`，**不是"没有下单"**。
5. 无支付历史时：次数、金额为 `0`，最近支付时间为 `NULL`。

## 构造的核查场景

所有样本共享 `t = 2026-11-10 00:00:00`，特征窗口 `[11-03, 11-10)`、标签窗口 `[11-10, 11-17)`。

| 样本 | 场景 | 正确特征（次数/金额） | 全历史错误基线 | 正确标签 vs 基线 |
| --- | --- | --- | --- | --- |
| S0 | 基线 | 2 / 250 | 2 / 250 | 1 vs 1 |
| S1 | **晚到支付**：P1-2 业务时间在窗内，但 11-10 08:00 才入库 | 1 / 120 | 2 / 200（泄漏） | 1 vs 1 |
| S2 | **事后撤销**：撤销版 v2 在 11-11 才入库，t 时刻只能看到 ACTIVE 的 v1 | 1 / 200 | 0 / 0（被未来撤销抹掉） | 1 vs 1 |
| S3 | **窗口边界**：左边界整点支付纳入；t 整点支付排除（含 t 前已可用的一条）；t 前可见的撤销生效；t+7d 整点订单排除 | 1 / 50 | 1 / 50 | 1 vs 1 |
| S4 | **未成熟标签**：观察截止仅到 t+3d，标签留空 | 1 / 60 | 1 / 60 | **NULL（未成熟）vs 1（伪造标签）** |
| S5 | **无支付历史** | 0 / 0，最近支付时间为空 | 0 / 0 | 1 vs 1 |
| S6 | **事后补录修订**：金额 100→80 的修订 t 后才到；窗内保留 100；另有取消订单（截止前可见 CANCELED，标签为 0） | 1 / 100 | 1 / 80（被未来修订覆盖） | 0 vs 0 |

## 正确做法 vs "把全部历史直接关联"

管道同时计算两套结果并落库：

- `pay_count_7d / pay_amount_7d / last_pay_time / label_order_7d`：点时间正确结果
- `naive_*`：错误基线——忽略 `ingest_time`、始终取全局最新修订、忽略标签成熟度

前端每个特征单元格把两者**并排展示**，出现分歧时红色高亮；点开后可看到该样本下**每一条记录的每个修订版本**：

- 正确逻辑的纳入 / 排除结论及中文原因（晚到、被新版本取代、已撤销、窗口外……）
- 错误基线对同一行的结论，两者不一致的行黄色高亮——即泄漏发生的具体位置
- 支持「仅纳入 / 仅排除 / 仅判定不一致」筛选

## 目录结构

```
backend/
  config.py          # 路径与窗口配置
  generate_data.py   # 合成数据（显式构造各泄漏场景）
  pipeline.py        # DuckDB SQL 管道：版本选择 → 窗口判定 → 特征/标签 → Parquet
  app.py             # FastAPI：/api/samples、/api/samples/{id}、静态托管
  tests/test_pipeline.py  # 七个场景的语义回归测试
frontend/
  src/               # React 预览（样本对比表 + 记录级下钻）
data/                # 生成物：pipeline.duckdb 与 parquet/（git 忽略）
```

导出的 Parquet：

- `data/parquet/samples.parquet` — 样本主表（正确列 + naive 列 + 泄漏标记）
- `data/parquet/payment_lineage.parquet` — 每个支付修订的逐行判定
- `data/parquet/order_lineage.parquet` — 每个订单修订的逐行判定

## 快速开始

```bash
make install          # 建虚拟环境、装 Python 与 npm 依赖
make data             # 1. 生成合成数据
make pipeline         # 2. 计算特征/标签并导出 Parquet
make test             # 3. 跑语义测试（8 个）
make build            # 4. 构建前端
make backend          # 5. 启动服务 → http://127.0.0.1:8000
```

开发模式（热更新）：分别运行 `make backend` 与 `make frontend`，前端在 http://localhost:5173 ，`/api` 自动代理到 8000。

也可直接用虚拟环境：

```bash
.venv/bin/python -m backend.generate_data
.venv/bin/python -m backend.pipeline
.venv/bin/python -m uvicorn backend.app:app --port 8000
```

## 关键 SQL 思路

版本选择用窗口函数，把"可用性"嵌进版本筛选，而不是先 join 再过滤：

```sql
MAX(version) FILTER (WHERE ingest_time <= prediction_time)
  OVER (PARTITION BY sample_id, event_id) AS latest_visible_version
```

取到 `latest_visible_version` 后，才对该版本做窗口与状态判定；
标签成熟度由 `observation_cutoff >= prediction_time + INTERVAL 7 DAY` 决定，
未成熟时 `label_order_7d` 直接输出 `NULL`。
