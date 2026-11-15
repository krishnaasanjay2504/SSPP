"""Admin, TPO and Faculty portal: students, approvals, companies, drives, users and learning content."""
import csv
import io
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, Response
from werkzeug.security import generate_password_hash
from .. import db
from ..security import roles_required, ROLES
from ..services import readiness as rd
from ..services.notify import notify
from .drives import announce_drive, _sync_drive_events

bp = Blueprint("admin", __name__, url_prefix="/admin")
STAFF3 = ("TPO", "ADMIN", "FACULTY")


# ---------------------------------------------------------------- students
@bp.route("/students")
@roles_required(*STAFF3)
def students():
    a = request.args
    sql = ["""SELECT u.id, u.full_name, u.email, s.reg_no, s.department, s.batch_year, s.cgpa, s.active_backlogs,
              s.placement_status, s.gender, s.career_goal,
              (SELECT COUNT(*) FROM sp_resumes r WHERE r.user_id=u.id) resumes,
              (SELECT COUNT(*) FROM sp_applications x WHERE x.student_id=u.id) apps,
              (SELECT COUNT(*) FROM sp_offers o WHERE o.student_id=u.id) offers,
              (SELECT MAX(o.ctc_lpa) FROM sp_offers o WHERE o.student_id=u.id) best_ctc
              FROM sp_users u LEFT JOIN sp_students s ON s.user_id=u.id WHERE u.user_role='STUDENT'"""]
    p = {}
    for key, col in (("dept", "s.department"), ("batch", "s.batch_year"), ("status", "s.placement_status")):
        if a.get(key):
            sql.append(f"AND {col}=:{key}")
            p[key] = a[key]
    if a.get("min_cgpa"):
        sql.append("AND s.cgpa>=:mc")
        p["mc"] = float(a["min_cgpa"])
    if a.get("backlogs") == "0":
        sql.append("AND NVL(s.active_backlogs,0)=0")
    if a.get("skill"):
        sql.append("AND EXISTS (SELECT 1 FROM sp_profile_items i WHERE i.user_id=u.id AND i.item_type='SKILL' AND LOWER(i.title) LIKE :sk)")
        p["sk"] = f"%{a['skill'].lower()}%"
    if a.get("q"):
        sql.append("AND (LOWER(u.full_name) LIKE :q OR LOWER(s.reg_no) LIKE :q OR LOWER(u.email) LIKE :q)")
        p["q"] = f"%{a['q'].lower()}%"
    rows = db.query(" ".join(sql) + " ORDER BY s.department, u.full_name", p)
    if a.get("export"):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Name", "Email", "Reg No", "Dept", "Batch", "CGPA", "Active Backlogs", "Status", "Applications", "Offers", "Best CTC"])
        for r in rows:
            w.writerow([r["full_name"], r["email"], r["reg_no"], r["department"], r["batch_year"], r["cgpa"],
                        r["active_backlogs"], r["placement_status"], r["apps"], r["offers"], r["best_ctc"]])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=students.csv"})
    batches = [r["batch_year"] for r in db.query("SELECT DISTINCT batch_year FROM sp_students WHERE batch_year IS NOT NULL ORDER BY 1")]
    return render_template("admin/students.html", rows=rows, a=a, batches=batches)


@bp.route("/students/<int:uid>")
@roles_required(*STAFF3)
def student_detail(uid):
    s = db.one("SELECT u.*, s.* FROM sp_users u LEFT JOIN sp_students s ON s.user_id=u.id WHERE u.id=:u AND u.user_role='STUDENT'",
               {"u": uid}) or abort(404)
    r = rd.readiness(uid)
    items = db.query("SELECT * FROM sp_profile_items WHERE user_id=:u ORDER BY item_type", {"u": uid})
    apps = db.query("SELECT a.*, d.job_role, c.name company FROM sp_applications a JOIN sp_drives d ON d.id=a.drive_id "
                    "JOIN sp_companies c ON c.id=d.company_id WHERE a.student_id=:u ORDER BY a.applied_at DESC", {"u": uid})
    offers = db.query("SELECT * FROM sp_offers WHERE student_id=:u", {"u": uid})
    attempts = db.query("SELECT a.*, q.title, q.topic FROM sp_attempts a JOIN sp_quizzes q ON q.id=a.quiz_id WHERE a.user_id=:u "
                        "ORDER BY a.submitted_at DESC FETCH FIRST 10 ROWS ONLY", {"u": uid})
    resumes = db.query("SELECT id, title FROM sp_resumes WHERE user_id=:u", {"u": uid})
    topics = rd.topic_scores(uid)
    return render_template("admin/student_detail.html", s=s, r=r, items=items, apps=apps, offers=offers,
                           attempts=attempts, resumes=resumes, topics=topics)


