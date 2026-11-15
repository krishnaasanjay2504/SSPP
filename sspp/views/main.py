from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, g, request, flash, jsonify
from .. import db
from ..security import login_required
from ..services import readiness as rd, eligibility

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if g.get("user"):
        return redirect(url_for("main.dashboard"))
    stats = {
        "students": db.scalar("SELECT COUNT(*) FROM sp_students"),
        "companies": db.scalar("SELECT COUNT(*) FROM sp_companies WHERE status='APPROVED'"),
        "offers": db.scalar("SELECT COUNT(*) FROM sp_offers"),
        "highest": db.scalar("SELECT MAX(ctc_lpa) FROM sp_offers"),
    }
    return render_template("landing.html", stats=stats)


@bp.route("/dashboard")
@login_required
def dashboard():
    role = g.user["user_role"]
    if role == "STUDENT":
        return student_dashboard()
    if role == "RECRUITER":
        return redirect(url_for("recruiter.home"))
    if role == "ALUMNI":
        return redirect(url_for("community.mentoring"))
    return redirect(url_for("analytics.tpo_dashboard"))


def student_dashboard():
    uid = g.user["id"]
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
    r = rd.readiness(uid)
    skills = eligibility.student_skills(uid)
    drives = db.query("SELECT d.*, c.name company FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id "
                      "WHERE d.status='OPEN' AND (d.reg_deadline IS NULL OR d.reg_deadline >= :n) ORDER BY d.reg_deadline",
                      {"n": datetime.now()})
    applied = {a["drive_id"] for a in db.query("SELECT drive_id FROM sp_applications WHERE student_id=:u", {"u": uid})}
    eligible = [d for d in drives if eligibility.check(student, d, skills)["ok"]]
    week = datetime.now() + timedelta(days=7)
    deadlines = [d for d in eligible if d["reg_deadline"] and d["reg_deadline"] <= week and d["id"] not in applied]
    apps = db.query("SELECT status, COUNT(*) n FROM sp_applications WHERE student_id=:u GROUP BY status", {"u": uid})
    counts = {a["status"]: a["n"] for a in apps}
    total_apps = sum(counts.values())
    shortlisted = sum(counts.get(s, 0) for s in ("SHORTLISTED", "ASSESSMENT", "TECH_INTERVIEW", "HR_INTERVIEW", "SELECTED"))
    interviewed = sum(counts.get(s, 0) for s in ("TECH_INTERVIEW", "HR_INTERVIEW", "SELECTED"))
    offers = db.scalar("SELECT COUNT(*) FROM sp_offers WHERE student_id=:u", {"u": uid})
    upcoming = db.query("""SELECT e.* FROM sp_events e WHERE e.start_at >= :n AND e.start_at <= :w
                           AND (e.drive_id IS NULL OR e.drive_id IN (SELECT drive_id FROM sp_applications WHERE student_id=:u))
                           ORDER BY e.start_at FETCH FIRST 6 ROWS ONLY""",
                        {"n": datetime.now(), "w": datetime.now() + timedelta(days=30), "u": uid})
    interviews = sum(1 for e in upcoming if e["event_type"] in ("TECH", "HR", "MOCK"))
    assessments = sum(1 for e in upcoming if e["event_type"] in ("APTITUDE", "CODING"))
    offcampus = db.scalar("SELECT COUNT(*) FROM sp_offcampus WHERE status='APPROVED' AND (deadline IS NULL OR deadline >= TRUNC(SYSDATE))")
    recent_apps = db.query("SELECT a.*, d.job_role, c.name company FROM sp_applications a JOIN sp_drives d ON d.id=a.drive_id "
                           "JOIN sp_companies c ON c.id=d.company_id WHERE a.student_id=:u ORDER BY a.updated_at DESC "
                           "FETCH FIRST 5 ROWS ONLY", {"u": uid})
    announcements = db.query("SELECT * FROM sp_announcements ORDER BY created_at DESC FETCH FIRST 4 ROWS ONLY")
    topics = rd.topic_scores(uid)
    return render_template("student/dashboard.html", student=student, r=r, eligible=eligible[:5],
                           n_eligible=len(eligible), deadlines=deadlines, counts=counts, total_apps=total_apps,
                           shortlisted=shortlisted, interviewed=interviewed, offers=offers, upcoming=upcoming,
                           interviews=interviews, assessments=assessments, offcampus=offcampus,
                           recent_apps=recent_apps, announcements=announcements, topics=topics, applied=applied)


