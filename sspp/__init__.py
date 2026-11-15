"""Smart Student Placement Portal - Flask application factory."""
import json
import os
from datetime import datetime, date

from flask import Flask, g, render_template, request
from markupsafe import Markup, escape

from . import db, security
from .services import ai, notify

STATUS_COLORS = {
    "APPLIED": "secondary", "SHORTLISTED": "info", "ASSESSMENT": "primary", "TECH_INTERVIEW": "warning",
    "HR_INTERVIEW": "warning", "SELECTED": "success", "REJECTED": "danger", "WITHDRAWN": "dark",
    "OPEN": "success", "CLOSED": "secondary", "COMPLETED": "dark", "PENDING_APPROVAL": "warning",
    "PENDING": "warning", "APPROVED": "success", "ACCEPTED": "success", "DECLINED": "danger",
    "RESOLVED": "success", "IN_PROGRESS": "info", "ANSWERED": "success", "PLACED": "success", "NOT_PLACED": "secondary",
    "EASY": "success", "MEDIUM": "warning", "HARD": "danger",
}
APP_STAGES = ["APPLIED", "SHORTLISTED", "ASSESSMENT", "TECH_INTERVIEW", "HR_INTERVIEW", "SELECTED"]
STAGE_LABELS = {"APPLIED": "Applied", "SHORTLISTED": "Shortlisted", "ASSESSMENT": "Assessment",
                "TECH_INTERVIEW": "Technical Interview", "HR_INTERVIEW": "HR Interview", "SELECTED": "Selected",
                "REJECTED": "Rejected", "WITHDRAWN": "Withdrawn"}
DEPARTMENTS = ["CSE", "IT", "ECE", "EEE", "MECH", "CIVIL", "AIDS"]
EVENT_TYPES = {
    "DEADLINE": ("Registration Deadline", "#dc3545"), "DRIVE": ("Placement Drive", "#6f42c1"),
    "PPT": ("Pre-Placement Talk", "#0dcaf0"), "APTITUDE": ("Aptitude Test", "#fd7e14"),
    "CODING": ("Coding Assessment", "#20c997"), "TECH": ("Technical Interview", "#0d6efd"),
    "HR": ("HR Interview", "#d63384"), "TRAINING": ("Training Session", "#198754"),
    "MOCK": ("Mock Interview", "#6610f2"), "RESULT": ("Results", "#ffc107"),
    "OFFER_DEADLINE": ("Offer Acceptance Deadline", "#842029"),
}


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object("config.Config")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_pool(app.config)
    ai.configure(app)
    notify.configure(app)

    from .views import (auth, main, profile, resume, drives, calendar, jobs, learn, forum,
                        community, admin, recruiter, analytics, assistant)
    for bp in (auth.bp, main.bp, profile.bp, resume.bp, drives.bp, calendar.bp, jobs.bp, learn.bp, forum.bp,
               community.bp, admin.bp, recruiter.bp, analytics.bp, assistant.bp):
        app.register_blueprint(bp)

    app.before_request(security.load_user)

    @app.context_processor
    def inject():
        unread = 0
        if g.get("user"):
            unread = db.scalar("SELECT COUNT(*) FROM sp_notifications WHERE user_id=:u AND is_read=0",
                               {"u": g.user["id"]})
        return {"unread_count": unread, "ROLE_LABELS": security.ROLE_LABELS, "STATUS_COLORS": STATUS_COLORS,
                "APP_STAGES": APP_STAGES, "STAGE_LABELS": STAGE_LABELS, "DEPARTMENTS": DEPARTMENTS,
                "EVENT_TYPES": EVENT_TYPES, "has_role": security.has_role, "now": datetime.now(),
                "APP_NAME": app.config["APP_NAME"], "INSTITUTION": app.config["INSTITUTION"],
                "ai_llm": ai.llm_available}

    @app.template_filter("dt")
    def fmt_dt(v, f="%d %b %Y, %I:%M %p"):
        return v.strftime(f) if isinstance(v, (datetime, date)) else (v or "")

    @app.template_filter("d")
    def fmt_d(v):
        return v.strftime("%d %b %Y") if isinstance(v, (datetime, date)) else (v or "")

    @app.template_filter("ago")
    def ago(v):
        if not isinstance(v, datetime):
            return ""
        s = (datetime.now() - v).total_seconds()
        for unit, sec in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
            if s >= sec:
                return f"{int(s // sec)}{unit} ago"
        return "just now"

    @app.template_filter("csv")
    def csv_list(v):
        return [x.strip() for x in (v or "").split(",") if x.strip()]

    @app.template_filter("nl2br")
    def nl2br(v):
        return Markup("<br>".join(escape(v or "").split("\n")))

    @app.template_filter("md")
    def md_filter(v):
        return ai.to_html(v)

    @app.template_filter("fromjson")
    def fromjson(v):
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    @app.template_filter("label")
    def label(v):
        return STAGE_LABELS.get(v) or (v or "").replace("_", " ").title()

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, msg="You don't have access to this page."), 403

    @app.errorhandler(404)
    def notfound(e):
        return render_template("error.html", code=404, msg="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, msg="Something went wrong. Please try again."), 500

    # start the reminder scheduler only in the serving process (avoid double start with the reloader)
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        notify.start_scheduler(app)
    return app
