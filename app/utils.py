"""Time, money, formatting and pagination helpers. All timestamps are stored in UTC."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from flask import current_app, request, url_for
from sqlalchemy import func, select

from .extensions import db

CENT = Decimal("0.01")


def now_utc() -> datetime:
    return datetime.now(UTC)


def gym_tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["GYM_TIMEZONE"])


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(gym_tz())


def local_today(now: datetime | None = None) -> date:
    return to_local(now or now_utc()).date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC [start, end) instants covering one calendar day in the gym timezone."""
    tz = gym_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_naive_to_utc(naive: datetime) -> datetime:
    return naive.replace(tzinfo=gym_tz()).astimezone(UTC)


def quantize_money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def format_duration(td: timedelta | None) -> str:
    if td is None:
        return "—"
    minutes = max(0, int(td.total_seconds() // 60))
    if minutes < 1:
        return "less than a minute"
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_page() -> int:
    try:
        return max(1, int(request.args.get("page", "1")))
    except ValueError:
        return 1


class Page:
    def __init__(self, items, page, per_page, total):
        self.items, self.page, self.per_page, self.total = items, page, per_page, total
        self.pages = max(1, math.ceil(total / per_page))
        self.has_prev, self.has_next = page > 1, page < self.pages

    def iter_pages(self):
        return range(max(1, self.page - 2), min(self.pages, self.page + 2) + 1)


def paginate(stmt, page: int | None = None, per_page: int | None = None, scalars: bool = True) -> Page:
    per_page = per_page or current_app.config["PER_PAGE"]
    page = page or get_page()
    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    page = min(page, max(1, math.ceil(total / per_page)))
    result = db.session.execute(stmt.limit(per_page).offset((page - 1) * per_page))
    items = result.unique().scalars().all() if scalars else result.unique().all()
    return Page(items, page, per_page, total)


def register_template_helpers(app) -> None:
    fmt = "%d %b %Y, %I:%M %p"

    @app.template_filter("localdt")
    def _localdt(dt):
        return to_local(dt).strftime(fmt) if dt else "—"

    @app.template_filter("localtime")
    def _localtime(dt):
        return to_local(dt).strftime("%I:%M %p") if dt else "—"

    @app.template_filter("datefmt")
    def _datefmt(d):
        return d.strftime("%d %b %Y") if d else "—"

    @app.template_filter("duration")
    def _duration(td):
        return format_duration(td)

    @app.template_filter("iso")
    def _iso(dt):
        return dt.astimezone(UTC).isoformat() if dt else ""

    @app.template_filter("money")
    def _money(value, currency=None):
        currency = currency or current_app.config["DEFAULT_CURRENCY"]
        return f"{currency} {Decimal(value or 0):,.2f}"

    @app.template_filter("inputdt")
    def _inputdt(dt):
        return to_local(dt).strftime("%Y-%m-%dT%H:%M") if dt else ""

    @app.template_global()
    def page_url(n: int) -> str:
        args = request.args.to_dict()
        args["page"] = n
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    @app.template_global()
    def now_iso() -> str:
        return now_utc().isoformat()
