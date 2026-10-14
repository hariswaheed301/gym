from uuid import uuid4

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..forms import PaymentForm, ReasonForm
from ..models import Member, Membership, Payment
from ..security import admin_required, staff_required
from ..services import payments as svc
from ..services.errors import ServiceError
from ..utils import paginate

bp = Blueprint("payments", __name__, url_prefix="/payments")


@bp.get("/")
@staff_required
def index():
    q, status = request.args.get("q", "").strip(), request.args.get("status", "")
    stmt = (select(Payment).join(Membership).join(Member)
            .options(joinedload(Payment.membership).joinedload(Membership.member), joinedload(Payment.recorded_by))
            .order_by(Payment.paid_at.desc()))
    if q:
        from ..services.members import search_clause
        stmt = stmt.where(search_clause(q))
    if status in ("valid", "voided"):
        stmt = stmt.where(Payment.status == status)
    return render_template("payments/list.html", page=paginate(stmt), q=q, status=status)


@bp.get("/dues")
@staff_required
def dues():
    q = request.args.get("q", "").strip()
    page = paginate(svc.dues_stmt(q), scalars=False)
    members_n, total = svc.dues_totals()
    return render_template("payments/dues.html", page=page, q=q, members_n=members_n, total=total)


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def create():
    membership = db.get_or_404(Membership, request.args.get("membership_id", type=int) or 0)
    form = PaymentForm()
    if request.method == "GET":
        form.idempotency_key.data = uuid4().hex
        form.amount.data = max(membership.outstanding, 0)
    if form.validate_on_submit():
        try:
            payment, created = svc.record_payment(
                membership.id, form.amount.data, form.method.data, current_user.id,
                form.reference_number.data, form.idempotency_key.data or None, form.notes.data)
        except ServiceError as exc:
            flash(str(exc), "danger")
        else:
            flash("Payment recorded." if created else "This payment was already recorded (duplicate submission ignored).",
                  "success" if created else "info")
            return redirect(url_for("payments.receipt", payment_id=payment.id))
    return render_template("payments/form.html", form=form, m=membership)


@bp.get("/<int:payment_id>")
@staff_required
def receipt(payment_id):
    p = db.get_or_404(Payment, payment_id)
    return render_template("payments/receipt.html", p=p, m=p.membership, form=ReasonForm())


@bp.post("/<int:payment_id>/void")
@admin_required
def void(payment_id):
    form = ReasonForm()
    if form.validate_on_submit():
        try:
            svc.void_payment(payment_id, form.reason.data, current_user.id)
            flash("Payment voided. The record is kept for audit; record a new payment if it was a correction.", "success")
        except ServiceError as exc:
            flash(str(exc), "danger")
    else:
        flash("A reason is required to void a payment.", "danger")
    return redirect(url_for("payments.receipt", payment_id=payment_id))