@bp.route("/students/<int:uid>/status", methods=["POST"])
@roles_required("TPO", "ADMIN")
def set_student_status(uid):
    db.execute("UPDATE sp_students SET placement_status=:s WHERE user_id=:u", {"s": request.form["status"], "u": uid})
    flash("Placement status updated.", "success")
    return redirect(url_for("admin.student_detail", uid=uid))


@bp.route("/students/import", methods=["POST"])
@roles_required("TPO", "ADMIN")
def import_students():
    """CSV: full_name,email,reg_no,department,batch_year,cgpa,tenth_pct,twelfth_pct,active_backlogs,gender"""
    f = request.files.get("file")
    if not f:
        flash("Choose a CSV file.", "warning")
        return redirect(url_for("admin.students"))
    created, skipped = 0, 0
    for row in csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))):
        email = (row.get("email") or "").strip().lower()
        if not email or db.scalar("SELECT COUNT(*) FROM sp_users WHERE LOWER(email)=:e", {"e": email}):
            skipped += 1
            continue
        uid = db.insert("INSERT INTO sp_users(email,password_hash,full_name,user_role) VALUES(:e,:p,:n,'STUDENT') "
                        "RETURNING id INTO :new_id",
                        {"e": email, "p": generate_password_hash(row.get("reg_no") or "Welcome@123"), "n": row.get("full_name")})
        num = lambda k, c=float: c(row[k]) if row.get(k) not in (None, "") else None
        db.execute("INSERT INTO sp_students(user_id,reg_no,department,batch_year,cgpa,tenth_pct,twelfth_pct,active_backlogs,gender) "
                   "VALUES(:u,:r,:d,:b,:c,:t,:tw,:ab,:g)",
                   {"u": uid, "r": row.get("reg_no"), "d": row.get("department"), "b": num("batch_year", int), "c": num("cgpa"),
                    "t": num("tenth_pct"), "tw": num("twelfth_pct"), "ab": num("active_backlogs", int) or 0, "g": row.get("gender")})
        db.execute("INSERT INTO sp_notif_prefs(user_id) VALUES(:u)", {"u": uid})
        created += 1
    flash(f"Imported {created} students ({skipped} skipped). Initial password = register number.", "success")
    return redirect(url_for("admin.students"))


# ---------------------------------------------------------------- approvals workflow
@bp.route("/approvals")
@roles_required("TPO", "ADMIN")
def approvals():
    users = db.query("SELECT u.*, c.name company FROM sp_users u LEFT JOIN sp_companies c ON c.id=u.company_id "
                     "WHERE u.is_approved=0 ORDER BY u.created_at")
    companies = db.query("SELECT c.*, u.full_name FROM sp_companies c LEFT JOIN sp_users u ON u.id=c.created_by "
                         "WHERE c.status='PENDING' ORDER BY c.created_at")
    drives = db.query("SELECT d.*, c.name company, u.full_name FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id "
                      "LEFT JOIN sp_users u ON u.id=d.created_by WHERE d.status='PENDING_APPROVAL' ORDER BY d.created_at")
    jobs = db.query("SELECT j.*, u.full_name FROM sp_offcampus j LEFT JOIN sp_users u ON u.id=j.posted_by "
                    "WHERE j.status='PENDING' ORDER BY j.created_at")
    return render_template("admin/approvals.html", users=users, companies=companies, drives=drives, jobs=jobs)


