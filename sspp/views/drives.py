import csv
import io
import os
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, abort, current_app,
                   send_from_directory, Response)
from werkzeug.utils import secure_filename
from .. import db
from ..security import roles_required, login_required, require_drive_access, can_manage_drive, STAFF
from ..services import eligibility
from ..services.notify import notify

bp = Blueprint("drives", __name__)
STATUSES = ["APPLIED", "SHORTLISTED", "ASSESSMENT", "TECH_INTERVIEW", "HR_INTERVIEW", "SELECTED", "REJECTED"]


def get_drive(did):
    d = db.one("SELECT d.*, c.name company, c.website, c.about, c.industry FROM sp_drives d "
               "JOIN sp_companies c ON c.id=d.company_id WHERE d.id=:i", {"i": did})
    if not d:
        abort(404)
    return d


# ------------------------------------------------------------------ student side
@bp.route("/drives")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("tab", "open")
    status = {"open": "('OPEN')", "past": "('CLOSED','COMPLETED')"}.get(tab, "('OPEN')")
    drives = db.query(f"SELECT d.*, c.name company, c.industry FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id "
                      f"WHERE d.status IN {status} ORDER BY d.drive_date DESC NULLS LAST")
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) if g.user["user_role"] == "STUDENT" else None
    applied = {a["drive_id"]: a["status"] for a in db.query(
        "SELECT drive_id, status FROM sp_applications WHERE student_id=:u", {"u": uid})}
    skills = eligibility.student_skills(uid) if student else set()
    for d in drives:
        d["elig"] = eligibility.check(student, d, skills) if student else None
        d["applicants"] = None
    only = request.args.get("only")
    if only == "eligible" and student:
        drives = [d for d in drives if d["elig"]["ok"]]
    return render_template("drives/index.html", drives=drives, applied=applied, tab=tab, only=only, student=student)


@bp.route("/drives/<int:did>")
@login_required
def detail(did):
    d = get_drive(did)
    if d["status"] in ("PENDING_APPROVAL", "REJECTED") and not can_manage_drive(d):
        abort(404)
    uid = g.user["id"]
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid}) if g.user["user_role"] == "STUDENT" else None
    elig = eligibility.check(student, d) if student else None
    app = db.one("SELECT * FROM sp_applications WHERE drive_id=:d AND student_id=:u", {"d": did, "u": uid})
    history = db.query("SELECT * FROM sp_app_history WHERE application_id=:a ORDER BY changed_at", {"a": app["id"]}) if app else []
    events = db.query("SELECT * FROM sp_events WHERE drive_id=:d ORDER BY start_at", {"d": did})
    resumes = db.query("SELECT id, title FROM sp_resumes WHERE user_id=:u", {"u": uid}) if student else []
    n_app = db.scalar("SELECT COUNT(*) FROM sp_applications WHERE drive_id=:d", {"d": did})
    experiences = db.query("SELECT * FROM sp_experiences WHERE LOWER(company)=LOWER(:c) ORDER BY created_at DESC "
                           "FETCH FIRST 3 ROWS ONLY", {"c": d["company"]})
    return render_template("drives/detail.html", d=d, elig=elig, app=app, history=history, events=events,
                           resumes=resumes, n_app=n_app, can_register=eligibility.registration_open(d),
                           experiences=experiences, manage=can_manage_drive(d))


@bp.route("/drives/<int:did>/apply", methods=["POST"])
@roles_required("STUDENT")
def apply(did):
    d = get_drive(did)
    uid = g.user["id"]
    student = db.one("SELECT * FROM sp_students WHERE user_id=:u", {"u": uid})
    res = eligibility.check(student, d)
    if not eligibility.registration_open(d):
        flash("Registration for this drive is closed.", "warning")
    elif not res["ok"]:
        flash("You are not eligible: " + "; ".join(res["reasons"]), "danger")
    elif db.scalar("SELECT COUNT(*) FROM sp_applications WHERE drive_id=:d AND student_id=:u", {"d": did, "u": uid}):
        flash("You have already registered for this drive.", "info")
    else:
        rid = request.form.get("resume_id")
        aid = db.insert("INSERT INTO sp_applications(drive_id,student_id,status,current_round,resume_id) "
                        "VALUES(:d,:u,'APPLIED','Registration',:r) RETURNING id INTO :new_id",
                        {"d": did, "u": uid, "r": int(rid) if rid and rid.isdigit() else None})
        db.execute("INSERT INTO sp_app_history(application_id,status,note,changed_by) VALUES(:a,'APPLIED',"
                   "'Registered for the drive; eligibility check passed',:u)", {"a": aid, "u": uid})
        notify(uid, f"Registered: {d['company']}", f"You registered for {d['company']} – {d['job_role']}. "
               f"Watch the calendar for the next round.", link=f"/drives/{did}", category="APPLICATION")
        flash(f"Registered for {d['company']} – {d['job_role']}. All the best!", "success")
    return redirect(url_for("drives.detail", did=did))


