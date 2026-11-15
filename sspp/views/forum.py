from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort
from .. import db
from ..security import login_required, STAFF
from ..services.notify import notify

bp = Blueprint("forum", __name__)
POPULAR_TAGS = ["SQL", "DBMS", "Java", "Python", "DSA", "OS", "Networks", "Aptitude", "Interview", "HR", "Cloud",
                "AI-ML", "Resume", "Placement", "Alumni"]


def _score(tt, tid):
    return db.scalar("SELECT NVL(SUM(vote_val),0) FROM sp_votes WHERE target_type=:t AND target_id=:i", {"t": tt, "i": tid})


@bp.route("/forum")
@login_required
def index():
    a = request.args
    sql = ["""SELECT q.*, u.full_name, u.user_role,
              (SELECT COUNT(*) FROM sp_forum_answers x WHERE x.question_id=q.id) answers,
              (SELECT NVL(SUM(vote_val),0) FROM sp_votes v WHERE v.target_type='Q' AND v.target_id=q.id) votes
              FROM sp_forum_questions q JOIN sp_users u ON u.id=q.user_id WHERE 1=1"""]
    p = {}
    if a.get("q"):
        sql.append("AND (LOWER(q.title) LIKE :q OR LOWER(DBMS_LOB.SUBSTR(q.body, 4000, 1)) LIKE :q OR LOWER(q.tags) LIKE :q)")
        p["q"] = f"%{a['q'].lower()}%"
    if a.get("tag"):
        sql.append("AND (',' || LOWER(REPLACE(q.tags,' ','')) || ',') LIKE :tg")
        p["tg"] = f"%,{a['tag'].lower()},%"
    if a.get("view") == "bookmarks":
        sql.append("AND q.id IN (SELECT question_id FROM sp_bookmarks WHERE user_id=:u)")
        p["u"] = g.user["id"]
    elif a.get("view") == "following":
        sql.append("AND EXISTS (SELECT 1 FROM sp_tag_follows f WHERE f.user_id=:u AND (',' || LOWER(REPLACE(q.tags,' ','')) "
                   "|| ',') LIKE '%,' || LOWER(f.tag) || ',%')")
        p["u"] = g.user["id"]
    elif a.get("view") == "unanswered":
        sql.append("AND NOT EXISTS (SELECT 1 FROM sp_forum_answers x WHERE x.question_id=q.id)")
    elif a.get("view") == "alumni":
        sql.append("AND q.audience='ALUMNI'")
    order = {"votes": "votes DESC", "views": "q.views DESC"}.get(a.get("sort"), "q.created_at DESC")
    rows = db.query(" ".join(sql) + f" ORDER BY {order} FETCH FIRST 100 ROWS ONLY", p)
    follows = {r["tag"] for r in db.query("SELECT tag FROM sp_tag_follows WHERE user_id=:u", {"u": g.user["id"]})}
    return render_template("forum/index.html", rows=rows, a=a, tags=POPULAR_TAGS, follows=follows)