@bp.route("/approvals/<kind>/<int:oid>/<decision>", methods=["POST"])
@roles_required("TPO", "ADMIN")
def decide(kind, oid, decision):
    ok = decision == "approve"
    if kind == "user":
        if ok:
            db.execute("UPDATE sp_users SET is_approved=1 WHERE id=:i", {"i": oid})
            notify(oid, "Account approved", "Your SSPP account has been approved. You can now sign in.", link="/login")
        else:
            db.execute("UPDATE sp_users SET is_active=0 WHERE id=:i", {"i": oid})
    elif kind == "company":
        c = db.one("SELECT * FROM sp_companies WHERE id=:i", {"i": oid}) or abort(404)
        db.execute("UPDATE sp_companies SET status=:s WHERE id=:i", {"s": "APPROVED" if ok else "REJECTED", "i": oid})
        if c["created_by"]:
            notify(c["created_by"], f"Company {'approved' if ok else 'rejected'}", f"{c['name']} registration was "
                   f"{'approved' if ok else 'not approved'} by the placement cell.", link="/recruiter/")
    elif kind == "drive":
        d = db.one("SELECT * FROM sp_drives WHERE id=:i", {"i": oid}) or abort(404)
        db.execute("UPDATE sp_drives SET status=:s WHERE id=:i", {"s": "OPEN" if ok else "REJECTED", "i": oid})
        if ok:
            db.execute("UPDATE sp_companies SET status='APPROVED' WHERE id=:c AND status='PENDING'", {"c": d["company_id"]})
            _sync_drive_events(oid)
            n = announce_drive(oid)
            flash(f"Drive approved and announced to {n} eligible students.", "success")
        if d["created_by"]:
            notify(d["created_by"], f"Drive {'approved' if ok else 'rejected'}", f"{d['job_role']} drive was "
                   f"{'approved and published' if ok else 'not approved'}.", link=f"/manage/drives/{oid}")
    elif kind == "job":
        db.execute("UPDATE sp_offcampus SET status=:s, verified=:v WHERE id=:i",
                   {"s": "APPROVED" if ok else "REJECTED", "v": 1 if ok else 0, "i": oid})
    else:
        abort(404)
    if not (kind == "drive" and ok):
        flash(f"{kind.title()} {'approved' if ok else 'rejected'}.", "success" if ok else "info")
    return redirect(url_for("admin.approvals"))


# ---------------------------------------------------------------- companies & drives
@bp.route("/companies", methods=["GET", "POST"])
@roles_required("TPO", "ADMIN")
def companies():
    if request.method == "POST":
        f = request.form
        db.execute("INSERT INTO sp_companies(name,industry,website,about,hq_location,status,created_by) "
                   "VALUES(:n,:i,:w,:a,:h,'APPROVED',:u)", {"n": f["name"], "i": f.get("industry"), "w": f.get("website"),
                                                             "a": f.get("about"), "h": f.get("hq_location"), "u": g.user["id"]})
        flash("Company added.", "success")
        return redirect(url_for("admin.companies"))
    rows = db.query("""SELECT c.*, (SELECT COUNT(*) FROM sp_drives d WHERE d.company_id=c.id) drives,
                       (SELECT COUNT(*) FROM sp_offers o WHERE o.company_name=c.name) offers
                       FROM sp_companies c ORDER BY c.name""")
    return render_template("admin/companies.html", rows=rows)


@bp.route("/drives")
@roles_required(*STAFF3)
def drives():
    rows = db.query("""SELECT d.*, c.name company,
                       (SELECT COUNT(*) FROM sp_applications a WHERE a.drive_id=d.id) applicants,
                       (SELECT COUNT(*) FROM sp_applications a WHERE a.drive_id=d.id AND a.status='SELECTED') selected
                       FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id ORDER BY d.created_at DESC""")
    return render_template("admin/drives.html", rows=rows)


