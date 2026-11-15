import json
import re
import random
import time
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, session, jsonify, current_app
from .. import db
from ..security import login_required, roles_required, STAFF
from ..services import readiness as rd, ai, code_runner

bp = Blueprint("learn", __name__)


# ================================================================ Skill Builder
@bp.route("/skills")
@login_required
def skills():
    paths = rd.skill_progress(g.user["id"])
    topics = rd.topic_scores(g.user["id"])
    return render_template("learn/skills.html", paths=paths, topics=topics)


@bp.route("/skills/<int:pid>")
@login_required
def path(pid):
    p = db.one("SELECT * FROM sp_paths WHERE id=:i", {"i": pid}) or abort(404)
    steps = db.query("SELECT st.*, (SELECT COUNT(*) FROM sp_step_progress sp WHERE sp.step_id=st.id AND sp.user_id=:u) done "
                     "FROM sp_path_steps st WHERE st.path_id=:p ORDER BY st.seq", {"p": pid, "u": g.user["id"]})
    done = sum(s["done"] for s in steps)
    pct = round(100 * done / len(steps)) if steps else 0
    return render_template("learn/path.html", p=p, steps=steps, pct=pct, done=done)


@bp.route("/skills/step/<int:sid>/toggle", methods=["POST"])
@login_required
def toggle_step(sid):
    st = db.one("SELECT * FROM sp_path_steps WHERE id=:i", {"i": sid}) or abort(404)
    p = {"u": g.user["id"], "s": sid}
    if db.scalar("SELECT COUNT(*) FROM sp_step_progress WHERE user_id=:u AND step_id=:s", p):
        db.execute("DELETE FROM sp_step_progress WHERE user_id=:u AND step_id=:s", p)
    else:
        db.execute("INSERT INTO sp_step_progress(user_id, step_id) VALUES(:u,:s)", p)
    return redirect(url_for("learn.path", pid=st["path_id"]) + f"#step-{sid}")


@bp.route("/skills/<int:pid>/certificate")
@login_required
def certificate(pid):
    p = db.one("SELECT * FROM sp_paths WHERE id=:i", {"i": pid}) or abort(404)
    total = db.scalar("SELECT COUNT(*) FROM sp_path_steps WHERE path_id=:p", {"p": pid})
    done_at = db.one("SELECT COUNT(*) n, MAX(sp.completed_at) last FROM sp_step_progress sp JOIN sp_path_steps st "
                     "ON st.id=sp.step_id WHERE st.path_id=:p AND sp.user_id=:u", {"p": pid, "u": g.user["id"]})
    if not total or done_at["n"] < total:
        flash("Complete every step of the path to unlock the certificate.", "warning")
        return redirect(url_for("learn.path", pid=pid))
    return render_template("learn/certificate.html", p=p, when=done_at["last"])


# ================================================================ Quiz & Assessment Engine
QUIZ_CATS = {"APTITUDE": "Aptitude", "TECHNICAL": "Technical", "MOCK": "Mock Placement Tests", "COMPANY": "Company-specific"}


@bp.route("/quizzes")
@login_required
def quizzes():
    cat = request.args.get("category")
    sql = ("SELECT q.*, (SELECT COUNT(*) FROM sp_questions x WHERE x.quiz_id=q.id) nq, "
           "(SELECT MAX(a.score*100/NULLIF(a.max_score,0)) FROM sp_attempts a WHERE a.quiz_id=q.id AND a.user_id=:u) best, "
           "(SELECT COUNT(*) FROM sp_attempts a WHERE a.quiz_id=q.id AND a.user_id=:u) tries "
           "FROM sp_quizzes q WHERE q.is_active=1")
    p = {"u": g.user["id"]}
    if cat:
        sql += " AND q.category=:c"
        p["c"] = cat
    rows = db.query(sql + " ORDER BY q.category, q.topic, q.title", p)
    history = db.query("SELECT a.*, q.title FROM sp_attempts a JOIN sp_quizzes q ON q.id=a.quiz_id WHERE a.user_id=:u "
                       "ORDER BY a.submitted_at DESC FETCH FIRST 10 ROWS ONLY", {"u": g.user["id"]})
    return render_template("learn/quizzes.html", quizzes=rows, cats=QUIZ_CATS, cat=cat, history=history)