@bp.route("/notifications")
@login_required
def notifications():
    rows = db.query("SELECT * FROM sp_notifications WHERE user_id=:u ORDER BY created_at DESC FETCH FIRST 100 ROWS ONLY",
                    {"u": g.user["id"]})
    prefs = db.one("SELECT * FROM sp_notif_prefs WHERE user_id=:u", {"u": g.user["id"]}) or \
        {"offsets": "10080,2880,1440,60", "email_on": 1, "sms_on": 0, "inapp_on": 1}
    return render_template("notifications.html", rows=rows, prefs=prefs,
                           offsets=set((prefs.get("offsets") or "").split(",")))


@bp.route("/notifications/read", methods=["POST"])
@login_required
def mark_read():
    nid = request.form.get("id")
    if nid:
        db.execute("UPDATE sp_notifications SET is_read=1 WHERE id=:i AND user_id=:u", {"i": nid, "u": g.user["id"]})
    else:
        db.execute("UPDATE sp_notifications SET is_read=1 WHERE user_id=:u", {"u": g.user["id"]})
    return redirect(request.form.get("next") or url_for("main.notifications"))


@bp.route("/notifications/open/<int:nid>")
@login_required
def open_notification(nid):
    n = db.one("SELECT * FROM sp_notifications WHERE id=:i AND user_id=:u", {"i": nid, "u": g.user["id"]})
    if not n:
        return redirect(url_for("main.notifications"))
    db.execute("UPDATE sp_notifications SET is_read=1 WHERE id=:i", {"i": nid})
    return redirect(n["link"] or url_for("main.notifications"))


@bp.route("/notifications/prefs", methods=["POST"])
@login_required
def save_prefs():
    offs = ",".join(request.form.getlist("offsets")) or "1440"
    p = {"u": g.user["id"], "o": offs, "e": 1 if request.form.get("email_on") else 0,
         "s": 1 if request.form.get("sms_on") else 0}
    if db.scalar("SELECT COUNT(*) FROM sp_notif_prefs WHERE user_id=:u", {"u": g.user["id"]}):
        db.execute("UPDATE sp_notif_prefs SET offsets=:o, email_on=:e, sms_on=:s WHERE user_id=:u", p)
    else:
        db.execute("INSERT INTO sp_notif_prefs(user_id,offsets,email_on,sms_on) VALUES(:u,:o,:e,:s)", p)
    flash("Notification preferences saved.", "success")
    return redirect(url_for("main.notifications"))


@bp.route("/api/notifications")
@login_required
def api_notifications():
    rows = db.query("SELECT id, title, message, link, created_at, is_read FROM sp_notifications WHERE user_id=:u "
                    "ORDER BY created_at DESC FETCH FIRST 8 ROWS ONLY", {"u": g.user["id"]})
    for r in rows:
        r["created_at"] = r["created_at"].strftime("%d %b, %I:%M %p")
    return jsonify(rows)


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    res = {}
    if q:
        like = {"q": f"%{q.lower()}%"}
        res["Campus Drives"] = [(f"{r['company']} – {r['job_role']}", url_for("drives.detail", did=r["id"])) for r in db.query(
            "SELECT d.id, d.job_role, c.name company FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id "
            "WHERE d.status IN ('OPEN','CLOSED','COMPLETED') AND (LOWER(c.name) LIKE :q OR LOWER(d.job_role) LIKE :q) "
            "FETCH FIRST 10 ROWS ONLY", like)]
        res["Off-Campus Jobs"] = [(f"{r['company']} – {r['job_role']}", url_for("jobs.detail", jid=r["id"])) for r in db.query(
            "SELECT id, company, job_role FROM sp_offcampus WHERE status='APPROVED' AND (LOWER(company) LIKE :q OR "
            "LOWER(job_role) LIKE :q OR LOWER(skills) LIKE :q) FETCH FIRST 10 ROWS ONLY", like)]
        res["Discussions"] = [(r["title"], url_for("forum.thread", qid=r["id"])) for r in db.query(
            "SELECT id, title FROM sp_forum_questions WHERE LOWER(title) LIKE :q OR LOWER(tags) LIKE :q "
            "FETCH FIRST 10 ROWS ONLY", like)]
        res["Learning Paths"] = [(r["name"], url_for("learn.path", pid=r["id"])) for r in db.query(
            "SELECT id, name FROM sp_paths WHERE LOWER(name) LIKE :q OR LOWER(description) LIKE :q", like)]
        res["Quizzes"] = [(r["title"], url_for("learn.quiz_start", qid=r["id"])) for r in db.query(
            "SELECT id, title FROM sp_quizzes WHERE is_active=1 AND (LOWER(title) LIKE :q OR LOWER(topic) LIKE :q)", like)]
        res["Interview Questions"] = [(r["question_text"][:120], url_for("learn.interview_bank", q=q)) for r in db.query(
            "SELECT question_text FROM sp_interview_questions WHERE LOWER(question_text) LIKE :q FETCH FIRST 8 ROWS ONLY", like)]
    return render_template("search.html", q=q, res=res)
