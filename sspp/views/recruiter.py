from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from .. import db
from ..security import roles_required
from ..services.notify import notify

bp = Blueprint("recruiter", __name__, url_prefix="/recruiter")


@bp.route("/")
@roles_required("RECRUITER")
def home():
    cid = g.user["company_id"]
    company = db.one("SELECT * FROM sp_companies WHERE id=:c", {"c": cid}) if cid else None
    drives = db.query("""SELECT d.*, (SELECT COUNT(*) FROM sp_applications a WHERE a.drive_id=d.id) applicants,
                         (SELECT COUNT(*) FROM sp_applications a WHERE a.drive_id=d.id AND a.status='SELECTED') selected
                         FROM sp_drives d WHERE d.company_id=:c ORDER BY d.created_at DESC""", {"c": cid or -1})
    funnel = {r["status"]: r["n"] for r in db.query(
        "SELECT a.status, COUNT(*) n FROM sp_applications a JOIN sp_drives d ON d.id=a.drive_id WHERE d.company_id=:c "
        "GROUP BY a.status", {"c": cid or -1})}
    offers = db.scalar("SELECT COUNT(*) FROM sp_offers o JOIN sp_applications a ON a.id=o.application_id JOIN sp_drives d "
                       "ON d.id=a.drive_id WHERE d.company_id=:c", {"c": cid or -1})
    return render_template("recruiter/home.html", company=company, drives=drives, funnel=funnel, offers=offers)


@bp.route("/company", methods=["GET", "POST"])
@roles_required("RECRUITER")
def company():
    cid = g.user["company_id"]
    c = db.one("SELECT * FROM sp_companies WHERE id=:c", {"c": cid}) if cid else None
    if request.method == "POST":
        f = request.form
        p = {"n": f["name"], "i": f.get("industry"), "w": f.get("website"), "a": f.get("about"), "h": f.get("hq_location")}
        if c:
            db.execute("UPDATE sp_companies SET name=:n, industry=:i, website=:w, about=:a, hq_location=:h WHERE id=:id",
                       {**p, "id": cid})
        else:
            cid = db.insert("INSERT INTO sp_companies(name,industry,website,about,hq_location,status,created_by) "
                            "VALUES(:n,:i,:w,:a,:h,'PENDING',:u) RETURNING id INTO :new_id", {**p, "u": g.user["id"]})
            db.execute("UPDATE sp_users SET company_id=:c WHERE id=:u", {"c": cid, "u": g.user["id"]})
            tpos = [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role IN ('TPO','ADMIN')")]
            notify(tpos, "Company registration awaiting approval", f"{f['name']} registered by {g.user['full_name']}.",
                   link="/admin/approvals", category="APPROVAL", email=False)
        flash("Company profile saved.", "success")
        return redirect(url_for("recruiter.home"))
    return render_template("recruiter/company.html", c=c or {})