@bp.route("/quizzes/<int:qid>")
@login_required
def quiz_start(qid):
    q = db.one("SELECT * FROM sp_quizzes WHERE id=:i", {"i": qid}) or abort(404)
    questions = db.query("SELECT * FROM sp_questions WHERE quiz_id=:q ORDER BY id", {"q": qid})
    for x in questions:
        x["options"] = json.loads(x["options_json"]) if x["options_json"] else []
    starts = session.get("quiz_start", {})
    starts[str(qid)] = time.time()
    session["quiz_start"] = starts
    return render_template("learn/quiz_take.html", q=q, questions=questions)


def _grade(question, given):
    ans = (question["answer"] or "").strip()
    if question["qtype"] == "MSQ":
        return sorted(given) == sorted(a.strip() for a in ans.split(",") if a.strip())
    if question["qtype"] == "FILL":
        g_ = (given[0] if given else "").strip().lower()
        return g_ in [a.strip().lower() for a in ans.split("|")]
    return bool(given) and given[0].strip().lower() == ans.lower()


@bp.route("/quizzes/<int:qid>/submit", methods=["POST"])
@login_required
def quiz_submit(qid):
    q = db.one("SELECT * FROM sp_quizzes WHERE id=:i", {"i": qid}) or abort(404)
    questions = db.query("SELECT * FROM sp_questions WHERE quiz_id=:q ORDER BY id", {"q": qid})
    started = session.get("quiz_start", {}).pop(str(qid), None)
    elapsed = int(time.time() - started) if started else 0
    answers, score, max_score, attempted, correct_n = {}, 0, 0, 0, 0
    topics = {}
    for x in questions:
        given = [v for v in request.form.getlist(f"q{x['id']}") if v != ""]
        ok = _grade(x, given)
        m = x["marks"] or 1
        max_score += m
        if given:
            attempted += 1
        if ok:
            score += m
            correct_n += 1
        answers[str(x["id"])] = {"given": given, "ok": ok}
        t = topics.setdefault(x["topic"] or q["topic"] or "General", [0, 0])
        t[0] += ok
        t[1] += 1
    accuracy = round(100 * correct_n / attempted, 1) if attempted else 0
    aid = db.insert("INSERT INTO sp_attempts(quiz_id,user_id,score,max_score,accuracy,time_sec,answers_json,topic_json) "
                    "VALUES(:q,:u,:s,:m,:a,:t,:aj,:tj) RETURNING id INTO :new_id",
                    {"q": qid, "u": g.user["id"], "s": score, "m": max_score, "a": accuracy, "t": elapsed,
                     "aj": json.dumps(answers), "tj": json.dumps(topics)})
    return redirect(url_for("learn.attempt", aid=aid))


@bp.route("/attempts/<int:aid>")
@login_required
def attempt(aid):
    a = db.one("SELECT a.*, q.title, q.category, q.topic, q.duration_min FROM sp_attempts a JOIN sp_quizzes q ON q.id=a.quiz_id "
               "WHERE a.id=:i", {"i": aid}) or abort(404)
    if a["user_id"] != g.user["id"] and g.user["user_role"] not in STAFF:
        abort(403)
    questions = db.query("SELECT * FROM sp_questions WHERE quiz_id=:q ORDER BY id", {"q": a["quiz_id"]})
    answers = json.loads(a["answers_json"] or "{}")
    for x in questions:
        x["options"] = json.loads(x["options_json"]) if x["options_json"] else []
        x["res"] = answers.get(str(x["id"]), {"given": [], "ok": False})
    topics = json.loads(a["topic_json"] or "{}")
    strong = [t for t, (c, n) in topics.items() if n and c / n >= 0.7]
    weak = [t for t, (c, n) in topics.items() if n and c / n < 0.6]
    pct = round(100 * a["score"] / a["max_score"]) if a["max_score"] else 0
    return render_template("learn/quiz_result.html", a=a, questions=questions, strong=strong, weak=weak, topics=topics, pct=pct)


# ================================================================ Aptitude & Communication
@bp.route("/aptitude")
@login_required
def aptitude():
    quizzes = db.query("SELECT q.*, (SELECT MAX(a.score*100/NULLIF(a.max_score,0)) FROM sp_attempts a WHERE a.quiz_id=q.id "
                       "AND a.user_id=:u) best FROM sp_quizzes q WHERE q.category='APTITUDE' AND q.is_active=1 ORDER BY q.topic",
                       {"u": g.user["id"]})
    path = db.one("SELECT * FROM sp_paths WHERE skill_tag='APTITUDE'")
    return render_template("learn/aptitude.html", quizzes=quizzes, path=path)


