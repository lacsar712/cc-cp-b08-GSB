# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

顶栏「周报签出」进入周报落地页：选周查看后台实时计算的在线汇总（合格量、超温量、合格率/超温率），记录员可一键签出当周只读副本；副本在签出时冻结，之后新办结只刷新在线汇总，旧副本不再变动。值班员可查看在线汇总与已签出副本，但不能签出。

## 周报接口

| 方法/路径 | 权限 | 说明 |
|-----------|------|------|
| `GET /api/reports/weekly?week=YYYY-Www` | 登录 | 该 ISO 周在线汇总，计数与占比均由后台 SQL 聚合；附带是否已签出 |
| `GET /api/reports/weekly/checkouts` | 登录 | 已签出副本列表 |
| `GET /api/reports/weekly/checkouts/{id}` | 登录 | 单个只读副本（签出时冻结的数字） |
| `POST /api/reports/weekly/checkout` | 仅记录员 | 事务内聚合当周数字并写入 `weekly_checkouts`；同周重复签出返回 409 |

页面只展示接口返回的成品数字，不从读数列表自行加总。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