# ---------------------------------------------------------------- users (system admin)
@bp.route("/users", methods=["GET", "POST"])
@roles_required("ADMIN", "TPO")
def users():
    if request.method == "POST":
        f = request.form
        role = f["role"]
        if role == "ADMIN" and g.user["user_role"] != "ADMIN":
            abort(403)
        if db.scalar("SELECT COUNT(*) FROM sp_users WHERE LOWER(email)=LOWER(:e)", {"e": f["email"]}):
            flash("Email already exists.", "danger")
        else:
            uid = db.insert("INSERT INTO sp_users(email,password_hash,full_name,user_role,designation,company_id) "
                            "VALUES(:e,:p,:n,:r,:d,:c) RETURNING id INTO :new_id",
                            {"e": f["email"].lower(), "p": generate_password_hash(f["password"]), "n": f["full_name"],
                             "r": role, "d": f.get("designation"), "c": int(f["company_id"]) if f.get("company_id") else None})
            db.execute("INSERT INTO sp_notif_prefs(user_id) VALUES(:u)", {"u": uid})
            if role == "STUDENT":
                db.execute("INSERT INTO sp_students(user_id) VALUES(:u)", {"u": uid})
            flash("User created.", "success")
        return redirect(url_for("admin.users"))
    role = request.args.get("role")
    rows = db.query("SELECT u.*, c.name company FROM sp_users u LEFT JOIN sp_companies c ON c.id=u.company_id"
                    + (" WHERE u.user_role=:r" if role else "") + " ORDER BY u.user_role, u.full_name",
                    {"r": role} if role else {})
    companies = db.query("SELECT id, name FROM sp_companies ORDER BY name")
    return render_template("admin/users.html", rows=rows, roles=ROLES, role=role, companies=companies)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@roles_required("ADMIN", "TPO")
def toggle_user(uid):
    if uid == g.user["id"]:
        flash("You cannot disable your own account.", "warning")
    else:
        db.execute("UPDATE sp_users SET is_active=1-is_active WHERE id=:i AND user_role<>'ADMIN'", {"i": uid})
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/users/<int:uid>/reset", methods=["POST"])
@roles_required("ADMIN", "TPO")
def reset_password(uid):
    db.execute("UPDATE sp_users SET password_hash=:p WHERE id=:i", {"p": generate_password_hash("Welcome@123"), "i": uid})
    flash("Password reset to Welcome@123.", "info")
    return redirect(request.referrer or url_for("admin.users"))


# ---------------------------------------------------------------- learning content
@bp.route("/content")
@roles_required(*STAFF3)
def content():
    quizzes = db.query("SELECT q.*, (SELECT COUNT(*) FROM sp_questions x WHERE x.quiz_id=q.id) nq, "
                       "(SELECT COUNT(*) FROM sp_attempts a WHERE a.quiz_id=q.id) attempts FROM sp_quizzes q ORDER BY q.category, q.title")
    paths = db.query("SELECT p.*, (SELECT COUNT(*) FROM sp_path_steps s WHERE s.path_id=p.id) steps FROM sp_paths p ORDER BY p.seq")
    problems = db.query("SELECT id, title, difficulty, topic, points FROM sp_problems ORDER BY id")
    iq = db.query("SELECT category, COUNT(*) n FROM sp_interview_questions GROUP BY category ORDER BY category")
    return render_template("admin/content.html", quizzes=quizzes, paths=paths, problems=problems, iq=iq)


@bp.route("/content/quiz", methods=["POST"])
@roles_required(*STAFF3)
def new_quiz():
    f = request.form
    qid = db.insert("INSERT INTO sp_quizzes(title,category,topic,company,duration_min,description,created_by) "
                    "VALUES(:t,:c,:tp,:co,:d,:de,:u) RETURNING id INTO :new_id",
                    {"t": f["title"], "c": f["category"], "tp": f.get("topic"), "co": f.get("company") or None,
                     "d": int(f.get("duration_min") or 15), "de": f.get("description"), "u": g.user["id"]})
    flash("Quiz created – now add questions.", "success")
    return redirect(url_for("admin.quiz_edit", qid=qid))


