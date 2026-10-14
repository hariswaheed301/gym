from flask import Blueprint, render_template, request
from sqlalchemy import select  # noqa: F401

from ..security import staff_required
from ..services import attendance as att_svc, reports
from ..utils import local_today, paginate, parse_date

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.get("/daily")
@staff_required
def daily():
    day = parse_date(request.args.get("date")) or local_today()
    report = reports.daily_report(day)
    page = paginate(att_svc.history_stmt(date_from=day, date_to=day))
    return render_template("reports/daily.html", r=report, page=page, day=day)
