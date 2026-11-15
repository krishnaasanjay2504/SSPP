from flask import Blueprint, render_template, request, g, jsonify
from .. import db
from ..security import login_required, roles_required
from ..services import ai, readiness as rd, eligibility
from .resume import load_items

bp = Blueprint("assistant", __name__, url_prefix="/ai")
_HISTORY = {}  # per-user chat history kept server-side (cookies are too small for AI replies)


def _context(uid):
    s = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
    return {"name": g.user["full_name"], "department": s.get("department"), "cgpa": s.get("cgpa"),
            "goal": s.get("career_goal"), "skills": eligibility.student_skills(uid), "topics": rd.topic_scores(uid)}


@bp.route("/")
@login_required
def chat_page():
    history = _HISTORY.get(g.user["id"], [])
    return render_template("ai/chat.html", history=history, llm=ai.llm_available(), goals=list(ai.CAREER_PATHS))


@bp.route("/chat", methods=["POST"])
@login_required
def chat():
    msg = (request.get_json(force=True).get("message") or "").strip()[:2000]
    if not msg:
        return jsonify({"error": "empty"}), 400
    ctx = _context(g.user["id"]) if g.user["user_role"] == "STUDENT" else {"name": g.user["full_name"], "skills": set()}
    if ctx.get("skills") is not None and g.user["user_role"] == "STUDENT":
        r = rd.readiness(g.user["id"])
        ctx["readiness"] = {k: r[k] for k in ("profile", "resume", "aptitude", "technical", "coding", "interview", "overall")}
    reply, from_llm = ai.chat(msg, ctx)
    hist = _HISTORY.get(g.user["id"], [])[-18:]
    hist += [{"role": "user", "text": msg}, {"role": "ai", "text": reply}]
    _HISTORY[g.user["id"]] = hist
    return jsonify({"html": str(ai.to_html(reply)), "llm": from_llm})


@bp.route("/clear", methods=["POST"])
@login_required
def clear():
    _HISTORY.pop(g.user["id"], None)
    return jsonify({"ok": True})


@bp.route("/resume", methods=["GET", "POST"])
@roles_required("STUDENT")
def resume_assistant():
    uid = g.user["id"]
    resumes = db.query("SELECT * FROM sp_resumes WHERE user_id=:u ORDER BY updated_at DESC", {"u": uid})
    drives = db.query("SELECT d.id, d.job_role, c.name company FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id "
                      "WHERE d.status='OPEN'")
    items = load_items(uid)
    projects = items.get("PROJECT", [])
    result, grammar, improved, mode = None, None, None, request.form.get("mode", "match")
    f = request.form
    if request.method == "POST":
        student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
        if mode == "match":
            jd = f.get("jd", "")
            if f.get("drive_id"):
                d = db.one("SELECT job_desc, required_skills, job_role FROM sp_drives WHERE id=:i", {"i": int(f["drive_id"])})
                jd = f"{d['job_role']}\n{d['job_desc'] or ''}\nRequired skills: {d['required_skills'] or ''}" if d else jd
            r = next((x for x in resumes if str(x["id"]) == f.get("resume_id")), None)
            text = ai.resume_text(student, items, r)
            result = ai.match_jd(text, jd)
            result["jd"] = jd
            result["projects"] = [p["title"] for p in projects if ai.extract_skills(f"{p['title']} {p['description']} {p['tech']}")
                                  & set(result["matched"] + result["missing"])]
            result["ai"] = ai.llm(ai.SYSTEM_COACH, f"Compare this student's resume with the job description. List matched skills, "
                                  f"missing skills, 4 concrete resume improvements and which projects/certifications to "
                                  f"highlight.\n\nRESUME:\n{text[:6000]}\n\nJOB DESCRIPTION:\n{jd[:4000]}")
        elif mode == "grammar":
            grammar = {"text": f.get("text", ""), "issues": ai.grammar_check(f.get("text", "")),
                       "ai": ai.llm(ai.SYSTEM_COACH, "Proofread this resume text. List grammar/style issues and give a "
                                    f"corrected version:\n\n{f.get('text', '')[:4000]}")}
        elif mode == "improve":
            p = next((x for x in projects if str(x["id"]) == f.get("project_id")), None)
            title = p["title"] if p else f.get("title", "")
            desc = p["description"] if p else f.get("text", "")
            tech = p["tech"] if p else ""
            improved = ai.improve_description(title, desc, tech)
            improved["title"], improved["original"] = title, desc
            improved["ai"] = ai.llm(ai.SYSTEM_COACH, "Rewrite this student project as 3-4 strong resume bullet points with "
                                    f"action verbs and measurable impact placeholders.\nTitle: {title}\nTech: {tech}\n"
                                    f"Description: {desc}")
    return render_template("ai/resume_assistant.html", resumes=resumes, drives=drives, projects=projects, result=result,
                           grammar=grammar, improved=improved, mode=mode, f=f, llm=ai.llm_available())


@bp.route("/career", methods=["GET", "POST"])
@roles_required("STUDENT")
def career():
    uid = g.user["id"]
    s = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) or {}
    goal = request.values.get("goal") or s.get("career_goal") or "Software Developer"
    gap = ai.career_gap(goal, eligibility.student_skills(uid), rd.topic_scores(uid))
    paths = {p["skill_tag"]: p for p in db.query("SELECT * FROM sp_paths")}
    ai_plan = None
    if request.method == "POST":
        ai_plan = ai.llm(ai.SYSTEM_COACH, f"Student goal: {goal}. Current skills: {', '.join(gap['matched'])}. Missing: "
                         f"{', '.join(gap['missing'])}. Weak quiz topics: {', '.join(gap['weak_topics'])}. Create a "
                         f"personalised 8-week learning plan with weekly goals, one project idea and placement prep tips.")
    return render_template("ai/career.html", gap=gap, goals=list(ai.CAREER_PATHS), goal=goal, paths=paths,
                           ai_plan=ai_plan, llm=ai.llm_available())
