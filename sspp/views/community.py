"""Announcements, alumni mentoring, placement experiences, helpdesk tickets and feedback/surveys."""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort
from .. import db
from ..security import login_required, roles_required, STAFF
from ..services.notify import notify, all_students

bp = Blueprint("community", __name__)
ANN_CATS = {"GENERAL": "General", "EVENT": "Events & Workshops", "HACKATHON": "Hackathons & Contests",
            "SCHOLARSHIP": "Scholarships", "HIGHER_STUDIES": "Higher Studies (GATE/GRE/CAT)",
            "PREP_MATERIAL": "Preparation Material", "INTERNSHIP": "Internships"}


@bp.route("/announcements")
@login_required
def announcements():
    cat = request.args.get("category")
    sql = "SELECT a.*, u.full_name FROM sp_announcements a LEFT JOIN sp_users u ON u.id=a.created_by"
    rows = db.query(sql + (" WHERE a.category=:c" if cat else "") + " ORDER BY a.created_at DESC", {"c": cat} if cat else {})
    return render_template("community/announcements.html", rows=rows, cats=ANN_CATS, cat=cat)


@bp.route("/announcements/new", methods=["POST"])
@roles_required("TPO", "ADMIN", "FACULTY")
def new_announcement():
    f = request.form
    db.execute("INSERT INTO sp_announcements(title,body,category,link,created_by) VALUES(:t,:b,:c,:l,:u)",
               {"t": f["title"], "b": f.get("body"), "c": f.get("category", "GENERAL"), "l": f.get("link"), "u": g.user["id"]})
    if f.get("notify"):
        notify(all_students(), f"Announcement: {f['title']}", (f.get("body") or "")[:300], link="/announcements",
               category="ANNOUNCEMENT", email=False)
    flash("Announcement published.", "success")
    return redirect(url_for("community.announcements"))


@bp.route("/announcements/<int:aid>/delete", methods=["POST"])
@roles_required("TPO", "ADMIN", "FACULTY")
def delete_announcement(aid):
    db.execute("DELETE FROM sp_announcements WHERE id=:i", {"i": aid})
    return redirect(url_for("community.announcements"))


# ---------------------------------------------------------------- alumni mentoring
@bp.route("/mentoring", methods=["GET", "POST"])
@login_required
def mentoring():
    role = g.user["user_role"]
    if request.method == "POST" and role == "STUDENT":
        f = request.form
        db.execute("INSERT INTO sp_mentorship(student_id,mentor_id,topic,message) VALUES(:s,:m,:t,:msg)",
                   {"s": g.user["id"], "m": int(f["mentor_id"]), "t": f["topic"], "msg": f["message"]})
        notify(int(f["mentor_id"]), "New mentoring request", f"{g.user['full_name']}: {f['topic']}", link="/mentoring",
               category="MENTORING")
        flash("Request sent to your mentor.", "success")
        return redirect(url_for("community.mentoring"))
    mentors = db.query("SELECT id, full_name, designation, email FROM sp_users WHERE user_role='ALUMNI' AND is_active=1 "
                       "AND is_approved=1 ORDER BY full_name")
    if role == "ALUMNI":
        threads = db.query("SELECT m.*, u.full_name student FROM sp_mentorship m JOIN sp_users u ON u.id=m.student_id "
                           "WHERE m.mentor_id=:u ORDER BY m.status DESC, m.created_at DESC", {"u": g.user["id"]})
    elif role == "STUDENT":
        threads = db.query("SELECT m.*, u.full_name mentor FROM sp_mentorship m JOIN sp_users u ON u.id=m.mentor_id "
                           "WHERE m.student_id=:u ORDER BY m.created_at DESC", {"u": g.user["id"]})
    else:
        threads = db.query("SELECT m.*, s.full_name student, u.full_name mentor FROM sp_mentorship m JOIN sp_users u "
                           "ON u.id=m.mentor_id JOIN sp_users s ON s.id=m.student_id ORDER BY m.created_at DESC")
    alumni_q = db.query("SELECT id, title, created_at FROM sp_forum_questions WHERE audience='ALUMNI' ORDER BY created_at DESC "
                        "FETCH FIRST 6 ROWS ONLY")
    return render_template("community/mentoring.html", mentors=mentors, threads=threads, alumni_q=alumni_q)


@bp.route("/mentoring/<int:mid>/reply", methods=["POST"])
@roles_required("ALUMNI")
def mentoring_reply(mid):
    m = db.one("SELECT * FROM sp_mentorship WHERE id=:i AND mentor_id=:u", {"i": mid, "u": g.user["id"]}) or abort(404)
    db.execute("UPDATE sp_mentorship SET reply=:r, status='ANSWERED', replied_at=:n WHERE id=:i",
               {"r": request.form["reply"], "n": datetime.now(), "i": mid})
    notify(m["student_id"], "Your mentor replied", f"{g.user['full_name']} replied to: {m['topic']}", link="/mentoring",
           category="MENTORING")
    flash("Reply sent.", "success")
    return redirect(url_for("community.mentoring"))


