import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")

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


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可提交读数"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def _json_error(status_cls, detail: str) -> Exception:
    return status_cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def parse_week(value: str | None) -> tuple[str, date, date]:
    """把 2026-W40 形式的周键解析为 (周键, 周一, 周日)；缺省取当前 UTC ISO 周。"""
    if not value:
        today = datetime.now(timezone.utc).date()
        iso = today.isocalendar()
        year, week_no = iso.year, iso.week
    else:
        m = WEEK_RE.match(value.strip())
        if not m:
            raise _json_error(web.HTTPBadRequest, "周格式应为 YYYY-Www，例如 2026-W40")
        year, week_no = int(m.group(1)), int(m.group(2))
        try:
            date.fromisocalendar(year, week_no, 1)
        except ValueError as exc:
            raise _json_error(web.HTTPBadRequest, "周编号不存在") from exc
    week_start = date.fromisocalendar(year, week_no, 1)
    week_end = week_start + timedelta(days=6)
    return f"{year:04d}-W{week_no:02d}", week_start, week_end


WEEKLY_AGG_SQL = """
SELECT
    count(*) FILTER (WHERE verdict = '合格') AS pass_count,
    count(*) FILTER (WHERE verdict = '超温') AS fail_count,
    count(*) AS total_count
FROM probe_readings
WHERE status = 'done'
  AND processed_at >= $1
  AND processed_at < $2
"""


def _summary_payload(week_key: str, week_start: date, week_end: date, agg) -> dict:
    """汇总数字一律在此成型：计数是 SQL 聚合结果，占比也在后台算好，页面不得自行加总。"""
    total = int(agg["total_count"])
    pass_count = int(agg["pass_count"])
    fail_count = int(agg["fail_count"])
    pass_ratio = round(pass_count / total, 4) if total else 0.0
    fail_ratio = round(fail_count / total, 4) if total else 0.0
    return {
        "week_key": week_key,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "total_count": total,
        "pass_ratio": pass_ratio,
        "fail_ratio": fail_ratio,
        "pass_percent": round(pass_ratio * 100, 1),
        "fail_percent": round(fail_ratio * 100, 1),
    }


def _checkout_payload(row) -> dict:
    return {
        "id": row["id"],
        "week_key": row["week_key"],
        "week_start": row["week_start"].isoformat(),
        "week_end": row["week_end"].isoformat(),
        "pass_count": row["pass_count"],
        "fail_count": row["fail_count"],
        "total_count": row["total_count"],
        "pass_ratio": row["pass_ratio"],
        "fail_ratio": row["fail_ratio"],
        "pass_percent": round(row["pass_ratio"] * 100, 1),
        "fail_percent": round(row["fail_ratio"] * 100, 1),
        "checked_out_by": row["checked_out_by"],
        "checked_out_at": row["checked_out_at"].isoformat() if row["checked_out_at"] else None,
    }


async def weekly_summary(request: web.Request) -> web.Response:
    require_user(request)
    week_key, week_start, week_end = parse_week(request.query.get("week"))
    pool: asyncpg.Pool = request.app["pool"]
    start_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc)
    end_dt = datetime(week_end.year, week_end.month, week_end.day, tzinfo=timezone.utc) + timedelta(days=1)
    async with pool.acquire() as conn:
        agg = await conn.fetchrow(WEEKLY_AGG_SQL, start_dt, end_dt)
        frozen = await conn.fetchrow(
            "SELECT id, checked_out_at FROM weekly_checkouts WHERE week_key = $1",
            week_key,
        )
    payload = _summary_payload(week_key, week_start, week_end, agg)
    payload["frozen"] = frozen is not None
    payload["checkout_id"] = frozen["id"] if frozen else None
    payload["checked_out_at"] = frozen["checked_out_at"].isoformat() if frozen else None
    return web.json_response(payload)


async def list_checkouts(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, week_key, week_start, week_end, pass_count, fail_count, total_count,
               pass_ratio, fail_ratio, checked_out_by, checked_out_at
        FROM weekly_checkouts
        ORDER BY week_key DESC, id DESC
        """
    )
    return web.json_response([_checkout_payload(r) for r in rows])


async def get_checkout(request: web.Request) -> web.Response:
    require_user(request)
    checkout_id = int(request.match_info["id"])
    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        SELECT id, week_key, week_start, week_end, pass_count, fail_count, total_count,
               pass_ratio, fail_ratio, checked_out_by, checked_out_at
        FROM weekly_checkouts
        WHERE id = $1
        """,
        checkout_id,
    )
    if not row:
        raise _json_error(web.HTTPNotFound, "签出副本不存在")
    return web.json_response(_checkout_payload(row))


async def create_checkout(request: web.Request) -> web.Response:
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(web.HTTPForbidden, "仅记录员可签出周报")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    week_key, week_start, week_end = parse_week(str(body.get("week", "")).strip())
    start_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc)
    end_dt = datetime(week_end.year, week_end.month, week_end.day, tzinfo=timezone.utc) + timedelta(days=1)

    pool: asyncpg.Pool = request.app["pool"]
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 同一事务内聚合再落副本：副本保存签出这一刻的当周数字，之后不再被改写。
                agg = await conn.fetchrow(WEEKLY_AGG_SQL, start_dt, end_dt)
                summary = _summary_payload(week_key, week_start, week_end, agg)
                existing = await conn.fetchrow(
                    "SELECT id FROM weekly_checkouts WHERE week_key = $1 FOR UPDATE",
                    week_key,
                )
                if existing:
                    raise _json_error(web.HTTPConflict, "该周周报已签出，副本不可重复签出或修改")
                row = await conn.fetchrow(
                    """
                    INSERT INTO weekly_checkouts
                        (week_key, week_start, week_end, pass_count, fail_count, total_count,
                         pass_ratio, fail_ratio, checked_out_by, checked_out_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
                    RETURNING id, week_key, week_start, week_end, pass_count, fail_count, total_count,
                              pass_ratio, fail_ratio, checked_out_by, checked_out_at
                    """,
                    week_key,
                    week_start,
                    week_end,
                    summary["pass_count"],
                    summary["fail_count"],
                    summary["total_count"],
                    summary["pass_ratio"],
                    summary["fail_ratio"],
                    user["username"],
                )
    except asyncpg.UniqueViolationError as exc:
        raise _json_error(web.HTTPConflict, "该周周报已签出，副本不可重复签出或修改") from exc
    return web.json_response(_checkout_payload(row), status=201)


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
    app.router.add_get("/api/reports/weekly", weekly_summary)
    app.router.add_get("/api/reports/weekly/checkouts", list_checkouts)
    app.router.add_get(r"/api/reports/weekly/checkouts/{id:\d+}", get_checkout)
    app.router.add_post("/api/reports/weekly/checkout", create_checkout)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