@bp.route("/applications")
@roles_required("STUDENT")
def applications():
    apps = db.query("""SELECT a.*, d.job_role, d.ctc_lpa, d.rounds, d.drive_date, c.name company FROM sp_applications a
                       JOIN sp_drives d ON d.id=a.drive_id JOIN sp_companies c ON c.id=d.company_id
                       WHERE a.student_id=:u ORDER BY a.updated_at DESC""", {"u": g.user["id"]})
    hist = {}
    if apps:
        for h in db.query("SELECT h.* FROM sp_app_history h JOIN sp_applications a ON a.id=h.application_id "
                          "WHERE a.student_id=:u ORDER BY h.changed_at", {"u": g.user["id"]}):
            hist.setdefault(h["application_id"], []).append(h)
    return render_template("drives/applications.html", apps=apps, hist=hist)


@bp.route("/applications/<int:aid>/withdraw", methods=["POST"])
@roles_required("STUDENT")
def withdraw(aid):
    a = db.one("SELECT * FROM sp_applications WHERE id=:i AND student_id=:u", {"i": aid, "u": g.user["id"]})
    if a and a["status"] in ("APPLIED", "SHORTLISTED"):
        db.execute("UPDATE sp_applications SET status='WITHDRAWN', updated_at=:n WHERE id=:i", {"n": datetime.now(), "i": aid})
        db.execute("INSERT INTO sp_app_history(application_id,status,note,changed_by) VALUES(:a,'WITHDRAWN','Withdrawn by student',:u)",
                   {"a": aid, "u": g.user["id"]})
        flash("Application withdrawn.", "info")
    else:
        flash("This application can no longer be withdrawn.", "warning")
    return redirect(url_for("drives.applications"))


# ------------------------------------------------------------------ offers
@bp.route("/offers", methods=["GET", "POST"])
@roles_required("STUDENT")
def offers():
    uid = g.user["id"]
    if request.method == "POST":  # record an off-campus offer
        f = request.form
        db.execute("INSERT INTO sp_offers(student_id,company_name,job_role,ctc_lpa,location,joining_date,source,status,created_by) "
                   "VALUES(:u,:c,:r,:ctc,:l,:j,'OFF_CAMPUS','ACCEPTED',:u)",
                   {"u": uid, "c": f["company_name"], "r": f["job_role"], "ctc": float(f.get("ctc_lpa") or 0),
                    "l": f.get("location"), "j": datetime.strptime(f["joining_date"], "%Y-%m-%d") if f.get("joining_date") else None})
        flash("Off-campus offer recorded. Congratulations!", "success")
        return redirect(url_for("drives.offers"))
    rows = db.query("SELECT * FROM sp_offers WHERE student_id=:u ORDER BY created_at DESC", {"u": uid})
    return render_template("drives/offers.html", offers=rows)


@bp.route("/offers/<int:oid>/respond", methods=["POST"])
@roles_required("STUDENT")
def respond_offer(oid):
    o = db.one("SELECT * FROM sp_offers WHERE id=:i AND student_id=:u", {"i": oid, "u": g.user["id"]})
    action = request.form.get("action")
    if not o or o["status"] != "PENDING" or action not in ("ACCEPTED", "DECLINED"):
        flash("This offer cannot be updated.", "warning")
    else:
        db.execute("UPDATE sp_offers SET status=:s WHERE id=:i", {"s": action, "i": oid})
        if action == "ACCEPTED":
            db.execute("UPDATE sp_students SET placement_status='PLACED' WHERE user_id=:u", {"u": g.user["id"]})
        staff = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role='TPO'")]
        notify(staff, f"Offer {action.lower()}", f"{g.user['full_name']} {action.lower()} the {o['company_name']} offer.",
               link="/admin/students", category="OFFER", email=False)
        flash(f"Offer {action.lower()}.", "success")
    return redirect(url_for("drives.offers"))


