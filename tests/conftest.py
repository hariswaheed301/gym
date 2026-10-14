"""Test fixtures. Tests run only against a database whose name ends in `_test` (never your real data)."""

import pytest
from sqlalchemy import text

from app import create_app
from app.extensions import db


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    dbname = app.config["SQLALCHEMY_DATABASE_URI"].rsplit("/", 1)[-1].split("?")[0]
    if not dbname.endswith("_test"):
        pytest.exit(f"Refusing to run tests against database '{dbname}': its name must end with '_test'.")
    with app.app_context():
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        db.session.commit()
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _isolated_db(app):
    ctx = app.app_context()
    ctx.push()
    yield
    db.session.remove()
    tables = ", ".join(t.name for t in db.metadata.sorted_tables)
    db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.session.commit()
    ctx.pop()


@pytest.fixture()
def client(app):
    return app.test_client()
