from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort
from .. import db
from ..security import login_required, roles_required
from ..services.notify import notify

bp = Blueprint("jobs", __name__)
CATEGORIES = ["Software Development", "Data Science", "AI/ML", "Cybersecurity", "Cloud", "DevOps", "QA",
              "Business Analyst"]
JOB_TYPES = {"INTERNSHIP": "Internship", "FRESHER": "Fresher", "FULL_TIME": "Full-Time"}
WORK_MODES = {"ONSITE": "On-site", "REMOTE": "Work from Home", "HYBRID": "Hybrid"}
CITIES = ["Chennai", "Bengaluru", "Hyderabad", "Pune", "Mumbai", "Noida", "Coimbatore", "Remote"]


@bp.route("/jobs")
@login_required
def index():
    a = request.args
    sql = ["SELECT j.* FROM sp_offcampus j WHERE j.status='APPROVED'"]
    p = {}
    if not a.get("expired"):
        sql.append("AND (j.deadline IS NULL OR j.deadline >= TRUNC(SYSDATE))")
    cats = a.getlist("category")
    if cats:
        sql.append("AND j.category IN (" + ",".join(f":c{i}" for i in range(len(cats))) + ")")
        p.update({f"c{i}": c for i, c in enumerate(cats)})
    types = a.getlist("type")
    if types:
        sql.append("AND j.job_type IN (" + ",".join(f":t{i}" for i in range(len(types))) + ")")
        p.update({f"t{i}": t for i, t in enumerate(types)})
    modes = a.getlist("mode")
    if modes:
        sql.append("AND j.work_mode IN (" + ",".join(f":m{i}" for i in range(len(modes))) + ")")
        p.update({f"m{i}": m for i, m in enumerate(modes)})
    cities = a.getlist("city")
    if cities:
        sql.append("AND (" + " OR ".join(f"LOWER(j.location) LIKE :l{i}" for i in range(len(cities))) + ")")
        p.update({f"l{i}": f"%{c.lower()}%" for i, c in enumerate(cities)})
    if a.get("q"):
        sql.append("AND (LOWER(j.company) LIKE :q OR LOWER(j.job_role) LIKE :q OR LOWER(j.skills) LIKE :q)")
        p["q"] = f"%{a['q'].lower()}%"
    if a.get("exp"):
        sql.append("AND LOWER(j.experience) LIKE :ex")
        p["ex"] = f"%{a['exp'].lower()}%"
    if a.get("verified"):
        sql.append("AND j.verified=1")
    if a.get("saved"):
        sql.append("AND j.id IN (SELECT job_id FROM sp_saved_jobs WHERE user_id=:u)")
        p["u"] = g.user["id"]
    sql.append("ORDER BY j.deadline NULLS LAST, j.created_at DESC")
    jobs = db.query(" ".join(sql), p)
    saved = {r["job_id"] for r in db.query("SELECT job_id FROM sp_saved_jobs WHERE user_id=:u", {"u": g.user["id"]})}
    return render_template("jobs/index.html", jobs=jobs, saved=saved, categories=CATEGORIES, job_types=JOB_TYPES,
                           work_modes=WORK_MODES, cities=CITIES, a=a)


@bp.route("/jobs/<int:jid>")
@login_required
def detail(jid):
    j = db.one("SELECT * FROM sp_offcampus WHERE id=:i", {"i": jid})
    if not j or (j["status"] != "APPROVED" and g.user["user_role"] not in ("TPO", "ADMIN") and j["posted_by"] != g.user["id"]):
        abort(404)
    saved = db.scalar("SELECT COUNT(*) FROM sp_saved_jobs WHERE user_id=:u AND job_id=:j", {"u": g.user["id"], "j": jid})
    return render_template("jobs/detail.html", j=j, saved=saved, job_types=JOB_TYPES, work_modes=WORK_MODES)


@bp.route("/jobs/<int:jid>/save", methods=["POST"])
@login_required
def toggle_save(jid):
    p = {"u": g.user["id"], "j": jid}
    if db.scalar("SELECT COUNT(*) FROM sp_saved_jobs WHERE user_id=:u AND job_id=:j", p):
        db.execute("DELETE FROM sp_saved_jobs WHERE user_id=:u AND job_id=:j", p)
    else:
        db.execute("INSERT INTO sp_saved_jobs(user_id, job_id) VALUES(:u,:j)", p)
    return redirect(request.referrer or url_for("jobs.index"))


@bp.route("/jobs/new", methods=["GET", "POST"])
@roles_required("TPO", "ADMIN", "ALUMNI", "RECRUITER", "FACULTY")
def new():
    if request.method == "POST":
        f = request.form
        auto = g.user["user_role"] in ("TPO", "ADMIN")
        db.execute("""INSERT INTO sp_offcampus(company,job_role,description,skills,category,job_type,experience,batch,ctc,
            location,work_mode,deadline,apply_url,source,verified,status,posted_by) VALUES(:c,:r,:d,:sk,:cat,:jt,:ex,:b,
            :ctc,:l,:wm,:dl,:url,:src,:v,:st,:u)""",
                   {"c": f["company"], "r": f["job_role"], "d": f.get("description"), "sk": f.get("skills"),
                    "cat": f.get("category"), "jt": f.get("job_type"), "ex": f.get("experience"), "b": f.get("batch"),
                    "ctc": f.get("ctc"), "l": f.get("location"), "wm": f.get("work_mode"),
                    "dl": datetime.strptime(f["deadline"], "%Y-%m-%d") if f.get("deadline") else None,
                    "url": f.get("apply_url"), "src": f.get("source") or g.user["full_name"],
                    "v": 1 if auto and f.get("verified") else 0, "st": "APPROVED" if auto else "PENDING", "u": g.user["id"]})
        if not auto:
            tpos = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role IN ('TPO','ADMIN')")]
            notify(tpos, "Off-campus job awaiting verification", f"{f['company']} – {f['job_role']} posted by "
                   f"{g.user['full_name']}.", link="/admin/approvals", category="APPROVAL", email=False)
        flash("Opportunity published." if auto else "Opportunity submitted for TPO verification.", "success")
        return redirect(url_for("jobs.index"))
    return render_template("jobs/form.html", categories=CATEGORIES, job_types=JOB_TYPES, work_modes=WORK_MODES)