@bp.route("/offers/<int:oid>/letter")
@login_required
def offer_letter(oid):
    o = db.one("SELECT * FROM sp_offers WHERE id=:i", {"i": oid})
    if not o or not o["offer_letter"] or (o["student_id"] != g.user["id"] and g.user["user_role"] not in STAFF + ("RECRUITER",)):
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], o["offer_letter"], as_attachment=False)


# ------------------------------------------------------------------ management (TPO / Admin / Recruiter)
def _drive_form_params(f):
    def dt(v):
        return datetime.strptime(v, "%Y-%m-%dT%H:%M") if v else None
    return {"c": int(f["company_id"]), "r": f["job_role"], "jd": f.get("job_desc"), "jt": f.get("job_type", "FULL_TIME"),
            "ctc": float(f.get("ctc_lpa") or 0), "loc": f.get("locations"), "cg": float(f.get("min_cgpa") or 0),
            "t10": float(f.get("min_tenth") or 0), "t12": float(f.get("min_twelfth") or 0),
            "br": ",".join(f.getlist("branches")), "b": int(f["batch_year"]) if f.get("batch_year") else None,
            "ab": int(f.get("max_active_backlogs") or 0), "hb": int(f.get("max_history_backlogs") or 99),
            "sk": f.get("required_skills"), "rd": f.get("rounds"), "dl": dt(f.get("reg_deadline")),
            "dd": dt(f.get("drive_date")), "v": f.get("venue")}