# ---------------------------------------------------------------- placement experiences / company reviews
@bp.route("/experiences", methods=["GET", "POST"])
@login_required
def experiences():
    if request.method == "POST":
        f = request.form
        db.execute("INSERT INTO sp_experiences(user_id,company,job_role,exp_year,rounds_desc,content,rating,tips,outcome) "
                   "VALUES(:u,:c,:r,:y,:rd,:ct,:ra,:t,:o)",
                   {"u": g.user["id"], "c": f["company"], "r": f.get("job_role"), "y": int(f.get("exp_year") or datetime.now().year),
                    "rd": f.get("rounds_desc"), "ct": f.get("content"), "ra": int(f.get("rating") or 4), "t": f.get("tips"),
                    "o": f.get("outcome")})
        flash("Thanks for sharing your experience!", "success")
        return redirect(url_for("community.experiences"))
    c = request.args.get("company")
    sql = "SELECT e.*, u.full_name, u.user_role FROM sp_experiences e JOIN sp_users u ON u.id=e.user_id"
    rows = db.query(sql + (" WHERE LOWER(e.company)=LOWER(:c)" if c else "") + " ORDER BY e.created_at DESC",
                    {"c": c} if c else {})
    summary = db.query("SELECT company, COUNT(*) n, ROUND(AVG(rating),1) rating FROM sp_experiences GROUP BY company "
                       "ORDER BY n DESC")
    return render_template("community/experiences.html", rows=rows, summary=summary, company=c)


# ---------------------------------------------------------------- helpdesk
@bp.route("/helpdesk", methods=["GET", "POST"])
@login_required
def helpdesk():
    staff = g.user["user_role"] in ("TPO", "ADMIN")
    if request.method == "POST":
        f = request.form
        if f.get("ticket_id") and staff:
            t = db.one("SELECT * FROM sp_tickets WHERE id=:i", {"i": int(f["ticket_id"])}) or abort(404)
            db.execute("UPDATE sp_tickets SET response=:r, status=:s, updated_at=:n WHERE id=:i",
                       {"r": f.get("response"), "s": f.get("status", "RESOLVED"), "n": datetime.now(), "i": t["id"]})
            notify(t["user_id"], f"Ticket #{t['id']} updated", f"Status: {f.get('status')}. {f.get('response', '')[:200]}",
                   link="/helpdesk", category="HELPDESK")
            flash("Ticket updated.", "success")
        else:
            tid = db.insert("INSERT INTO sp_tickets(user_id,subject,body,category) VALUES(:u,:s,:b,:c) RETURNING id INTO :new_id",
                            {"u": g.user["id"], "s": f["subject"], "b": f.get("body"), "c": f.get("category")})
            tpos = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role='TPO'")]
            notify(tpos, f"New helpdesk ticket #{tid}", f["subject"], link="/helpdesk", category="HELPDESK", email=False)
            flash(f"Ticket #{tid} raised. The placement cell will respond soon.", "success")
        return redirect(url_for("community.helpdesk"))
    if staff:
        rows = db.query("SELECT t.*, u.full_name, u.user_role FROM sp_tickets t JOIN sp_users u ON u.id=t.user_id "
                        "ORDER BY CASE t.status WHEN 'OPEN' THEN 0 WHEN 'IN_PROGRESS' THEN 1 ELSE 2 END, t.created_at DESC")
    else:
        rows = db.query("SELECT t.*, u.full_name, u.user_role FROM sp_tickets t JOIN sp_users u ON u.id=t.user_id "
                        "WHERE t.user_id=:u ORDER BY t.created_at DESC", {"u": g.user["id"]})
    return render_template("community/helpdesk.html", rows=rows, staff=staff)


# ---------------------------------------------------------------- feedback & surveys
@bp.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    if request.method == "POST":
        f = request.form
        db.execute("INSERT INTO sp_feedback(user_id,category,rating,comments) VALUES(:u,:c,:r,:m)",
                   {"u": g.user["id"], "c": f.get("category"), "r": int(f.get("rating") or 5), "m": f.get("comments")})
        flash("Thank you for your feedback!", "success")
        return redirect(url_for("community.feedback"))
    summary = db.query("SELECT category, COUNT(*) n, ROUND(AVG(rating),1) avg FROM sp_feedback GROUP BY category ORDER BY category") \
        if g.user["user_role"] in STAFF else []
    recent = db.query("SELECT f.*, u.full_name FROM sp_feedback f JOIN sp_users u ON u.id=f.user_id ORDER BY f.created_at DESC "
                      "FETCH FIRST 20 ROWS ONLY") if g.user["user_role"] in STAFF else []
    return render_template("community/feedback.html", summary=summary, recent=recent)


@bp.route("/career-hub")
@login_required
def career_hub():
    """Higher studies, scholarships, GATE/GRE/CAT links and counselling resources."""
    anns = db.query("SELECT * FROM sp_announcements WHERE category IN ('SCHOLARSHIP','HIGHER_STUDIES') ORDER BY created_at DESC")
    return render_template("community/career_hub.html", anns=anns)
