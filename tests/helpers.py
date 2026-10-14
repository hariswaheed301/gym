import itertools
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Equipment, Member, MembershipPlan, User
from app.security import hash_password
from app.services import memberships as ms_svc
from app.services import payments as pay_svc
from app.utils import gym_tz, local_today

PASSWORD = "Str0ng-Passw0rd!"
_n = itertools.count(1)


def make_user(role="receptionist", username=None):
    n = next(_n)
    user = User(username=username or f"{role}{n}", full_name=f"{role.title()} {n}", role=role,
                password_hash=hash_password(PASSWORD))
    db.session.add(user)
    db.session.commit()
    return user


def make_member(name=None, active=True):
    n = next(_n)
    m = Member(member_number=f"T{n:05d}", full_name=name or f"Member {n}", phone=f"0300{n:07d}", is_active=active)
    db.session.add(m)
    db.session.commit()
    return m


def make_plan(name=None, price="5000", days=30, hours=None):
    plan = MembershipPlan(name=name or f"Plan {next(_n)}", duration_days=days, price=Decimal(price), currency="PKR",
                          access_start_time=hours[0] if hours else None, access_end_time=hours[1] if hours else None)
    db.session.add(plan)
    db.session.commit()
    return plan


def make_equipment(name=None, restricted=False, status="operational"):
    eq = Equipment(name=name or f"Machine {next(_n)}", category="Strength", is_restricted=restricted, status=status)
    db.session.add(eq)
    db.session.commit()
    return eq


def make_membership(member, plan=None, start=None, discount="0", paid=None, actor=None):
    actor = actor or make_user("admin")
    plan = plan or make_plan()
    start = start or local_today() - timedelta(days=5)
    ms = ms_svc.create_membership(member.id, plan.id, start, Decimal(discount), actor.id)
    if paid is not None:
        pay_svc.record_payment(ms.id, Decimal(str(paid)), "cash", actor.id)
    return ms


def eligible_member(**kw):
    """A member with a current, fully paid membership."""
    m = make_member()
    make_membership(m, paid="5000", **kw)
    return m


def local_dt(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=gym_tz()).astimezone(UTC)


def login(client, user, password=PASSWORD):
    return client.post("/login", data={"username": user.username, "password": password})
