from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from .. import db
from ..services.notify import notify

bp = Blueprint("auth", __name__)
SELF_REGISTER = {"STUDENT": "Student", "RECRUITER": "Recruiter", "ALUMNI": "Alumni / Mentor"}


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = db.one("SELECT * FROM sp_users WHERE LOWER(email)=:e", {"e": email})
        if not u or not check_password_hash(u["password_hash"], request.form.get("password", "")):
            flash("Invalid email or password.", "danger")
        elif not u["is_active"]:
            flash("Your account is disabled. Contact the placement cell.", "warning")
        elif not u["is_approved"]:
            flash("Your account is awaiting approval by the Training & Placement Officer.", "warning")
        else:
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            db.execute("UPDATE sp_users SET last_login=:n WHERE id=:i", {"n": datetime.now(), "i": u["id"]})
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        role = f.get("role", "STUDENT")
        if role not in SELF_REGISTER:
            role = "STUDENT"
        email = f.get("email", "").strip().lower()
        if len(f.get("password", "")) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif f.get("password") != f.get("confirm"):
            flash("Passwords do not match.", "danger")
        elif db.scalar("SELECT COUNT(*) FROM sp_users WHERE LOWER(email)=:e", {"e": email}):
            flash("An account with this email already exists.", "danger")
        else:
            approved = 1 if role == "STUDENT" else 0
            uid = db.insert("INSERT INTO sp_users(email,password_hash,full_name,user_role,phone,designation,is_approved) "
                            "VALUES(:e,:p,:n,:r,:ph,:d,:a) RETURNING id INTO :new_id",
                            {"e": email, "p": generate_password_hash(f["password"]), "n": f.get("full_name", "").strip(),
                             "r": role, "ph": f.get("phone"), "d": f.get("designation"), "a": approved})
            if role == "STUDENT":
                db.execute("INSERT INTO sp_students(user_id, reg_no, department, batch_year) VALUES(:u,:r,:d,:b)",
                           {"u": uid, "r": f.get("reg_no"), "d": f.get("department"),
                            "b": int(f["batch_year"]) if f.get("batch_year", "").isdigit() else None})
            if role == "RECRUITER" and f.get("company"):
                cid = db.insert("INSERT INTO sp_companies(name, website, status, created_by) VALUES(:n,:w,'PENDING',:u) "
                                "RETURNING id INTO :new_id", {"n": f["company"], "w": f.get("website"), "u": uid})
                db.execute("UPDATE sp_users SET company_id=:c WHERE id=:u", {"c": cid, "u": uid})
            db.execute("INSERT INTO sp_notif_prefs(user_id) VALUES(:u)", {"u": uid})
            if approved:
                notify(uid, "Welcome to SSPP!", "Complete your profile to unlock eligible drives and resume building.",
                       link="/profile", category="GENERAL", email=False)
                flash("Registration successful. Please sign in.", "success")
            else:
                tpos = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role IN ('TPO','ADMIN')")]
                notify(tpos, "New registration awaiting approval",
                       f"{f.get('full_name')} registered as {SELF_REGISTER[role]}.", link="/admin/approvals",
                       category="APPROVAL", email=False)
                flash("Registration submitted. The Placement Officer will approve your account shortly.", "info")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html", roles=SELF_REGISTER)


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if not g.get("user"):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        u = db.one("SELECT password_hash FROM sp_users WHERE id=:i", {"i": g.user["id"]})
        if not check_password_hash(u["password_hash"], request.form.get("current", "")):
            flash("Current password is incorrect.", "danger")
        elif len(request.form.get("new", "")) < 8:
            flash("New password must be at least 8 characters.", "danger")
        else:
            db.execute("UPDATE sp_users SET password_hash=:p WHERE id=:i",
                       {"p": generate_password_hash(request.form["new"]), "i": g.user["id"]})
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/password.html")
