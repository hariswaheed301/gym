from flask import Blueprint, render_template, request
from flask_login import login_required

from ..services import reports
from ..utils import parse_date

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    day = parse_date(request.args.get("date"))
    return render_template("dashboard/index.html", m=reports.dashboard_metrics(day))