@bp.route("/communication", methods=["GET", "POST"])
@login_required
def communication():
    result = None
    if request.method == "POST":
        text = request.form.get("text", "")
        kind = request.form.get("kind")
        qid = request.form.get("qid")
        q = db.one("SELECT * FROM sp_interview_questions WHERE id=:i", {"i": qid}) if qid else None
        if kind == "EMAIL":
            issues = ai.grammar_check(text)
            checks = {"Greeting": bool(re.search(r"^(dear|hello|hi|respected)", text.strip(), re.I)),
                      "Subject/purpose stated early": len(text.split(".")[0].split()) < 40,
                      "Polite closing": bool(re.search(r"(regards|sincerely|thank you|thanks)", text, re.I)),
                      "Concise (< 200 words)": len(text.split()) < 200}
            ai_txt = ai.llm(ai.SYSTEM_COACH, f"Review this professional email written by a student for the task: "
                            f"{q['question_text'] if q else 'formal email'}.\n\n{text}\n\nGive feedback and a polished version.")
            result = {"kind": "EMAIL", "issues": [i for i in issues if "first-person" not in i], "checks": checks, "ai": ai_txt}
        else:
            fb = ai.interview_feedback(q["question_text"] if q else "", text, q["model_answer"] if q else "",
                                       q["keywords"] if q else "", "BEHAVIORAL" if kind == "GD" else "HR")
            result = {"kind": kind, **fb}
    items = db.query("SELECT * FROM sp_interview_questions WHERE category IN ('GD','EMAIL','PRESENTATION') ORDER BY category, id")
    by = {}
    for i in items:
        by.setdefault(i["category"], []).append(i)
    path = db.one("SELECT * FROM sp_paths WHERE skill_tag='COMM'")
    return render_template("learn/communication.html", by=by, result=result, path=path, form=request.form)


# ================================================================ Interview preparation
@bp.route("/interview")
@login_required
def interview_bank():
    a = request.args
    sql = "SELECT * FROM sp_interview_questions WHERE category IN ('TECHNICAL','HR','BEHAVIORAL')"
    p = {}
    if a.get("category"):
        sql += " AND category=:c"
        p["c"] = a["category"]
    if a.get("topic"):
        sql += " AND topic=:t"
        p["t"] = a["topic"]
    if a.get("company"):
        sql += " AND (company=:co OR company IS NULL)"
        p["co"] = a["company"]
    if a.get("q"):
        sql += " AND LOWER(question_text) LIKE :q"
        p["q"] = f"%{a['q'].lower()}%"
    rows = db.query(sql + " ORDER BY category, topic, id", p)
    topics = [r["topic"] for r in db.query("SELECT DISTINCT topic FROM sp_interview_questions WHERE topic IS NOT NULL "
                                           "AND category IN ('TECHNICAL','HR','BEHAVIORAL') ORDER BY topic")]
    companies = [r["name"] for r in db.query("SELECT name FROM sp_companies WHERE status='APPROVED' ORDER BY name")]
    sessions_ = db.query("SELECT * FROM sp_interview_sessions WHERE user_id=:u ORDER BY created_at DESC FETCH FIRST 8 ROWS ONLY",
                         {"u": g.user["id"]})
    return render_template("learn/interview.html", rows=rows, topics=topics, companies=companies, a=a, sessions=sessions_)


@bp.route("/interview/practice", methods=["POST"])
@login_required
def interview_start():
    cat = request.form.get("category", "TECHNICAL")
    topic = request.form.get("topic") or None
    sql = "SELECT id FROM sp_interview_questions WHERE category=:c"
    p = {"c": cat}
    if topic:
        sql += " AND topic=:t"
        p["t"] = topic
    ids = [r["id"] for r in db.query(sql, p)]
    if not ids:
        flash("No questions for this selection yet.", "warning")
        return redirect(url_for("learn.interview_bank"))
    random.shuffle(ids)
    n = min(int(request.form.get("count", 5)), len(ids))
    sid = db.insert("INSERT INTO sp_interview_sessions(user_id,category,topic) VALUES(:u,:c,:t) RETURNING id INTO :new_id",
                    {"u": g.user["id"], "c": cat, "t": topic})
    session[f"iv{sid}"] = ids[:n]
    return redirect(url_for("learn.interview_session", sid=sid, i=0))