@bp.route("/content/quiz/<int:qid>", methods=["GET", "POST"])
@roles_required(*STAFF3)
def quiz_edit(qid):
    q = db.one("SELECT * FROM sp_quizzes WHERE id=:i", {"i": qid}) or abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "toggle":
            db.execute("UPDATE sp_quizzes SET is_active=1-is_active WHERE id=:i", {"i": qid})
        elif f.get("action") == "delete_q":
            db.execute("DELETE FROM sp_questions WHERE id=:i AND quiz_id=:q", {"i": int(f["question_id"]), "q": qid})
        else:
            opts = [o.strip() for o in f.get("options", "").split("\n") if o.strip()]
            qtype = f["qtype"]
            if qtype == "TF":
                opts = ["True", "False"]
            db.execute("INSERT INTO sp_questions(quiz_id,qtype,question_text,options_json,answer,explanation,topic,marks) "
                       "VALUES(:q,:t,:tx,:o,:a,:e,:tp,:m)",
                       {"q": qid, "t": qtype, "tx": f["question_text"], "o": json.dumps(opts) if opts else None,
                        "a": f["answer"].strip(), "e": f.get("explanation"), "tp": f.get("topic") or q["topic"],
                        "m": int(f.get("marks") or 1)})
            flash("Question added.", "success")
        return redirect(url_for("admin.quiz_edit", qid=qid))
    questions = db.query("SELECT * FROM sp_questions WHERE quiz_id=:q ORDER BY id", {"q": qid})
    for x in questions:
        x["options"] = json.loads(x["options_json"]) if x["options_json"] else []
    return render_template("admin/quiz_edit.html", q=q, questions=questions)


@bp.route("/content/path/<int:pid>", methods=["GET", "POST"])
@roles_required(*STAFF3)
def path_edit(pid):
    p = db.one("SELECT * FROM sp_paths WHERE id=:i", {"i": pid}) or abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete":
            db.execute("DELETE FROM sp_path_steps WHERE id=:i AND path_id=:p", {"i": int(f["step_id"]), "p": pid})
        else:
            seq = db.scalar("SELECT NVL(MAX(seq),0)+1 FROM sp_path_steps WHERE path_id=:p", {"p": pid})
            db.execute("INSERT INTO sp_path_steps(path_id,seq,title,content_kind,content,resource_url,quiz_id) "
                       "VALUES(:p,:s,:t,:k,:c,:u,:q)",
                       {"p": pid, "s": seq, "t": f["title"], "k": f["content_kind"], "c": f.get("content"),
                        "u": f.get("resource_url"), "q": int(f["quiz_id"]) if f.get("quiz_id") else None})
            flash("Step added.", "success")
        return redirect(url_for("admin.path_edit", pid=pid))
    steps = db.query("SELECT * FROM sp_path_steps WHERE path_id=:p ORDER BY seq", {"p": pid})
    quizzes = db.query("SELECT id, title FROM sp_quizzes ORDER BY title")
    return render_template("admin/path_edit.html", p=p, steps=steps, quizzes=quizzes)


@bp.route("/content/path", methods=["POST"])
@roles_required(*STAFF3)
def new_path():
    f = request.form
    pid = db.insert("INSERT INTO sp_paths(name,skill_tag,description,icon,color,seq) VALUES(:n,:t,:d,:i,:c,:s) "
                    "RETURNING id INTO :new_id", {"n": f["name"], "t": f.get("skill_tag"), "d": f.get("description"),
                                                  "i": f.get("icon") or "book", "c": f.get("color") or "#0d6efd",
                                                  "s": db.scalar("SELECT NVL(MAX(seq),0)+1 FROM sp_paths")})
    return redirect(url_for("admin.path_edit", pid=pid))


@bp.route("/content/interview", methods=["POST"])
@roles_required(*STAFF3)
def new_interview_q():
    f = request.form
    db.execute("INSERT INTO sp_interview_questions(category,topic,company,question_text,model_answer,tips,keywords) "
               "VALUES(:c,:t,:co,:q,:m,:ti,:k)", {"c": f["category"], "t": f.get("topic"), "co": f.get("company") or None,
                                                  "q": f["question_text"], "m": f.get("model_answer"), "ti": f.get("tips"),
                                                  "k": f.get("keywords")})
    flash("Interview question added.", "success")
    return redirect(url_for("admin.content"))


