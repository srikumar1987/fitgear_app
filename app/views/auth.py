from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db
from ..models import Admin

bp = Blueprint("auth", __name__)
MIN_PASSWORD = 6


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256")


def _admin() -> Admin | None:
    return db.session.scalars(select(Admin)).first()


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("balances.index")


def _sign_in() -> None:
    session.clear()
    session["admin"] = True
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    admin = _admin()
    if admin is None:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        if check_password_hash(admin.password_hash, request.form.get("password", "")):
            _sign_in()
            return redirect(_safe_next(request.args.get("next")))
        flash("Wrong password", "danger")
    return render_template("auth/login.html")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if _admin() is not None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < MIN_PASSWORD:
            flash(f"Use at least {MIN_PASSWORD} characters", "danger")
        elif password != request.form.get("confirm", ""):
            flash("The two passwords do not match", "danger")
        else:
            db.session.add(Admin(password_hash=hash_password(password)))
            db.session.commit()
            _sign_in()
            return redirect(url_for("balances.index"))
    return render_template("auth/setup.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
