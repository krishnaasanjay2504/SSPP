import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, send_file
from .. import db
from ..security import roles_required, login_required, STAFF
from ..services import readiness as rd, ai

bp = Blueprint("resume", __name__)

SECTIONS = [("SUMMARY", "Objective / Summary"), ("EDUCATION", "Education"), ("SKILL", "Technical Skills"),
            ("PROJECT", "Projects"), ("INTERNSHIP", "Internships"), ("CERTIFICATION", "Certifications"),
            ("ACHIEVEMENT", "Achievements"), ("HACKATHON", "Hackathons"), ("WORKSHOP", "Workshops"),
            ("ACTIVITY", "Extracurricular Activities"), ("LANGUAGE", "Languages"), ("LINKS", "Profiles & Links")]
TEMPLATES = {"modern": ("Modern", "#0d6efd"), "classic": ("Classic", "#212529"),
             "minimal": ("Minimal", "#495057"), "elegant": ("Elegant", "#6f42c1"), "teal": ("Fresh Teal", "#0f766e")}
PRESETS = {
    "Software Developer Resume": ("Software Developer", ["java", "python", "data structures", "sql", "git"]),
    "Data Analyst Resume": ("Data Analyst", ["sql", "excel", "power bi", "python", "statistics"]),
    "Cloud/DevOps Resume": ("Cloud/DevOps Engineer", ["aws", "docker", "linux", "ci/cd", "kubernetes"]),
    "AI/ML Resume": ("AI/ML Engineer", ["python", "machine learning", "deep learning", "pandas", "tensorflow"]),
}


def load_items(user_id):
    by = {}
    for i in db.query("SELECT * FROM sp_profile_items WHERE user_id=:u ORDER BY id", {"u": user_id}):
        by.setdefault(i["item_type"], []).append(i)
    return by


def _own(rid):
    r = db.one("SELECT * FROM sp_resumes WHERE id=:i", {"i": rid})
    if not r:
        abort(404)
    if r["user_id"] != g.user["id"] and g.user["user_role"] not in STAFF + ("RECRUITER",):
        abort(403)
    return r


def build_context(r):
    uid = r["user_id"]
    student = db.one("SELECT s.*, u.full_name, u.email, u.phone FROM sp_students s JOIN sp_users u ON u.id=s.user_id "
                     "WHERE s.user_id=:u", {"u": uid}) or {}
    if not student:
        student = db.one("SELECT full_name, email, phone FROM sp_users WHERE id=:u", {"u": uid})
    items = load_items(uid)
    excluded = {int(x) for x in (r.get("excluded_items") or "").split(",") if x.strip().isdigit()}
    sections = [s for s in (r.get("sections") or ",".join(k for k, _ in SECTIONS)).split(",") if s]
    filtered = {k: [i for i in v if i["id"] not in excluded] for k, v in items.items()}
    color = TEMPLATES.get(r.get("template_code"), TEMPLATES["modern"])[1]
    return {"r": r, "s": student, "items": filtered, "sections": sections, "labels": dict(SECTIONS), "color": color}


@bp.route("/resumes", methods=["GET", "POST"])
@roles_required("STUDENT")
def index():
    uid = g.user["id"]
    if request.method == "POST":
        title = request.form.get("title") or "My Resume"
        preset = PRESETS.get(title)
        student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
        target = preset[0] if preset else (request.form.get("target_role") or student.get("career_goal"))
        obj = (f"Motivated {student.get('department') or ''} graduate seeking a {target or 'software'} role where I can "
               f"apply strong fundamentals, hands-on project experience and a passion for learning to deliver "
               f"high-quality solutions.").replace("  ", " ")
        rid = db.insert("INSERT INTO sp_resumes(user_id,title,template_code,target_role,objective,sections) "
                        "VALUES(:u,:t,:tc,:tr,:o,:s) RETURNING id INTO :new_id",
                        {"u": uid, "t": title[:120], "tc": request.form.get("template_code", "modern"), "tr": target,
                         "o": student.get("summary") or obj, "s": ",".join(k for k, _ in SECTIONS)})
        flash("Resume created from your profile. Customise it below.", "success")
        return redirect(url_for("resume.edit", rid=rid))
    resumes = db.query("SELECT * FROM sp_resumes WHERE user_id=:u ORDER BY updated_at DESC", {"u": uid})
    items = load_items(uid)
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
    for r in resumes:
        r["score"], _ = rd.resume_completeness(r, items, student)
    return render_template("resume/index.html", resumes=resumes, presets=PRESETS, templates=TEMPLATES)