@bp.route("/content/problem", methods=["POST"])
@roles_required(*STAFF3)
def new_problem():
    f = request.form
    try:
        samples = json.loads(f.get("samples_json") or "[]")
        tests = json.loads(f.get("tests_json") or "[]")
    except ValueError:
        flash("Test cases must be valid JSON: [{\"input\": \"...\", \"output\": \"...\"}]", "danger")
        return redirect(url_for("admin.content"))
    db.execute("INSERT INTO sp_problems(title,difficulty,topic,statement,input_format,output_format,samples_json,tests_json,"
               "solution,starter_code,points) VALUES(:t,:d,:tp,:s,:i,:o,:sj,:tj,:so,:sc,:p)",
               {"t": f["title"], "d": f["difficulty"], "tp": f.get("topic"), "s": f.get("statement"), "i": f.get("input_format"),
                "o": f.get("output_format"), "sj": json.dumps(samples), "tj": json.dumps(tests), "so": f.get("solution"),
                "sc": f.get("starter_code"), "p": int(f.get("points") or 10)})
    flash("Problem added.", "success")
    return redirect(url_for("admin.content"))


# ---------------------------------------------------------------- faculty: readiness & skill gaps
@bp.route("/skill-gaps")
@roles_required(*STAFF3)
def skill_gaps():
    dept = request.args.get("dept")
    p = {"d": dept} if dept else {}
    dfilter = " AND s.department=:d" if dept else ""
    topic = db.query(f"""SELECT q.topic, ROUND(AVG(a.score*100/NULLIF(a.max_score,0))) pct, COUNT(DISTINCT a.user_id) students
                         FROM sp_attempts a JOIN sp_quizzes q ON q.id=a.quiz_id JOIN sp_students s ON s.user_id=a.user_id
                         WHERE 1=1 {dfilter} GROUP BY q.topic ORDER BY pct""", p)
    skills = db.query(f"""SELECT LOWER(i.title) skill, COUNT(DISTINCT i.user_id) n FROM sp_profile_items i
                          JOIN sp_students s ON s.user_id=i.user_id WHERE i.item_type='SKILL' {dfilter}
                          GROUP BY LOWER(i.title) ORDER BY n DESC FETCH FIRST 25 ROWS ONLY""", p)
    total = db.scalar(f"SELECT COUNT(*) FROM sp_students s WHERE 1=1 {dfilter}", p) or 1
    demanded = {}
    for r in db.query("SELECT required_skills FROM sp_drives WHERE required_skills IS NOT NULL"):
        for sk in r["required_skills"].split(","):
            if sk.strip():
                demanded[sk.strip().lower()] = demanded.get(sk.strip().lower(), 0) + 1
    have = {r["skill"]: r["n"] for r in skills}
    gaps = sorted(({"skill": k, "drives": v, "coverage": round(100 * have.get(k, 0) / total)} for k, v in demanded.items()),
                  key=lambda x: (x["coverage"], -x["drives"]))
    students = db.query(f"""SELECT u.id, u.full_name, s.department, s.cgpa FROM sp_users u JOIN sp_students s ON s.user_id=u.id
                            WHERE u.user_role='STUDENT' {dfilter} ORDER BY u.full_name""", p)
    ready = []
    for st in students[:300]:
        r = rd.readiness(st["id"])
        ready.append({**st, "overall": r["overall"], "aptitude": r["aptitude"], "technical": r["technical"],
                      "coding": r["coding"], "profile": r["profile"]})
    ready.sort(key=lambda x: x["overall"])
    buckets = {"Ready (≥70)": sum(1 for x in ready if x["overall"] >= 70),
               "Developing (40–69)": sum(1 for x in ready if 40 <= x["overall"] < 70),
               "Needs support (<40)": sum(1 for x in ready if x["overall"] < 40)}
    return render_template("admin/skill_gaps.html", topic=topic, skills=skills, gaps=gaps[:15], ready=ready,
                           buckets=buckets, dept=dept, total=total)
