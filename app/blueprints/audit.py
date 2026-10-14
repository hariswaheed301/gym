from flask import Blueprint, render_template, request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..models import AuditLog
from ..security import admin_required
from ..utils import local_day_bounds, paginate, parse_date

bp = Blueprint("audit", __name__, url_prefix="/audit")


@bp.get("/")
@admin_required
def index():
    action = request.args.get("action", "").strip()
    entity = request.args.get("entity", "").strip()
    df, dt = parse_date(request.args.get("from")), parse_date(request.args.get("to"))
    stmt = select(AuditLog).options(joinedload(AuditLog.actor)).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if entity:
        stmt = stmt.where(AuditLog.entity_type == entity)
    if df:
        stmt = stmt.where(AuditLog.at >= local_day_bounds(df)[0])
    if dt:
        stmt = stmt.where(AuditLog.at < local_day_bounds(dt)[1])
    return render_template("audit/list.html", page=paginate(stmt), action=action, entity=entity,
                           date_from=request.args.get("from", ""), date_to=request.args.get("to", ""))