@bp.route("/forum/ask", methods=["GET", "POST"])
@login_required
def ask():
    if request.method == "POST":
        f = request.form
        if len(f.get("title", "").strip()) < 10:
            flash("Please write a descriptive title (10+ characters).", "danger")
            return render_template("forum/ask.html", tags=POPULAR_TAGS, f=f)
        tags = ",".join(t.strip() for t in f.get("tags", "").split(",") if t.strip())[:300]
        qid = db.insert("INSERT INTO sp_forum_questions(user_id,title,body,tags,audience) VALUES(:u,:t,:b,:tg,:au) "
                        "RETURNING id INTO :new_id", {"u": g.user["id"], "t": f["title"].strip()[:300], "b": f.get("body"),
                                                      "tg": tags, "au": f.get("audience", "ALL")})
        # notify followers of these tags and, for alumni questions, alumni mentors
        tag_list = [t.lower() for t in tags.split(",") if t]
        followers = set()
        for t in tag_list:
            followers |= {r["user_id"] for r in db.query("SELECT user_id FROM sp_tag_follows WHERE LOWER(tag)=:t", {"t": t})}
        if f.get("audience") == "ALUMNI":
            followers |= {r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role='ALUMNI'")}
        followers.discard(g.user["id"])
        if followers:
            notify(list(followers), "New question in topics you follow", f["title"][:150], link=f"/forum/{qid}",
                   category="FORUM", email=False)
        flash("Question posted.", "success")
        return redirect(url_for("forum.thread", qid=qid))
    return render_template("forum/ask.html", tags=POPULAR_TAGS, f={"audience": request.args.get("audience", "ALL")})


@bp.route("/forum/<int:qid>")
@login_required
def thread(qid):
    q = db.one("SELECT q.*, u.full_name, u.user_role FROM sp_forum_questions q JOIN sp_users u ON u.id=q.user_id "
               "WHERE q.id=:i", {"i": qid}) or abort(404)
    db.execute("UPDATE sp_forum_questions SET views=views+1 WHERE id=:i", {"i": qid})
    q["votes"] = _score("Q", qid)
    answers = db.query("""SELECT a.*, u.full_name, u.user_role,
                          (SELECT NVL(SUM(vote_val),0) FROM sp_votes v WHERE v.target_type='A' AND v.target_id=a.id) votes
                          FROM sp_forum_answers a JOIN sp_users u ON u.id=a.user_id WHERE a.question_id=:q""", {"q": qid})
    answers.sort(key=lambda a: (a["id"] != q["accepted_answer_id"], -a["is_verified"], -a["votes"], a["created_at"]))
    ids = [qid] + [a["id"] for a in answers]
    comments = {}
    for c in db.query("SELECT c.*, u.full_name FROM sp_forum_comments c JOIN sp_users u ON u.id=c.user_id WHERE "
                      "(c.target_type='Q' AND c.target_id=:q) OR (c.target_type='A' AND c.target_id IN "
                      "(SELECT id FROM sp_forum_answers WHERE question_id=:q)) ORDER BY c.created_at", {"q": qid}):
        comments.setdefault((c["target_type"], c["target_id"]), []).append(c)
    my_votes = {(v["target_type"], v["target_id"]): v["vote_val"] for v in db.query(
        "SELECT * FROM sp_votes WHERE user_id=:u", {"u": g.user["id"]})}
    bookmarked = db.scalar("SELECT COUNT(*) FROM sp_bookmarks WHERE user_id=:u AND question_id=:q", {"u": g.user["id"], "q": qid})
    related = db.query("SELECT id, title FROM sp_forum_questions WHERE id<>:i AND tags IS NOT NULL AND "
                       "REGEXP_LIKE(tags, :rx, 'i') FETCH FIRST 5 ROWS ONLY",
                       {"i": qid, "rx": "|".join(t.strip() for t in (q["tags"] or "none").split(",") if t.strip()) or "none"})
    return render_template("forum/thread.html", q=q, answers=answers, comments=comments, my_votes=my_votes,
                           bookmarked=bookmarked, related=related, can_verify=g.user["user_role"] in STAFF + ("ALUMNI",))


@bp.route("/forum/<int:qid>/answer", methods=["POST"])
@login_required
def answer(qid):
    q = db.one("SELECT * FROM sp_forum_questions WHERE id=:i", {"i": qid}) or abort(404)
    body = request.form.get("body", "").strip()
    if len(body) < 5:
        flash("Answer is too short.", "warning")
    else:
        verified = 1 if g.user["user_role"] in ("FACULTY", "TPO") and request.form.get("verify") else 0
        db.execute("INSERT INTO sp_forum_answers(question_id,user_id,body,is_verified) VALUES(:q,:u,:b,:v)",
                   {"q": qid, "u": g.user["id"], "b": body, "v": verified})
        if q["user_id"] != g.user["id"]:
            notify(q["user_id"], "New answer to your question", f"{g.user['full_name']} answered: {q['title'][:120]}",
                   link=f"/forum/{qid}", category="FORUM", email=False)
        flash("Answer posted.", "success")
    return redirect(url_for("forum.thread", qid=qid))


@bp.route("/forum/comment", methods=["POST"])
@login_required
def comment():
    f = request.form
    if f.get("body", "").strip():
        db.execute("INSERT INTO sp_forum_comments(target_type,target_id,user_id,body) VALUES(:t,:i,:u,:b)",
                   {"t": f["target_type"], "i": int(f["target_id"]), "u": g.user["id"], "b": f["body"][:1000]})
    return redirect(url_for("forum.thread", qid=int(f["qid"])))


@bp.route("/forum/vote", methods=["POST"])
@login_required
def vote():
    f = request.form
    tt, tid, val = f["target_type"], int(f["target_id"]), int(f.get("value", 1))
    val = 1 if val > 0 else -1
    key = {"u": g.user["id"], "t": tt, "i": tid}
    cur = db.one("SELECT vote_val FROM sp_votes WHERE user_id=:u AND target_type=:t AND target_id=:i", key)
    if cur and cur["vote_val"] == val:
        db.execute("DELETE FROM sp_votes WHERE user_id=:u AND target_type=:t AND target_id=:i", key)
    elif cur:
        db.execute("UPDATE sp_votes SET vote_val=:v WHERE user_id=:u AND target_type=:t AND target_id=:i", {**key, "v": val})
    else:
        db.execute("INSERT INTO sp_votes(user_id,target_type,target_id,vote_val) VALUES(:u,:t,:i,:v)", {**key, "v": val})
    return redirect(url_for("forum.thread", qid=int(f["qid"])) + (f"#a{tid}" if tt == "A" else ""))


@bp.route("/forum/accept/<int:aid>", methods=["POST"])
@login_required
def accept(aid):
    a = db.one("SELECT a.*, q.user_id owner FROM sp_forum_answers a JOIN sp_forum_questions q ON q.id=a.question_id "
               "WHERE a.id=:i", {"i": aid}) or abort(404)
    if a["owner"] != g.user["id"]:
        abort(403)
    db.execute("UPDATE sp_forum_questions SET accepted_answer_id=:a WHERE id=:q", {"a": aid, "q": a["question_id"]})
    notify(a["user_id"], "Your answer was accepted 🎉", "The asker marked your answer as accepted.",
           link=f"/forum/{a['question_id']}", category="FORUM", email=False)
    return redirect(url_for("forum.thread", qid=a["question_id"]))


@bp.route("/forum/verify/<int:aid>", methods=["POST"])
@login_required
def verify(aid):
    if g.user["user_role"] not in ("FACULTY", "TPO", "ADMIN"):
        abort(403)
    a = db.one("SELECT * FROM sp_forum_answers WHERE id=:i", {"i": aid}) or abort(404)
    db.execute("UPDATE sp_forum_answers SET is_verified=1-is_verified WHERE id=:i", {"i": aid})
    return redirect(url_for("forum.thread", qid=a["question_id"]))


@bp.route("/forum/<int:qid>/bookmark", methods=["POST"])
@login_required
def bookmark(qid):
    p = {"u": g.user["id"], "q": qid}
    if db.scalar("SELECT COUNT(*) FROM sp_bookmarks WHERE user_id=:u AND question_id=:q", p):
        db.execute("DELETE FROM sp_bookmarks WHERE user_id=:u AND question_id=:q", p)
    else:
        db.execute("INSERT INTO sp_bookmarks(user_id,question_id) VALUES(:u,:q)", p)
    return redirect(url_for("forum.thread", qid=qid))


@bp.route("/forum/follow/<tag>", methods=["POST"])
@login_required
def follow(tag):
    p = {"u": g.user["id"], "t": tag[:40]}
    if db.scalar("SELECT COUNT(*) FROM sp_tag_follows WHERE user_id=:u AND tag=:t", p):
        db.execute("DELETE FROM sp_tag_follows WHERE user_id=:u AND tag=:t", p)
    else:
        db.execute("INSERT INTO sp_tag_follows(user_id,tag) VALUES(:u,:t)", p)
    return redirect(request.referrer or url_for("forum.index"))