@bp.route("/interview/session/<int:sid>", methods=["GET", "POST"])
@login_required
def interview_session(sid):
    s = db.one("SELECT * FROM sp_interview_sessions WHERE id=:i AND user_id=:u", {"i": sid, "u": g.user["id"]}) or abort(404)
    qids = session.get(f"iv{sid}")
    answered = db.query("SELECT a.*, q.question_text FROM sp_interview_answers a JOIN sp_interview_questions q ON "
                        "q.id=a.question_id WHERE a.session_id=:s ORDER BY a.id", {"s": sid})
    if not qids or s["completed"]:
        return render_template("learn/interview_summary.html", s=s, answered=answered)
    i = int(request.args.get("i", 0))
    if i >= len(qids):
        avg = (sum(a["score"] or 0 for a in answered) / len(answered)) if answered else 0
        db.execute("UPDATE sp_interview_sessions SET completed=1, avg_score=:a WHERE id=:i", {"a": avg, "i": sid})
        session.pop(f"iv{sid}", None)
        s["avg_score"], s["completed"] = avg, 1
        return render_template("learn/interview_summary.html", s=s, answered=answered)
    q = db.one("SELECT * FROM sp_interview_questions WHERE id=:i", {"i": qids[i]})
    fb = None
    if request.method == "POST":
        resp = request.form.get("response", "")
        fb = ai.interview_feedback(q["question_text"], resp, q["model_answer"] or "", q["keywords"] or "", s["category"])
        text = "\n".join(fb["feedback"]) + ("\n\n" + fb["ai"] if fb["ai"] else "")
        db.execute("INSERT INTO sp_interview_answers(session_id,question_id,response,score,feedback) VALUES(:s,:q,:r,:sc,:f)",
                   {"s": sid, "q": q["id"], "r": resp[:4000], "sc": fb["score"], "f": text[:4000]})
    return render_template("learn/interview_session.html", s=s, q=q, i=i, n=len(qids), fb=fb,
                           response=request.form.get("response", ""))


@bp.route("/prep/<company>")
@login_required
def company_prep(company):
    c = db.one("SELECT * FROM sp_companies WHERE LOWER(name)=LOWER(:n)", {"n": company}) or abort(404)
    drives = db.query("SELECT * FROM sp_drives WHERE company_id=:c AND status IN ('OPEN','CLOSED','COMPLETED') "
                      "ORDER BY drive_date DESC", {"c": c["id"]})
    rounds = (drives[0]["rounds"] if drives else "Aptitude Test,Coding Test,Technical Interview,HR Interview").split(",")
    quizzes = db.query("SELECT * FROM sp_quizzes WHERE (LOWER(company)=LOWER(:n) OR category='MOCK') AND is_active=1",
                       {"n": c["name"]})
    questions = db.query("SELECT * FROM sp_interview_questions WHERE LOWER(company)=LOWER(:n) ORDER BY category",
                         {"n": c["name"]})
    general = db.query("SELECT * FROM sp_interview_questions WHERE company IS NULL AND category IN ('TECHNICAL','HR') "
                       "FETCH FIRST 6 ROWS ONLY")
    exps = db.query("SELECT e.*, u.full_name FROM sp_experiences e JOIN sp_users u ON u.id=e.user_id "
                    "WHERE LOWER(e.company)=LOWER(:n) ORDER BY e.created_at DESC", {"n": c["name"]})
    return render_template("learn/company_prep.html", c=c, drives=drives, rounds=[r.strip() for r in rounds],
                           quizzes=quizzes, questions=questions, general=general, exps=exps)


