from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, jsonify
from .. import db, EVENT_TYPES
from ..security import login_required, roles_required, STAFF
from ..services.notify import notify, all_students

bp = Blueprint("calendar", __name__)


@bp.route("/calendar")
@login_required
def view():
    upcoming = _visible_events(datetime.now(), None, limit=12)
    return render_template("calendar.html", upcoming=upcoming, focus=request.args.get("event"))


def _visible_events(start, end, limit=None):
    sql = """SELECT e.*, c.name company FROM sp_events e LEFT JOIN sp_drives d ON d.id=e.drive_id
             LEFT JOIN sp_companies c ON c.id=d.company_id WHERE e.start_at >= :s"""
    p = {"s": start}
    if end:
        sql += " AND e.start_at < :e"
        p["e"] = end
    role = g.user["user_role"]
    if role == "STUDENT":  # general events + events of open drives + drives they applied to
        sql += """ AND (e.drive_id IS NULL OR d.status='OPEN' OR e.drive_id IN
                   (SELECT drive_id FROM sp_applications WHERE student_id=:u))"""
        p["u"] = g.user["id"]
    elif role == "RECRUITER":
        sql += " AND (d.company_id = :c)"
        p["c"] = g.user["company_id"] or -1
    sql += " ORDER BY e.start_at"
    if limit:
        sql += f" FETCH FIRST {int(limit)} ROWS ONLY"
    return db.query(sql, p)


@bp.route("/api/events")
@login_required
def api_events():
    def parse(v):
        return datetime.fromisoformat(v[:19]) if v else None
    start = parse(request.args.get("start")) or datetime(2000, 1, 1)
    end = parse(request.args.get("end"))
    out = []
    for e in _visible_events(start, end):
        label, color = EVENT_TYPES.get(e["event_type"], (e["event_type"], "#6c757d"))
        out.append({"id": e["id"], "title": e["title"], "start": e["start_at"].isoformat(),
                    "end": e["end_at"].isoformat() if e["end_at"] else None, "color": color,
                    "extendedProps": {"type": label, "venue": e["venue"] or "", "description": e["description"] or "",
                                      "company": e["company"] or "", "drive_id": e["drive_id"]}})
    return jsonify(out)


@bp.route("/calendar/new", methods=["POST"])
@roles_required("TPO", "ADMIN", "FACULTY")
def new_event():
    f = request.form
    start = datetime.strptime(f["start_at"], "%Y-%m-%dT%H:%M")
    end = datetime.strptime(f["end_at"], "%Y-%m-%dT%H:%M") if f.get("end_at") else None
    db.execute("INSERT INTO sp_events(title,event_type,start_at,end_at,description,venue,created_by) "
               "VALUES(:t,:et,:s,:e,:d,:v,:u)", {"t": f["title"], "et": f["event_type"], "s": start, "e": end,
                                                 "d": f.get("description"), "v": f.get("venue"), "u": g.user["id"]})
    if f.get("notify"):
        notify(all_students(), f"New event: {f['title']}", f"{EVENT_TYPES.get(f['event_type'], ('Event',))[0]} on "
               f"{start:%d %b %Y, %I:%M %p}{' at ' + f['venue'] if f.get('venue') else ''}.", link="/calendar",
               category="SCHEDULE", email=False)
    flash("Event added to the placement calendar.", "success")
    return redirect(url_for("calendar.view"))


@bp.route("/calendar/<int:eid>/delete", methods=["POST"])
@roles_required("TPO", "ADMIN", "FACULTY")
def delete_event(eid):
    db.execute("DELETE FROM sp_events WHERE id=:i", {"i": eid})
    flash("Event deleted.", "info")
    return redirect(url_for("calendar.view"))


# ---------------- attendance for placement training sessions
@bp.route("/attendance")
@login_required
def attendance():
    sessions = db.query("SELECT e.*, (SELECT COUNT(*) FROM sp_event_attendance a WHERE a.event_id=e.id) present "
                        "FROM sp_events e WHERE e.event_type IN ('TRAINING','MOCK','PPT') ORDER BY e.start_at DESC")
    mine = {r["event_id"] for r in db.query("SELECT event_id FROM sp_event_attendance WHERE user_id=:u", {"u": g.user["id"]})}
    total_students = db.scalar("SELECT COUNT(*) FROM sp_users WHERE user_role='STUDENT'")
    return render_template("attendance.html", sessions=sessions, mine=mine, total_students=total_students,
                           now=datetime.now())


@bp.route("/attendance/<int:eid>/checkin", methods=["POST"])
@roles_required("STUDENT")
def checkin(eid):
    e = db.one("SELECT * FROM sp_events WHERE id=:i", {"i": eid})
    if not e or e["start_at"].date() != datetime.now().date():
        flash("Check-in is available only on the day of the session.", "warning")
    elif not db.scalar("SELECT COUNT(*) FROM sp_event_attendance WHERE event_id=:e AND user_id=:u", {"e": eid, "u": g.user["id"]}):
        db.execute("INSERT INTO sp_event_attendance(event_id,user_id) VALUES(:e,:u)", {"e": eid, "u": g.user["id"]})
        flash("Attendance marked.", "success")
    return redirect(url_for("calendar.attendance"))


@bp.route("/attendance/<int:eid>", methods=["GET", "POST"])
@roles_required("TPO", "ADMIN", "FACULTY")
def mark_attendance(eid):
    e = db.one("SELECT * FROM sp_events WHERE id=:i", {"i": eid})
    if request.method == "POST":
        db.execute("DELETE FROM sp_event_attendance WHERE event_id=:e", {"e": eid})
        rows = [{"e": eid, "u": int(x)} for x in request.form.getlist("present")]
        if rows:
            db.executemany("INSERT INTO sp_event_attendance(event_id,user_id) VALUES(:e,:u)", rows)
        flash(f"Attendance saved ({len(rows)} present).", "success")
        return redirect(url_for("calendar.attendance"))
    dep = request.args.get("dept")
    sql = "SELECT u.id, u.full_name, s.reg_no, s.department FROM sp_users u JOIN sp_students s ON s.user_id=u.id"
    students = db.query(sql + (" WHERE s.department=:d" if dep else "") + " ORDER BY s.department, u.full_name",
                        {"d": dep} if dep else {})
    present = {r["user_id"] for r in db.query("SELECT user_id FROM sp_event_attendance WHERE event_id=:e", {"e": eid})}
    return render_template("attendance_mark.html", e=e, students=students, present=present, dept=dep)
