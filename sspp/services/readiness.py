"""Placement readiness scoring used by student, faculty and TPO dashboards."""
from .. import db

PROFILE_FIELDS = ["reg_no", "department", "batch_year", "gender", "dob", "cgpa", "tenth_pct", "twelfth_pct",
                  "career_goal", "summary", "github", "linkedin"]
ITEM_TYPES_EXPECTED = ["EDUCATION", "SKILL", "PROJECT", "CERTIFICATION", "INTERNSHIP", "ACHIEVEMENT"]


def profile_completeness(user_id, student=None, counts=None):
    student = student or db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": user_id}) or {}
    if counts is None:
        counts = {r["item_type"]: r["n"] for r in db.query(
            "SELECT item_type, COUNT(*) n FROM sp_profile_items WHERE user_id=:u GROUP BY item_type", {"u": user_id})}
    filled = sum(1 for f in PROFILE_FIELDS if student.get(f) not in (None, ""))
    items = sum(1 for t in ITEM_TYPES_EXPECTED if counts.get(t))
    score = 100 * (filled + items) / (len(PROFILE_FIELDS) + len(ITEM_TYPES_EXPECTED))
    missing = [f.replace("_", " ").title() for f in PROFILE_FIELDS if student.get(f) in (None, "")]
    missing += [t.title() + "s" for t in ITEM_TYPES_EXPECTED if not counts.get(t)]
    return round(score), missing


def resume_completeness(resume, items_by_type, student):
    checks = [
        bool(resume.get("objective") and len(resume["objective"]) > 60),
        bool(resume.get("target_role")),
        bool(items_by_type.get("EDUCATION")),
        len(items_by_type.get("SKILL", [])) >= 5,
        len(items_by_type.get("PROJECT", [])) >= 2,
        bool(items_by_type.get("INTERNSHIP")),
        bool(items_by_type.get("CERTIFICATION")),
        bool(items_by_type.get("ACHIEVEMENT") or items_by_type.get("HACKATHON")),
        bool(student.get("github") or student.get("linkedin")),
        all(len(p.get("description") or "") > 80 for p in items_by_type.get("PROJECT", [])) and bool(items_by_type.get("PROJECT")),
    ]
    tips = ["Write a 2–3 line objective tailored to the role", "Set a target role", "Add your education",
            "List at least 5 technical skills", "Add at least 2 projects", "Add an internship",
            "Add a certification", "Add achievements or hackathons", "Add GitHub/LinkedIn links",
            "Describe each project in 2+ lines with impact"]
    return round(100 * sum(checks) / len(checks)), [t for t, c in zip(tips, checks) if not c]


def skill_progress(user_id):
    """Percent completion of each learning path for the user."""
    rows = db.query("""
        SELECT p.id, p.name, p.skill_tag, p.icon, p.color, COUNT(st.id) total,
               COUNT(sp.step_id) done
          FROM sp_paths p LEFT JOIN sp_path_steps st ON st.path_id = p.id
          LEFT JOIN sp_step_progress sp ON sp.step_id = st.id AND sp.user_id = :u
         GROUP BY p.id, p.name, p.skill_tag, p.icon, p.color, p.seq ORDER BY p.seq""", {"u": user_id})
    for r in rows:
        r["pct"] = round(100 * r["done"] / r["total"]) if r["total"] else 0
    return rows


def quiz_score(user_id, category):
    return round(db.scalar("""
        SELECT AVG(best) FROM (SELECT MAX(a.score*100/NULLIF(a.max_score,0)) best FROM sp_attempts a
          JOIN sp_quizzes q ON q.id=a.quiz_id WHERE a.user_id=:u AND q.category=:c GROUP BY a.quiz_id)""",
                           {"u": user_id, "c": category}) or 0)


def topic_scores(user_id):
    """Average accuracy per topic, e.g. Java 80, SQL 65."""
    rows = db.query("SELECT q.topic, AVG(a.score*100/NULLIF(a.max_score,0)) pct FROM sp_attempts a "
                    "JOIN sp_quizzes q ON q.id=a.quiz_id WHERE a.user_id=:u GROUP BY q.topic", {"u": user_id})
    return {r["topic"]: round(r["pct"] or 0) for r in rows if r["topic"]}


def coding_score(user_id):
    total = db.scalar("SELECT COUNT(*) FROM sp_problems") or 1
    solved = db.scalar("SELECT COUNT(DISTINCT problem_id) FROM sp_submissions WHERE user_id=:u AND verdict='ACCEPTED'",
                       {"u": user_id})
    return round(100 * solved / total), solved, total


def interview_score(user_id):
    return round((db.scalar("SELECT AVG(score) FROM sp_interview_answers a JOIN sp_interview_sessions s "
                            "ON s.id=a.session_id WHERE s.user_id=:u", {"u": user_id}) or 0) * 10)


def best_resume_score(user_id, student=None):
    from ..views.resume import load_items
    resumes = db.query("SELECT * FROM sp_resumes WHERE user_id=:u", {"u": user_id})
    if not resumes:
        return 0
    items = load_items(user_id)
    student = student or db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": user_id}) or {}
    return max(resume_completeness(r, items, student)[0] for r in resumes)


def readiness(user_id):
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": user_id}) or {}
    prof, missing = profile_completeness(user_id, student)
    paths = skill_progress(user_id)
    started = [p["pct"] for p in paths if p["pct"] > 0]
    skill = round(sum(started) / len(started)) if started else 0
    apt = quiz_score(user_id, "APTITUDE")
    tech = quiz_score(user_id, "TECHNICAL")
    code, solved, total = coding_score(user_id)
    intv = interview_score(user_id)
    res = best_resume_score(user_id, student)
    overall = round(0.15 * prof + 0.15 * res + 0.15 * skill + 0.15 * apt + 0.15 * tech + 0.15 * code + 0.10 * intv)
    return {"profile": prof, "profile_missing": missing, "resume": res, "skill": skill, "aptitude": apt,
            "technical": tech, "coding": code, "solved": solved, "problems": total, "interview": intv,
            "overall": overall, "paths": paths}
