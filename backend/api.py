import json
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import (
    judge_temp,
    parse_week_day,
    summarize_counts,
    week_bounds,
    week_window_utc,
)

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request, action: str = "提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": f"仅记录员可{action}"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, now())
        RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        """,
        probe_id,
        temp_c,
        user["username"],
    )
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


def _resolve_week_bounds(mapping) -> tuple[date, date]:
    """从 query/body 取周参数（week_of / week / week_start），缺省为今天（UTC）所在周。"""
    raw = str(
        mapping.get("week_of") or mapping.get("week") or mapping.get("week_start") or ""
    ).strip()
    if not raw:
        day = datetime.now(timezone.utc).date()
    else:
        try:
            day = parse_week_day(raw)
        except ValueError as exc:
            raise web.HTTPBadRequest(
                text=json.dumps(
                    {"detail": "周参数格式错误，应为 YYYY-MM-DD 或 YYYY-Www"},
                    ensure_ascii=False,
                ),
                content_type="application/json",
            ) from exc
    return week_bounds(day)


async def _compute_week_summary(pool: asyncpg.Pool, monday: date) -> dict:
    """后台按周聚合已办结读数：合格量、超温量、合计与占比。"""
    start, end = week_window_utc(monday)
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE verdict = '合格') AS qualified_count,
               COUNT(*) FILTER (WHERE verdict = '超温') AS overtemp_count
        FROM probe_readings
        WHERE status = 'done' AND processed_at >= $1 AND processed_at < $2
        """,
        start,
        end,
    )
    return summarize_counts(row["qualified_count"], row["overtemp_count"])


def _snapshot_json(r) -> dict:
    return {
        "id": r["id"],
        "week_start": r["week_start"].isoformat() if r["week_start"] else None,
        "week_end": r["week_end"].isoformat() if r["week_end"] else None,
        "qualified_count": r["qualified_count"],
        "overtemp_count": r["overtemp_count"],
        "total_count": r["total_count"],
        "qualified_ratio": r["qualified_ratio"],
        "overtemp_ratio": r["overtemp_ratio"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


async def weekly_summary(request: web.Request) -> web.Response:
    """在线汇总：后台实时统计所选周，页面只展示不自行加总。"""
    require_user(request)
    monday, sunday = _resolve_week_bounds(request.query)
    summary = await _compute_week_summary(request.app["pool"], monday)
    return web.json_response(
        {
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            **summary,
        }
    )


async def checkout_weekly_report(request: web.Request) -> web.Response:
    """签出：把当周汇总冻结进只读副本；已签出的周返回 409，旧副本不再变动。"""
    user = require_writer(request, action="签出周报")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    monday, sunday = _resolve_week_bounds(body)
    pool: asyncpg.Pool = request.app["pool"]
    summary = await _compute_week_summary(pool, monday)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO weekly_report_snapshots
                (week_start, week_end, qualified_count, overtemp_count, total_count,
                 qualified_ratio, overtemp_ratio, created_by, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
            RETURNING id, week_start, week_end, qualified_count, overtemp_count,
                      total_count, qualified_ratio, overtemp_ratio, created_by, created_at
            """,
            monday,
            sunday,
            summary["qualified_count"],
            summary["overtemp_count"],
            summary["total_count"],
            summary["qualified_ratio"],
            summary["overtemp_ratio"],
            user["username"],
        )
    except asyncpg.UniqueViolationError as exc:
        raise web.HTTPConflict(
            text=json.dumps(
                {"detail": "该周已签出，只读副本已锁定不再变动"}, ensure_ascii=False
            ),
            content_type="application/json",
        ) from exc
    return web.json_response(
        {**_snapshot_json(row), "message": "已签出，当周汇总已锁入只读副本"},
        status=201,
    )


async def list_weekly_snapshots(request: web.Request) -> web.Response:
    """已签出区：记录员与值班员都可查看的只读副本列表。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, week_start, week_end, qualified_count, overtemp_count, total_count,
               qualified_ratio, overtemp_ratio, created_by, created_at
        FROM weekly_report_snapshots
        ORDER BY week_start DESC, id DESC
        """
    )
    return web.json_response([_snapshot_json(r) for r in rows])


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/weekly-report/summary", weekly_summary)
    app.router.add_post("/api/weekly-report/checkout", checkout_weekly_report)
    app.router.add_get("/api/weekly-report/snapshots", list_weekly_snapshots)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
