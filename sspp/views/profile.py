from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from .. import db
from ..security import roles_required
from ..services import readiness as rd

bp = Blueprint("profile", __name__)

ITEM_TYPES = {
    "EDUCATION": {"label": "Education", "icon": "mortarboard", "title": "Degree / Course", "org": "Institution", "score": "CGPA / %"},
    "SKILL": {"label": "Technical Skills", "icon": "cpu", "title": "Skill", "level": True},
    "PROJECT": {"label": "Projects", "icon": "kanban", "title": "Project title", "org": "Team / Guide", "tech": "Technologies", "desc": True, "url": True},
    "INTERNSHIP": {"label": "Internships", "icon": "briefcase", "title": "Role", "org": "Company", "desc": True},
    "CERTIFICATION": {"label": "Certifications", "icon": "patch-check", "title": "Certification", "org": "Issuer", "url": True},
    "ACHIEVEMENT": {"label": "Achievements", "icon": "trophy", "title": "Achievement", "desc": True},
    "HACKATHON": {"label": "Hackathons", "icon": "lightning-charge", "title": "Hackathon", "org": "Organiser", "score": "Rank / Result", "desc": True},
    "WORKSHOP": {"label": "Workshops", "icon": "easel", "title": "Workshop", "org": "Conducted by"},
    "ACTIVITY": {"label": "Extracurricular", "icon": "people", "title": "Activity / Role", "org": "Club / Body", "desc": True},
    "LANGUAGE": {"label": "Languages", "icon": "translate", "title": "Language", "level": True},
}
GOALS = ["Software Developer", "Java Developer", "Python Developer", "Full Stack Developer", "Data Analyst",
         "AI/ML Engineer", "Cloud/DevOps Engineer", "Cybersecurity Analyst", "QA / Test Engineer", "Business Analyst"]


def _num(v, cast=float):
    try:
        return cast(v) if v not in (None, "") else None
    except ValueError:
        return None


@bp.route("/profile", methods=["GET", "POST"])
@roles_required("STUDENT")
def view():
    uid = g.user["id"]
    if request.method == "POST":
        f = request.form
        dob = None
        if f.get("dob"):
            try:
                dob = datetime.strptime(f["dob"], "%Y-%m-%d")
            except ValueError:
                pass
        cg = _num(f.get("cgpa"))
        if cg is not None and not 0 <= cg <= 10:
            flash("CGPA must be between 0 and 10.", "danger")
            return redirect(url_for("profile.view"))
        params = {"u": uid, "reg": f.get("reg_no"), "dep": f.get("department"), "b": _num(f.get("batch_year"), int),
                  "gen": f.get("gender"), "dob": dob, "addr": f.get("address"), "cg": cg,
                  "t10": _num(f.get("tenth_pct")), "t12": _num(f.get("twelfth_pct")),
                  "ab": _num(f.get("active_backlogs"), int) or 0, "hb": _num(f.get("history_backlogs"), int) or 0,
                  "goal": f.get("career_goal"), "int": f.get("interests"), "sum": f.get("summary"),
                  "gh": f.get("github"), "li": f.get("linkedin"), "lc": f.get("leetcode"), "hr": f.get("hackerrank"),
                  "pf": f.get("portfolio"), "n": datetime.now()}
        if db.scalar("SELECT COUNT(*) FROM sp_students WHERE user_id=:u", {"u": uid}):
            db.execute("""UPDATE sp_students SET reg_no=:reg, department=:dep, batch_year=:b, gender=:gen, dob=:dob,
                address=:addr, cgpa=:cg, tenth_pct=:t10, twelfth_pct=:t12, active_backlogs=:ab, history_backlogs=:hb,
                career_goal=:goal, interests=:int, summary=:sum, github=:gh, linkedin=:li, leetcode=:lc, hackerrank=:hr,
                portfolio=:pf, updated_at=:n WHERE user_id=:u""", params)
        else:
            db.execute("""INSERT INTO sp_students(user_id,reg_no,department,batch_year,gender,dob,address,cgpa,tenth_pct,
                twelfth_pct,active_backlogs,history_backlogs,career_goal,interests,summary,github,linkedin,leetcode,
                hackerrank,portfolio,updated_at) VALUES(:u,:reg,:dep,:b,:gen,:dob,:addr,:cg,:t10,:t12,:ab,:hb,:goal,:int,
                :sum,:gh,:li,:lc,:hr,:pf,:n)""", params)
        db.execute("UPDATE sp_users SET full_name=:n, phone=:p WHERE id=:u",
                   {"n": f.get("full_name") or g.user["full_name"], "p": f.get("phone"), "u": uid})
        flash("Profile saved.", "success")
        return redirect(url_for("profile.view", tab=request.args.get("tab", "personal")))
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
    items = db.query("SELECT * FROM sp_profile_items WHERE user_id=:u ORDER BY item_type, id", {"u": uid})
    by_type = {}
    for i in items:
        by_type.setdefault(i["item_type"], []).append(i)
    score, missing = rd.profile_completeness(uid, student, {k: len(v) for k, v in by_type.items()})
    return render_template("student/profile.html", s=student, by_type=by_type, types=ITEM_TYPES, goals=GOALS,
                           score=score, missing=missing, tab=request.args.get("tab", "personal"))


@bp.route("/profile/items", methods=["POST"])
@roles_required("STUDENT")
def add_item():
    f = request.form
    t = f.get("item_type")
    if t not in ITEM_TYPES or not f.get("title", "").strip():
        flash("Please enter a title.", "danger")
    else:
        db.execute("""INSERT INTO sp_profile_items(user_id,item_type,title,org,description,start_label,end_label,url,
                      item_level,score,tech) VALUES(:u,:t,:ti,:o,:d,:s,:e,:url,:l,:sc,:te)""",
                   {"u": g.user["id"], "t": t, "ti": f["title"].strip()[:200], "o": f.get("org"),
                    "d": (f.get("description") or "")[:4000], "s": f.get("start_label"), "e": f.get("end_label"),
                    "url": f.get("url"), "l": f.get("item_level"), "sc": f.get("score"), "te": f.get("tech")})
        flash(f"{ITEM_TYPES[t]['label']} entry added.", "success")
    return redirect(url_for("profile.view", tab="items") + f"#sec-{t}")


@bp.route("/profile/items/<int:iid>/update", methods=["POST"])
@roles_required("STUDENT")
def update_item(iid):
    f = request.form
    db.execute("""UPDATE sp_profile_items SET title=:ti, org=:o, description=:d, start_label=:s, end_label=:e, url=:url,
                  item_level=:l, score=:sc, tech=:te WHERE id=:i AND user_id=:u""",
               {"i": iid, "u": g.user["id"], "ti": f.get("title", "")[:200], "o": f.get("org"),
                "d": (f.get("description") or "")[:4000], "s": f.get("start_label"), "e": f.get("end_label"),
                "url": f.get("url"), "l": f.get("item_level"), "sc": f.get("score"), "te": f.get("tech")})
    flash("Entry updated.", "success")
    return redirect(url_for("profile.view", tab="items"))


@bp.route("/profile/items/<int:iid>/delete", methods=["POST"])
@roles_required("STUDENT")
def delete_item(iid):
    db.execute("DELETE FROM sp_profile_items WHERE id=:i AND user_id=:u", {"i": iid, "u": g.user["id"]})
    flash("Entry removed.", "info")
    return redirect(url_for("profile.view", tab="items"))