# ================================================================ Coding Practice
@bp.route("/coding")
@login_required
def coding():
    diff = request.args.get("difficulty")
    sql = ("SELECT p.id, p.title, p.difficulty, p.topic, p.points, "
           "(SELECT MAX(CASE WHEN s.verdict='ACCEPTED' THEN 1 ELSE 0 END) FROM sp_submissions s WHERE s.problem_id=p.id AND s.user_id=:u) solved, "
           "(SELECT COUNT(*) FROM sp_submissions s WHERE s.problem_id=p.id AND s.user_id=:u) tries FROM sp_problems p")
    p = {"u": g.user["id"]}
    if diff:
        sql += " WHERE p.difficulty=:d"
        p["d"] = diff
    probs = db.query(sql + " ORDER BY CASE p.difficulty WHEN 'EASY' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, p.id", p)
    score = db.scalar("SELECT SUM(p.points) FROM sp_problems p WHERE p.id IN (SELECT problem_id FROM sp_submissions "
                      "WHERE user_id=:u AND verdict='ACCEPTED')", {"u": g.user["id"]})
    pct, solved, total = rd.coding_score(g.user["id"])
    board = db.query("""SELECT u.full_name, SUM(p.points) pts, COUNT(*) solved FROM
                        (SELECT DISTINCT user_id, problem_id FROM sp_submissions WHERE verdict='ACCEPTED') x
                        JOIN sp_problems p ON p.id=x.problem_id JOIN sp_users u ON u.id=x.user_id
                        GROUP BY u.full_name ORDER BY pts DESC FETCH FIRST 10 ROWS ONLY""")
    return render_template("learn/coding.html", probs=probs, diff=diff, score=score, pct=pct, solved=solved, total=total, board=board)


@bp.route("/coding/<int:pid>")
@login_required
def problem(pid):
    pr = db.one("SELECT * FROM sp_problems WHERE id=:i", {"i": pid}) or abort(404)
    pr["samples"] = json.loads(pr["samples_json"] or "[]")
    subs = db.query("SELECT id, lang, passed, total, verdict, submitted_at FROM sp_submissions WHERE problem_id=:p AND "
                    "user_id=:u ORDER BY submitted_at DESC FETCH FIRST 10 ROWS ONLY", {"p": pid, "u": g.user["id"]})
    last = db.one("SELECT code, lang FROM sp_submissions WHERE problem_id=:p AND user_id=:u ORDER BY submitted_at DESC "
                  "FETCH FIRST 1 ROWS ONLY", {"p": pid, "u": g.user["id"]})
    solved = any(s["verdict"] == "ACCEPTED" for s in subs)
    return render_template("learn/problem.html", pr=pr, subs=subs, last=last, solved=solved,
                           langs=code_runner.available_languages(), enabled=current_app.config["CODE_EXEC_ENABLED"])


@bp.route("/coding/<int:pid>/run", methods=["POST"])
@login_required
def run_code(pid):
    if not current_app.config["CODE_EXEC_ENABLED"]:
        return jsonify({"error": "Code execution is disabled on this server."}), 400
    pr = db.one("SELECT * FROM sp_problems WHERE id=:i", {"i": pid}) or abort(404)
    data = request.get_json(force=True)
    code, lang, mode = data.get("code", ""), data.get("lang", "python"), data.get("mode", "run")
    if len(code) > 20000:
        return jsonify({"error": "Code too long."}), 400
    tests = json.loads(pr["samples_json"] or "[]")
    if mode == "submit":
        tests = tests + json.loads(pr["tests_json"] or "[]")
    if mode == "custom":
        tests = [{"input": data.get("stdin", ""), "output": ""}]
    res = code_runner.run(lang, code, tests, current_app.config["CODE_EXEC_TIMEOUT"])
    if mode == "submit":
        db.execute("INSERT INTO sp_submissions(problem_id,user_id,lang,code,passed,total,verdict) VALUES(:p,:u,:l,:c,:pa,:t,:v)",
                   {"p": pid, "u": g.user["id"], "l": lang, "c": code, "pa": res["passed"], "t": res["total"], "v": res["verdict"]})
        n_samples = len(json.loads(pr["samples_json"] or "[]"))
        for r in res["results"][n_samples:]:  # hide hidden test data
            r["input"], r["expected"], r["actual"] = "(hidden)", "(hidden)", "(hidden)" if r["status"] != "PASS" else "✓"
    return jsonify(res)


@bp.route("/coding/<int:pid>/solution")
@login_required
def solution(pid):
    pr = db.one("SELECT id, title, solution FROM sp_problems WHERE id=:i", {"i": pid}) or abort(404)
    tries = db.scalar("SELECT COUNT(*) FROM sp_submissions WHERE problem_id=:p AND user_id=:u", {"p": pid, "u": g.user["id"]})
    if not tries and g.user["user_role"] == "STUDENT":
        return jsonify({"error": "Make at least one submission to unlock the reference solution."}), 403
    return jsonify({"solution": pr["solution"]})