@bp.route("/manage/drives/new", methods=["GET", "POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def new_drive():
    is_rec = g.user["user_role"] == "RECRUITER"
    if is_rec and not g.user["company_id"]:
        flash("Register your company first.", "warning")
        return redirect(url_for("recruiter.company"))
    if request.method == "POST":
        p = _drive_form_params(request.form)
        if is_rec:
            p["c"] = g.user["company_id"]
        p["st"] = "PENDING_APPROVAL" if is_rec else request.form.get("status", "OPEN")
        p["u"] = g.user["id"]
        did = db.insert("""INSERT INTO sp_drives(company_id,job_role,job_desc,job_type,ctc_lpa,locations,min_cgpa,min_tenth,
            min_twelfth,branches,batch_year,max_active_backlogs,max_history_backlogs,required_skills,rounds,reg_deadline,
            drive_date,venue,status,created_by) VALUES(:c,:r,:jd,:jt,:ctc,:loc,:cg,:t10,:t12,:br,:b,:ab,:hb,:sk,:rd,:dl,:dd,:v,:st,:u)
            RETURNING id INTO :new_id""", p)
        _sync_drive_events(did)
        if p["st"] == "OPEN":
            announce_drive(did)
        else:
            tpos = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role IN ('TPO','ADMIN')")]
            notify(tpos, "Drive awaiting approval", f"{p['r']} drive submitted by {g.user['full_name']}.",
                   link="/admin/approvals", category="APPROVAL", email=False)
        flash("Drive created" + (" and sent for TPO approval." if is_rec else "."), "success")
        return redirect(url_for("drives.manage", did=did))
    companies = db.query("SELECT id, name FROM sp_companies WHERE status='APPROVED' ORDER BY name") if not is_rec else \
        db.query("SELECT id, name FROM sp_companies WHERE id=:c", {"c": g.user["company_id"]})
    return render_template("drives/form.html", d={}, companies=companies)


@bp.route("/manage/drives/<int:did>/edit", methods=["GET", "POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def edit_drive(did):
    d = get_drive(did)
    require_drive_access(d)
    if request.method == "POST":
        p = _drive_form_params(request.form)
        if g.user["user_role"] == "RECRUITER":
            p["c"] = g.user["company_id"]
        p["i"] = did
        db.execute("""UPDATE sp_drives SET company_id=:c, job_role=:r, job_desc=:jd, job_type=:jt, ctc_lpa=:ctc, locations=:loc,
            min_cgpa=:cg, min_tenth=:t10, min_twelfth=:t12, branches=:br, batch_year=:b, max_active_backlogs=:ab,
            max_history_backlogs=:hb, required_skills=:sk, rounds=:rd, reg_deadline=:dl, drive_date=:dd, venue=:v WHERE id=:i""", p)
        if g.user["user_role"] in ("TPO", "ADMIN") and request.form.get("status"):
            db.execute("UPDATE sp_drives SET status=:s WHERE id=:i", {"s": request.form["status"], "i": did})
        _sync_drive_events(did)
        flash("Drive updated.", "success")
        return redirect(url_for("drives.manage", did=did))
    companies = db.query("SELECT id, name FROM sp_companies WHERE status='APPROVED' ORDER BY name")
    return render_template("drives/form.html", d=d, companies=companies)


def _sync_drive_events(did):
    """Keep the calendar's deadline + drive-day entries in step with the drive record."""
    d = get_drive(did)
    for etype, when, title in (("DEADLINE", d["reg_deadline"], f"{d['company']} registration closes"),
                               ("DRIVE", d["drive_date"], f"{d['company']} placement drive")):
        ex = db.one("SELECT id FROM sp_events WHERE drive_id=:d AND event_type=:t", {"d": did, "t": etype})
        if when is None:
            continue
        if ex:
            db.execute("UPDATE sp_events SET start_at=:s, title=:ti WHERE id=:i", {"s": when, "ti": title, "i": ex["id"]})
        else:
            db.execute("INSERT INTO sp_events(title,event_type,start_at,drive_id,venue,created_by) VALUES(:ti,:t,:s,:d,:v,:u)",
                       {"ti": title, "t": etype, "s": when, "d": did, "v": d.get("venue"), "u": g.user["id"] if g.get("user") else None})


def announce_drive(did):
    d = get_drive(did)
    ids = [s["user_id"] for s in eligibility.eligible_students(d)]
    notify(ids, f"New drive: {d['company']}", f"{d['company']} is hiring {d['job_role']} (₹{d['ctc_lpa']} LPA). "
           f"You are eligible – register before {d['reg_deadline']:%d %b}." if d["reg_deadline"] else "You are eligible.",
           link=f"/drives/{did}", category="DRIVE")
    return len(ids)


@bp.route("/manage/drives/<int:did>")
@roles_required("TPO", "ADMIN", "RECRUITER", "FACULTY")
def manage(did):
    d = get_drive(did)
    if g.user["user_role"] != "FACULTY":
        require_drive_access(d)
    status_f = request.args.get("status")
    sql = """SELECT a.*, u.full_name, u.email, s.reg_no, s.department, s.cgpa, s.active_backlogs, s.batch_year
             FROM sp_applications a JOIN sp_users u ON u.id=a.student_id LEFT JOIN sp_students s ON s.user_id=a.student_id
             WHERE a.drive_id=:d"""
    params = {"d": did}
    if status_f:
        sql += " AND a.status=:s"
        params["s"] = status_f
    apps = db.query(sql + " ORDER BY s.cgpa DESC NULLS LAST", params)
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM sp_applications WHERE drive_id=:d "
                                                    "GROUP BY status", {"d": did})}
    eligible = eligibility.eligible_students(d)
    applied_ids = {r["student_id"] for r in db.query("SELECT student_id FROM sp_applications WHERE drive_id=:d", {"d": did})}
    not_applied = [s for s in eligible if s["user_id"] not in applied_ids]
    events = db.query("SELECT * FROM sp_events WHERE drive_id=:d ORDER BY start_at", {"d": did})
    offers = db.query("SELECT o.*, u.full_name FROM sp_offers o JOIN sp_applications a ON a.id=o.application_id "
                      "JOIN sp_users u ON u.id=o.student_id WHERE a.drive_id=:d", {"d": did})
    return render_template("drives/manage.html", d=d, apps=apps, counts=counts, statuses=STATUSES, n_eligible=len(eligible),
                           not_applied=not_applied, events=events, offers=offers, status_f=status_f,
                           readonly=g.user["user_role"] == "FACULTY")


@bp.route("/manage/drives/<int:did>/invite", methods=["POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def invite(did):
    d = get_drive(did)
    require_drive_access(d)
    if d["status"] != "OPEN":
        flash("Only open (approved) drives can be announced.", "warning")
    else:
        n = announce_drive(did)
        flash(f"Invitation sent to {n} eligible students.", "success")
    return redirect(url_for("drives.manage", did=did))


@bp.route("/manage/drives/<int:did>/status", methods=["POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def update_status(did):
    d = get_drive(did)
    require_drive_access(d)
    new = request.form.get("new_status")
    ids = [int(x) for x in request.form.getlist("app_ids")]
    note = request.form.get("note") or ""
    if new not in STATUSES or not ids:
        flash("Select applicants and a status.", "warning")
        return redirect(url_for("drives.manage", did=did))
    n = _set_status(d, ids, new, note)
    flash(f"Updated {n} application(s) to {new.replace('_', ' ').title()}.", "success")
    return redirect(url_for("drives.manage", did=did))


def _set_status(d, app_ids, new, note=""):
    n = 0
    for aid in app_ids:
        a = db.one("SELECT * FROM sp_applications WHERE id=:i AND drive_id=:d", {"i": aid, "d": d["id"]})
        if not a:
            continue
        db.execute("UPDATE sp_applications SET status=:s, current_round=:r, remarks=:m, updated_at=:n WHERE id=:i",
                   {"s": new, "r": new.replace("_", " ").title(), "m": note or a["remarks"], "n": datetime.now(), "i": aid})
        db.execute("INSERT INTO sp_app_history(application_id,status,note,changed_by) VALUES(:a,:s,:no,:u)",
                   {"a": aid, "s": new, "no": note, "u": g.user["id"]})
        msg = {"SELECTED": f"Congratulations! You have been selected by {d['company']}.",
               "REJECTED": f"Thank you for participating in the {d['company']} drive. Keep preparing – more opportunities are coming."
               }.get(new, f"Your {d['company']} application moved to: {new.replace('_', ' ').title()}.")
        notify(a["student_id"], f"{d['company']}: application update", msg + (f" Note: {note}" if note else ""),
               link="/applications", category="RESULT" if new in ("SELECTED", "REJECTED") else "APPLICATION")
        n += 1
    return n


@bp.route("/manage/drives/<int:did>/results", methods=["POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def upload_results(did):
    """CSV upload: email_or_regno,status[,note]."""
    d = get_drive(did)
    require_drive_access(d)
    f = request.files.get("file")
    text = f.read().decode("utf-8-sig") if f and f.filename else request.form.get("csv_text", "")
    updated, errors = 0, []
    for row in csv.reader(io.StringIO(text)):
        if not row or row[0].strip().lower() in ("email", "reg_no", "regno"):
            continue
        key, status = row[0].strip().lower(), (row[1].strip().upper() if len(row) > 1 else "")
        if status not in STATUSES:
            errors.append(f"{key}: invalid status '{status}'")
            continue
        a = db.one("""SELECT a.id FROM sp_applications a JOIN sp_users u ON u.id=a.student_id
                      LEFT JOIN sp_students s ON s.user_id=a.student_id
                      WHERE a.drive_id=:d AND (LOWER(u.email)=:k OR LOWER(s.reg_no)=:k)""", {"d": did, "k": key})
        if not a:
            errors.append(f"{key}: no application found")
            continue
        updated += _set_status(d, [a["id"]], status, row[2].strip() if len(row) > 2 else "Result uploaded")
    flash(f"Results processed: {updated} updated." + (f" {len(errors)} issue(s): " + "; ".join(errors[:5]) if errors else ""),
          "success" if not errors else "warning")
    return redirect(url_for("drives.manage", did=did))


@bp.route("/manage/drives/<int:did>/schedule", methods=["POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def schedule_round(did):
    d = get_drive(did)
    require_drive_access(d)
    f = request.form
    start = datetime.strptime(f["start_at"], "%Y-%m-%dT%H:%M")
    end = datetime.strptime(f["end_at"], "%Y-%m-%dT%H:%M") if f.get("end_at") else None
    db.execute("INSERT INTO sp_events(title,event_type,start_at,end_at,drive_id,description,venue,created_by) "
               "VALUES(:t,:et,:s,:e,:d,:de,:v,:u)",
               {"t": f.get("title") or f"{d['company']} – {f['event_type'].title()}", "et": f["event_type"], "s": start,
                "e": end, "d": did, "de": f.get("description"), "v": f.get("venue"), "u": g.user["id"]})
    ids = [r["student_id"] for r in db.query("SELECT student_id FROM sp_applications WHERE drive_id=:d AND status NOT IN "
                                             "('REJECTED','WITHDRAWN')", {"d": did})]
    notify(ids, f"{d['company']}: round scheduled", f"{f.get('title') or f['event_type'].title()} on {start:%d %b %Y, %I:%M %p}"
           f"{' at ' + f['venue'] if f.get('venue') else ''}.", link="/calendar", category="SCHEDULE")
    flash("Round scheduled and candidates notified.", "success")
    return redirect(url_for("drives.manage", did=did))


@bp.route("/manage/drives/<int:did>/offer", methods=["POST"])
@roles_required("TPO", "ADMIN", "RECRUITER")
def issue_offer(did):
    d = get_drive(did)
    require_drive_access(d)
    f = request.form
    a = db.one("SELECT * FROM sp_applications WHERE id=:i AND drive_id=:d", {"i": int(f["app_id"]), "d": did})
    if not a:
        abort(404)
    fname = None
    up = request.files.get("letter")
    if up and up.filename:
        fname = f"offer_{did}_{a['student_id']}_{secure_filename(up.filename)}"
        up.save(os.path.join(current_app.config["UPLOAD_FOLDER"], fname))
    pd = lambda v: datetime.strptime(v, "%Y-%m-%d") if v else None
    db.execute("INSERT INTO sp_offers(student_id,application_id,company_name,job_role,ctc_lpa,location,joining_date,accept_by,"
               "offer_letter,source,status,created_by) VALUES(:s,:a,:c,:r,:ctc,:l,:j,:ab,:f,'CAMPUS','PENDING',:u)",
               {"s": a["student_id"], "a": a["id"], "c": d["company"], "r": f.get("job_role") or d["job_role"],
                "ctc": float(f.get("ctc_lpa") or d["ctc_lpa"] or 0), "l": f.get("location") or d["locations"],
                "j": pd(f.get("joining_date")), "ab": pd(f.get("accept_by")), "f": fname, "u": g.user["id"]})
    if a["status"] != "SELECTED":
        _set_status(d, [a["id"]], "SELECTED", "Offer issued")
    if f.get("accept_by"):
        db.execute("INSERT INTO sp_events(title,event_type,start_at,drive_id,created_by) VALUES(:t,'OFFER_DEADLINE',:s,:d,:u)",
                   {"t": f"{d['company']} offer acceptance deadline", "s": pd(f["accept_by"]), "d": did, "u": g.user["id"]})
    notify(a["student_id"], f"Offer letter from {d['company']}", f"You have received an offer for {d['job_role']}. "
           "Review and respond on the Offers page.", link="/offers", category="OFFER")
    flash("Offer issued.", "success")
    return redirect(url_for("drives.manage", did=did))


@bp.route("/manage/drives/<int:did>/export")
@roles_required("TPO", "ADMIN", "RECRUITER")
def export(did):
    d = get_drive(did)
    require_drive_access(d)
    rows = db.query("""SELECT u.full_name, u.email, s.reg_no, s.department, s.batch_year, s.cgpa, a.status, a.current_round,
                       a.applied_at FROM sp_applications a JOIN sp_users u ON u.id=a.student_id
                       LEFT JOIN sp_students s ON s.user_id=a.student_id WHERE a.drive_id=:d ORDER BY u.full_name""", {"d": did})
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Email", "Reg No", "Department", "Batch", "CGPA", "Status", "Round", "Applied At"])
    for r in rows:
        w.writerow([r["full_name"], r["email"], r["reg_no"], r["department"], r["batch_year"], r["cgpa"], r["status"],
                    r["current_round"], r["applied_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=drive_{did}_applicants.csv"})
