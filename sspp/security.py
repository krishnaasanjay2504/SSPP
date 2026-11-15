"""Session-based authentication and role-based access control (RBAC)."""
from functools import wraps
from flask import session, redirect, url_for, flash, abort, request, g
from . import db

ROLES = ("STUDENT", "TPO", "FACULTY", "RECRUITER", "ALUMNI", "ADMIN")
STAFF = ("TPO", "ADMIN", "FACULTY")
ROLE_LABELS = {
    "STUDENT": "Student", "TPO": "Placement Officer (TPO)", "FACULTY": "Faculty / Trainer",
    "RECRUITER": "Recruiter", "ALUMNI": "Alumni / Mentor", "ADMIN": "System Administrator",
}


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = db.one("SELECT id, email, full_name, user_role, company_id, designation, phone, is_approved "
                        "FROM sp_users WHERE id = :id AND is_active = 1", {"id": uid})
        if not g.user:
            session.clear()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not g.get("user"):
            return redirect(url_for("auth.login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not g.get("user"):
                return redirect(url_for("auth.login", next=request.path))
            if g.user["user_role"] not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def is_staff():
    return g.get("user") and g.user["user_role"] in STAFF


def has_role(*roles):
    return bool(g.get("user")) and g.user["user_role"] in roles


def can_manage_drive(drive):
    """TPO/Admin manage every drive; a recruiter only drives of their own company."""
    if not g.get("user") or not drive:
        return False
    r = g.user["user_role"]
    if r in ("TPO", "ADMIN"):
        return True
    return r == "RECRUITER" and g.user["company_id"] and drive["company_id"] == g.user["company_id"]


def require_drive_access(drive):
    if not can_manage_drive(drive):
        flash("You are not authorised to manage this drive.", "danger")
        abort(403)
