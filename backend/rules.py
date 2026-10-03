"""冷链探头读数判定：摄氏温度不超过 8 为合格，否则超温。"""

from datetime import date, datetime, timedelta, timezone


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"


def parse_week_day(raw: str) -> date:
    """解析周参数：'YYYY-MM-DD'（周内任意一天）或 ISO 周 'YYYY-Www'。

    返回该周内的某一天；格式非法时抛 ValueError。
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("empty week param")
    if "-W" in value or "-w" in value:
        return datetime.strptime(value.upper() + "-1", "%G-W%V-%u").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def week_bounds(day: date) -> tuple[date, date]:
    """返回 day 所在周（周一至周日）的起止日期。"""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def week_window_utc(monday: date) -> tuple[datetime, datetime]:
    """周一零点（UTC）到下一周一零点，用于 SQL 区间 [start, end)。"""
    start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=7)


def summarize_counts(qualified: int, overtemp: int) -> dict:
    """由合格量与超温量算出合计与占比（百分比，保留一位小数）。

    周报数字一律由后台在此计算，页面只展示，不自行加总。
    """
    total = qualified + overtemp
    if total > 0:
        qualified_ratio = round(100.0 * qualified / total, 1)
        overtemp_ratio = round(100.0 * overtemp / total, 1)
    else:
        qualified_ratio = 0.0
        overtemp_ratio = 0.0
    return {
        "qualified_count": qualified,
        "overtemp_count": overtemp,
        "total_count": total,
        "qualified_ratio": qualified_ratio,
        "overtemp_ratio": overtemp_ratio,
    }