@bp.route("/resumes/<int:rid>/edit", methods=["GET", "POST"])
@roles_required("STUDENT")
def edit(rid):
    r = _own(rid)
    if r["user_id"] != g.user["id"]:
        abort(403)
    if request.method == "POST":
        f = request.form
        all_ids = {i["id"] for v in load_items(g.user["id"]).values() for i in v}
        keep = {int(x) for x in f.getlist("include_item")}
        excluded = ",".join(str(i) for i in sorted(all_ids - keep))
        db.execute("UPDATE sp_resumes SET title=:t, template_code=:tc, target_role=:tr, objective=:o, sections=:s, "
                   "excluded_items=:x, updated_at=:n WHERE id=:i",
                   {"t": f.get("title")[:120], "tc": f.get("template_code"), "tr": f.get("target_role"),
                    "o": (f.get("objective") or "")[:2000], "s": ",".join(f.getlist("sections")),
                    "x": excluded, "n": datetime.now(), "i": rid})
        flash("Resume saved.", "success")
        return redirect(url_for("resume.edit", rid=rid))
    items = load_items(g.user["id"])
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": g.user["id"]}) or {}
    score, tips = rd.resume_completeness(r, items, student)
    excluded = {int(x) for x in (r.get("excluded_items") or "").split(",") if x.strip().isdigit()}
    # recommendations: skills expected for the target role that are not on the profile
    role = r.get("target_role") or student.get("career_goal") or "Software Developer"
    have = {i["title"].lower() for i in items.get("SKILL", [])}
    gap = ai.career_gap(role if role in ai.CAREER_PATHS else "Software Developer", have)
    project_ideas = PROJECT_IDEAS.get(gap["goal"], PROJECT_IDEAS["Software Developer"])
    return render_template("resume/edit.html", r=r, items=items, sections=SECTIONS, templates=TEMPLATES,
                           active=set((r.get("sections") or "").split(",")), excluded=excluded, score=score,
                           tips=tips, gap=gap, project_ideas=project_ideas)


PROJECT_IDEAS = {
    "Software Developer": ["Library management system with REST API + SQL", "Real-time chat app with WebSockets",
                           "URL shortener with analytics dashboard"],
    "Java Developer": ["Spring Boot e-commerce backend with JWT auth", "Microservice-based ticket booking system",
                       "Bank account simulator with JUnit tests"],
    "Python Developer": ["Flask/Django job portal", "Automated report generator with pandas", "CLI expense tracker with SQLite"],
    "Full Stack Developer": ["MERN/React task manager with auth", "Portfolio CMS with REST API", "Food-ordering web app"],
    "Data Analyst": ["Sales dashboard in Power BI", "SQL analysis of an open dataset", "Customer churn EDA with pandas"],
    "AI/ML Engineer": ["Resume-screening NLP classifier", "Image classifier with CNN + Flask demo", "RAG chatbot over college documents"],
    "Cloud/DevOps Engineer": ["CI/CD pipeline with GitHub Actions + Docker", "Terraform-provisioned 3-tier app on AWS",
                              "Kubernetes deployment with monitoring"],
    "Cybersecurity Analyst": ["Network vulnerability scanner", "Phishing URL detector", "Secure login system with OWASP checks"],
    "QA / Test Engineer": ["Selenium automation suite for a web app", "API test framework with pytest", "Performance test with JMeter"],
    "Business Analyst": ["Requirement & process model for a campus app", "KPI dashboard in Excel/Power BI", "Market analysis report"],
}


@bp.route("/resumes/<int:rid>/preview")
@login_required
def preview(rid):
    r = _own(rid)
    return render_template("resume/preview.html", **build_context(r), templates=TEMPLATES)


@bp.route("/resumes/<int:rid>/pdf")
@login_required
def pdf(rid):
    from xhtml2pdf import pisa
    r = _own(rid)
    html = render_template("resume/pdf.html", **build_context(r))
    out = io.BytesIO()
    status = pisa.CreatePDF(src=html, dest=out, encoding="utf-8")
    if status.err:
        flash("PDF generation failed. Try the browser Print → Save as PDF option from the preview.", "danger")
        return redirect(url_for("resume.preview", rid=rid))
    out.seek(0)
    name = f"{(build_context(r)['s'].get('full_name') or 'resume').replace(' ', '_')}_{r['title'].replace(' ', '_')}.pdf"
    return send_file(out, mimetype="application/pdf", as_attachment=request.args.get("download") == "1",
                     download_name=name)


@bp.route("/resumes/<int:rid>/duplicate", methods=["POST"])
@roles_required("STUDENT")
def duplicate(rid):
    r = _own(rid)
    new = db.insert("INSERT INTO sp_resumes(user_id,title,template_code,target_role,objective,sections,excluded_items) "
                    "VALUES(:u,:t,:tc,:tr,:o,:s,:x) RETURNING id INTO :new_id",
                    {"u": g.user["id"], "t": (r["title"] + " (copy)")[:120], "tc": r["template_code"],
                     "tr": r["target_role"], "o": r["objective"], "s": r["sections"], "x": r["excluded_items"]})
    flash("Resume duplicated.", "success")
    return redirect(url_for("resume.edit", rid=new))


@bp.route("/resumes/<int:rid>/delete", methods=["POST"])
@roles_required("STUDENT")
def delete(rid):
    db.execute("DELETE FROM sp_resumes WHERE id=:i AND user_id=:u", {"i": rid, "u": g.user["id"]})
    flash("Resume deleted.", "info")
    return redirect(url_for("resume.index"))
