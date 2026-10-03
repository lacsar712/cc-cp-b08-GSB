# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

顶栏「周报签出」页提供周维度合格率周报：选定周后由后台统计该周已办结读数的合格量、超温量与占比（页面只展示，不自行加总）；记录员可一键**签出**，把当周汇总冻结为只读副本，之后新办结的读数只刷新在线汇总，已签出副本不再变动。记录员与值班员都能查看在线汇总与已签出副本，仅记录员可签出。

## 周报接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/weekly-report/summary?week_of=YYYY-MM-DD` | 在线汇总（也支持 `week=YYYY-Www`），两种角色可查 |
| POST | `/api/weekly-report/checkout` | 签出当周，body `{"week_of":"YYYY-MM-DD"}`，仅记录员；该周已签出返回 409 |
| GET | `/api/weekly-report/snapshots` | 已签出只读副本列表，两种角色可查 |

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
